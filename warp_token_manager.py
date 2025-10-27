#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp Token 自动化管理系统

通过动态部署和销毁 Cloudflare Workers 来绕过 IP 限制，
实现无限制的 Warp 匿名 token 获取。

核心流程：
1. 生成随机 Worker 名称
2. 部署 Worker 到 Cloudflare
3. 调用 Worker 获取 token
4. 删除 Worker 释放资源
"""

import asyncio
import httpx
import json
import os
import random
import string
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from warp2protobuf.core.logging import logger


class CloudflareWorkerManager:
    """Cloudflare Worker 自动化管理器"""

    def __init__(self, cf_api_token: str, cf_account_id: str, cf_subdomain: str=""):
        """
        初始化管理器

        Args:
            cf_api_token: Cloudflare API Token (需要 Workers:Edit 权限)
            cf_account_id: Cloudflare Account ID
        """
        self.cf_api_token = cf_api_token
        self.cf_account_id = cf_account_id
        self.subdomain = cf_subdomain
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}"
        self.headers = {
            "Authorization": f"Bearer {cf_api_token}",
            "Content-Type": "application/json"
        }

    def _generate_worker_name(self) -> str:
        """生成随机的 Worker 名称"""
        timestamp = str(int(time.time()))
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return f"warp-token-{timestamp}-{random_suffix}"

    async def _get_workers_subdomain(self) -> str:
        """获取 Workers subdomain - 优先使用配置的 subdomain"""
        # 1. 优先使用初始化时提供的 subdomain
        if self.subdomain:
            logger.debug(f"使用配置的 subdomain: {self.subdomain}")
            return self.subdomain

        # 2. 检查环境变量
        env_subdomain = os.getenv("CLOUDFLARE_WORKERS_SUBDOMAIN")
        if env_subdomain:
            logger.debug(f"使用环境变量 subdomain: {env_subdomain}")
            return env_subdomain

        # 3. 尝试 API 获取（可能会 404）
        try:
            url = f"{self.base_url}/workers/subdomain"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    result = response.json()
                    if result.get("success"):
                        subdomain = result["result"]["subdomain"]
                        logger.debug(f"API 获取到 Workers subdomain: {subdomain}")
                        return subdomain
                    else:
                        logger.warning(f"API 获取 subdomain 失败: {result.get('errors')}")
                else:
                    logger.warning(f"Workers subdomain API 返回 {response.status_code}，可能接口不存在")
        except Exception as e:
            logger.warning(f"API 获取 subdomain 失败: {e}")

        # 4. 使用默认值
        default_subdomain = "mucsbr"
        logger.warning(f"使用默认 subdomain: {default_subdomain}")
        logger.info("💡 建议设置环境变量 CLOUDFLARE_WORKERS_SUBDOMAIN 或在多账号配置中指定 subdomain")
        return default_subdomain

    async def _enable_workers_dev_route(self, worker_name: str, subdomain: str) -> bool:
        """启用 Worker 的 workers.dev 路由"""
        try:
            logger.info(f"正在启用 Worker 路由: {worker_name}")

            url = f"{self.base_url}/workers/scripts/{worker_name}/subdomain"

            payload = {
                "enabled": True
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=self.headers, json=payload)

                if response.status_code in [200, 201]:
                    result = response.json()
                    if result.get("success"):
                        logger.info(f"Worker 路由启用成功: {worker_name}")
                        return True
                    else:
                        logger.error(f"启用路由失败: {result.get('errors')}")
                        return False
                else:
                    logger.error(f"启用路由 API 调用失败: {response.status_code} {response.text}")
                    return False

        except Exception as e:
            logger.error(f"启用 Worker 路由时发生错误: {e}")
            return False

    async def _get_worker_script(self) -> str:
        """获取 Worker 脚本内容"""
        # warp_token_manager.py 在项目根目录，cloudflare-worker.js 也在项目根目录
        script_path = Path(__file__).parent / "cloudflare-worker.js"

        if not script_path.exists():
            raise FileNotFoundError(f"Worker 脚本未找到: {script_path}")

        return script_path.read_text(encoding='utf-8')

    async def deploy_worker(self, worker_name: str) -> Dict[str, Any]:
        """
        部署 Worker 到 Cloudflare

        Args:
            worker_name: Worker 名称

        Returns:
            部署结果，包含 Worker URL
        """
        logger.info(f"正在部署 Worker: {worker_name}")

        try:
            # 获取 Worker 脚本
            script_content = await self._get_worker_script()

            # 部署 Worker
            url = f"{self.base_url}/workers/scripts/{worker_name}"

            # 使用新的 Workers API 格式
            metadata = {
                "main_module": "worker.js",
                "compatibility_date": "2024-01-01",
                "compatibility_flags": ["nodejs_compat"]
            }

            files = {
                'metadata': ('metadata.json', json.dumps(metadata), 'application/json'),
                'worker.js': ('worker.js', script_content, 'application/javascript+module')
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    url,
                    headers={"Authorization": f"Bearer {self.cf_api_token}"},
                    files=files
                )

                if response.status_code not in [200, 201]:
                    raise Exception(f"Worker 部署失败: {response.status_code} {response.text}")

                result = response.json()

                if not result.get("success"):
                    errors = result.get("errors", [])
                    raise Exception(f"Worker 部署失败: {errors}")

                # 获取 Worker 的 subdomain
                # 需要调用 API 获取账户的 workers subdomain
                subdomain = await self._get_workers_subdomain()
                worker_url = f"https://{worker_name}.{subdomain}.workers.dev"

                logger.info(f"Worker 部署成功: {worker_url}")

                # 启用 workers.dev 路由
                await self._enable_workers_dev_route(worker_name, subdomain)

                return {
                    "success": True,
                    "worker_name": worker_name,
                    "worker_url": worker_url,
                    "script_id": result["result"]["id"]
                }

        except Exception as e:
            logger.error(f"Worker 部署失败: {e}")
            raise

    async def delete_worker(self, worker_name: str) -> bool:
        """
        删除 Worker

        Args:
            worker_name: Worker 名称

        Returns:
            是否删除成功
        """
        logger.info(f"正在删除 Worker: {worker_name}")

        try:
            url = f"{self.base_url}/workers/scripts/{worker_name}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(url, headers=self.headers)

                if response.status_code in (200, 202, 204):
                    # 200: 带 JSON 成功; 202/204: 已接受/无内容也视为成功
                    try:
                        result = response.json()
                        if isinstance(result, dict) and result.get("success") is False:
                            logger.error(f"Worker 删除失败: {result.get('errors')}")
                            return False
                    except Exception:
                        pass
                    logger.info(f"Worker 删除成功: {worker_name}")
                    return True
                elif response.status_code == 404:
                    logger.warning(f"Worker 不存在，可能已被删除: {worker_name}")
                    return True
                else:
                    logger.error(f"Worker 删除失败: {response.status_code} {response.text}")
                    return False

        except Exception as e:
            logger.error(f"删除 Worker 时发生错误: {e}")
            return False

    async def get_token_from_worker(self, worker_url: str, max_retries: int = 3) -> Optional[str]:
        """
        从 Worker 获取 token

        Args:
            worker_url: Worker URL
            max_retries: 最大重试次数

        Returns:
            获取到的 access token，失败返回 None
        """
        logger.info(f"正在从 Worker 获取 token: {worker_url}")

        for attempt in range(max_retries):
            try:
                # 等待 Worker 完全部署（第一次尝试时）
                if attempt == 0:
                    await asyncio.sleep(2)

                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{worker_url}/token")

                    if response.status_code == 200:
                        data = response.json()

                        if data.get("success"):
                            access_token = data.get("accessToken")
                            if access_token:
                                logger.info(f"成功获取 token: {access_token[:50]}...")
                                return access_token
                            else:
                                logger.error("响应中未包含 accessToken")
                                logger.error(f"完整响应: {data}")
                        else:
                            error_msg = data.get("error", "未知错误")
                            logger.error(f"Worker 返回错误: {error_msg}")
                            logger.error(f"完整响应: {data}")
                    else:
                        logger.error(f"HTTP 错误: {response.status_code}")
                        logger.error(f"响应内容: {response.text[:500]}")

            except Exception as e:
                logger.error(f"第 {attempt + 1} 次尝试失败: {e}")

            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 指数退避
                logger.info(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)

        logger.error("所有尝试均失败，无法获取 token")
        return None

    # === 新增：Workers 列表与清理能力 ===

    async def list_all_workers(self) -> List[Dict[str, Any]]:
        """列出账户下所有 scripts 形式的 Workers（单次请求）。"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.base_url}/workers/scripts"
                resp = await client.get(url, headers=self.headers)
                if resp.status_code != 200:
                    logger.error(f"列出 Workers 失败: {resp.status_code} {resp.text}")
                    return []
                data = resp.json()
                if not data.get("success"):
                    logger.error(f"列出 Workers 返回错误: {data.get('errors')}")
                    return []
                workers = data.get("result", [])
                logger.info(f"共列出 {len(workers)} 个 Workers")
                return workers
        except Exception as e:
            logger.error(f"列出 Workers 时发生异常: {e}")
            return []

    async def cleanup_workers_by_prefix(self, prefix: str, threshold: Optional[int] = None, max_concurrency: int = 5) -> Dict[str, Any]:
        """
        按前缀批量清理 Workers。
        - prefix: 需要匹配的名前缀，例如 "warp-token-"
        - threshold: 若提供且当前匹配数量 <= threshold，则不清理（保护阈值）
        - max_concurrency: 并发删除上限
        返回统计信息。
        """
        workers = await self.list_all_workers()
        targets = []
        for w in workers:
            # scripts 列表返回的对象中，一般包含 "id" 或 "name" 字段
            name = w.get("id") or w.get("name") or ""
            if isinstance(name, str) and name.startswith(prefix):
                targets.append(name)
        total = len(targets)
        if threshold is not None and total <= threshold:
            logger.info(f"匹配 {total} 个，未超过阈值 {threshold}，不执行清理")
            return {"matched": total, "deleted": 0, "skipped": total}

        logger.warning(f"开始清理 {total} 个匹配前缀 '{prefix}' 的 Workers")

        sem = asyncio.Semaphore(max_concurrency)
        deleted = 0

        async def _del(name: str) -> bool:
            async with sem:
                ok = await self.delete_worker(name)
                return ok

        results = await asyncio.gather(*[_del(n) for n in targets], return_exceptions=True)
        for r in results:
            if r is True:
                deleted += 1
        logger.info(f"清理完成：匹配 {total}，成功删除 {deleted}，失败 {total - deleted}")
        return {"matched": total, "deleted": deleted, "failed": total - deleted}


