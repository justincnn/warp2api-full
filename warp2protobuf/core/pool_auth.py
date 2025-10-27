#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账号池认证模块（移植自 warp-register）
从账号池服务获取账号，替代临时账号注册
"""

import asyncio
import os
import time
from typing import Optional, Dict, Any

import httpx

from .auth import update_env_file
from .logging import logger
from .proxy_manager import AsyncProxyManager

# 账号池服务配置
POOL_SERVICE_URL = os.getenv("POOL_SERVICE_URL", "http://localhost:8019")
USE_POOL_SERVICE = os.getenv("USE_POOL_SERVICE", "true").lower() == "true"


class PoolAuthManager:
    """
账号池认证管理器 (无状态设计，适合并发)
    """

    def __init__(self):
        self.pool_url = POOL_SERVICE_URL

    async def acquire_session(self) -> Optional[Dict[str, Any]]:
        """
        从账号池获取一个新的会话（包含令牌和会话ID）。

        Returns:
            一个包含 'access_token', 'session_id', 'account' 的字典，或在失败时返回 None。
        """
        logger.info(f"正在从账号池服务获取新会话: {self.pool_url}")

        try:
            client_config = {
                "timeout": httpx.Timeout(30.0),
                "verify": False,
                "trust_env": True
            }

            async with httpx.AsyncClient(**client_config) as client:
                # 分配账号
                response = await client.post(
                    f"{self.pool_url}/api/accounts/allocate",
                    json={"count": 1}
                )

                if response.status_code != 200:
                    logger.error(f"分配账号失败: HTTP {response.status_code} {response.text}")
                    return None

                data = response.json()

                if not data.get("success"):
                    logger.error(f"分配账号失败: {data.get('message', '未知错误')}")
                    return None

                accounts = data.get("accounts", [])
                if not accounts:
                    logger.error("账号池未返回任何账号")
                    return None

                account = accounts[0]
                session_id = data.get("session_id")

                logger.info(f"✅ 成功获得新账号: {account.get('email', 'N/A')}, 会话ID: {session_id}")

                # 获取访问令牌
                access_token = await self._get_access_token_from_account(account)
                if not access_token:
                    # 如果获取token失败，也应该释放会话
                    await self.release_session(session_id)
                    return None

                # 更新环境变量（兼容旧代码）
                update_env_file(access_token)

                return {
                    "session_id": session_id,
                    "account": account,
                    "access_token": access_token,
                    "created_at": time.time()
                }

        except Exception as e:
            logger.error(f"从账号池获取会话时发生异常: {e}")
            return None

    async def _get_access_token_from_account(self, account: Dict[str, Any]) -> Optional[str]:
        """
        从账号信息获取访问令牌

        Args:
            account: 账号信息

        Returns:
            访问令牌或None
        """
        # 使用账号的refresh_token获取新的access_token
        refresh_token = account.get("refresh_token")
        id_token = account.get("id_token")  # 备用token

        if not refresh_token:
            # 如果没有refresh_token，直接使用id_token
            if id_token:
                logger.warning("账号缺少refresh_token，直接使用id_token")
                return id_token
            logger.error("账号缺少任何有效令牌")
            return None

        # 调用Warp的token刷新接口
        refresh_url = os.getenv("REFRESH_URL",
                                "https://app.warp.dev/proxy/token?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs")

        payload = f"grant_type=refresh_token&refresh_token={refresh_token}".encode("utf-8")
        headers = {
            "x-warp-client-version": os.getenv("CLIENT_VERSION", "v0.2025.08.06.08.12.stable_02"),
            "x-warp-os-category": os.getenv("OS_CATEGORY", "Darwin"),
            "x-warp-os-name": os.getenv("OS_NAME", "macOS"),
            "x-warp-os-version": os.getenv("OS_VERSION", "14.0"),
            "content-type": "application/x-www-form-urlencoded",
            "accept": "*/*",
            "accept-encoding": "gzip, br",
            "content-length": str(len(payload))
        }

        proxy_manager = AsyncProxyManager()
        max_proxy_retries = 3

        for proxy_attempt in range(max_proxy_retries):
            try:
                proxy_str = await proxy_manager.get_proxy()
                proxy_config = None

                if proxy_str:
                    proxy_config = proxy_manager.format_proxy_for_httpx(proxy_str)
                else:
                    logger.warning("账号Token刷新无法获取代理，使用直连")

                client_config = {
                    "timeout": httpx.Timeout(30.0),
                    "verify": False,
                    "trust_env": True
                }

                if proxy_config:
                    client_config["proxy"] = proxy_config

                async with httpx.AsyncClient(**client_config) as client:
                    resp = await client.post(refresh_url, headers=headers, content=payload)
                    if resp.status_code == 200:
                        token_data = resp.json()
                        access_token = token_data.get("access_token")

                        if not access_token:
                            # 如果没有access_token，使用id_token
                            access_token = account.get("id_token") or token_data.get("id_token")
                            if access_token:
                                logger.warning("使用id_token作为访问令牌")
                                return access_token
                            logger.error(f"响应中无访问令牌: {token_data}")
                            return None

                        logger.info("成功刷新访问令牌")
                        return access_token
                    else:
                        if proxy_attempt < max_proxy_retries - 1:
                            logger.warning(
                                f"账号Token刷新失败，尝试换代理 (attempt {proxy_attempt + 1}/{max_proxy_retries})"
                            )
                            await asyncio.sleep(0.5)
                            continue
                        logger.warning("刷新令牌失败，尝试使用id_token")
                        if id_token:
                            return id_token
                        return None

            except (httpx.ConnectError, httpx.ProxyError, httpx.RemoteProtocolError) as ssl_error:
                logger.warning(
                    f"账号Token刷新 SSL/代理错误 (attempt {proxy_attempt + 1}/{max_proxy_retries}): {ssl_error}"
                )
                if proxy_attempt < max_proxy_retries - 1:
                    await asyncio.sleep(0.5)
                    continue
                if id_token:
                    logger.warning("由于网络错误，使用id_token作为备用")
                    return id_token
                return None

            except Exception as e:
                logger.error(f"刷新令牌时发生异常: {e}")
                if proxy_attempt < max_proxy_retries - 1:
                    await asyncio.sleep(0.5)
                    continue
                if id_token:
                    return id_token
                return None

        logger.error("刷新令牌在多次尝试后均失败")
        return id_token  # 最后尝试返回id_token

    async def release_session(self, session_id: Optional[str]):
        """
        根据会话ID释放会话
        """
        if not session_id:
            return

        logger.info(f"正在释放会话: {session_id}")

        try:
            client_config = {
                "timeout": httpx.Timeout(10.0),
                "verify": False,
                "trust_env": True
            }

            async with httpx.AsyncClient(**client_config) as client:
                response = await client.post(
                    f"{self.pool_url}/api/accounts/release",
                    json={"session_id": session_id}
                )

                if response.status_code == 200:
                    logger.info(f"✅ 成功释放会话: {session_id}")
                else:
                    logger.warning(f"释放会话失败: HTTP {response.status_code}")
                return

        except Exception as e:
            logger.error(f"释放会话时发生异常: {e}")

    async def mark_blocked(self, jwt_token: Optional[str] = None, email: Optional[str] = None):
        """
        上报疑似封禁账号（失败不抛异常）。优先使用 jwt_token，上报失败或无 jwt_token 时可使用 email。
        """
        try:
            payload: Dict[str, Any] = {}
            if jwt_token:
                payload["jwt_token"] = jwt_token
            if email:
                payload["email"] = email
            if not payload:
                logger.warning("mark_blocked 跳过：未提供 jwt_token 或 email")
                return

            client_config = {
                "timeout": httpx.Timeout(10.0),
                "verify": False,
                "trust_env": True
            }
            async with httpx.AsyncClient(**client_config) as client:
                resp = await client.post(f"{self.pool_url}/api/accounts/mark_blocked", json=payload)
                if resp.status_code == 200:
                    logger.info("✅ 已上报封禁账号到账号池 mark_blocked")
                else:
                    logger.warning(f"mark_blocked 上报失败: HTTP {resp.status_code} {resp.text}")
        except Exception as e:
            logger.warning(f"mark_blocked 调用异常: {e}")


_pool_manager = None


def get_pool_manager() -> PoolAuthManager:
    global _pool_manager
    if _pool_manager is None:
        _pool_manager = PoolAuthManager()
    return _pool_manager