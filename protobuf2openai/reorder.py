from __future__ import annotations

import logging
from typing import Dict, List, Optional
from .models import ChatMessage
from .helpers import normalize_content_to_list, segments_to_text

logger = logging.getLogger(__name__)


def reorder_messages_for_anthropic(history: List[ChatMessage]) -> List[ChatMessage]:
    if not history:
        return []

    expanded: List[ChatMessage] = []
    for m in history:
        if m.role == "user":
            items = normalize_content_to_list(m.content)
            if isinstance(m.content, list) and len(items) > 1:
                for seg in items:
                    if isinstance(seg, dict) and seg.get("type") == "text" and isinstance(seg.get("text"), str):
                        expanded.append(ChatMessage(role="user", content=seg.get("text")))
                    else:
                        expanded.append(ChatMessage(role="user", content=[seg] if isinstance(seg, dict) else seg))
            else:
                expanded.append(m)
        elif m.role == "assistant" and m.tool_calls and len(m.tool_calls) > 1:
            _assistant_text = segments_to_text(normalize_content_to_list(m.content))
            if _assistant_text:
                expanded.append(ChatMessage(role="assistant", content=_assistant_text))
            for tc in (m.tool_calls or []):
                expanded.append(ChatMessage(role="assistant", content="", tool_calls=[tc]))
        else:
            expanded.append(m)

    last_input_tool_id: Optional[str] = None
    last_input_is_tool = False
    for m in reversed(expanded):
        if m.role == "tool" and m.tool_call_id:
            last_input_tool_id = m.tool_call_id
            last_input_is_tool = True
            break
        if m.role == "user":
            break

    tool_results_by_id: Dict[str, ChatMessage] = {}
    assistant_tc_ids: set[str] = set()
    for m in expanded:
        if m.role == "tool" and m.tool_call_id and m.tool_call_id not in tool_results_by_id:
            tool_results_by_id[m.tool_call_id] = m
        if m.role == "assistant" and m.tool_calls:
            try:
                for tc in (m.tool_calls or []):
                    _id = (tc or {}).get("id")
                    if isinstance(_id, str) and _id:
                        assistant_tc_ids.add(_id)
            except Exception:
                pass

    result: List[ChatMessage] = []
    trailing_assistant_msg: Optional[ChatMessage] = None
    for m in expanded:
        if m.role == "tool":
            # Preserve unmatched tool results inline
            if not m.tool_call_id or m.tool_call_id not in assistant_tc_ids:
                result.append(m)
                if m.tool_call_id:
                    tool_results_by_id.pop(m.tool_call_id, None)
            continue
        if m.role == "assistant" and m.tool_calls:
            ids: List[str] = []
            try:
                for tc in (m.tool_calls or []):
                    _id = (tc or {}).get("id")
                    if isinstance(_id, str) and _id:
                        ids.append(_id)
            except Exception:
                pass

            if last_input_is_tool and last_input_tool_id and (last_input_tool_id in ids):
                if trailing_assistant_msg is None:
                    trailing_assistant_msg = m
                continue

            result.append(m)
            for _id in ids:
                tr = tool_results_by_id.pop(_id, None)
                if tr is not None:
                    result.append(tr)
            continue
        result.append(m)

    if last_input_is_tool and last_input_tool_id and trailing_assistant_msg is not None:
        result.append(trailing_assistant_msg)
        tr = tool_results_by_id.pop(last_input_tool_id, None)
        if tr is not None:
            result.append(tr)

    return result