class WarpTokenService:
    """Warp Token 服务 - 高级封装"""

    def __init__(self, cf_api_token: str, cf_account_id: str, cf_subdomain: str=""):
        self.worker_manager = CloudflareWorkerManager(cf_api_token, cf_account_id, cf_subdomain)

    async def acquire_fresh_token(self) -> Optional[str]:
        """
        获取新的 Warp 访问令牌

        通过部署临时 Worker 获取 token，然后清理资源

        Returns:
            获取到的 access token，失败返回 None
        """
        worker_name = None

        try:
            # 1. 生成 Worker 名称
            worker_name = self.worker_manager._generate_worker_name()
            logger.info(f"开始获取新 token，Worker 名称: {worker_name}")

            # 2. 部署 Worker
            deploy_result = await self.worker_manager.deploy_worker(worker_name)
            worker_url = deploy_result["worker_url"]

            # 3. 获取 token
            access_token = await self.worker_manager.get_token_from_worker(worker_url)

            if access_token:
                logger.info("成功获取新的 Warp 访问令牌")
                return access_token
            else:
                logger.error("获取 token 失败")
                return None

        except Exception as e:
            logger.error(f"获取 token 过程中发生错误: {e}")
            return None

        finally:
            # 4. 清理 Worker（无论成功失败都要清理）
            if worker_name:
                try:
                    await self.worker_manager.delete_worker(worker_name)
                except Exception as e:
                    logger.error(f"清理 Worker 时发生错误: {e}")

    async def ensure_valid_token(self) -> str:
        """
        确保有有效的 token

        检查现有 token 是否有效，无效则获取新的

        Returns:
            有效的 access token

        Raises:
            RuntimeError: 无法获取有效 token
        """
        from .auth import get_jwt_token, is_token_expired

        # 检查现有 token
        current_token = get_jwt_token()

        if current_token and not is_token_expired(current_token, buffer_minutes=5):
            logger.info("现有 token 仍然有效")
            return current_token

        # 获取新 token
        logger.info("需要获取新的 token")
        new_token = await self.acquire_fresh_token()

        if new_token:
            # 保存新 token
            from .auth import update_env_file
            update_env_file(new_token)
            return new_token
        else:
            raise RuntimeError("无法获取有效的 Warp 访问令牌")


