"""Token 计数工具模块
使用 tiktoken 库提供准确的 token 计数
"""

import tiktoken
from typing import Dict, List, Any, Optional, Union
import json
from .logging import logger

# 缓存编码器实例
_encoders_cache = {}

# Claude 模型到 tiktoken 编码器的映射
# Claude 使用类似 GPT-4 的 tokenizer
MODEL_TO_ENCODING = {
    # Claude 3.5 系列
    "claude-3-5-sonnet": "cl100k_base",
    "claude-3-5-sonnet-20241022": "cl100k_base",
    "claude-3-5-haiku": "cl100k_base",
    "claude-3-5-opus": "cl100k_base",

    # Claude 3 系列
    "claude-3-sonnet": "cl100k_base",
    "claude-3-haiku": "cl100k_base",
    "claude-3-opus": "cl100k_base",

    # Claude 4 系列
    "claude-4": "cl100k_base",
    "claude-4.1-opus": "cl100k_base",

    # 默认使用 cl100k_base（GPT-4 的编码器）
    "default": "cl100k_base"
}


def get_encoder(model: str = "default") -> tiktoken.Encoding:
    """获取指定模型的编码器

    Args:
        model: 模型名称

    Returns:
        tiktoken 编码器实例
    """
    # 从缓存获取或创建新的编码器
    if model not in _encoders_cache:
        encoding_name = MODEL_TO_ENCODING.get(model, MODEL_TO_ENCODING["default"])
        try:
            _encoders_cache[model] = tiktoken.get_encoding(encoding_name)
            logger.info(f"[TokenCounter] 为模型 {model} 创建编码器 {encoding_name}")
        except Exception as e:
            logger.warning(f"[TokenCounter] 获取编码器失败: {e}，使用默认编码器")
            _encoders_cache[model] = tiktoken.get_encoding("cl100k_base")

    return _encoders_cache[model]


def count_tokens(text: str, model: str = "default") -> int:
    """计算文本的 token 数量

    Args:
        text: 要计算的文本
        model: 使用的模型

    Returns:
        token 数量
    """
    if not text:
        return 0

    try:
        encoder = get_encoder(model)
        tokens = encoder.encode(text)
        return len(tokens)
    except Exception as e:
        logger.error(f"[TokenCounter] 计算 token 失败: {e}")
        # 回退到简单估算
        return estimate_tokens_fallback(text)


def count_messages_tokens(messages: List[Dict[str, Any]], model: str = "default") -> int:
    """计算消息列表的 token 数量

    包括消息格式的额外开销（role、分隔符等）

    Args:
        messages: 消息列表
        model: 使用的模型

    Returns:
        总 token 数量
    """
    encoder = get_encoder(model)
    total_tokens = 0

    # 每条消息的格式开销（role、分隔符等）
    # 对于 Claude 和 GPT-4，每条消息大约有 3-4 个 token 的开销
    message_overhead = 4

    for message in messages:
        # 计算 role 的 tokens
        role = message.get("role", "")
        if role:
            total_tokens += len(encoder.encode(role))

        # 计算 content 的 tokens
        content = message.get("content")
        if content:
            if isinstance(content, str):
                total_tokens += len(encoder.encode(content))
            elif isinstance(content, list):
                # 处理多部分内容（如图片+文本）
                for part in content:
                    if isinstance(part, dict):
                        if "text" in part:
                            total_tokens += len(encoder.encode(part["text"]))
                        # 图片等其他内容类型的 token 估算
                        if "image" in part or "image_url" in part:
                            # 图片通常消耗较多 tokens，这里估算 500
                            total_tokens += 500

        # 添加消息格式开销
        total_tokens += message_overhead

    return total_tokens


def count_tools_tokens(tools: List[Dict[str, Any]], model: str = "default") -> int:
    """计算工具定义的 token 数量

    Args:
        tools: 工具定义列表
        model: 使用的模型

    Returns:
        工具定义的 token 数量
    """
    if not tools:
        return 0

    # 将工具定义序列化为 JSON 字符串
    try:
        tools_json = json.dumps(tools, ensure_ascii=False)
        return count_tokens(tools_json, model)
    except Exception as e:
        logger.error(f"[TokenCounter] 计算工具 token 失败: {e}")
        return 0


