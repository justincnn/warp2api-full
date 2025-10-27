from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional, Set

import httpx
from .logging import logger
from .token_counter import count_packet_tokens, count_tokens

from .config import BRIDGE_BASE_URL
from .helpers import _get


# 自定义异常：用于触发自动恢复
class InternalErrorRecoverable(Exception):
    """表示遇到了可恢复的 internal_error"""
    def __init__(self, tool_name: Optional[str], error_message: str):
        self.tool_name = tool_name
        self.error_message = error_message
        super().__init__(f"Internal error with tool: {tool_name}")


class LLMUnavailableRecoverable(Exception):
    """表示遇到了可恢复的 llm_unavailable"""
    pass


def get_model_context_window(model_name: str) -> int:
    """根据模型名称获取上下文窗口大小

    基于已知的 Claude 模型上下文窗口：
    - Claude 3.5 Sonnet: 200k tokens
    - Claude 3 Opus: 200k tokens
    - Claude 3.5 Haiku: 200k tokens
    - Claude 4 Sonnet: 200k tokens (假设)
    - Claude 4.1 Opus: 200k tokens (假设)
    - 默认: 100k tokens (保守估计)
    """
    model_lower = model_name.lower() if model_name else ""

    # Claude 3 和 3.5 系列
    if "claude-3" in model_lower:
        return 200000

    # Claude 4 系列 (包括 claude-4-sonnet 和 claude-4.1-opus)
    if "claude-4" in model_lower:
        # claude-4.1-opus 也包含在这里
        return 200000

    # 默认值（保守估计）
    return 100000