class MultiAccountTokenService:
    """多账号轮换 Token 服务

    支持多个 Cloudflare 账号轮换使用，提高 token 获取成功率。
    每次请求使用不同的账号，避免单一账号的 IP 限制。
    """

    def __init__(self, accounts: Optional[List[Dict[str, str]]] = None):
        """
        初始化多账号服务

        Args:
            accounts: 账号列表，每个账号包含 api_token 和 account_id
                     如果不提供，将从环境变量加载

        Example:
            accounts = [
                {"api_token": "xxx", "account_id": "yyy"},
                {"api_token": "aaa", "account_id": "bbb"},
            ]
        """
        if accounts:
            self.accounts = accounts
        else:
            # 从环境变量加载多账号配置
            self.accounts = self._load_accounts_from_env()

        if not self.accounts:
            raise ValueError("至少需要配置一个 Cloudflare 账号")

        self.current_index = 0
        self.lock = asyncio.Lock()
        self.failed_accounts = set()  # 记录失败的账号索引

        logger.info(f"多账号服务初始化成功，共有 {len(self.accounts)} 个账号")

    def _load_accounts_from_env(self) -> List[Dict[str, str]]:
        """从环境变量加载多账号配置

        支持两种配置方式：
        1. 单账号（向后兼容）：CLOUDFLARE_API_TOKEN 和 CLOUDFLARE_ACCOUNT_ID
        2. 多账号：CLOUDFLARE_ACCOUNTS（JSON 格式）或
                  CLOUDFLARE_API_TOKEN_1, CLOUDFLARE_ACCOUNT_ID_1,
                  CLOUDFLARE_API_TOKEN_2, CLOUDFLARE_ACCOUNT_ID_2, ...
        """
        accounts = []

        # 方式1：JSON 格式的多账号配置
        accounts_json = os.getenv("CLOUDFLARE_ACCOUNTS")
        if accounts_json:
            try:
                accounts_data = json.loads(accounts_json)
                if isinstance(accounts_data, list):
                    accounts = accounts_data
                logger.info(f"从 CLOUDFLARE_ACCOUNTS 加载了 {len(accounts)} 个账号")
            except json.JSONDecodeError as e:
                logger.error(f"解析 CLOUDFLARE_ACCOUNTS 失败: {e}")

        # 方式2：编号的环境变量
        if not accounts:
            index = 1
            while True:
                api_token = os.getenv(f"CLOUDFLARE_API_TOKEN_{index}")
                account_id = os.getenv(f"CLOUDFLARE_ACCOUNT_ID_{index}")

                if api_token and account_id:
                    account_config = {
                        "api_token": api_token,
                        "account_id": account_id
                    }
                    # 可选的 subdomain 配置
                    subdomain = os.getenv(f"CLOUDFLARE_WORKERS_SUBDOMAIN_{index}")
                    if subdomain:
                        account_config["subdomain"] = subdomain
                    accounts.append(account_config)
                    index += 1
                else:
                    break

            if accounts:
                logger.info(f"从编号环境变量加载了 {len(accounts)} 个账号")

        # 方式3：单账号（向后兼容）
        if not accounts:
            api_token = os.getenv("CLOUDFLARE_API_TOKEN")
            account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")

            if api_token and account_id:
                accounts.append({
                    "api_token": api_token,
                    "account_id": account_id
                })
                logger.info("使用单账号模式（向后兼容）")

        return accounts

    async def get_next_account(self) -> Dict[str, str]:
        """获取下一个可用账号

        使用轮换策略，跳过失败的账号。

        Returns:
            账号信息字典，包含 api_token 和 account_id

        Raises:
            RuntimeError: 所有账号都不可用
        """
        async with self.lock:
            # 如果所有账号都失败了，重置失败列表（给它们第二次机会）
            if len(self.failed_accounts) >= len(self.accounts):
                logger.warning("所有账号都失败过，重置失败列表")
                self.failed_accounts.clear()

            # 寻找下一个未失败的账号
            attempts = 0
            while attempts < len(self.accounts):
                account = self.accounts[self.current_index]

                # 移动到下一个索引（轮换）
                next_index = (self.current_index + 1) % len(self.accounts)

                if self.current_index not in self.failed_accounts:
                    self.current_index = next_index
                    logger.debug(f"使用账号 #{self.current_index} ({account['account_id'][:8]}...)")
                    return account

                self.current_index = next_index
                attempts += 1

            raise RuntimeError("没有可用的 Cloudflare 账号")

    async def mark_account_failed(self, account: Dict[str, str]):
        """标记账号失败

        Args:
            account: 失败的账号信息
        """
        async with self.lock:
            # 找到账号索引
            for i, acc in enumerate(self.accounts):
                if acc['account_id'] == account['account_id']:
                    self.failed_accounts.add(i)
                    logger.warning(f"账号 #{i} ({account['account_id'][:8]}...) 被标记为失败")
                    break

    async def acquire_fresh_token(self) -> Optional[str]:
        """获取新的 Warp 访问令牌（使用多账号轮换）

        Returns:
            获取到的 access token，失败返回 None
        """
        max_retries = min(3, len(self.accounts))  # 最多重试次数不超过账号数

        for attempt in range(max_retries):
            try:
                # 获取下一个账号
                account = await self.get_next_account()

                # 创建 token 服务实例
                subdomain = account.get('subdomain', '')
                service = WarpTokenService(
                    account['api_token'],
                    account['account_id'],
                    subdomain
                )

                # 尝试获取 token
                token = await service.acquire_fresh_token()

                if token:
                    logger.info(f"成功从账号 {account['account_id'][:8]}... 获取 token")
                    return token
                else:
                    logger.warning(f"账号 {account['account_id'][:8]}... 获取 token 失败")
                    await self.mark_account_failed(account)

            except Exception as e:
                logger.error(f"获取 token 时发生错误: {e}")
                if 'account' in locals():
                    await self.mark_account_failed(account)

            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 指数退避
                logger.info(f"等待 {wait_time} 秒后重试...")
                await asyncio.sleep(wait_time)

        logger.error("多账号轮换后仍无法获取 token")
        return None

    async def ensure_valid_token(self) -> str:
        """确保有有效的 token（多账号版本）

        Returns:
            有效的 access token

        Raises:
            RuntimeError: 无法获取有效 token
        """
        from warp2protobuf.core.auth import get_jwt_token, is_token_expired

        # 检查现有 token
        current_token = get_jwt_token()

        if current_token and not is_token_expired(current_token, buffer_minutes=5):
            logger.info("现有 token 仍然有效")
            return current_token

        # 使用多账号轮换获取新 token
        logger.info("需要获取新的 token（多账号轮换）")
        new_token = await self.acquire_fresh_token()

        if new_token:
            # 保存新 token
            from warp2protobuf.core.auth import update_env_file
            update_env_file(new_token)
            return new_token
        else:
            raise RuntimeError("无法获取有效的 Warp 访问令牌")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息

        Returns:
            统计信息字典
        """
        return {
            "total_accounts": len(self.accounts),
            "failed_accounts": len(self.failed_accounts),
            "available_accounts": len(self.accounts) - len(self.failed_accounts),
            "current_index": self.current_index
        }


# 全局服务实例
_token_service: Optional[WarpTokenService] = None
_multi_account_service = None  # 缺失定义补齐

# === 新增：后台保洁任务 ===
_cleanup_task: Optional[asyncio.Task] = None
_cleanup_manager: Optional[CloudflareWorkerManager] = None


def _collect_accounts_from_env() -> List[Dict[str, str]]:
    """收集 Cloudflare 账号配置：优先多账号，回退单账号。"""
    try:
        mats = MultiAccountTokenService()
        return mats.accounts
    except ValueError:
        pass
    cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    subdomain = os.getenv("CLOUDFLARE_WORKERS_SUBDOMAIN", "")
    if cf_api_token and cf_account_id:
        return [{"api_token": cf_api_token, "account_id": cf_account_id, "subdomain": subdomain}]
    return []


async def startup_cleanup(prefix: str = "warp-token-"):
    """服务启动时清理所有指定前缀的残留 Workers（支持多账号遍历）。"""
    accounts = _collect_accounts_from_env()
    if not accounts:
        logger.warning("startup_cleanup 跳过：未检测到任何 Cloudflare 账号配置")
        return

    total_deleted = 0
    total_matched = 0
    for acc in accounts:
        api_token = acc.get("api_token", "")
        account_id = acc.get("account_id", "")
        subdomain = acc.get("subdomain", "")
        if not (api_token and account_id):
            continue
        mgr = CloudflareWorkerManager(api_token, account_id, subdomain)
        try:
            stats = await mgr.cleanup_workers_by_prefix(prefix=prefix, threshold=None)
            total_deleted += int(stats.get("deleted", 0))
            total_matched += int(stats.get("matched", 0))
            logger.info(f"账号 {account_id[:8]}... 清理完成: {stats}")
        except Exception as e:
            logger.error(f"账号 {account_id[:8]}... 启动清理异常: {e}")

    logger.info(f"启动清理总计：匹配 {total_matched}，删除 {total_deleted}")


async def _periodic_cleanup(prefix: str, threshold: int, interval_seconds: int):
    """每 interval_seconds 轮询一次，对所有账号巡检并在超过阈值时清理。"""
    while True:
        try:
            # 获取账号集合
            accounts: List[Dict[str, str]] = _collect_accounts_from_env()

            if not accounts:
                logger.warning("周期清理跳过：未检测到任何 Cloudflare 账号配置")
            else:
                for acc in accounts:
                    api_token = acc.get("api_token", "")
                    account_id = acc.get("account_id", "")
                    subdomain = acc.get("subdomain", acc.get("subdomain", ""))
                    if not (api_token and account_id):
                        continue
                    mgr = CloudflareWorkerManager(api_token, account_id, subdomain)
                    try:
                        workers = await mgr.list_all_workers()
                        count = 0
                        for w in workers:
                            name = w.get("id") or w.get("name") or ""
                            if isinstance(name, str) and name.startswith(prefix):
                                count += 1
                        logger.info(f"周期检查：账号 {account_id[:8]}... 前缀 '{prefix}' 数量 {count}")
                        if count >= threshold:
                            logger.warning(f"账号 {account_id[:8]}... 达到阈值 {threshold}，触发清理")
                            await mgr.cleanup_workers_by_prefix(prefix=prefix, threshold=None)
                    except Exception as e:
                        logger.error(f"周期清理账号 {account_id[:8]}... 时异常: {e}")
        except Exception as e:
            logger.error(f"周期清理任务异常: {e}")
        finally:
            await asyncio.sleep(interval_seconds)


def init_worker_cleanup_tasks(prefix: str = "warp-token-", threshold: int = 50, interval_seconds: int = 1800):
    """
    初始化后台清理任务：
    - 启动时执行一次全量清理（删除所有残留 warp-token-*）
    - 每 interval_seconds 检查数量，超过阈值则清理
    调用时机：服务启动完成后调用一次即可。
    """
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        logger.info("清理任务已在运行，跳过重复初始化")
        return

    async def _bootstrap():
        await startup_cleanup(prefix=prefix)
        await _periodic_cleanup(prefix=prefix, threshold=threshold, interval_seconds=interval_seconds)

    _cleanup_task = asyncio.create_task(_bootstrap())
    logger.info(f"已启动 Worker 清理任务：前缀={prefix}, 阈值={threshold}, 周期={interval_seconds}s")




def get_token_service():
    """获取 Token 服务实例：优先多账号，失败回退单账号。"""
    global _multi_account_service, _token_service

    # 优先：多账号服务（从环境变量自动加载），成功则复用返回
    if _multi_account_service is None:
        try:
            _multi_account_service = MultiAccountTokenService()
            logger.info(f"使用多账号 Token 服务 ({len(_multi_account_service.accounts)} 个账号)")
            return _multi_account_service
        except ValueError:
            _multi_account_service = None
    else:
        return _multi_account_service

    # 回退：单账号服务
    if _token_service is None:
        cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        if not cf_api_token or not cf_account_id:
            raise ValueError("需要设置环境变量: CLOUDFLARE_API_TOKEN 和 CLOUDFLARE_ACCOUNT_ID，或配置多账号")
        _token_service = WarpTokenService(cf_api_token, cf_account_id)
        logger.info("使用单账号 Token 服务")

    return _token_service


async def get_fresh_warp_token() -> str:
    """
    便捷函数：获取新的 Warp token

    Returns:
        新的 access token

    Raises:
        RuntimeError: 获取失败
    """
    service = get_token_service()
    return await service.ensure_valid_token()


# 使用示例
async def main():
    """测试函数"""
    try:
        # 设置环境变量（实际使用时应该在 .env 文件中）
        # os.environ["CLOUDFLARE_API_TOKEN"] = "your_api_token"
        # os.environ["CLOUDFLARE_ACCOUNT_ID"] = "your_account_id"

        # 启动清理任务（服务化部署时建议在应用启动阶段调用）
        init_worker_cleanup_tasks(prefix="warp-token-", threshold=50, interval_seconds=1800)

        # 获取 token
        token = await get_fresh_warp_token()
        print(f"获取到 token: {token[:50]}...")

    except Exception as e:
        print(f"错误: {e}")


if __name__ == "__main__":
    asyncio.run(main())
