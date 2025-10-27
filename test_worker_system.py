#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Cloudflare Worker 自动化系统

测试完整的 Worker 部署 → 获取 token → 清理流程
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from warp_token_manager import WarpTokenService, get_fresh_warp_token


async def test_worker_service():
    """测试 Worker 服务"""
    print("🚀 测试 Cloudflare Worker 自动化系统")
    print("=" * 60)

    # 检查环境变量
    cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")

    if not cf_api_token or not cf_account_id:
        print("❌ 缺少必需的环境变量:")
        print("   CLOUDFLARE_API_TOKEN")
        print("   CLOUDFLARE_ACCOUNT_ID")
        print("\n请参考 CLOUDFLARE_SETUP.md 进行配置")
        return

    print(f"✅ API Token: {cf_api_token[:20]}...")
    print(f"✅ Account ID: {cf_account_id}")

    try:
        # 测试 Worker 服务
        print("\n📡 测试 Worker 服务...")
        service = WarpTokenService(cf_api_token, cf_account_id)

        # 获取 token
        print("🔄 开始获取 token...")
        token = await service.acquire_fresh_token()

        if token:
            print(f"✅ 成功获取 token: {token[:50]}...")
            print(f"📏 Token 长度: {len(token)} 字符")

            # 验证 token 格式（JWT 应该有三个部分）
            parts = token.split('.')
            if len(parts) == 3:
                print("✅ Token 格式正确 (JWT)")
            else:
                print("⚠️  Token 格式异常")

        else:
            print("❌ 获取 token 失败")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


async def test_convenience_function():
    """测试便捷函数"""
    print("\n" + "=" * 60)
    print("🎯 测试便捷函数")
    print("=" * 60)

    try:
        print("🔄 调用 get_fresh_warp_token()...")
        token = await get_fresh_warp_token()

        print(f"✅ 成功: {token[:50]}...")

    except Exception as e:
        print(f"❌ 失败: {e}")


async def test_integration():
    """测试与现有系统的集成"""
    print("\n" + "=" * 60)
    print("🔗 测试系统集成")
    print("=" * 60)

    try:
        # 测试集成到 auth.py 的功能
        from warp2protobuf.core.auth import acquire_anonymous_access_token

        print("🔄 调用 acquire_anonymous_access_token()...")
        token = await acquire_anonymous_access_token()

        print(f"✅ 集成测试成功: {token[:50]}...")

        # 检查是否保存到了环境变量
        updated_token = os.getenv("WARP_JWT")
        if updated_token == token:
            print("✅ Token 已正确保存到环境变量")
        else:
            print("⚠️  Token 未保存到环境变量")

    except Exception as e:
        print(f"❌ 集成测试失败: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """主测试函数"""
    print("🧪 Cloudflare Worker 自动化系统测试")
    print(f"⏰ 测试时间: {asyncio.get_event_loop().time()}")

    # 1. 测试 Worker 服务
    await test_worker_service()

    # 2. 测试便捷函数
    await test_convenience_function()

    # 3. 测试系统集成
    await test_integration()

    print("\n" + "=" * 60)
    print("🎉 测试完成！")
    print("=" * 60)

    print("\n💡 提示:")
    print("- 如果测试成功，说明系统已正确配置")
    print("- 现在你的应用会自动使用 Worker 方案获取 token")
    print("- 每次获取都会使用新的 IP，绕过限制")


if __name__ == "__main__":
    # 设置日志级别
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  测试被用户中断")
    except Exception as e:
        print(f"\n💥 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()