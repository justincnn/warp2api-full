#!/usr/bin/env python3
"""详细测试工具调用问题，特别是连接断开的情况"""

import asyncio
import json
import httpx
import logging
from typing import Dict, Any, List

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_bash_with_file_operation() -> Dict[str, Any]:
    """测试 Bash 工具与文件操作相关的命令"""

    request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": "使用 Bash 工具查找所有的 auth.py 文件"
            }
        ],
        "tools": [{
            "name": "Bash",
            "description": "Execute bash commands",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute"},
                    "description": {"type": "string", "description": "Description of what the command does"}
                },
                "required": ["command"]
            }
        }],
        "stream": True,
        "max_tokens": 500
    }

    result = {
        "test_name": "Bash with file operation",
        "success": False,
        "error": None,
        "events": [],
        "connection_closed": False,
        "update_task_description_seen": False
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "http://localhost:8010/v1/messages",
                json=request,
                headers={"Content-Type": "application/json"}
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            event_type = data.get("type", "")
                            result["events"].append(event_type)

                            # 记录详细的事件
                            logger.info(f"Event: {event_type} - {json.dumps(data, ensure_ascii=False)[:200]}")

                            # 检查是否有 message_stop
                            if event_type == "message_stop":
                                result["success"] = True
                                logger.info("✅ 收到 message_stop，连接正常完成")

                            # 检查是否有错误
                            if event_type == "error":
                                result["error"] = data.get("error", {})
                                logger.error(f"❌ 收到错误: {result['error']}")

                        except json.JSONDecodeError:
                            if line == "data: [DONE]":
                                logger.info("收到 [DONE] 标记")
                            else:
                                logger.warning(f"无法解析的行: {line}")

    except httpx.HTTPError as e:
        result["error"] = f"HTTP error: {str(e)}"
        result["connection_closed"] = True
        logger.error(f"❌ HTTP 错误: {e}")
    except Exception as e:
        result["error"] = str(e)
        result["connection_closed"] = True
        logger.error(f"❌ 未预期的错误: {e}")

    return result

async def test_multiple_tool_calls() -> Dict[str, Any]:
    """测试多个工具调用的情况"""

    request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": "我发现有很多地方都不符合多账号模式，比如有些地方还会自己去调用WarpTokenService，然后api_key又是取CLOUDFLARE_API_TOKEN，请帮我查找并修复这些问题"
            }
        ],
        "tools": [
            {
                "name": "Search",
                "description": "Search for text in files",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "The pattern to search for"},
                        "path": {"type": "string", "description": "The path to search in"}
                    },
                    "required": ["pattern"]
                }
            },
            {
                "name": "Edit",
                "description": "Edit a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to the file to edit"},
                        "old_string": {"type": "string", "description": "String to replace"},
                        "new_string": {"type": "string", "description": "Replacement string"}
                    },
                    "required": ["file_path", "old_string", "new_string"]
                }
            }
        ],
        "stream": True,
        "max_tokens": 1000
    }

    result = {
        "test_name": "Multiple tool calls with file modification intent",
        "success": False,
        "error": None,
        "events": [],
        "connection_closed": False,
        "tool_use_count": 0
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "http://localhost:8010/v1/messages",
                json=request,
                headers={"Content-Type": "application/json"}
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            event_type = data.get("type", "")
                            result["events"].append(event_type)

                            # 记录工具使用
                            if event_type == "content_block_start":
                                block = data.get("content_block", {})
                                if block.get("type") == "tool_use":
                                    result["tool_use_count"] += 1
                                    tool_name = block.get("name", "")
                                    logger.info(f"🔧 工具调用: {tool_name}")

                            # 检查是否有 message_stop
                            if event_type == "message_stop":
                                result["success"] = True
                                logger.info("✅ 收到 message_stop，连接正常完成")

                        except json.JSONDecodeError:
                            pass

    except httpx.HTTPError as e:
        result["error"] = f"HTTP error: {str(e)}"
        result["connection_closed"] = True
        logger.error(f"❌ 连接错误: {e}")
    except Exception as e:
        result["error"] = str(e)
        result["connection_closed"] = True
        logger.error(f"❌ 未预期的错误: {e}")

    return result

async def test_with_context_and_tools() -> Dict[str, Any]:
    """测试带上下文的工具调用"""

    request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {
                "role": "user",
                "content": "查找 auth.py 文件"
            },
            {
                "role": "assistant",
                "content": "我来帮你查找 auth.py 文件。让我使用 Bash 工具来搜索。",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "arguments": '{"command": "find . -name auth.py", "description": "Find auth.py files"}'
                        }
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "./warp2protobuf/core/auth.py\n./protobuf2openai/auth.py"
            },
            {
                "role": "user",
                "content": "现在请修复 auth.py 中的多账号问题"
            }
        ],
        "tools": [
            {
                "name": "Bash",
                "description": "Execute bash commands",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "description": {"type": "string"}
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "Edit",
                "description": "Edit files",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"}
                    },
                    "required": ["file_path", "old_string", "new_string"]
                }
            }
        ],
        "stream": True,
        "max_tokens": 2000
    }

    result = {
        "test_name": "Context with tool calls and file modification",
        "success": False,
        "error": None,
        "events": [],
        "connection_closed": False
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "http://localhost:8010/v1/messages",
                json=request,
                headers={"Content-Type": "application/json"}
            ) as response:
                response.raise_for_status()

                event_count = 0
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        event_count += 1
                        try:
                            data = json.loads(line[6:])
                            event_type = data.get("type", "")
                            result["events"].append(event_type)

                            # 只记录关键事件
                            if event_type in ["message_start", "content_block_start", "message_stop", "error"]:
                                logger.info(f"Event #{event_count}: {event_type}")

                            if event_type == "message_stop":
                                result["success"] = True
                                logger.info("✅ 成功完成")

                        except json.JSONDecodeError:
                            pass

    except httpx.HTTPError as e:
        result["error"] = f"HTTP error: {str(e)}"
        result["connection_closed"] = True
        logger.error(f"❌ 连接断开: {e}")
    except Exception as e:
        result["error"] = str(e)
        result["connection_closed"] = True
        logger.error(f"❌ 错误: {e}")

    return result