def clean_incomplete_tool_calls(messages: List[ChatMessage]) -> List[ChatMessage]:
    """
    修复工具调用中断导致的消息序列问题。

    参考 cb2api 的 fix_tool_call_sequence 实现。

    发生场景：用户打断了工具调用请求，导致消息历史中 tool_use 和 tool_result 之间插入了其他消息。
    Anthropic API 要求每个 tool_use 必须在**下一条消息**中有对应的 tool_result，否则返回 400 错误。

    处理策略：
    1. 当遇到 assistant 的 tool_calls 消息时，收集期望的工具调用 ID
    2. 查找后续的 tool_result 消息和插入的其他消息
    3. 重新排序：tool_calls -> tool_results -> 插入的消息
    4. 保持所有消息不丢失，只调整顺序
    5. 如果 tool_result 的 content 为空，成对删除对应的 tool_use 和 tool_result

    Args:
        messages: 原始消息列表

    Returns:
        修复后的消息列表
    """
    if not messages:
        return messages

    logger.info(f"[Clean Tool Calls] 开始清理不完整工具调用，输入消息数量: {len(messages)}")

    # 统计输入的 tool_calls 和 tool 消息
    input_tool_calls_count = 0
    input_tool_results_count = 0
    tool_call_ids = []
    tool_result_ids = []

    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc and tc.get("id"):
                    input_tool_calls_count += 1
                    tool_call_ids.append(tc.get("id"))
        elif msg.role == "tool" and msg.tool_call_id:
            input_tool_results_count += 1
            tool_result_ids.append(msg.tool_call_id)

    logger.info(f"[Clean Tool Calls] 输入统计: {input_tool_calls_count} 个 tool_calls, {input_tool_results_count} 个 tool_results")
    logger.info(f"[Clean Tool Calls] tool_call_ids: {tool_call_ids}")
    logger.info(f"[Clean Tool Calls] tool_result_ids: {tool_result_ids}")

    # 检查未匹配的 tool_calls
    unmatched_tool_calls = [tc_id for tc_id in tool_call_ids if tc_id not in tool_result_ids]
    if unmatched_tool_calls:
        logger.warning(f"[Clean Tool Calls] ⚠️ 发现 {len(unmatched_tool_calls)} 个未匹配的 tool_calls: {unmatched_tool_calls}")

    # 第一轮扫描：找出所有 content 为空的 tool_result (用于日志记录)
    empty_content_count = 0
    for msg in messages:
        if msg.role == "tool" and msg.tool_call_id:
            # 检查 content 是否为空
            if not msg.content or (isinstance(msg.content, str) and not msg.content.strip()):
                empty_content_count += 1
                logger.info(f"[Clean Tool Calls] 发现 tool_result content 为空: {msg.tool_call_id}，将填充为 'No content'")

    if empty_content_count > 0:
        logger.info(f"[Clean Tool Calls] 共发现 {empty_content_count} 个空 content 的 tool_result，将全部填充为 'No content'")

    fixed_messages: List[ChatMessage] = []
    i = 0

    while i < len(messages):
        current_msg = messages[i]

        # 检查是否是assistant的tool_calls消息
        if (current_msg.role == "assistant" and current_msg.tool_calls):

            # 收集所有工具调用ID
            expected_tool_ids = {tc.get("id") for tc in current_msg.tool_calls if tc and tc.get("id")}

            # 添加tool_calls消息（先暂存，后面可能会修改）
            fixed_messages.append(current_msg)
            i += 1

            # 查找对应的tool_result消息和插入的消息
            tool_results = []
            found_tool_ids = set()
            interrupted_messages = []
            # empty_tool_ids 不再需要，因为我们会填充空的content

            # 收集后续消息直到找到所有tool_result或遇到新的assistant消息
            while i < len(messages):
                next_msg = messages[i]

                # 如果是tool_result消息
                if next_msg.role == "tool" and next_msg.tool_call_id:
                    tool_call_id = next_msg.tool_call_id

                    # 验证tool_call_id是否匹配
                    if tool_call_id in expected_tool_ids:
                        # 检查content是否为空，如果为空则填充 "No content"
                        if not next_msg.content or (isinstance(next_msg.content, str) and not next_msg.content.strip()):
                            logger.warning(f"[Clean Tool Calls] 发现content为空的tool_result: {tool_call_id}，填充为 'No content'")
                            # 创建一个新的消息，填充 "No content"
                            filled_msg = ChatMessage(
                                role=next_msg.role,
                                content="No content",
                                tool_call_id=next_msg.tool_call_id
                            )
                            tool_results.append(filled_msg)
                            found_tool_ids.add(tool_call_id)
                        else:
                            # content不为空，正常添加
                            tool_results.append(next_msg)
                            found_tool_ids.add(tool_call_id)
                    else:
                        # 不匹配的tool_result，仍然添加但记录
                        tool_results.append(next_msg)

                    i += 1

                # 如果是用户消息或其他消息，暂存（无论是中断还是其他原因）
                elif next_msg.role in ["user", "system"]:
                    interrupted_messages.append(next_msg)
                    i += 1

                # 如果遇到新的assistant消息，停止收集
                elif next_msg.role == "assistant":
                    break
                else:
                    # 其他类型消息，停止收集
                    break

            # 检查是否有缺失的工具调用结果
            missing_tools = expected_tool_ids - found_tool_ids
            invalid_tools = missing_tools  # 现在只处理缺失的，不处理空的（因为已经填充）

            if invalid_tools:
                # 如果有缺失或content为空的工具调用结果，移除对应的工具调用
                valid_tool_calls = []
                for tc in current_msg.tool_calls:
                    if tc and tc.get("id") and tc.get("id") not in invalid_tools:
                        valid_tool_calls.append(tc)

                # 更新最后添加的assistant消息
                if valid_tool_calls:
                    # 更新工具调用列表
                    updated_msg = ChatMessage(
                        role=current_msg.role,
                        content=current_msg.content,
                        tool_calls=valid_tool_calls
                    )
                    fixed_messages[-1] = updated_msg
                elif current_msg.content:
                    # 如果没有有效工具调用但有内容，移除工具调用
                    updated_msg = ChatMessage(
                        role=current_msg.role,
                        content=current_msg.content,
                        tool_calls=None
                    )
                    fixed_messages[-1] = updated_msg
                else:
                    # 如果既没有内容也没有有效工具调用，移除消息
                    fixed_messages.pop()

                # 只保留有对应工具调用的tool_result（且content不为空的）
                tool_results = [tr for tr in tool_results if tr.tool_call_id in found_tool_ids]

            # 按正确顺序添加消息：tool_results -> interrupted_messages
            fixed_messages.extend(tool_results)
            fixed_messages.extend(interrupted_messages)

        elif current_msg.role == "tool":
            # 检查这个 tool_result 的 content 是否为空
            if not current_msg.content or (isinstance(current_msg.content, str) and not current_msg.content.strip()):
                # content为空，填充为 "No content"
                logger.warning(f"[Clean Tool Calls] 发现独立的content为空的tool_result: {current_msg.tool_call_id}，填充为 'No content'")
                filled_msg = ChatMessage(
                    role=current_msg.role,
                    content="No content",
                    tool_call_id=current_msg.tool_call_id
                )
                # 检查这个 tool_result 是否有对应的 tool_use 在前面
                has_matching_tool_use = False
                for prev_msg in reversed(fixed_messages):
                    if prev_msg.role == "assistant" and prev_msg.tool_calls:
                        if any(tc and tc.get("id") == current_msg.tool_call_id for tc in prev_msg.tool_calls):
                            has_matching_tool_use = True
                            break
                    elif prev_msg.role == "assistant":
                        # 遇到没有工具调用的assistant消息，停止查找
                        break

                if has_matching_tool_use:
                    # 有对应的 tool_use，添加填充后的 tool_result
                    fixed_messages.append(filled_msg)
                # 否则跳过孤立的 tool_result
            else:
                # content不为空，检查是否有对应的 tool_use
                has_matching_tool_use = False
                for prev_msg in reversed(fixed_messages):
                    if prev_msg.role == "assistant" and prev_msg.tool_calls:
                        if any(tc and tc.get("id") == current_msg.tool_call_id for tc in prev_msg.tool_calls):
                            has_matching_tool_use = True
                            break
                    elif prev_msg.role == "assistant":
                        # 遇到没有工具调用的assistant消息，停止查找
                        break

                if has_matching_tool_use:
                    # 有对应的 tool_use，保留这个 tool_result
                    fixed_messages.append(current_msg)
                # 否则跳过孤立的 tool_result

            i += 1

        else:
            # 普通消息，直接添加
            fixed_messages.append(current_msg)
            i += 1

    # 统计输出结果
    output_tool_calls_count = 0
    output_tool_results_count = 0
    output_tool_call_ids = []
    output_tool_result_ids = []

    for msg in fixed_messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc and tc.get("id"):
                    output_tool_calls_count += 1
                    output_tool_call_ids.append(tc.get("id"))
        elif msg.role == "tool" and msg.tool_call_id:
            output_tool_results_count += 1
            output_tool_result_ids.append(msg.tool_call_id)

    logger.info(f"[Clean Tool Calls] 输出统计: {output_tool_calls_count} 个 tool_calls, {output_tool_results_count} 个 tool_results")
    logger.info(f"[Clean Tool Calls] 输出 tool_call_ids: {output_tool_call_ids}")
    logger.info(f"[Clean Tool Calls] 输出 tool_result_ids: {output_tool_result_ids}")

    # 检查是否有工具调用被移除
    removed_tool_calls = [tc for tc in tool_call_ids if tc not in output_tool_call_ids]
    removed_tool_results = [tr for tr in tool_result_ids if tr not in output_tool_result_ids]

    if removed_tool_calls:
        logger.warning(f"[Clean Tool Calls] ⚠️ 已移除 {len(removed_tool_calls)} 个 tool_calls: {removed_tool_calls}")
    if removed_tool_results:
        logger.warning(f"[Clean Tool Calls] ⚠️ 已移除 {len(removed_tool_results)} 个 tool_results: {removed_tool_results}")

    logger.info(f"[Clean Tool Calls] 清理完成，输出消息数量: {len(fixed_messages)}")

    return fixed_messages 