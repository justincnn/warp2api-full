#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 warp-res/1.txt 中的 SSE 响应数据
参照 api_client.py 中的解析逻辑，并按照 sse_transform.py 输出 OpenAI 格式
"""
import re
import base64
import json
import uuid
import time
from typing import Dict, Any, List, Optional

# 导入项目模块
from warp2protobuf.core.protobuf_utils import protobuf_to_dict
from warp2protobuf.core.logging import logger


def _get(d: Dict[str, Any], *names: str) -> Any:
    """Return the first matching key value (camelCase/snake_case tolerant)."""
    for name in names:
        if isinstance(d, dict) and name in d:
            return d[name]
    return None


def _get_event_type(event_data: dict) -> str:
    """Determine the type of SSE event for logging"""
    if "init" in event_data:
        return "INITIALIZATION"
    client_actions = _get(event_data, "client_actions", "clientActions")
    if isinstance(client_actions, dict):
        actions = _get(client_actions, "actions", "Actions") or []
        if not actions:
            return "CLIENT_ACTIONS_EMPTY"

        action_types = []
        for action in actions:
            if _get(action, "create_task", "createTask") is not None:
                action_types.append("CREATE_TASK")
            elif _get(action, "append_to_message_content", "appendToMessageContent") is not None:
                action_types.append("APPEND_CONTENT")
            elif _get(action, "add_messages_to_task", "addMessagesToTask") is not None:
                action_types.append("ADD_MESSAGE")
            elif _get(action, "tool_call", "toolCall") is not None:
                action_types.append("TOOL_CALL")
            elif _get(action, "tool_response", "toolResponse") is not None:
                action_types.append("TOOL_RESPONSE")
            else:
                action_types.append("UNKNOWN_ACTION")

        return f"CLIENT_ACTIONS({', '.join(action_types)})"
    elif "finished" in event_data:
        return "FINISHED"
    else:
        return "UNKNOWN_EVENT"


def _parse_payload_bytes(data_str: str):
    """解析 payload 数据，参照 api_client.py 中的逻辑"""
    s = re.sub(r"\s+", "", data_str or "")
    if not s:
        return None
    if re.fullmatch(r"[0-9a-fA-F]+", s or ""):
        try:
            return bytes.fromhex(s)
        except Exception:
            pass
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad)
    except Exception:
        try:
            return base64.b64decode(s + pad)
        except Exception:
            return None


def parse_sse_file_to_openai(file_path: str) -> List[Dict]:
    """解析 SSE 文件并转换为 OpenAI 格式的事件流"""
    # OpenAI 格式配置
    completion_id = f"chatcmpl-{str(uuid.uuid4())}"
    created_ts = int(time.time())
    model_id = "claude-3-5-sonnet-20241022"

    openai_events = []
    conversation_id = None
    task_id = None
    event_count = 0
    tool_calls_emitted = False

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 发送初始事件
    first_event = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_id,
        "choices": [{"index": 0, "delta": {"role": "assistant"}}],
    }
    openai_events.append(first_event)
    print(f"🚀 OpenAI Event #1: {json.dumps(first_event, ensure_ascii=False)}")

    current_data = ""
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                i += 1
                continue
            if payload == "[DONE]":
                print("收到[DONE]标记，结束处理")
                break
            current_data += payload
            i += 1
            continue

        # 遇到空行且有累积数据时进行解析
        if line == "" and current_data:
            raw_bytes = _parse_payload_bytes(current_data)
            current_data = ""
            if raw_bytes is None:
                i += 1
                continue

            try:
                event_data = protobuf_to_dict(raw_bytes, "warp.multi_agent.v1.ResponseEvent")
            except Exception as parse_error:
                print(f"解析事件失败，跳过: {str(parse_error)[:100]}")
                i += 1
                continue

            event_count += 1
            event_type = _get_event_type(event_data)
            print(f"🔄 Warp Event #{event_count}: {event_type}")

            # 处理初始化数据
            if "init" in event_data:
                init_data = event_data["init"]
                conversation_id = init_data.get("conversation_id", conversation_id)
                task_id = init_data.get("task_id", task_id)
                print(f"   会话初始化: {conversation_id}")

            # 处理客户端动作，转换为 OpenAI 格式
            client_actions = _get(event_data, "client_actions", "clientActions")
            if isinstance(client_actions, dict):
                actions = _get(client_actions, "actions", "Actions") or []
                for action in actions:
                    # 处理追加内容
                    append_data = _get(action, "append_to_message_content", "appendToMessageContent")
                    if isinstance(append_data, dict):
                        message = append_data.get("message", {})
                        agent_output = _get(message, "agent_output", "agentOutput") or {}
                        text_content = agent_output.get("text", "")
                        if text_content:
                            delta_event = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": model_id,
                                "choices": [{"index": 0, "delta": {"content": text_content}}],
                            }
                            openai_events.append(delta_event)
                            print(f"   📝 OpenAI Content: {json.dumps(delta_event, ensure_ascii=False)}")

                    # 处理添加消息
                    messages_data = _get(action, "add_messages_to_task", "addMessagesToTask")
                    if isinstance(messages_data, dict):
                        messages = messages_data.get("messages", [])
                        task_id = messages_data.get("task_id", messages_data.get("taskId", task_id))
                        for message in messages:
                            # 处理工具调用
                            tool_call = _get(message, "tool_call", "toolCall") or {}
                            call_mcp = _get(tool_call, "call_mcp_tool", "callMcpTool") or {}
                            if isinstance(call_mcp, dict) and call_mcp.get("name"):
                                try:
                                    args_obj = call_mcp.get("args", {}) or {}
                                    args_str = json.dumps(args_obj, ensure_ascii=False)
                                except Exception:
                                    args_str = "{}"
                                tool_call_id = tool_call.get("tool_call_id") or str(uuid.uuid4())

                                tool_event = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model_id,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [{
                                                "index": 0,
                                                "id": tool_call_id,
                                                "type": "function",
                                                "function": {"name": call_mcp.get("name"), "arguments": args_str},
                                            }]
                                        }
                                    }],
                                }
                                openai_events.append(tool_event)
                                tool_calls_emitted = True
                                print(f"   🔧 OpenAI Tool Call: {json.dumps(tool_event, ensure_ascii=False)}")

                            # 处理工具调用结果
                            tool_call_result = _get(message, "tool_call_result", "toolCallResult") or {}
                            if isinstance(tool_call_result, dict) and tool_call_result.get("tool_call_id"):
                                tool_call_id = tool_call_result.get("tool_call_id")
                                server_result = _get(tool_call_result, "server", "server") or {}
                                serialized_result = server_result.get("serialized_result", "")

                                # 解码 serialized_result (Base64URL)
                                result_content = ""
                                if serialized_result:
                                    try:
                                        decoded_bytes = base64.urlsafe_b64decode(serialized_result + '=' * (-len(serialized_result) % 4))
                                        result_content = decoded_bytes.decode('utf-8')
                                        print(f"   🔧 工具结果解码: {result_content[:200]}...")
                                    except Exception as e:
                                        result_content = f"[解码失败: {str(e)}]"

                                # 发送工具调用结果
                                tool_result_event = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model_id,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [{
                                                "index": 0,
                                                "id": tool_call_id,
                                                "type": "function",
                                                "function": {"name": "tool_result", "arguments": "{}"},
                                            }]
                                        }
                                    }],
                                }
                                openai_events.append(tool_result_event)
                                print(f"   🔧 OpenAI Tool Result: {json.dumps(tool_result_event, ensure_ascii=False)}")

                                # 发送工具结果内容
                                if result_content:
                                    content_event = {
                                        "id": completion_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_id,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {
                                                "tool_calls": [{
                                                    "index": 0,
                                                    "id": tool_call_id,
                                                    "type": "function",
                                                    "function": {"name": "tool_result_content", "arguments": json.dumps({"content": result_content}, ensure_ascii=False)},
                                                }]
                                            }
                                        }],
                                    }
                                    openai_events.append(content_event)
                                    print(f"   📝 OpenAI Tool Content: {json.dumps(content_event, ensure_ascii=False)}")
                            else:
                                # 处理普通文本内容
                                agent_output = _get(message, "agent_output", "agentOutput") or {}
                                text_content = agent_output.get("text", "")
                                if text_content:
                                    delta_event = {
                                        "id": completion_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_id,
                                        "choices": [{"index": 0, "delta": {"content": text_content}}],
                                    }
                                    openai_events.append(delta_event)
                                    print(f"   📝 OpenAI Message: {json.dumps(delta_event, ensure_ascii=False)}")

            # 处理完成事件
            if "finished" in event_data:
                finished_data = event_data.get("finished", {})
                request_cost = finished_data.get("request_cost", {})
                context_window_info = finished_data.get("context_window_info", {})

                # 估算 token 使用情况
                total_cost = request_cost.get("exact", 0)
                context_usage = context_window_info.get("context_window_usage", 0)
                estimated_input_tokens = int(context_usage * 100000) if context_usage else 0
                estimated_output_tokens = int(total_cost * 1000) if total_cost else 0

                done_event = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_id,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": ("tool_calls" if tool_calls_emitted else "stop")}],
                    "usage": {
                        "prompt_tokens": estimated_input_tokens,
                        "completion_tokens": estimated_output_tokens,
                        "total_tokens": estimated_input_tokens + estimated_output_tokens
                    }
                }
                openai_events.append(done_event)
                print(f"✅ OpenAI Done: {json.dumps(done_event, ensure_ascii=False)}")

        i += 1

    print("=" * 60)
    print("📊 OpenAI SSE STREAM SUMMARY")
    print("=" * 60)
    print(f"📈 Total Warp Events Processed: {event_count}")
    print(f"📤 Total OpenAI Events Generated: {len(openai_events)}")
    print(f"🆔 Conversation ID: {conversation_id}")
    print(f"🆔 Task ID: {task_id}")
    print(f"🔧 Tool Calls Emitted: {tool_calls_emitted}")
    print("=" * 60)

    return openai_events


def parse_sse_file(file_path: str) -> tuple[str, Optional[str], Optional[str], List[Dict]]:
    """解析 SSE 文件（原始格式）"""
    conversation_id = None
    task_id = None
    complete_response = []
    all_events = []
    event_count = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    current_data = ""
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                i += 1
                continue
            if payload == "[DONE]":
                print("收到[DONE]标记，结束处理")
                break
            current_data += payload
            i += 1
            continue

        # 遇到空行且有累积数据时进行解析
        if line == "" and current_data:
            raw_bytes = _parse_payload_bytes(current_data)
            current_data = ""
            if raw_bytes is None:
                print("跳过无法解析的SSE数据块（非hex/base64或不完整）")
                i += 1
                continue

            try:
                event_data = protobuf_to_dict(raw_bytes, "warp.multi_agent.v1.ResponseEvent")
            except Exception as parse_error:
                print(f"解析事件失败，跳过: {str(parse_error)[:100]}")
                i += 1
                continue

            event_count += 1
            event_type = _get_event_type(event_data)
            all_events.append({
                "event_number": event_count,
                "event_type": event_type,
                "raw_data": event_data
            })

            print(f"🔄 Event #{event_count}: {event_type}")

            # 处理初始化数据
            if "init" in event_data:
                init_data = event_data["init"]
                conversation_id = init_data.get("conversation_id", conversation_id)
                task_id = init_data.get("task_id", task_id)
                print(f"会话初始化: {conversation_id}")

            # 处理客户端动作
            client_actions = _get(event_data, "client_actions", "clientActions")
            if isinstance(client_actions, dict):
                actions = _get(client_actions, "actions", "Actions") or []
                for j, action in enumerate(actions):
                    print(f"   🎯 Action #{j+1}: {list(action.keys())}")

                    # 处理追加内容
                    append_data = _get(action, "append_to_message_content", "appendToMessageContent")
                    if isinstance(append_data, dict):
                        message = append_data.get("message", {})
                        agent_output = _get(message, "agent_output", "agentOutput") or {}
                        text_content = agent_output.get("text", "")
                        if text_content:
                            complete_response.append(text_content)
                            print(f"   📝 Text Fragment: {text_content[:100]}...")

                    # 处理添加消息
                    messages_data = _get(action, "add_messages_to_task", "addMessagesToTask")
                    if isinstance(messages_data, dict):
                        messages = messages_data.get("messages", [])
                        task_id = messages_data.get("task_id", messages_data.get("taskId", task_id))
                        for k, message in enumerate(messages):
                            print(f"   📨 Message #{k+1}: {list(message.keys())}")
                            if _get(message, "agent_output", "agentOutput") is not None:
                                agent_output = _get(message, "agent_output", "agentOutput") or {}
                                text_content = agent_output.get("text", "")
                                if text_content:
                                    complete_response.append(text_content)
                                    print(f"   📝 Complete Message: {text_content[:100]}...")

        i += 1

    full_response = "".join(complete_response)

    print("=" * 60)
    print("📊 SSE STREAM SUMMARY")
    print("=" * 60)
    print(f"📈 Total Events Processed: {event_count}")
    print(f"🆔 Conversation ID: {conversation_id}")
    print(f"🆔 Task ID: {task_id}")
    print(f"📝 Response Length: {len(full_response)} characters")
    print("=" * 60)

    return full_response, conversation_id, task_id, all_events


def main():
    """主函数"""
    file_path = "warp-res/1.txt"

    import sys
    mode = "openai"  # 默认使用 OpenAI 格式
    if len(sys.argv) > 1:
        mode = sys.argv[1]

    print(f"开始解析文件: {file_path}")
    print(f"输出模式: {mode}")
    print("=" * 60)

    try:
        if mode == "openai":
            # 输出 OpenAI 格式
            openai_events = parse_sse_file_to_openai(file_path)

            print("\n" + "=" * 80)
            print("📤 OpenAI 格式 SSE 事件流:")
            print("=" * 80)

            for i, event in enumerate(openai_events, 1):
                print(f"🔸 Event #{i}:")
                print(f"data: {json.dumps(event, ensure_ascii=False)}")
                print()

            print("data: [DONE]")
            print("=" * 80)

            # 保存到文件
            output_file = "openai_formatted_events.jsonl"
            with open(output_file, 'w', encoding='utf-8') as f:
                for event in openai_events:
                    f.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n")
                f.write("data: [DONE]\n\n")
            print(f"✅ OpenAI 格式事件已保存到: {output_file}")

        else:
            # 原始格式解析
            full_response, conversation_id, task_id, all_events = parse_sse_file(file_path)

            print("\n" + "=" * 60)
            print("📄 完整响应内容:")
            print("=" * 60)
            print(full_response)
            print("=" * 60)

            if all_events:
                print(f"\n解析了 {len(all_events)} 个事件")
                print("事件类型统计:")
                event_types = {}
                for event in all_events:
                    event_type = event["event_type"]
                    event_types[event_type] = event_types.get(event_type, 0) + 1

                for event_type, count in event_types.items():
                    print(f"  - {event_type}: {count}")

    except Exception as e:
        print(f"解析失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()