async def _process_sse_response_lines(response, completion_id: str, created_ts: int, model_id: str, input_tokens: int = 0) -> AsyncGenerator[str, None]:
    """处理 SSE 响应流的共用函数

    Args:
        response: HTTP 响应对象
        completion_id: 完成请求 ID
        created_ts: 创建时间戳
        model_id: 模型 ID
        input_tokens: 预计算的输入 token 数
    """
    current = ""
    tool_calls_emitted = False
    output_text = ""  # 累积所有输出文本，用于准确计算 token
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload:
                continue
            # 打印接收到的 Protobuf SSE 原始事件片段
            try:
                logger.info("[OpenAI Compat] 接收到的 Protobuf SSE(data): %s", payload)
            except Exception:
                pass
            if payload == "[DONE]":
                break
            current += payload
            continue
        if (line.strip() == "") and current:
            try:
                ev = json.loads(current)
            except Exception:
                current = ""
                continue
            current = ""
            event_data = (ev or {}).get("parsed_data") or {}

            # 打印接收到的 Protobuf 事件（解析后）
            try:
                logger.info("[OpenAI Compat] 接收到的 Protobuf 事件(parsed): %s", json.dumps(event_data, ensure_ascii=False))
            except Exception:
                pass

            if "init" in event_data:
                pass

            client_actions = _get(event_data, "client_actions", "clientActions")
            if isinstance(client_actions, dict):
                actions = _get(client_actions, "actions", "Actions") or []
                for action in actions:
                    append_data = _get(action, "append_to_message_content", "appendToMessageContent")
                    if isinstance(append_data, dict):
                        message = append_data.get("message", {})
                        agent_output = _get(message, "agent_output", "agentOutput") or {}
                        text_content = agent_output.get("text", "")
                        if text_content:
                            output_text += text_content  # 累积输出文本
                            delta = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": model_id,
                                "choices": [{"index": 0, "delta": {"content": text_content}}],
                            }
                            # 打印转换后的 OpenAI SSE 事件
                            try:
                                logger.info("[OpenAI Compat] 转换后的 SSE(emit): %s", json.dumps(delta, ensure_ascii=False))
                            except Exception:
                                pass
                            yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

                    messages_data = _get(action, "add_messages_to_task", "addMessagesToTask")
                    if isinstance(messages_data, dict):
                        messages = messages_data.get("messages", [])
                        for message in messages:
                            # 先检查是否是工具调用结果
                            tool_call_result = _get(message, "tool_call_result", "toolCallResult") or {}
                            if tool_call_result:
                                # 这是工具调用结果，按原有逻辑处理
                                if isinstance(tool_call_result, dict) and tool_call_result.get("tool_call_id"):
                                    tool_call_id = tool_call_result.get("tool_call_id")
                                    server_result = _get(tool_call_result, "server", "server") or {}
                                    serialized_result = server_result.get("serialized_result", "")

                                    # 解码 serialized_result (Base64URL)
                                    result_content = ""
                                    task_data = None
                                    if serialized_result:
                                        try:
                                            import base64
                                            # Base64URL 解码
                                            decoded_bytes = base64.urlsafe_b64decode(serialized_result + '=' * (-len(serialized_result) % 4))

                                            # 尝试用 blackboxprotobuf 解析为 protobuf
                                            try:
                                                import blackboxprotobuf
                                            except ImportError:
                                                blackboxprotobuf = None

                                            if blackboxprotobuf:
                                                try:
                                                    decoded_data, _ = blackboxprotobuf.decode_message(decoded_bytes)
                                                    logger.info("[OpenAI Compat] tool_call_result serialized_result 解码成功: %s", json.dumps(decoded_data, ensure_ascii=False))

                                                    # 检查是否包含任务数据 (11 或 9 键)
                                                    if "11" in decoded_data or "9" in decoded_data:
                                                        task_data = decoded_data
                                                        logger.info("[OpenAI Compat] 检测到 tool_call_result 中的任务数据")
                                                    else:
                                                        # 不是任务数据，按原来的方式处理
                                                        result_content = decoded_bytes.decode('utf-8')
                                                except Exception as e:
                                                    logger.debug("[OpenAI Compat] tool_call_result Protobuf 解码失败: %s", e)
                                                    result_content = decoded_bytes.decode('utf-8')
                                            else:
                                                result_content = decoded_bytes.decode('utf-8')

                                            if not task_data:
                                                logger.info("[OpenAI Compat] 解码工具结果: %s", result_content[:200] + "..." if len(result_content) > 200 else result_content)
                                        except Exception as e:
                                            logger.error("[OpenAI Compat] 解码 serialized_result 失败: %s", e)
                                            result_content = f"[解码失败: {str(e)}]"

                                    # 如果检测到任务数据，生成 TodoWrite 工具调用
                                    if task_data:
                                        # 转换任务数据为 TodoWrite 格式
                                        todos = []
                                        task_container = None

                                        # 检查是否是任务列表数据 (11 或 9 键)
                                        if "11" in task_data:
                                            nested_data = task_data["11"]
                                            if isinstance(nested_data, dict) and "1" in nested_data:
                                                task_container = nested_data["1"]
                                        elif "9" in task_data:
                                            nested_data = task_data["9"]
                                            if isinstance(nested_data, dict) and "1" in nested_data:
                                                task_container = nested_data["1"]

                                        if task_container and isinstance(task_container, dict):
                                            # 未开始任务
                                            if "1" in task_container and isinstance(task_container["1"], list):
                                                for task in task_container["1"]:
                                                    if isinstance(task, dict) and "1" in task:
                                                        todos.append({
                                                            "content": task.get("2", ""),
                                                            "status": "pending",
                                                            "activeForm": f"执行 {task.get('2', '')}"
                                                        })

                                            # 已完成任务
                                            if "2" in task_container and isinstance(task_container["2"], list):
                                                for task in task_container["2"]:
                                                    if isinstance(task, dict) and "1" in task:
                                                        todos.append({
                                                            "content": task.get("2", ""),
                                                            "status": "completed",
                                                            "activeForm": f"已完成 {task.get('2', '')}"
                                                        })

                                        # 生成 TodoWrite 工具调用
                                        todo_args = json.dumps({"todos": todos}, ensure_ascii=False)
                                        delta = {
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
                                                        "function": {"name": "TodoWrite", "arguments": todo_args},
                                                    }]
                                                }
                                            }],
                                        }
                                        logger.info("[OpenAI Compat] 生成 TodoWrite 工具调用，包含 %d 个任务", len(todos))
                                    # else:
                                    #     # 发送普通工具调用结果
                                    #     delta = {
                                    #         "id": completion_id,
                                    #         "object": "chat.completion.chunk",
                                    #         "created": created_ts,
                                    #         "model": model_id,
                                    #         "choices": [{
                                    #             "index": 0,
                                    #             "delta": {
                                    #                 "tool_calls": [{
                                    #                     "index": 0,
                                    #                     "id": tool_call_id,
                                    #                     "type": "function",
                                    #                     "function": {"name": "tool_result", "arguments": "{}"},
                                    #                 }]
                                    #             }
                                    #         }],
                                    #     }
                                        try:
                                            logger.info("[OpenAI Compat] 转换后的 SSE(emit tool_result): %s", json.dumps(delta, ensure_ascii=False))
                                        except Exception:
                                            pass
                                        yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

                                    # 发送工具结果内容（仅当不是任务数据时）
                                    # if result_content and not task_data:
                                    #     content_delta = {
                                    #         "id": completion_id,
                                    #         "object": "chat.completion.chunk",
                                    #         "created": created_ts,
                                    #         "model": model_id,
                                    #         "choices": [{
                                    #             "index": 0,
                                    #             "delta": {
                                    #                 "tool_calls": [{
                                    #                     "index": 0,
                                    #                     "id": tool_call_id,
                                    #                     "type": "function",
                                    #                     "function": {"name": "tool_result_content", "arguments": json.dumps({"content": result_content}, ensure_ascii=False)},
                                    #                 }]
                                    #             }
                                    #         }],
                                    #     }
                                    #     try:
                                    #         logger.info("[OpenAI Compat] 转换后的 SSE(emit tool_result_content): %s", json.dumps(content_delta, ensure_ascii=False))
                                    #     except Exception:
                                    #         pass
                                    #     yield f"data: {json.dumps(content_delta, ensure_ascii=False)}\n\n"
                            else:
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
                                    delta = {
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
                                    # 打印转换后的 OpenAI 工具调用事件
                                    try:
                                        logger.info("[OpenAI Compat] 转换后的 SSE(emit tool_calls): %s", json.dumps(delta, ensure_ascii=False))
                                    except Exception:
                                        pass
                                    yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"
                                    tool_calls_emitted = True
                                else:
                                    # 处理普通文本内容
                                    agent_output = _get(message, "agent_output", "agentOutput") or {}
                                    text_content = agent_output.get("text", "")
                                    if text_content:
                                        output_text += text_content  # 累积输出文本
                                        delta = {
                                            "id": completion_id,
                                            "object": "chat.completion.chunk",
                                            "created": created_ts,
                                            "model": model_id,
                                            "choices": [{"index": 0, "delta": {"content": text_content}}],
                                        }
                                        try:
                                            logger.info("[OpenAI Compat] 转换后的 SSE(emit): %s", json.dumps(delta, ensure_ascii=False))
                                        except Exception:
                                            pass
                                        yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

                    # 处理 update_task_message
                    update_task_message = _get(action, "update_task_message", "updateTaskMessage")
                    if isinstance(update_task_message, dict):
                        message = update_task_message.get("message", {})
                        if isinstance(message, dict):
                            # 处理 agent_output.text
                            agent_output = _get(message, "agent_output", "agentOutput") or {}
                            text_content = agent_output.get("text", "")
                            if text_content:
                                output_text += text_content  # 累积输出文本
                                delta = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model_id,
                                    "choices": [{"index": 0, "delta": {"content": text_content}}],
                                }
                                try:
                                    logger.info("[OpenAI Compat] 转换后的 SSE(emit update_task_message): %s", json.dumps(delta, ensure_ascii=False))
                                except Exception:
                                    pass
                                yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

                    # 处理 create_task
                    create_task = _get(action, "create_task", "createTask")
                    if isinstance(create_task, dict):
                        task = create_task.get("task", {})
                        if isinstance(task, dict):
                            messages = task.get("messages", [])
                            for message in messages:
                                if isinstance(message, dict):
                                    agent_output = _get(message, "agent_output", "agentOutput") or {}
                                    text_content = agent_output.get("text", "")
                                    if text_content:
                                        output_text += text_content  # 累积输出文本
                                        delta = {
                                            "id": completion_id,
                                            "object": "chat.completion.chunk",
                                            "created": created_ts,
                                            "model": model_id,
                                            "choices": [{"index": 0, "delta": {"content": text_content}}],
                                        }
                                        try:
                                            logger.info("[OpenAI Compat] 转换后的 SSE(emit create_task): %s", json.dumps(delta, ensure_ascii=False))
                                        except Exception:
                                            pass
                                        yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

                    # 处理 update_task_summary
                    update_task_summary = _get(action, "update_task_summary", "updateTaskSummary")
                    if isinstance(update_task_summary, dict):
                        summary = update_task_summary.get("summary", "")
                        if summary:
                            output_text += summary  # 累积输出文本
                            delta = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": model_id,
                                "choices": [{"index": 0, "delta": {"content": summary}}],
                            }
                            try:
                                logger.info("[OpenAI Compat] 转换后的 SSE(emit update_task_summary): %s", json.dumps(delta, ensure_ascii=False))
                            except Exception:
                                pass
                            yield f"data: {json.dumps(delta, ensure_ascii=False)}\n\n"

                    # 处理 add_messages_to_task 中的 tool_call server.payload (Base64编码的任务初始化数据)
                    # 处理更新任务消息事件等其他逻辑...
                    # (为了简化提取，这里暂时省略其他复杂事件处理逻辑)

            # 处理上下文重置事件 - 标记但不立即结束
            context_reset_pending_tasks = ""
            if "update_task_description" in event_data:
                try:
                    logger.info("[OpenAI Compat] 检测到上下文重置事件，准备任务延续提示")

                    # 提取任务描述中的待处理任务
                    task_desc = event_data.get("update_task_description", {}).get("description", "")

                    # 简单解析待处理任务
                    if "Pending Tasks:" in task_desc:
                        tasks_section = task_desc.split("Pending Tasks:")[1].split("\n\n")[0]
                        context_reset_pending_tasks = f"\n\n📋 **上下文已重置，但有待处理任务:**\n{tasks_section.strip()}\n\n⚠️ **重要提醒：** 为避免重复重置，请：\n• 方案1：执行压缩上下文指令（如 `/compact`）\n• 方案2：开启新对话继续未完成的任务\n\n💡 请继续之前的工作或询问需要完成的具体任务。"
                    elif "Optional Next Step:" in task_desc:
                        next_step_section = task_desc.split("Optional Next Step:")[1].split("\n\n")[0]
                        context_reset_pending_tasks = f"\n\n📋 **上下文已重置，建议下一步:**\n{next_step_section.strip()}\n\n⚠️ **重要提醒：** 为避免重复重置，请：\n• 方案1：执行压缩上下文指令（如 `/compact`）\n• 方案2：开启新对话继续未完成的任务\n\n💡 请继续之前的工作或询问需要完成的具体任务。"
                    else:
                        context_reset_pending_tasks = f"\n\n📋 **上下文已重置**\n\n⚠️ **重要提醒：** 为避免重复重置，请：\n• 方案1：执行压缩上下文指令（如 `/compact`）\n• 方案2：开启新对话继续工作\n\n💡 对话上下文过长已自动重置。如有未完成的任务，请重新说明需要继续的工作。"

                    # 发送任务延续提示
                    if context_reset_pending_tasks:
                        output_text += context_reset_pending_tasks  # 累积输出文本
                        continuation_delta = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model_id,
                            "choices": [{"index": 0, "delta": {"content": context_reset_pending_tasks}}],
                        }
                        logger.info("[OpenAI Compat] 发送任务延续提示: %s", json.dumps(continuation_delta, ensure_ascii=False))
                        yield f"data: {json.dumps(continuation_delta, ensure_ascii=False)}\n\n"

                except Exception as e:
                    logger.error(f"[OpenAI Compat] 处理上下文重置事件失败: {e}")
                    # 继续正常流程

            if "finished" in event_data:
                # Extract token usage from finished event
                finished_data = event_data.get("finished", {})
                request_cost = finished_data.get("request_cost", {})
                context_window_info = finished_data.get("context_window_info", {})

                # 检测到 internal_error - 抛出可恢复异常以触发自动重试
                if "internal_error" in finished_data:
                    error_info = finished_data["internal_error"]
                    error_message = error_info.get("message", "Unknown internal error")

                    logger.error(f"[OpenAI Compat] 服务返回 internal_error: {error_message}")

                    # 尝试从错误消息中提取工具名称
                    tool_name = None
                    import re
                    tool_match = re.search(r'tool_call:\{[^}]*?(\w+):\{\}', error_message)
                    if tool_match:
                        tool_name = tool_match.group(1)

                    # 抛出可恢复异常，由外层处理自动重试
                    raise InternalErrorRecoverable(tool_name, error_message)

                # 检测到 llm_unavailable - 抛出可恢复异常以触发自动重试
                if "llm_unavailable" in finished_data:
                    logger.error("[OpenAI Compat] 服务返回 llm_unavailable")
                    raise LLMUnavailableRecoverable()

                # 使用 tiktoken 准确计算输出 token 数
                estimated_output_tokens = count_tokens(output_text, model_id) if output_text else 0
                if estimated_output_tokens == 0:
                    estimated_output_tokens = 1  # 至少 1 个 token

                # 计算输入 token：使用 context_window_info 的比例值
                # context_window_info 包含一个 0-1 的比例值，表示使用了多少上下文窗口
                # 例如 0.45 表示使用了 45% 的上下文窗口
                if context_window_info:
                    # 从 context_window_info 中获取使用比例
                    # context_window_info 可能是一个字典或直接是数值
                    if isinstance(context_window_info, dict):
                        # 如果是字典，查找可能的键
                        context_usage = context_window_info.get("context_window_usage", 0) or context_window_info.get("used", 0) or context_window_info.get("ratio", 0)
                    else:
                        # 如果直接是数值
                        context_usage = float(context_window_info) if context_window_info else 0

                    # 获取模型的上下文窗口大小
                    max_context = get_model_context_window(model_id)

                    # 计算实际使用的 token 数
                    estimated_input_tokens = int(context_usage * max_context) if context_usage > 0 else (input_tokens if input_tokens > 0 else 1000)

                    # 记录日志
                    logger.info(f"[OpenAI Compat] Token 计算: context_usage={context_usage}, max_context={max_context}, prompt_tokens={estimated_input_tokens}")
                else:
                    # 如果没有 context_window_info，使用传入的预计算值或默认值
                    estimated_input_tokens = input_tokens if input_tokens > 0 else 1000

                done_chunk = {
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
                try:
                    logger.info("[OpenAI Compat] 转换后的 SSE(emit done): %s", json.dumps(done_chunk, ensure_ascii=False))
                except Exception:
                    pass
                yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"

    # 打印完成标记
    try:
        logger.info("[OpenAI Compat] 转换后的 SSE(emit): [DONE]")
    except Exception:
        pass
    yield "data: [DONE]\n\n"


def estimate_input_tokens(packet: Dict[str, Any]) -> int:
    """估算输入 packet 中的 token 数量

    Args:
        packet: 包含请求数据的字典

    Returns:
        估算的 token 数量
    """
    total_chars = 0

    # 计算 user inputs 中的文本
    if "input" in packet and "user_inputs" in packet["input"]:
        inputs = packet["input"]["user_inputs"].get("inputs", [])
        for inp in inputs:
            if isinstance(inp, dict):
                # 处理 text 字段
                if "text" in inp:
                    total_chars += len(str(inp["text"]))
                # 处理 attachments 中的文本
                if "attachments" in inp:
                    for attachment in inp["attachments"]:
                        if isinstance(attachment, dict) and "text" in attachment:
                            total_chars += len(str(attachment["text"]))

                # 处理 user_query 中的内容（包括查询和引用附件）
                if "user_query" in inp:
                    user_query = inp["user_query"]
                    if isinstance(user_query, dict):
                        # 计算查询文本
                        if "query" in user_query:
                            total_chars += len(str(user_query["query"]))

                        # 重要：计算 referenced_attachments（系统提示词、工具限制等）
                        if "referenced_attachments" in user_query:
                            refs = user_query["referenced_attachments"]
                            if isinstance(refs, dict):
                                for key, ref in refs.items():
                                    if isinstance(ref, dict):
                                        # 处理纯文本附件
                                        if "plain_text" in ref:
                                            total_chars += len(str(ref["plain_text"]))
                                        # 处理其他可能的文本字段
                                        if "text" in ref:
                                            total_chars += len(str(ref["text"]))

    # 计算 task_context 中的历史消息
    if "task_context" in packet:
        # messages 可能在 task_context.messages 或 task_context.tasks[0].messages
        messages = []

        # 尝试直接从 task_context 获取 messages
        if "messages" in packet["task_context"]:
            messages = packet["task_context"]["messages"]
        # 尝试从 tasks 列表获取 messages
        elif "tasks" in packet["task_context"] and packet["task_context"]["tasks"]:
            for task in packet["task_context"]["tasks"]:
                if isinstance(task, dict) and "messages" in task:
                    messages.extend(task["messages"])

        for msg in messages:
            if isinstance(msg, dict):
                # 处理 agent_output
                if "agent_output" in msg:
                    output = msg["agent_output"]
                    if isinstance(output, dict) and "text" in output:
                        total_chars += len(str(output["text"]))
                # 处理 user_input
                if "user_input" in msg:
                    user_input = msg["user_input"]
                    if isinstance(user_input, dict) and "text" in user_input:
                        total_chars += len(str(user_input["text"]))

    # 计算工具定义的字符数
    if "mcp_context" in packet and "tools" in packet["mcp_context"]:
        tools = packet["mcp_context"]["tools"]
        # 工具定义通常比较长，简单估算
        total_chars += len(json.dumps(tools, ensure_ascii=False))

    # 估算 token 数：平均每个 token 约 4 个字符（英文）
    # 对于混合中英文内容，这是一个合理的估算
    estimated_tokens = max(total_chars // 4, 1)

    return estimated_tokens


async def stream_openai_sse(
    packet: Dict[str, Any],
    completion_id: str,
    created_ts: int,
    model_id: str,
    retry_count: int = 0,
    restricted_tools: Optional[Set[str]] = None
) -> AsyncGenerator[str, None]:
    try:
        # 使用新的 tiktoken 计算输入 token 数
        input_tokens = count_packet_tokens(packet, model_id)
        logger.info(f"[OpenAI Compat] 计算的输入 token 数 (tiktoken): {input_tokens}")

        # 仅在首次调用时发送 role 首块，避免重试时重复发送
        if retry_count == 0:
            first = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            }
            # 打印转换后的首个 SSE 事件（OpenAI 格式）
            try:
                logger.info("[OpenAI Compat] 转换后的 SSE(emit): %s", json.dumps(first, ensure_ascii=False))
            except Exception:
                pass
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

        timeout = httpx.Timeout(600.0)
        async with httpx.AsyncClient(http2=True, timeout=timeout, trust_env=True) as client:
            def _do_stream():
                """创建一个新的流式请求"""
                return client.stream(
                    "POST",
                    f"{BRIDGE_BASE_URL}/api/warp/send_stream_sse",
                    headers={"accept": "text/event-stream"},
                    json={"json_data": packet, "message_type": "warp.multi_agent.v1.Request"},
                )

            async def _check_response_error(response):
                """检查响应状态码，如果不是200则抛出错误"""
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_content = error_text.decode("utf-8") if error_text else ""
                    logger.error(f"[OpenAI Compat] Bridge HTTP error {response.status_code}: {error_content[:300]}")
                    raise RuntimeError(f"bridge error: {error_content}")

            # 首次请求
            response_cm = _do_stream()
            async with response_cm as response:
                # 处理 429 错误（令牌过期）
                if response.status_code == 429:
                    try:
                        r = await client.post(f"{BRIDGE_BASE_URL}/api/auth/refresh", timeout=10.0)
                        logger.warning("[OpenAI Compat] Bridge returned 429. Tried JWT refresh -> HTTP %s", r.status_code)
                    except Exception as _e:
                        logger.warning("[OpenAI Compat] JWT refresh attempt failed after 429: %s", _e)
                    # 重试请求
                    response_cm2 = _do_stream()
                    async with response_cm2 as response2:
                        await _check_response_error(response2)
                        # 使用共用函数处理响应流
                        async for chunk in _process_sse_response_lines(response2, completion_id, created_ts, model_id, input_tokens):
                            yield chunk
                    return

                # 检查响应状态码
                await _check_response_error(response)

                # 使用共用函数处理响应流
                async for chunk in _process_sse_response_lines(response, completion_id, created_ts, model_id, input_tokens):
                    yield chunk
    except InternalErrorRecoverable as e:
        # 检测到可恢复的 internal_error，尝试自动重试
        if retry_count >= 1:
            # 已经重试过一次，不再重试
            logger.error(f"[OpenAI Compat] Internal error 恢复失败，已达最大重试次数: {e.tool_name}")
            error_text = (
                f"\n\n⚠️ **服务内部错误（无法自动恢复）**\n\n"
                f"AI 多次尝试调用被限制的工具：`{e.tool_name}`\n\n"
                f"**建议解决方案：**\n"
                f"• 🔄 换个方式描述你的需求\n"
                f"• 💡 简化请求范围\n"
                f"• 📝 明确说明避免某些操作\n"
            )
            error_delta = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [{"index": 0, "delta": {"content": error_text}}],
            }
            yield f"data: {json.dumps(error_delta, ensure_ascii=False)}\n\n"

            done_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": 50, "total_tokens": input_tokens + 50}
            }
            yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 第一次遇到错误，自动恢复
        logger.info(f"[OpenAI Compat] 检测到 internal_error，自动恢复中... (工具: {e.tool_name}, 重试次数: {retry_count})")

        # 初始化 restricted_tools 集合
        if restricted_tools is None:
            restricted_tools = set()

        # 记录失败的工具
        if e.tool_name:
            restricted_tools.add(e.tool_name)

        # 构造恢复提示
        if e.tool_name:
            recovery_prompt = f"\n\n[系统自动恢复] 请继续之前的任务，但不要使用 {e.tool_name} 工具。可用的工具包括：Read、Write、Edit、Bash、Glob、Grep 等 MCP 工具。"
        else:
            recovery_prompt = "\n\n[系统自动恢复] 请继续之前的任务，使用可用的 MCP 工具完成。"

        # 发送恢复提示给用户（让用户知道正在自动恢复）
        recovery_notice = f"\n\n🔄 **正在自动恢复...**\n\n检测到工具限制冲突，系统正在重新尝试任务。\n"
        notice_delta = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_id,
            "choices": [{"index": 0, "delta": {"content": recovery_notice}}],
        }
        yield f"data: {json.dumps(notice_delta, ensure_ascii=False)}\n\n"

        # 修改 packet，在用户查询中添加恢复提示
        import copy
        new_packet = copy.deepcopy(packet)

        # 在 user_inputs 中的最后一个 user_query 添加恢复提示
        if "input" in new_packet and "user_inputs" in new_packet["input"]:
            inputs = new_packet["input"]["user_inputs"].get("inputs", [])
            if inputs:
                last_input = inputs[-1]
                if "user_query" in last_input and isinstance(last_input["user_query"], dict):
                    # 在查询末尾附加恢复提示
                    current_query = last_input["user_query"].get("query", "")
                    # 避免重复附加相同恢复提示
                    if "[系统自动恢复]" not in current_query:
                        last_input["user_query"]["query"] = current_query + recovery_prompt
                        logger.info(f"[OpenAI Compat] 已在请求中添加恢复提示: {recovery_prompt[:100]}...")
                    else:
                        logger.info("[OpenAI Compat] 检测到已包含系统自动恢复提示，跳过追加")

        # 递归调用自己，使用新的 packet 和增加的 retry_count
        logger.info(f"[OpenAI Compat] 开始自动重试 (retry_count={retry_count + 1})")
        async for chunk in stream_openai_sse(new_packet, completion_id, created_ts, model_id, retry_count + 1, restricted_tools):
            yield chunk

    except LLMUnavailableRecoverable:
        # 检测到 llm_unavailable，尝试自动重试
        if retry_count >= 1:
            logger.error("[OpenAI Compat] LLM unavailable 恢复失败，已达最大重试次数")
            error_text = "\n\n⚠️ **LLM 服务暂时不可用**\n\n请稍后重试。\n"
            error_delta = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [{"index": 0, "delta": {"content": error_text}}],
            }
            yield f"data: {json.dumps(error_delta, ensure_ascii=False)}\n\n"

            done_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": 10, "total_tokens": input_tokens + 10}
            }
            yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 第一次遇到 llm_unavailable，自动恢复
        logger.info(f"[OpenAI Compat] 检测到 llm_unavailable，自动恢复中... (重试次数: {retry_count})")

        # 发送恢复提示给用户
        recovery_notice = "\n\n🔄 **LLM 服务暂时不可用，正在自动重试...**\n\n"
        notice_delta = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_id,
            "choices": [{"index": 0, "delta": {"content": recovery_notice}}],
        }
        yield f"data: {json.dumps(notice_delta, ensure_ascii=False)}\n\n"

        # 修改 packet，添加继续任务提示
        import copy
        new_packet = copy.deepcopy(packet)

        if "input" in new_packet and "user_inputs" in new_packet["input"]:
            inputs = new_packet["input"]["user_inputs"].get("inputs", [])
            if inputs:
                last_input = inputs[-1]
                if "user_query" in last_input and isinstance(last_input["user_query"], dict):
                    current_query = last_input["user_query"].get("query", "")
                    if "继续任务" not in current_query and "[自动恢复]" not in current_query:
                        recovery_prompt = "\n\n[自动恢复] 继续之前的任务。"
                        last_input["user_query"]["query"] = current_query + recovery_prompt
                        logger.info("[OpenAI Compat] 已在请求中添加继续任务提示")

        # 递归调用，重试
        logger.info(f"[OpenAI Compat] 开始自动重试 llm_unavailable (retry_count={retry_count + 1})")
        async for chunk in stream_openai_sse(new_packet, completion_id, created_ts, model_id, retry_count + 1, restricted_tools):
            yield chunk

    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else repr(e)
        logger.error(f"[OpenAI Compat] stream processing failed: {error_msg}")
        logger.error(f"[OpenAI Compat] Exception type: {type(e).__name__}")
        logger.error(f"[OpenAI Compat] Traceback: {traceback.format_exc()}")
        error_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_id,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
            "error": {"message": error_msg},
        }
        try:
            logger.info("[OpenAI Compat] 转换后的 SSE(emit error): %s", json.dumps(error_chunk, ensure_ascii=False))
        except Exception:
            pass
        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n" 