def count_packet_tokens(packet: Dict[str, Any], model: str = "default") -> int:
    """计算整个请求包的 token 数量

    这是 estimate_input_tokens 的替代实现，使用 tiktoken 提供准确计数

    Args:
        packet: 请求数据包
        model: 使用的模型

    Returns:
        总 token 数量
    """
    encoder = get_encoder(model)
    total_tokens = 0

    # 1. 计算 user_inputs 中的文本
    if "input" in packet and "user_inputs" in packet["input"]:
        inputs = packet["input"]["user_inputs"].get("inputs", [])
        for inp in inputs:
            if isinstance(inp, dict):
                # 处理 text 字段
                if "text" in inp:
                    total_tokens += count_tokens(str(inp["text"]), model)

                # 处理 attachments
                if "attachments" in inp:
                    for attachment in inp["attachments"]:
                        if isinstance(attachment, dict) and "text" in attachment:
                            total_tokens += count_tokens(str(attachment["text"]), model)

                # 处理 user_query
                if "user_query" in inp:
                    user_query = inp["user_query"]
                    if isinstance(user_query, dict):
                        # 查询文本
                        if "query" in user_query:
                            total_tokens += count_tokens(str(user_query["query"]), model)

                        # referenced_attachments（系统提示词等）
                        if "referenced_attachments" in user_query:
                            refs = user_query["referenced_attachments"]
                            if isinstance(refs, dict):
                                for key, ref in refs.items():
                                    if isinstance(ref, dict):
                                        if "plain_text" in ref:
                                            total_tokens += count_tokens(str(ref["plain_text"]), model)
                                        if "text" in ref:
                                            total_tokens += count_tokens(str(ref["text"]), model)

    # 2. 计算 task_context 中的历史消息
    if "task_context" in packet:
        messages = []

        # 获取消息列表
        if "messages" in packet["task_context"]:
            messages = packet["task_context"]["messages"]
        elif "tasks" in packet["task_context"] and packet["task_context"]["tasks"]:
            for task in packet["task_context"]["tasks"]:
                if isinstance(task, dict) and "messages" in task:
                    messages.extend(task["messages"])

        # 计算每条消息的 tokens
        for msg in messages:
            if isinstance(msg, dict):
                # agent_output
                if "agent_output" in msg:
                    output = msg["agent_output"]
                    if isinstance(output, dict) and "text" in output:
                        total_tokens += count_tokens(str(output["text"]), model)

                # user_input
                if "user_input" in msg:
                    user_input = msg["user_input"]
                    if isinstance(user_input, dict) and "text" in user_input:
                        total_tokens += count_tokens(str(user_input["text"]), model)

                # tool_call 和 tool_result
                if "tool_call" in msg:
                    tool_call = msg["tool_call"]
                    if isinstance(tool_call, dict):
                        # 计算工具调用的 tokens
                        tool_json = json.dumps(tool_call, ensure_ascii=False)
                        total_tokens += count_tokens(tool_json, model)

                if "tool_result" in msg:
                    tool_result = msg["tool_result"]
                    if isinstance(tool_result, dict):
                        # 计算工具结果的 tokens
                        result_json = json.dumps(tool_result, ensure_ascii=False)
                        total_tokens += count_tokens(result_json, model)

    # 3. 计算工具定义
    if "mcp_context" in packet and "tools" in packet["mcp_context"]:
        tools = packet["mcp_context"]["tools"]
        total_tokens += count_tools_tokens(tools, model)

    # 添加一些格式开销（JSON 结构、分隔符等）
    total_tokens += 10

    return total_tokens


def estimate_tokens_fallback(text: str) -> int:
    """后备的简单 token 估算方法

    当 tiktoken 不可用时使用

    Args:
        text: 要估算的文本

    Returns:
        估算的 token 数量
    """
    if not text:
        return 0

    # 统计中文和英文字符
    chinese_chars = 0
    english_chars = 0

    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            # 中文字符
            chinese_chars += 1
        elif char.isalpha() or char.isspace() or char.isdigit():
            # 英文字符
            english_chars += 1
        else:
            # 其他字符（标点等）
            english_chars += 1

    # 估算 tokens
    # 中文：约 2 个字符一个 token
    # 英文：约 4 个字符一个 token
    chinese_tokens = chinese_chars / 2
    english_tokens = english_chars / 4

    return max(int(chinese_tokens + english_tokens), 1)


def estimate_output_tokens(text: str, model: str = "default") -> int:
    """估算输出文本的 token 数量

    用于实时流式响应中的 token 计数

    Args:
        text: 输出的文本
        model: 使用的模型

    Returns:
        token 数量
    """
    return count_tokens(text, model)