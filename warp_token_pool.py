#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp Token 池管理系统

实现 Token 预备机制，确保始终有可用的 token，
避免因 429 错误中断用户对话。

核心功能：
1. 维护 2-3 个有效 token 的池
2. 后台异步补充 token
3. 遇到 429 时无缝切换
4. 智能预测和预备
"""

import asyncio
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum
import threading
from concurrent.futures import ThreadPoolExecutor

from warp2protobuf.core.logging import logger
from warp_token_manager import WarpTokenService


class TokenStatus(Enum):
    """Token 状态"""
    VALID = "valid"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


@dataclass
class TokenInfo:
    """Token 信息"""
    token: str
    created_at: float
    last_used: float
    use_count: int
    status: TokenStatus

    def is_expired(self, buffer_minutes: int = 5) -> bool:
        """检查 token 是否过期"""
        from warp2protobuf.core.auth import is_token_expired
        return is_token_expired(self.token, buffer_minutes)

    def age_hours(self) -> float:
        """获取 token 年龄（小时）"""
        return (time.time() - self.created_at) / 3600

    def is_fresh(self, max_age_hours: float = 1.0) -> bool:
        """检查 token 是否新鲜"""
        return self.age_hours() < max_age_hours


class WarpTokenPool:
    """Warp Token 池管理器

    维护多个有效 token，支持快速切换和自动补充。
    现在集成多账号轮换功能，提高成功率。
    """

    def __init__(self, cf_api_token: str = None, cf_account_id: str = None, pool_size: int = 3):
        """
        初始化 Token 池

        Args:
            cf_api_token: 单个 Cloudflare API Token（向后兼容）
            cf_account_id: 单个 Cloudflare Account ID（向后兼容）
            pool_size: 池大小（建议 2-3 个）
        """
        # 尝试使用多账号服务
        self.token_service = None
        self.use_multi_account = False

        try:
            # 优先尝试初始化多账号服务
            from warp_token_manager import MultiAccountTokenService
            multi_service = MultiAccountTokenService()  # 会从环境变量加载配置
            stats = multi_service.get_stats()

            if stats['total_accounts'] > 1:
                logger.info(f"使用多账号模式，共有 {stats['total_accounts']} 个账号")
                self.token_service = multi_service
                self.use_multi_account = True
            else:
                logger.info("只有一个账号，使用单账号模式")
                # 回退到单账号模式
                if not cf_api_token or not cf_account_id:
                    # 从环境变量获取
                    import os
                    cf_api_token = cf_api_token or os.getenv("CLOUDFLARE_API_TOKEN")
                    cf_account_id = cf_account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID")

                from warp_token_manager import WarpTokenService
                self.token_service = WarpTokenService(cf_api_token, cf_account_id)

        except Exception as e:
            logger.warning(f"初始化多账号服务失败: {e}，使用单账号模式")
            # 回退到单账号模式
            if not cf_api_token or not cf_account_id:
                import os
                cf_api_token = cf_api_token or os.getenv("CLOUDFLARE_API_TOKEN")
                cf_account_id = cf_account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID")

            from warp_token_manager import WarpTokenService
            self.token_service = WarpTokenService(cf_api_token, cf_account_id)
        self.pool_size = pool_size
        self.tokens: List[TokenInfo] = []
        self.lock = asyncio.Lock()
        self.background_task: Optional[asyncio.Task] = None
        self.is_running = False

        # 统计信息
        self.stats = {
            "total_requests": 0,
            "successful_switches": 0,
            "tokens_created": 0,
            "rate_limit_hits": 0
        }

    async def start(self):
        """启动 Token 池管理"""
        if self.is_running:
            return

        logger.info("启动 Token 池管理系统...")
        self.is_running = True

        # 初始化填充池
        await self._fill_pool()

        # 启动后台维护任务
        self.background_task = asyncio.create_task(self._background_maintenance())

        logger.info(f"Token 池启动成功，当前池大小: {len(self.tokens)}")

    async def stop(self):
        """停止 Token 池管理"""
        logger.info("停止 Token 池管理系统...")
        self.is_running = False

        if self.background_task:
            self.background_task.cancel()
            try:
                await self.background_task
            except asyncio.CancelledError:
                pass

        logger.info("Token 池管理系统已停止")

    async def get_valid_token(self) -> str:
        """
        获取有效的 token

        Returns:
            有效的 access token

        Raises:
            RuntimeError: 无法获取有效 token
        """
        async with self.lock:
            self.stats["total_requests"] += 1

            # 查找有效的 token
            valid_token = await self._find_valid_token()

            if valid_token:
                # 更新使用信息
                valid_token.last_used = time.time()
                valid_token.use_count += 1

                logger.debug(f"使用池中 token，使用次数: {valid_token.use_count}")

                # 触发后台补充（如果需要）
                asyncio.create_task(self._ensure_pool_health())

                return valid_token.token

            # 池中没有有效 token，紧急处理
            logger.warning("池中无有效 token，触发紧急补充...")

            # 1. 先紧急获取一个 token 用于当前请求
            emergency_token = await self._get_emergency_token()

            # 2. 同时触发池的完整补充（补充到满池）
            asyncio.create_task(self._ensure_pool_health())

            if emergency_token:
                logger.info("紧急 token 获取成功，后台正在补充池")
                return emergency_token
            else:
                raise RuntimeError("无法获取有效的 Warp 访问令牌")

    async def handle_rate_limit(self, failed_token: str) -> Optional[str]:
        """
        处理 429 错误，立即切换到备用 token

        Args:
            failed_token: 遇到 429 的 token

        Returns:
            备用 token，如果没有则返回 None
        """
        async with self.lock:
            self.stats["rate_limit_hits"] += 1

            # 标记失败的 token
            for token_info in self.tokens:
                if token_info.token == failed_token:
                    token_info.status = TokenStatus.RATE_LIMITED
                    logger.warning(f"Token 遇到 429，标记为受限: {failed_token[:50]}...")
                    break

            # 查找备用 token
            backup_token = await self._find_valid_token(exclude_token=failed_token)

            if backup_token:
                self.stats["successful_switches"] += 1
                backup_token.last_used = time.time()
                backup_token.use_count += 1

                logger.info(f"成功切换到备用 token: {backup_token.token[:50]}...")

                # 异步补充池
                asyncio.create_task(self._ensure_pool_health())

                return backup_token.token

            # 没有备用 token，紧急获取并触发池补充
            logger.error("没有备用 token，触发紧急补充...")

            # 1. 紧急获取一个 token
            emergency_token = await self._get_emergency_token()

            # 2. 触发池的完整补充
            asyncio.create_task(self._ensure_pool_health())

            if emergency_token:
                logger.info("紧急 token 获取成功，后台正在补充池")

            return emergency_token

    async def _find_valid_token(self, exclude_token: Optional[str] = None) -> Optional[TokenInfo]:
        """查找有效的 token"""
        for token_info in self.tokens:
            if exclude_token and token_info.token == exclude_token:
                continue

            if (token_info.status == TokenStatus.VALID and
                not token_info.is_expired()):
                return token_info

        return None

    async def _fill_pool(self):
        """填充 token 池"""
        logger.info(f"开始填充 Token 池，目标大小: {self.pool_size}")

        tasks = []
        needed = self.pool_size - len([t for t in self.tokens if t.status == TokenStatus.VALID])

        for i in range(needed):
            task = asyncio.create_task(self._create_token_with_retry())
            tasks.append(task)

        # 并发创建 token
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = 0
        for result in results:
            if isinstance(result, str):  # 成功获取的 token
                token_info = TokenInfo(
                    token=result,
                    created_at=time.time(),
                    last_used=0,
                    use_count=0,
                    status=TokenStatus.VALID
                )
                self.tokens.append(token_info)
                success_count += 1
                self.stats["tokens_created"] += 1

        logger.info(f"Token 池填充完成，成功创建: {success_count}/{needed}")

    async def _create_token_with_retry(self, max_retries: int = 2) -> Optional[str]:
        """创建 token，带重试机制"""
        for attempt in range(max_retries):
            try:
                token = await self.token_service.acquire_fresh_token()
                if token:
                    return token
            except Exception as e:
                logger.error(f"创建 token 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # 指数退避

        return None

    async def _get_emergency_token(self) -> Optional[str]:
        """紧急获取 token"""
        try:
            token = await self.token_service.acquire_fresh_token()
            if token:
                # 添加到池中
                token_info = TokenInfo(
                    token=token,
                    created_at=time.time(),
                    last_used=time.time(),
                    use_count=1,
                    status=TokenStatus.VALID
                )
                self.tokens.append(token_info)
                self.stats["tokens_created"] += 1

                logger.info("紧急获取 token 成功")
                return token
        except Exception as e:
            logger.error(f"紧急获取 token 失败: {e}")

        return None

    async def _background_maintenance(self):
        """后台维护任务"""
        logger.info("启动后台维护任务")

        while self.is_running:
            try:
                await asyncio.sleep(30)  # 每 30 秒检查一次

                if not self.is_running:
                    break

                async with self.lock:
                    await self._cleanup_invalid_tokens()
                    await self._ensure_pool_health()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"后台维护任务错误: {e}")
                await asyncio.sleep(5)

        logger.info("后台维护任务已停止")

    async def _cleanup_invalid_tokens(self):
        """清理无效的 token"""
        before_count = len(self.tokens)

        # 移除过期或受限的 token
        self.tokens = [
            t for t in self.tokens
            if (t.status == TokenStatus.VALID and not t.is_expired())
        ]

        removed_count = before_count - len(self.tokens)
        if removed_count > 0:
            logger.debug(f"清理了 {removed_count} 个无效 token")

    async def _ensure_pool_health(self):
        """确保池的健康状态 - 当有效 token 少于一半时立即补充"""
        valid_count = len([t for t in self.tokens
                          if t.status == TokenStatus.VALID and not t.is_expired()])

        # 计算补充阈值：池大小的一半（向上取整）
        threshold = (self.pool_size + 1) // 2

        # 当有效 token 数量少于或等于阈值时，立即补充到满池
        if valid_count <= threshold:
            needed = self.pool_size - valid_count
            logger.info(f"Token 池健康度低于 50% (当前: {valid_count}/{self.pool_size})，补充 {needed} 个 token")

            # 异步创建新 token
            for _ in range(needed):
                asyncio.create_task(self._create_and_add_token())
        else:
            logger.debug(f"Token 池健康 (有效: {valid_count}/{self.pool_size})")

    async def _create_and_add_token(self):
        """创建并添加新 token 到池中"""
        try:
            token = await self._create_token_with_retry()
            if token:
                async with self.lock:
                    token_info = TokenInfo(
                        token=token,
                        created_at=time.time(),
                        last_used=0,
                        use_count=0,
                        status=TokenStatus.VALID
                    )
                    self.tokens.append(token_info)
                    self.stats["tokens_created"] += 1

                    logger.debug(f"后台添加新 token 到池中，当前池大小: {len(self.tokens)}")
        except Exception as e:
            logger.error(f"后台创建 token 失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        valid_tokens = [t for t in self.tokens if t.status == TokenStatus.VALID]

        return {
            **self.stats,
            "pool_size": len(self.tokens),
            "valid_tokens": len(valid_tokens),
            "average_token_age": sum(t.age_hours() for t in valid_tokens) / len(valid_tokens) if valid_tokens else 0,
            "total_token_uses": sum(t.use_count for t in self.tokens)
        }


# 全局 Token 池实例
_token_pool: Optional[WarpTokenPool] = None


async def get_token_pool() -> WarpTokenPool:
    """获取 Token 池实例

    现在支持多账号配置，会自动检测并使用多账号模式。
    """
    global _token_pool

    if _token_pool is None:
        import os

        # 不再强制要求单账号配置，让 WarpTokenPool 自己判断
        # WarpTokenPool 会自动尝试多账号模式，如果失败则回退到单账号
        _token_pool = WarpTokenPool()
        await _token_pool.start()

    return _token_pool


async def get_pooled_token() -> str:
    """便捷函数：从池中获取 token"""
    pool = await get_token_pool()
    return await pool.get_valid_token()


async def handle_token_rate_limit(failed_token: str) -> Optional[str]:
    """便捷函数：处理 token 429 错误"""
    pool = await get_token_pool()
    return await pool.handle_rate_limit(failed_token)


# 使用示例
async def main():
    """测试函数"""
    try:
        # 启动 Token 池
        pool = await get_token_pool()

        # 模拟多次请求
        for i in range(5):
            token = await get_pooled_token()
            print(f"第 {i+1} 次请求，获得 token: {token[:50]}...")

            # 模拟 429 错误
            if i == 2:
                backup_token = await handle_token_rate_limit(token)
                if backup_token:
                    print(f"成功切换到备用 token: {backup_token[:50]}...")

            await asyncio.sleep(1)

        # 显示统计信息
        stats = pool.get_stats()
        print(f"统计信息: {stats}")

        # 停止池
        await pool.stop()

    except Exception as e:
        print(f"错误: {e}")


if __name__ == "__main__":
    asyncio.run(main())