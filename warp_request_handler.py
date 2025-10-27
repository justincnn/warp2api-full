#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp 请求处理器 - 带自动 Token 切换

提供智能的请求处理，在遇到 429 错误时自动切换到备用 token，
确保对话不被中断。
"""

import asyncio
import httpx
from typing import Optional, Dict, Any, Callable
import json

from warp2protobuf.core.logging import logger


class WarpRequestHandler:
    """Warp 请求处理器，支持自动 Token 切换"""

    def __init__(self):
        self.current_token: Optional[str] = None
        self.retry_count = 0
        self.max_retries = 2

    async def make_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Any] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
        **kwargs
    ) -> httpx.Response:
        """
        发送请求，自动处理 429 错误和 token 切换

        Args:
            method: HTTP 方法
            url: 请求 URL
            headers: 请求头
            data: 请求数据
            json_data: JSON 数据
            timeout: 超时时间
            **kwargs: 其他参数

        Returns:
            HTTP 响应

        Raises:
            Exception: 请求失败
        """
        headers = headers or {}

        # 确保有 token
        if not self.current_token:
            await self._refresh_token()

        # 设置 Authorization 头
        if self.current_token:
            headers["Authorization"] = f"Bearer {self.current_token}"

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    # 发送请求
                    if json_data:
                        response = await client.request(
                            method, url, headers=headers, json=json_data, **kwargs
                        )
                    else:
                        response = await client.request(
                            method, url, headers=headers, data=data, **kwargs
                        )

                    # 检查是否是 429 错误
                    if response.status_code == 429:
                        logger.warning(f"遇到 429 错误 (尝试 {attempt + 1}/{self.max_retries + 1})")

                        if attempt < self.max_retries:
                            # 尝试切换 token
                            success = await self._handle_rate_limit()
                            if success:
                                # 更新 Authorization 头
                                headers["Authorization"] = f"Bearer {self.current_token}"
                                logger.info("已切换到备用 token，重试请求...")
                                continue
                            else:
                                logger.error("无法获取备用 token")
                        else:
                            logger.error("已达到最大重试次数，返回 429 错误")

                    # 成功或其他错误，直接返回
                    return response

            except Exception as e:
                logger.error(f"请求异常 (尝试 {attempt + 1}/{self.max_retries + 1}): {e}")
                if attempt >= self.max_retries:
                    raise

                # 等待后重试
                await asyncio.sleep(1)

        # 不应该到达这里
        raise Exception("请求失败，已达到最大重试次数")

    async def _refresh_token(self) -> bool:
        """刷新当前 token"""
        try:
            # 导入 Token 池管理器
            import sys
            import os
            from pathlib import Path
            project_root = Path(__file__).parent
            sys.path.insert(0, str(project_root))

            # 优先使用 Token 池
            try:
                from warp_token_pool import get_pooled_token
                self.current_token = await get_pooled_token()
                logger.debug("从 Token 池获取新 token")
                return True
            except Exception as pool_error:
                logger.debug(f"Token 池不可用: {pool_error}")

            # 回退到传统方法（已经包含多账号支持）
            from warp2protobuf.core.auth import get_valid_jwt
            self.current_token = await get_valid_jwt()
            logger.debug("使用传统方法获取 token")
            return True

        except Exception as e:
            logger.error(f"刷新 token 失败: {e}")
            return False

    async def _handle_rate_limit(self) -> bool:
        """处理 429 错误，切换到备用 token"""
        try:
            # 优先使用 Token 池的切换功能
            try:
                from warp_token_pool import handle_token_rate_limit
                backup_token = await handle_token_rate_limit(self.current_token)
                if backup_token:
                    self.current_token = backup_token
                    logger.info("成功切换到备用 token")
                    return True
            except Exception as pool_error:
                logger.debug(f"Token 池切换失败: {pool_error}")

            # 回退到重新获取 token
            logger.info("重新获取 token...")
            return await self._refresh_token()

        except Exception as e:
            logger.error(f"处理 429 错误失败: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """获取请求处理器统计信息"""
        return {
            "current_token": self.current_token[:50] + "..." if self.current_token else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries
        }

    async def post_json(self, url: str, data: Dict[str, Any], **kwargs) -> httpx.Response:
        """便捷方法：发送 JSON POST 请求"""
        return await self.make_request("POST", url, json_data=data, **kwargs)

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """便捷方法：发送 GET 请求"""
        return await self.make_request("GET", url, **kwargs)

    async def post(self, url: str, data: Any = None, **kwargs) -> httpx.Response:
        """便捷方法：发送 POST 请求"""
        return await self.make_request("POST", url, data=data, **kwargs)


# 全局请求处理器实例
_request_handler: Optional[WarpRequestHandler] = None


def get_request_handler() -> WarpRequestHandler:
    """获取全局请求处理器实例"""
    global _request_handler
    if _request_handler is None:
        _request_handler = WarpRequestHandler()
    return _request_handler


# 便捷函数
async def warp_request(method: str, url: str, **kwargs) -> httpx.Response:
    """便捷函数：发送 Warp 请求，自动处理 token 切换"""
    handler = get_request_handler()
    return await handler.make_request(method, url, **kwargs)


async def warp_post_json(url: str, data: Dict[str, Any], **kwargs) -> httpx.Response:
    """便捷函数：发送 JSON POST 请求"""
    handler = get_request_handler()
    return await handler.post_json(url, data, **kwargs)


async def warp_get(url: str, **kwargs) -> httpx.Response:
    """便捷函数：发送 GET 请求"""
    handler = get_request_handler()
    return await handler.get(url, **kwargs)


# 使用示例
async def example_usage():
    """使用示例"""
    try:
        # 发送请求，自动处理 token 切换
        response = await warp_post_json(
            "https://app.warp.dev/graphql/v2?op=CreateAnonymousUser",
            {
                "query": "...",
                "variables": {...}
            }
        )

        if response.status_code == 200:
            data = response.json()
            print(f"请求成功: {data}")
        else:
            print(f"请求失败: {response.status_code}")

    except Exception as e:
        print(f"请求异常: {e}")


if __name__ == "__main__":
    asyncio.run(example_usage())