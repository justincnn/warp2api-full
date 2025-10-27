"""
Anthropic ↔ OpenAI 格式转换器
基于 @llms 库的转换逻辑实现
"""
from __future__ import annotations

import json
import uuid
import logging
from typing import Any, Dict, List, Union

from .models import (
    AnthropicMessagesRequest,
    ChatCompletionsRequest,
    ChatMessage,
    OpenAITool,
    OpenAIFunctionDef
)

logger = logging.getLogger(__name__)


def map_model_name(model: str) -> str:
    """Map unsupported model names to claude-4-sonnet.

    Only claude-4.1-opus is kept as-is, all others map to claude-4-sonnet.
    This handles cases like claude-3-5-haiku-20241022 and other variants.
    """
    return model

    if model and model.lower() == "claude-4.1-opus":
        return model
    # Map all other models to claude-4-sonnet
    return "claude-4-sonnet"


def anthropic_to_openai(anthropic_req: AnthropicMessagesRequest) -> ChatCompletionsRequest:
    """将 Anthropic Messages 请求转换为 OpenAI Chat Completions 请求"""

    logger.info(f"[Anthropic Converter] 开始转换 Anthropic 请求，包含 {len(anthropic_req.messages)} 条消息")

    # 统计 tool_use 和 tool_result
    tool_uses = []
    tool_results = []
    for msg in anthropic_req.messages:
        if isinstance(msg.content, list):
            for content_block in msg.content:
                if isinstance(content_block, dict):
                    if content_block.get("type") == "tool_use":
                        tool_uses.append(content_block.get("id", "unknown"))
                    elif content_block.get("type") == "tool_result":
                        tool_results.append(content_block.get("tool_use_id", "unknown"))

    logger.info(f"[Anthropic Converter] 检测到 {len(tool_uses)} 个 tool_use: {tool_uses}")
    logger.info(f"[Anthropic Converter] 检测到 {len(tool_results)} 个 tool_result: {tool_results}")

    # 检查未匹配的 tool_use
    unmatched_tool_uses = [tu for tu in tool_uses if tu not in tool_results]
    if unmatched_tool_uses:
        logger.warning(f"[Anthropic Converter] ⚠️ 发现 {len(unmatched_tool_uses)} 个未匹配的 tool_use: {unmatched_tool_uses}")

    openai_messages: List[ChatMessage] = []

    # 处理 system message
    if anthropic_req.system:
        if isinstance(anthropic_req.system, str):
            openai_messages.append(ChatMessage(
                role="system",
                content=anthropic_req.system
            ))
        elif isinstance(anthropic_req.system, list):
            # 处理复杂的 system 格式
            text_parts = []
            for item in anthropic_req.system:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    text_parts.append(item["text"])
            if text_parts:
                openai_messages.append(ChatMessage(
                    role="system",
                    content="\n\n".join(text_parts)
                ))

    # 转换消息
    for msg in anthropic_req.messages:
        if msg.role in ["user", "assistant"]:
            if isinstance(msg.content, str):
                openai_messages.append(ChatMessage(
                    role=msg.role,
                    content=msg.content
                ))
            elif isinstance(msg.content, list):
                if msg.role == "user":
                    # 处理 tool_result -> tool message
                    tool_parts = [c for c in msg.content if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id")]
                    logger.info(f"[Anthropic Converter] 在 user 消息中找到 {len(tool_parts)} 个 tool_result")

                    for tool in tool_parts:
                        tool_use_id = tool.get("tool_use_id")
                        logger.info(f"[Anthropic Converter] 处理 tool_result: {tool_use_id}")

                        content = tool.get("content", "")
                        if isinstance(content, list):
                            # 如果 content 是列表，提取文本部分
                            text_parts = []
                            for item in content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    text_parts.append(item)
                            content = "\n".join(text_parts) if text_parts else str(content)
                        elif not isinstance(content, str):
                            content = json.dumps(content, ensure_ascii=False)

                        openai_messages.append(ChatMessage(
                            role="tool",
                            content=content,
                            tool_call_id=tool_use_id
                        ))
                        logger.info(f"[Anthropic Converter] 已添加 tool message: {tool_use_id}")

                    # 处理文本和媒体内容
                    text_and_media_parts = [c for c in msg.content if isinstance(c, dict) and c.get("type") in ["text", "image"]]
                    if text_and_media_parts:
                        content_list = []
                        for part in text_and_media_parts:
                            if part.get("type") == "image" and part.get("source"):
                                # 转换图片格式
                                source = part["source"]
                                if source.get("type") == "base64":
                                    url = f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}"
                                else:
                                    url = source.get("url", "")
                                content_list.append({
                                    "type": "image_url",
                                    "image_url": {"url": url}
                                })
                            elif part.get("type") == "text" and part.get("text"):
                                content_list.append({
                                    "type": "text",
                                    "text": part["text"]
                                })

                        if content_list:
                            if len(content_list) == 1 and content_list[0].get("type") == "text":
                                # 如果只有一个文本部分，直接用字符串
                                openai_messages.append(ChatMessage(
                                    role="user",
                                    content=content_list[0]["text"]
                                ))
                            else:
                                openai_messages.append(ChatMessage(
                                    role="user",
                                    content=content_list
                                ))

                elif msg.role == "assistant":
                    # 处理 assistant 消息
                    text_parts = [c for c in msg.content if isinstance(c, dict) and c.get("type") == "text" and c.get("text")]
                    tool_call_parts = [c for c in msg.content if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("id")]

                    logger.info(f"[Anthropic Converter] 在 assistant 消息中找到 {len(text_parts)} 个 text 块, {len(tool_call_parts)} 个 tool_use")

                    content = ""
                    if text_parts:
                        content = "\n".join([part["text"] for part in text_parts])

                    tool_calls = []
                    if tool_call_parts:
                        for tool in tool_call_parts:
                            tool_id = tool.get("id")
                            tool_name = tool.get("name")
                            logger.info(f"[Anthropic Converter] 处理 tool_use: {tool_id} ({tool_name})")

                            tool_calls.append({
                                "id": tool_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(tool.get("input", {}), ensure_ascii=False)
                                }
                            })

                    openai_messages.append(ChatMessage(
                        role="assistant",
                        content=content,
                        tool_calls=tool_calls if tool_calls else None
                    ))
                    logger.info(f"[Anthropic Converter] 已添加 assistant 消息，包含 {len(tool_calls)} 个 tool_calls")

    # 转换工具
    openai_tools = None
    if anthropic_req.tools:
        openai_tools = []
        for tool in anthropic_req.tools:
            openai_tools.append(OpenAITool(
                type="function",
                function=OpenAIFunctionDef(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=tool.input_schema or {}
                )
            ))

    # 转换 tool_choice
    openai_tool_choice = None
    if anthropic_req.tool_choice:
        if isinstance(anthropic_req.tool_choice, dict):
            if anthropic_req.tool_choice.get("type") == "tool":
                openai_tool_choice = {
                    "type": "function",
                    "function": {"name": anthropic_req.tool_choice.get("name")}
                }
            else:
                openai_tool_choice = anthropic_req.tool_choice.get("type")
        else:
            openai_tool_choice = anthropic_req.tool_choice

    return ChatCompletionsRequest(
        model=map_model_name(anthropic_req.model),
        messages=openai_messages,
        stream=anthropic_req.stream,
        tools=openai_tools,
        tool_choice=openai_tool_choice
    )


def openai_to_anthropic_response(openai_response: Dict[str, Any], is_stream: bool = False) -> Dict[str, Any]:
    """将 OpenAI Chat Completions 响应转换为 Anthropic Messages 响应"""

    if is_stream:
        # 流式响应转换在 SSE transform 中处理
        return openai_response

    # 非流式响应转换
    choice = openai_response.get("choices", [{}])[0]
    message = choice.get("message", {})

    content = []

    # 处理文本内容
    if message.get("content"):
        content.append({
            "type": "text",
            "text": message["content"]
        })

    # 处理工具调用
    if message.get("tool_calls"):
        for tool_call in message["tool_calls"]:
            try:
                input_data = json.loads(tool_call["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                input_data = {}

            content.append({
                "type": "tool_use",
                "id": tool_call.get("id"),
                "name": tool_call["function"]["name"],
                "input": input_data
            })

    # 映射 finish_reason
    stop_reason_mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence"
    }
    stop_reason = stop_reason_mapping.get(choice.get("finish_reason"), "end_turn")

    return {
        "id": openai_response.get("id"),
        "type": "message",
        "role": "assistant",
        "model": openai_response.get("model"),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0,  # 这些值需要从实际响应中获取
            "output_tokens": 0,
        }
    }