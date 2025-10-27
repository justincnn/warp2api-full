"""
Anthropic SSE 流式响应转换器
将 OpenAI 流式响应转换为 Anthropic Messages API 流式格式
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Dict

from .logging import logger


def _get(obj, *keys):
    """安全获取嵌套字典的值"""
    for key in keys:
        if isinstance(obj, dict) and key in obj:
            return obj[key]
    return None






async def stream_anthropic_sse(
    openai_stream_generator: AsyncGenerator[str, None],
    anthropic_req: Dict[str, Any]
) -> AsyncGenerator[str, None]:
    """将 OpenAI SSE 流转换为 Anthropic SSE 流"""

    message_id = f"msg_{int(time.time() * 1000)}"
    model = anthropic_req.get("model", "claude-3-5-sonnet")

    # Track token usage
    input_tokens = 0
    output_tokens = 0

    # 发送 message_start 事件
    message_start = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }
        }
    }

    try:
        logger.info("[Anthropic SSE] 发送 message_start: %s", json.dumps(message_start, ensure_ascii=False))
    except Exception:
        pass

    yield f"event: message_start\ndata: {json.dumps(message_start, ensure_ascii=False)}\n\n"

    # 跟踪状态
    content_index = 0
    has_text_content = False
    has_tool_calls = False
    current_tool_call = None

    # 跟踪是否已经完成
    stream_completed = False

    try:
        async for chunk_line in openai_stream_generator:
            # 如果流已经完成，停止处理后续事件
            if stream_completed:
                try:
                    logger.debug("[Anthropic SSE] 流已完成，忽略后续事件: %s", chunk_line.strip())
                except Exception:
                    pass
                break

            if not chunk_line.strip():
                continue

            if chunk_line.startswith("data: "):
                data_str = chunk_line[6:].strip()

                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    # 如果流已经完成，忽略后续的解析错误
                    if stream_completed:
                        try:
                            logger.debug("[Anthropic SSE] 流已完成，忽略解析错误: %s", data_str)
                        except Exception:
                            pass
                    continue

                # 打印接收到的 OpenAI chunk
                try:
                    logger.info("[Anthropic SSE] 接收到 OpenAI chunk: %s", json.dumps(chunk, ensure_ascii=False))
                except Exception:
                    pass

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta", {})

                # 处理角色信息（第一个chunk）
                if delta.get("role") == "assistant" and not has_text_content and not has_tool_calls:
                    # 角色信息已经在 message_start 中发送，这里跳过
                    continue

                # 处理文本内容
                if "content" in delta and delta["content"]:
                    text_content = delta["content"]

                    if not has_text_content:
                        # 发送 content_block_start 事件
                        content_block_start = {
                            "type": "content_block_start",
                            "index": content_index,
                            "content_block": {
                                "type": "text",
                                "text": ""
                            }
                        }

                        try:
                            logger.info("[Anthropic SSE] 发送 content_block_start: %s", json.dumps(content_block_start, ensure_ascii=False))
                        except Exception:
                            pass

                        yield f"event: content_block_start\ndata: {json.dumps(content_block_start, ensure_ascii=False)}\n\n"
                        has_text_content = True

                    # 发送 content_block_delta 事件
                    content_block_delta = {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": {
                            "type": "text_delta",
                            "text": text_content
                        }
                    }

                    try:
                        logger.info("[Anthropic SSE] 发送 content_block_delta: %s", json.dumps(content_block_delta, ensure_ascii=False))
                    except Exception:
                        pass

                    yield f"event: content_block_delta\ndata: {json.dumps(content_block_delta, ensure_ascii=False)}\n\n"

                # 处理工具调用
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tool_call in delta["tool_calls"]:
                        # 如果是新的工具调用
                        if tool_call.get("id") and tool_call.get("function", {}).get("name"):
                            tool_name = tool_call["function"]["name"]

                            # 工具调用已经由 OpenAI SSE 转换器正确生成，直接使用
                            # 支持 TodoWrite, TaskStatusUpdate 等所有工具调用
                            logger.info("[Anthropic SSE] 处理工具调用: %s", tool_name)

                            # 关闭之前的文本内容块
                            if has_text_content:
                                content_block_stop = {
                                    "type": "content_block_stop",
                                    "index": content_index
                                }
                                yield f"event: content_block_stop\ndata: {json.dumps(content_block_stop, ensure_ascii=False)}\n\n"
                                content_index += 1
                                has_text_content = False

                            # 如果已经有工具调用，需要递增 index
                            if has_tool_calls:
                                content_index += 1
                            # 开始新的工具调用块
                            current_tool_call = {
                                "id": tool_call["id"],
                                "name": tool_name,
                                "arguments": ""
                            }

                            content_block_start = {
                                "type": "content_block_start",
                                "index": content_index,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": current_tool_call["id"],
                                    "name": current_tool_call["name"],
                                    "input": {}
                                }
                            }

                            try:
                                logger.info("[Anthropic SSE] 发送 tool_use content_block_start: %s", json.dumps(content_block_start, ensure_ascii=False))
                            except Exception:
                                pass

                            yield f"event: content_block_start\ndata: {json.dumps(content_block_start, ensure_ascii=False)}\n\n"
                            has_tool_calls = True

                        # 处理工具参数
                        if current_tool_call and tool_call.get("function", {}).get("arguments"):
                            arguments_chunk = tool_call["function"]["arguments"]
                            current_tool_call["arguments"] += arguments_chunk

                            # 工具参数已经由 OpenAI SSE 转换器正确格式化，直接使用
                            # 包括 TodoWrite 的 todos 参数和 TaskStatusUpdate 的状态参数

                            content_block_delta = {
                                "type": "content_block_delta",
                                "index": content_index,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": arguments_chunk
                                }
                            }

                            try:
                                logger.info("[Anthropic SSE] 发送 tool_use content_block_delta: %s", json.dumps(content_block_delta, ensure_ascii=False))
                            except Exception:
                                pass

                            yield f"event: content_block_delta\ndata: {json.dumps(content_block_delta, ensure_ascii=False)}\n\n"

                # 处理结束
                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    # Extract usage from the OpenAI chunk if present
                    if "usage" in chunk:
                        usage_data = chunk.get("usage", {})
                        input_tokens = usage_data.get("prompt_tokens", 0)
                        # 直接使用 OpenAI 提供的 completion_tokens
                        output_tokens = usage_data.get("completion_tokens", 0)
                    # 关闭当前内容块
                    if has_text_content or has_tool_calls:
                        content_block_stop = {
                            "type": "content_block_stop",
                            "index": content_index
                        }

                        try:
                            logger.info("[Anthropic SSE] 发送 content_block_stop: %s", json.dumps(content_block_stop, ensure_ascii=False))
                        except Exception:
                            pass

                        yield f"event: content_block_stop\ndata: {json.dumps(content_block_stop, ensure_ascii=False)}\n\n"
                        current_tool_call = None

                    # 映射 finish_reason
                    stop_reason_mapping = {
                        "stop": "end_turn",
                        "length": "max_tokens",
                        "tool_calls": "tool_use",
                        "content_filter": "stop_sequence"
                    }
                    stop_reason = stop_reason_mapping.get(finish_reason, "end_turn")

                    # 发送 message_delta 事件
                    message_delta = {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": stop_reason,
                            "stop_sequence": None
                        },
                        "usage": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens
                        }
                    }

                    try:
                        logger.info("[Anthropic SSE] 发送 message_delta: %s", json.dumps(message_delta, ensure_ascii=False))
                    except Exception:
                        pass

                    yield f"event: message_delta\ndata: {json.dumps(message_delta, ensure_ascii=False)}\n\n"

                    # 发送 message_stop 事件
                    message_stop = {
                        "type": "message_stop"
                    }

                    try:
                        logger.info("[Anthropic SSE] 发送 message_stop: %s", json.dumps(message_stop, ensure_ascii=False))
                    except Exception:
                        pass

                    yield f"event: message_stop\ndata: {json.dumps(message_stop, ensure_ascii=False)}\n\n"
                    stream_completed = True
                    break

    except Exception as e:
        logger.error(f"[Anthropic SSE] Stream processing failed: {e}")

        # 发送错误事件
        error_event = {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": str(e)
            }
        }

        try:
            logger.info("[Anthropic SSE] 发送 error: %s", json.dumps(error_event, ensure_ascii=False))
        except Exception:
            pass

        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"