async def main():
    """运行所有测试"""

    print("="*70)
    print("Warp 工具调用问题详细测试")
    print("="*70)

    tests = [
        test_bash_with_file_operation,
        test_multiple_tool_calls,
        test_with_context_and_tools
    ]

    results = []

    for test_func in tests:
        print(f"\n运行测试: {test_func.__name__}")
        print("-"*50)

        result = await test_func()
        results.append(result)

        # 打印测试结果
        print(f"\n测试: {result['test_name']}")
        if result['success']:
            print("✅ 状态: 成功")
        else:
            print("❌ 状态: 失败")

        if result.get('connection_closed'):
            print("⚠️  连接被关闭")

        if result.get('error'):
            print(f"错误: {result['error']}")

        print(f"收到事件数: {len(result['events'])}")
        if result['events']:
            print(f"事件类型: {', '.join(set(result['events']))}")

        # 等待一下避免请求过快
        await asyncio.sleep(3)

    # 总结
    print("\n" + "="*70)
    print("测试总结")
    print("="*70)

    success_count = sum(1 for r in results if r['success'])
    closed_count = sum(1 for r in results if r.get('connection_closed'))

    print(f"\n成功: {success_count}/{len(results)}")
    print(f"连接关闭: {closed_count}/{len(results)}")

    if closed_count > 0:
        print("\n⚠️  发现连接关闭问题:")
        for r in results:
            if r.get('connection_closed'):
                print(f"  - {r['test_name']}: {r.get('error', '未知错误')}")

    print("\n建议: 如果发现连接关闭，可能是工具名称冲突或服务端处理问题")

if __name__ == "__main__":
    asyncio.run(main())