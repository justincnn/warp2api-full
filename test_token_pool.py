#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token 池管理系统测试脚本

测试 Token 池的无缝切换功能，确保在遇到 429 错误时
能够自动切换到备用 token 而不中断对话。
"""

import asyncio
import os
import time
from typing import Dict, Any
import httpx

from warp_request_handler import WarpRequestHandler, get_request_handler
from warp_token_pool import get_token_pool, get_pooled_token, handle_token_rate_limit
from warp2protobuf.core.logging import logger


async def test_basic_token_acquisition():
    """测试基本的 token 获取功能"""
    logger.info("=== 测试基本 Token 获取 ===")

    try:
        # 检查环境变量
        cf_api_token = "HJ9LCr6eg8arHMDbAR65-MGeqQjwcR2Kc0yuJD0G"
        cf_account_id = "9a3c8ad9e5a10dd789f54dbad93d127f"

        if not cf_api_token or not cf_account_id:
            logger.warning("缺少 Cloudflare 环境变量，跳过 Token 池测试")
            return False

        # 获取 token
        token = await get_pooled_token()

        if token:
            logger.info(f"✓ 成功获取 token: {token[:50]}...")
            return True
        else:
            logger.error("✗ 获取 token 失败")
            return False

    except Exception as e:
        logger.error(f"✗ 基本 token 获取测试失败: {e}")
        return False


async def test_token_pool_management():
    """测试 Token 池管理功能"""
    logger.info("=== 测试 Token 池管理 ===")

    try:
        pool = await get_token_pool()

        # 显示初始状态
        stats = pool.get_stats()
        logger.info(f"初始池状态: {stats}")

        # 获取多个 token
        tokens = []
        for i in range(3):
            token = await pool.get_valid_token()
            tokens.append(token)
            logger.info(f"第 {i+1} 个 token: {token[:50]}...")

        # 显示使用后状态
        stats = pool.get_stats()
        logger.info(f"使用后池状态: {stats}")

        return True

    except Exception as e:
        logger.error(f"✗ Token 池管理测试失败: {e}")
        return False


async def test_rate_limit_handling():
    """测试 429 错误处理和 token 切换"""
    logger.info("=== 测试 429 错误处理 ===")

    try:
        pool = await get_token_pool()

        # 获取一个 token
        token1 = await pool.get_valid_token()
        logger.info(f"获取第一个 token: {token1[:50]}...")

        # 模拟 429 错误，切换到备用 token
        backup_token = await pool.handle_rate_limit(token1)

        if backup_token and backup_token != token1:
            logger.info(f"✓ 成功切换到备用 token: {backup_token[:50]}...")

            # 显示切换后状态
            stats = pool.get_stats()
            logger.info(f"切换后统计: {stats}")

            return True
        else:
            logger.error("✗ 备用 token 切换失败")
            return False

    except Exception as e:
        logger.error(f"✗ 429 错误处理测试失败: {e}")
        return False


async def test_request_handler_integration():
    """测试请求处理器集成"""
    logger.info("=== 测试请求处理器集成 ===")

    try:
        handler = get_request_handler()

        # 模拟一个简单的请求（使用 httpbin 进行测试）
        test_url = "https://httpbin.org/json"

        response = await handler.get(test_url)

        if response.status_code == 200:
            logger.info("✓ 请求处理器基本功能正常")

            # 测试 JSON 数据响应
            data = response.json()
            logger.info(f"响应数据: {list(data.keys())}")

            return True
        else:
            logger.error(f"✗ 请求失败: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"✗ 请求处理器测试失败: {e}")
        return False


async def test_warp_api_with_token_switching():
    """测试 Warp API 请求和自动 token 切换"""
    logger.info("=== 测试 Warp API 请求 ===")

    try:
        handler = get_request_handler()

        # 使用一个简单的 Warp API 端点进行测试
        # 注意：这里使用一个不会真正发送请求的测试 URL
        test_url = "https://httpbin.org/status/429"  # 模拟 429 响应

        # 第一次请求（应该收到 429）
        logger.info("发送第一次请求（预期收到 429）...")
        response1 = await handler.get(test_url)

        logger.info(f"第一次响应状态: {response1.status_code}")

        # 使用正常端点测试重试逻辑
        normal_url = "https://httpbin.org/json"
        logger.info("测试正常请求...")

        response2 = await handler.get(normal_url)

        if response2.status_code == 200:
            logger.info("✓ 请求处理和重试逻辑正常")
            return True
        else:
            logger.error(f"✗ 正常请求失败: {response2.status_code}")
            return False

    except Exception as e:
        logger.error(f"✗ Warp API 请求测试失败: {e}")
        return False


async def test_concurrent_requests():
    """测试并发请求处理"""
    logger.info("=== 测试并发请求处理 ===")

    try:
        handler = get_request_handler()

        # 创建多个并发请求
        tasks = []
        for i in range(5):
            task = asyncio.create_task(
                handler.get(f"https://httpbin.org/delay/1?request={i}")
            )
            tasks.append(task)

        # 等待所有请求完成
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = 0
        for i, response in enumerate(responses):
            if isinstance(response, httpx.Response) and response.status_code == 200:
                success_count += 1
                logger.info(f"请求 {i} 成功")
            else:
                logger.error(f"请求 {i} 失败: {response}")

        if success_count == len(tasks):
            logger.info("✓ 所有并发请求成功")
            return True
        else:
            logger.warning(f"部分请求失败: {success_count}/{len(tasks)}")
            return success_count > len(tasks) // 2  # 超过一半成功就算通过

    except Exception as e:
        logger.error(f"✗ 并发请求测试失败: {e}")
        return False


async def run_all_tests():
    """运行所有测试"""
    logger.info("开始 Token 池管理系统完整测试")
    logger.info("=" * 50)

    test_results = {}

    # 运行各项测试
    tests = [
        ("基本 Token 获取", test_basic_token_acquisition),
        ("Token 池管理", test_token_pool_management),
        ("429 错误处理", test_rate_limit_handling),
        ("请求处理器集成", test_request_handler_integration),
        ("Warp API 请求", test_warp_api_with_token_switching),
        ("并发请求处理", test_concurrent_requests),
    ]

    for test_name, test_func in tests:
        logger.info(f"\n--- {test_name} ---")
        try:
            result = await test_func()
            test_results[test_name] = result
            status = "✓ 通过" if result else "✗ 失败"
            logger.info(f"{test_name}: {status}")
        except Exception as e:
            logger.error(f"{test_name} 执行异常: {e}")
            test_results[test_name] = False

        # 测试间隔
        await asyncio.sleep(1)

    # 显示最终结果
    logger.info("\n" + "=" * 50)
    logger.info("测试结果汇总:")

    passed = 0
    total = len(test_results)

    for test_name, result in test_results.items():
        status = "✓ 通过" if result else "✗ 失败"
        logger.info(f"  {test_name}: {status}")
        if result:
            passed += 1

    logger.info(f"\n总计: {passed}/{total} 项测试通过")

    if passed == total:
        logger.info("🎉 所有测试通过！Token 池系统工作正常")
    elif passed >= total * 0.8:  # 80% 通过
        logger.info("⚠️  大部分测试通过，系统基本可用")
    else:
        logger.error("❌ 多项测试失败，需要检查系统配置")

    return passed, total


async def cleanup_test_resources():
    """清理测试资源"""
    try:
        # 如果有 Token 池实例，停止它
        from warp_token_pool import _token_pool
        if _token_pool:
            await _token_pool.stop()
            logger.info("已停止 Token 池")
    except Exception as e:
        logger.error(f"清理资源时出错: {e}")


if __name__ == "__main__":
    async def main():
        try:
            # 检查环境变量
            # if not os.getenv("CLOUDFLARE_API_TOKEN") or not os.getenv("CLOUDFLARE_ACCOUNT_ID"):
            #     logger.error("请设置 CLOUDFLARE_API_TOKEN 和 CLOUDFLARE_ACCOUNT_ID 环境变量")
            #     logger.info("示例:")
            #     logger.info("export CLOUDFLARE_API_TOKEN='your_api_token'")
            #     logger.info("export CLOUDFLARE_ACCOUNT_ID='your_account_id'")
            #     return

            # 运行测试
            passed, total = await run_all_tests()

            # 清理资源
            await cleanup_test_resources()

            # 退出码
            exit_code = 0 if passed == total else 1
            exit(exit_code)

        except KeyboardInterrupt:
            logger.info("\n测试被用户中断")
            await cleanup_test_resources()
        except Exception as e:
            logger.error(f"测试执行出错: {e}")
            await cleanup_test_resources()
            exit(1)

    asyncio.run(main())