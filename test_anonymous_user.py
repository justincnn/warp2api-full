#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试匿名用户创建接口

这个脚本专门用于测试 _create_anonymous_user 函数的随机化浏览器特征头功能。
可以单独运行，不依赖其他服务。
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from warp2protobuf.core.auth import (
    _create_anonymous_user,
    _exchange_id_token_for_refresh_token,
    acquire_anonymous_access_token,
    _generate_realistic_headers,
    _generate_randomized_variables
)
from warp2protobuf.core.logging import logger


async def test_generate_headers():
    """测试随机化头部生成"""
    print("=" * 60)
    print("测试随机化头部生成")
    print("=" * 60)

    for i in range(3):
        print(f"\n--- 第 {i+1} 次生成 ---")
        headers = _generate_realistic_headers()

        print(f"User-Agent: {headers.get('user-agent')}")
        print(f"Client Version: {headers.get('x-warp-client-version')}")
        print(f"OS Version: {headers.get('x-warp-os-version')}")
        print(f"Accept-Language: {headers.get('accept-language')}")
        print(f"Cache-Control: {headers.get('cache-control')}")
        print(f"Request ID: {headers.get('x-request-id')}")
        print(f"Origin: {headers.get('origin', 'N/A')}")
        print(f"DNT: {headers.get('dnt', 'N/A')}")


async def test_generate_variables():
    """测试随机化变量生成"""
    print("\n" + "=" * 60)
    print("测试随机化变量生成")
    print("=" * 60)

    for i in range(3):
        print(f"\n--- 第 {i+1} 次生成 ---")
        variables = _generate_randomized_variables()

        print(f"Client Version: {variables['requestContext']['clientContext']['version']}")
        print(f"OS Version: {variables['requestContext']['osContext']['version']}")
        print(f"Referral Code: {variables['input']['referralCode']}")

        # 显示额外的随机字段
        client_ctx = variables['requestContext']['clientContext']
        os_ctx = variables['requestContext']['osContext']

        if 'buildNumber' in client_ctx:
            print(f"Build Number: {client_ctx['buildNumber']}")
        if 'platform' in client_ctx:
            print(f"Platform: {client_ctx['platform']}")
        if 'arch' in os_ctx:
            print(f"Architecture: {os_ctx['arch']}")


async def test_create_anonymous_user():
    """测试创建匿名用户"""
    print("\n" + "=" * 60)
    print("测试创建匿名用户")
    print("=" * 60)

    try:
        print("正在创建匿名用户...")
        start_time = time.time()

        result = await _create_anonymous_user()

        end_time = time.time()
        duration = end_time - start_time

        print(f"✅ 创建成功！耗时: {duration:.2f}秒")
        print(f"响应类型: {result.get('data', {}).get('createAnonymousUser', {}).get('__typename')}")

        # 检查是否有 idToken
        create_user_data = result.get('data', {}).get('createAnonymousUser', {})
        if 'idToken' in create_user_data:
            print(f"✅ 获得 ID Token: {create_user_data['idToken'][:50]}...")
            print(f"匿名用户类型: {create_user_data.get('anonymousUserType')}")
            print(f"过期时间: {create_user_data.get('expiresAt')}")
            return create_user_data['idToken']
        else:
            print("❌ 未获得 ID Token")
            print(f"完整响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            return None

    except Exception as e:
        print(f"❌ 创建失败: {e}")
        logger.error(f"创建匿名用户失败: {e}")
        return None


async def test_exchange_token(id_token: str):
    """测试 ID Token 交换 Refresh Token"""
    print("\n" + "=" * 60)
    print("测试 ID Token 交换")
    print("=" * 60)

    try:
        print("正在交换 ID Token...")
        start_time = time.time()

        result = await _exchange_id_token_for_refresh_token(id_token)

        end_time = time.time()
        duration = end_time - start_time

        print(f"✅ 交换成功！耗时: {duration:.2f}秒")

        if 'refreshToken' in result:
            print(f"✅ 获得 Refresh Token: {result['refreshToken'][:50]}...")
            if 'idToken' in result:
                print(f"✅ 获得新的 ID Token: {result['idToken'][:50]}...")
            return result['refreshToken']
        else:
            print("❌ 未获得 Refresh Token")
            print(f"完整响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            return None

    except Exception as e:
        print(f"❌ 交换失败: {e}")
        logger.error(f"ID Token 交换失败: {e}")
        return None


async def test_full_flow():
    """测试完整的匿名访问令牌获取流程"""
    print("\n" + "=" * 60)
    print("测试完整流程")
    print("=" * 60)

    try:
        print("正在执行完整的匿名访问令牌获取流程...")
        start_time = time.time()

        access_token = await acquire_anonymous_access_token()

        end_time = time.time()
        duration = end_time - start_time

        print(f"✅ 完整流程成功！耗时: {duration:.2f}秒")
        print(f"✅ 获得访问令牌: {access_token[:50]}...")

        return access_token

    except Exception as e:
        print(f"❌ 完整流程失败: {e}")
        logger.error(f"完整流程失败: {e}")
        return None


async def test_multiple_requests():
    """测试多次请求，检查随机化效果"""
    print("\n" + "=" * 60)
    print("测试多次请求（检查随机化效果）")
    print("=" * 60)

    success_count = 0
    total_count = 3

    for i in range(total_count):
        print(f"\n--- 第 {i+1}/{total_count} 次请求 ---")

        try:
            result = await _create_anonymous_user()
            create_user_data = result.get('data', {}).get('createAnonymousUser', {})

            if 'idToken' in create_user_data:
                success_count += 1
                print(f"✅ 第 {i+1} 次请求成功")
            else:
                print(f"❌ 第 {i+1} 次请求失败: 未获得 ID Token")

        except Exception as e:
            print(f"❌ 第 {i+1} 次请求异常: {e}")

        # 请求间隔
        if i < total_count - 1:
            print("等待 2 秒后继续...")
            await asyncio.sleep(2)

    print(f"\n📊 测试结果: {success_count}/{total_count} 成功")
    success_rate = (success_count / total_count) * 100
    print(f"📊 成功率: {success_rate:.1f}%")


async def main():
    """主测试函数"""
    print("🚀 开始测试匿名用户创建接口")
    print(f"⏰ 测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. 测试随机化头部生成
    await test_generate_headers()

    # 2. 测试随机化变量生成
    await test_generate_variables()

    # 3. 测试创建匿名用户
    id_token = await test_create_anonymous_user()

    # 4. 如果获得了 ID Token，测试交换
    if id_token:
        refresh_token = await test_exchange_token(id_token)

    # 5. 测试完整流程
    await test_full_flow()

    # 6. 测试多次请求
    await test_multiple_requests()

    print("\n" + "=" * 60)
    print("🎉 测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    # 设置日志级别为 INFO，显示详细信息
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