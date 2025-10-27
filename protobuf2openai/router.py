from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .logging import logger

from .models import ChatCompletionsRequest, ChatMessage, AnthropicMessagesRequest
from .reorder import reorder_messages_for_anthropic, clean_incomplete_tool_calls
from .helpers import normalize_content_to_list, segments_to_text
from .packets import packet_template, map_history_to_warp_messages, attach_user_and_tools_to_inputs, map_model_name
from .state import STATE
from .config import BRIDGE_BASE_URL
from .bridge import initialize_once
from .sse_transform import stream_openai_sse


router = APIRouter()


@router.get("/")
def root():
    return {"service": "OpenAI Chat Completions (Warp bridge) - Streaming", "status": "ok"}


@router.get("/healthz")
def health_check():
    return {"status": "ok", "service": "OpenAI Chat Completions (Warp bridge) - Streaming"}


@router.get("/v1/models")
def list_models():
    """OpenAI-compatible model listing. Forwards to bridge, with local fallback."""
    try:
        resp = requests.get(f"{BRIDGE_BASE_URL}/v1/models", timeout=10.0)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"bridge_error: {resp.text}")
        return resp.json()
    except Exception as e:
        try:
            # Local fallback: construct models directly if bridge is unreachable
            from warp2protobuf.config.models import get_all_unique_models  # type: ignore
            models = get_all_unique_models()
            return {"object": "list", "data": models}
        except Exception:
            raise HTTPException(502, f"bridge_unreachable: {e}")


@router.post("/v1/messages")
async def anthropic_messages(req: AnthropicMessagesRequest):
    """Anthropic Messages API 兼容接口 - 接收 Anthropic 格式，转换为 OpenAI 后处理，再转回 Anthropic 格式"""
    from .anthropic_converter import anthropic_to_openai, openai_to_anthropic_response
    from .anthropic_sse_transform import stream_anthropic_sse

    try:
        initialize_once()
    except Exception as e:
        logger.warning(f"[Anthropic Compat] initialize_once failed or skipped: {e}")

    if not req.messages:
        raise HTTPException(400, "messages 不能为空")

    # 1) 打印接收到的 Anthropic Messages 原始请求体
    try:
        logger.info("[Anthropic Compat] 接收到的 Anthropic Messages 请求体(原始): %s", json.dumps(req.dict(), ensure_ascii=False))
    except Exception:
        logger.info("[Anthropic Compat] 接收到的 Anthropic Messages 请求体(原始) 序列化失败")

    # 2) 转换为 OpenAI 格式
    try:
        openai_req = anthropic_to_openai(req)
        logger.info("[Anthropic Compat] 转换为 OpenAI 格式: %s", json.dumps(openai_req.dict(), ensure_ascii=False))
    except Exception as e:
        logger.error(f"[Anthropic Compat] Anthropic to OpenAI conversion failed: {e}")
        raise HTTPException(400, f"格式转换失败: {e}")

    # 3) 使用相同的处理逻辑 (复用 chat_completions 的核心逻辑)
    history: List[ChatMessage] = reorder_messages_for_anthropic(list(openai_req.messages))

    # 清理不完整的工具调用序列（防止 Anthropic API 400 错误）
    history = clean_incomplete_tool_calls(history)
    logger.info("[Anthropic Compat] 清理不完整工具调用后的消息数量: %d", len(history))

    system_prompt_text: Optional[str] = None
    try:
        chunks: List[str] = []
        for _m in history:
            if _m.role == "system":
                _txt = segments_to_text(normalize_content_to_list(_m.content))
                if _txt.strip():
                    chunks.append(_txt)
        if chunks:
            system_prompt_text = "\n\n".join(chunks)
    except Exception:
        system_prompt_text = None

    task_id = STATE.baseline_task_id or str(uuid.uuid4())
    packet = packet_template()
    packet["task_context"] = {
        "tasks": [{
            "id": task_id,
            "description": "",
            "status": {"in_progress": {}},
            "messages": map_history_to_warp_messages(history, task_id, None, False),
        }],
        "active_task_id": task_id,
    }

    packet.setdefault("settings", {}).setdefault("model_config", {})
    packet["settings"]["model_config"]["base"] = openai_req.model or packet["settings"]["model_config"].get("base") or "claude-4.1-opus"

    if STATE.conversation_id:
        packet.setdefault("metadata", {})["conversation_id"] = STATE.conversation_id

    attach_user_and_tools_to_inputs(packet, history, system_prompt_text)

    if openai_req.tools:
        mcp_tools: List[Dict[str, Any]] = []
        for t in openai_req.tools:
            if t.type != "function" or not t.function:
                continue
            mcp_tools.append({
                "name": t.function.name,
                "description": t.function.description or "",
                "input_schema": t.function.parameters or {},
            })
        if mcp_tools:
            packet.setdefault("mcp_context", {}).setdefault("tools", []).extend(mcp_tools)

    created_ts = int(time.time())
    completion_id = str(uuid.uuid4())
    model_id = map_model_name(openai_req.model or "warp-default")

    # 4) 处理流式响应
    if req.stream:
        async def _anthropic_stream():
            # 先获取 OpenAI 流
            openai_stream = stream_openai_sse(packet, completion_id, created_ts, model_id)
            # 转换为 Anthropic 流
            async for chunk in stream_anthropic_sse(openai_stream, req.dict()):
                yield chunk

        return StreamingResponse(
            _anthropic_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )

    # 5) 处理非流式响应
    def _post_once() -> requests.Response:
        return requests.post(
            f"{BRIDGE_BASE_URL}/api/warp/send_stream",
            json={"json_data": packet, "message_type": "warp.multi_agent.v1.Request"},
            timeout=(5.0, 180.0),
        )

    try:
        resp = _post_once()
        if resp.status_code == 429:
            try:
                r = requests.post(f"{BRIDGE_BASE_URL}/api/auth/refresh", timeout=10.0)
                logger.warning("[Anthropic Compat] Bridge returned 429. Tried JWT refresh -> HTTP %s", getattr(r, 'status_code', 'N/A'))
            except Exception as _e:
                logger.warning("[Anthropic Compat] JWT refresh attempt failed after 429: %s", _e)
            resp = _post_once()
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"bridge_error: {resp.text}")
        bridge_resp = resp.json()
    except Exception as e:
        raise HTTPException(502, f"bridge_unreachable: {e}")

    try:
        STATE.conversation_id = bridge_resp.get("conversation_id") or STATE.conversation_id
        ret_task_id = bridge_resp.get("task_id")
        if isinstance(ret_task_id, str) and ret_task_id:
            STATE.baseline_task_id = ret_task_id
    except Exception:
        pass

    # 6) 构建 OpenAI 格式响应 (复用现有逻辑)
    tool_calls: List[Dict[str, Any]] = []
    try:
        parsed_events = bridge_resp.get("parsed_events", []) or []
        for ev in parsed_events:
            evd = ev.get("parsed_data") or ev.get("raw_data") or {}
            client_actions = evd.get("client_actions") or evd.get("clientActions") or {}
            actions = client_actions.get("actions") or client_actions.get("Actions") or []
            for action in actions:
                add_msgs = action.get("add_messages_to_task") or action.get("addMessagesToTask") or {}
                if not isinstance(add_msgs, dict):
                    continue
                for message in add_msgs.get("messages", []) or []:
                    tc = message.get("tool_call") or message.get("toolCall") or {}
                    call_mcp = tc.get("call_mcp_tool") or tc.get("callMcpTool") or {}
                    if isinstance(call_mcp, dict) and call_mcp.get("name"):
                        try:
                            args_obj = call_mcp.get("args", {}) or {}
                            args_str = json.dumps(args_obj, ensure_ascii=False)
                        except Exception:
                            args_str = "{}"
                        tool_calls.append({
                            "id": tc.get("tool_call_id") or str(uuid.uuid4()),
                            "type": "function",
                            "function": {"name": call_mcp.get("name"), "arguments": args_str},
                        })
    except Exception:
        pass

    if tool_calls:
        msg_payload = {"role": "assistant", "content": "", "tool_calls": tool_calls}
        finish_reason = "tool_calls"
    else:
        response_text = bridge_resp.get("response", "")
        msg_payload = {"role": "assistant", "content": response_text}
        finish_reason = "stop"

    # 估算 token 使用情况
    from .sse_transform import estimate_input_tokens
    input_tokens = estimate_input_tokens(packet)
    output_tokens = max(len(response_text if not tool_calls else "") // 4, 1)  # 简单估算

    openai_response = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model_id,
        "choices": [{"index": 0, "message": msg_payload, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens
        }
    }

    # 7) 转换回 Anthropic 格式
    try:
        anthropic_response = openai_to_anthropic_response(openai_response, is_stream=False)
        logger.info("[Anthropic Compat] 最终 Anthropic 响应: %s", json.dumps(anthropic_response, ensure_ascii=False))
        return anthropic_response
    except Exception as e:
        logger.error(f"[Anthropic Compat] OpenAI to Anthropic conversion failed: {e}")
        raise HTTPException(500, f"响应转换失败: {e}")


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionsRequest):
    try:
        initialize_once()
    except Exception as e:
        logger.warning(f"[OpenAI Compat] initialize_once failed or skipped: {e}")

    if not req.messages:
        raise HTTPException(400, "messages 不能为空")

    # 1) 打印接收到的 Chat Completions 原始请求体
    try:
        logger.info("[OpenAI Compat] 接收到的 Chat Completions 请求体(原始): %s", json.dumps(req.dict(), ensure_ascii=False))
    except Exception:
        logger.info("[OpenAI Compat] 接收到的 Chat Completions 请求体(原始) 序列化失败")

    # 整理消息
    history: List[ChatMessage] = reorder_messages_for_anthropic(list(req.messages))

    # 清理不完整的工具调用序列（防止 Anthropic API 400 错误）
    history = clean_incomplete_tool_calls(history)
    logger.info("[OpenAI Compat] 清理不完整工具调用后的消息数量: %d", len(history))

    # 2) 打印整理后的请求体（post-reorder）
    try:
        logger.info("[OpenAI Compat] 整理后的请求体(post-reorder): %s", json.dumps({
            **req.dict(),
            "messages": [m.dict() for m in history]
        }, ensure_ascii=False))
    except Exception:
        logger.info("[OpenAI Compat] 整理后的请求体(post-reorder) 序列化失败")

    system_prompt_text: Optional[str] = None
    try:
        chunks: List[str] = []
        for _m in history:
            if _m.role == "system":
                _txt = segments_to_text(normalize_content_to_list(_m.content))
                if _txt.strip():
                    chunks.append(_txt)
        if chunks:
            system_prompt_text = "\n\n".join(chunks)
    except Exception:
        system_prompt_text = None

    task_id = STATE.baseline_task_id or str(uuid.uuid4())
    packet = packet_template()
    packet["task_context"] = {
        "tasks": [{
            "id": task_id,
            "description": "",
            "status": {"in_progress": {}},
            "messages": map_history_to_warp_messages(history, task_id, None, False),
        }],
        "active_task_id": task_id,
    }

    packet.setdefault("settings", {}).setdefault("model_config", {})
    packet["settings"]["model_config"]["base"] = req.model or packet["settings"]["model_config"].get("base") or "claude-4.1-opus"

    if STATE.conversation_id:
        packet.setdefault("metadata", {})["conversation_id"] = STATE.conversation_id

    attach_user_and_tools_to_inputs(packet, history, system_prompt_text)

    if req.tools:
        mcp_tools: List[Dict[str, Any]] = []
        for t in req.tools:
            if t.type != "function" or not t.function:
                continue
            mcp_tools.append({
                "name": t.function.name,
                "description": t.function.description or "",
                "input_schema": t.function.parameters or {},
            })
        if mcp_tools:
            packet.setdefault("mcp_context", {}).setdefault("tools", []).extend(mcp_tools)

    # 3) 打印转换成 protobuf JSON 的请求体（发送到 bridge 的数据包）
    try:
        logger.info("[OpenAI Compat] 转换成 Protobuf JSON 的请求体: %s", json.dumps(packet, ensure_ascii=False))
    except Exception:
        logger.info("[OpenAI Compat] 转换成 Protobuf JSON 的请求体 序列化失败")

    created_ts = int(time.time())
    completion_id = str(uuid.uuid4())
    model_id = map_model_name(req.model or "warp-default")

    if req.stream:
        async def _agen():
            async for chunk in stream_openai_sse(packet, completion_id, created_ts, model_id):
                yield chunk
        return StreamingResponse(_agen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    def _post_once() -> requests.Response:
        return requests.post(
            f"{BRIDGE_BASE_URL}/api/warp/send_stream",
            json={"json_data": packet, "message_type": "warp.multi_agent.v1.Request"},
            timeout=(5.0, 180.0),
        )

    try:
        def _post_with_packet(p: Dict[str, Any]) -> requests.Response:
            return requests.post(
                f"{BRIDGE_BASE_URL}/api/warp/send_stream",
                json={"json_data": p, "message_type": "warp.multi_agent.v1.Request"},
                timeout=(5.0, 180.0),
            )

        resp = _post_with_packet(packet)
        if resp.status_code == 429:
            try:
                r = requests.post(f"{BRIDGE_BASE_URL}/api/auth/refresh", timeout=10.0)
                logger.warning("[OpenAI Compat] Bridge returned 429. Tried JWT refresh -> HTTP %s", getattr(r, 'status_code', 'N/A'))
            except Exception as _e:
                logger.warning("[OpenAI Compat] JWT refresh attempt failed after 429: %s", _e)
            resp = _post_with_packet(packet)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"bridge_error: {resp.text}")
        bridge_resp = resp.json()

        # 检测 internal_error 并自动恢复（非流式）
        def _extract_internal_error(br: Dict[str, Any]):
            try:
                parsed_events = br.get("parsed_events", []) or []
                for ev in parsed_events:
                    evd = ev.get("parsed_data") or ev.get("raw_data") or {}
                    finished = evd.get("finished") or {}
                    internal = finished.get("internal_error") if isinstance(finished, dict) else None
                    if internal:
                        msg = internal.get("message", "")
                        import re
                        m = re.search(r'tool_call:\{[^}]*?(\w+):\{\}', msg)
                        tool = m.group(1) if m else None
                        return tool, msg
            except Exception:
                pass
            return None, None

        def _has_llm_unavailable(br: Dict[str, Any]) -> bool:
            try:
                parsed_events = br.get("parsed_events", []) or []
                for ev in parsed_events:
                    evd = ev.get("parsed_data") or ev.get("raw_data") or {}
                    finished = evd.get("finished") or {}
                    if isinstance(finished, dict) and ("llm_unavailable" in finished):
                        return True
            except Exception:
                pass
            return False

        tool_name, err_msg = _extract_internal_error(bridge_resp)
        if tool_name or (err_msg and "internal_error" in (err_msg or "")):
            logger.info(f"[OpenAI Compat] 非流式检测到 internal_error，自动重试 (tool={tool_name})")
            import copy
            new_packet = copy.deepcopy(packet)
            try:
                if "input" in new_packet and "user_inputs" in new_packet["input"]:
                    inputs = new_packet["input"]["user_inputs"].get("inputs", [])
                    if inputs:
                        last_input = inputs[-1]
                        if "user_query" in last_input and isinstance(last_input["user_query"], dict):
                            recovery_prompt = (
                                f"\n\n[系统自动恢复] 请继续之前的任务，但不要使用 {tool_name} 工具。可用的工具包括：Read、Write、Edit、Bash、Glob、Grep 等 MCP 工具。"
                                if tool_name
                                else "\n\n[系统自动恢复] 请继续之前的任务，使用可用的 MCP 工具完成。"
                            )
                            current_query = last_input["user_query"].get("query", "")
                            if "[系统自动恢复]" not in current_query:
                                last_input["user_query"]["query"] = current_query + recovery_prompt
                                logger.info("[OpenAI Compat] 非流式已在请求中添加恢复提示")
            except Exception as _e:
                logger.warning(f"[OpenAI Compat] 非流式添加恢复提示失败: {_e}")

            # 重发一次
            resp2 = _post_with_packet(new_packet)
            if resp2.status_code == 429:
                try:
                    r2 = requests.post(f"{BRIDGE_BASE_URL}/api/auth/refresh", timeout=10.0)
                    logger.warning("[OpenAI Compat] Bridge returned 429 on retry. JWT refresh -> HTTP %s", getattr(r2, 'status_code', 'N/A'))
                except Exception as _e:
                    logger.warning("[OpenAI Compat] JWT refresh attempt failed on retry: %s", _e)
                resp2 = _post_with_packet(new_packet)
            if resp2.status_code == 200:
                bridge_resp = resp2.json()
                logger.info("[OpenAI Compat] 非流式自动恢复成功")
            else:
                logger.warning(f"[OpenAI Compat] 非流式自动恢复失败, HTTP {resp2.status_code}: {resp2.text[:200]}")

        # 检测 llm_unavailable 并自动恢复（非流式）
        if _has_llm_unavailable(bridge_resp):
            import copy
            new_packet = copy.deepcopy(packet)
            try:
                if "input" in new_packet and "user_inputs" in new_packet["input"]:
                    inputs = new_packet["input"]["user_inputs"].get("inputs", [])
                    if inputs:
                        last_input = inputs[-1]
                        if "user_query" in last_input and isinstance(last_input["user_query"], dict):
                            current_query = last_input["user_query"].get("query", "")
                            if "继续任务" not in current_query and "[自动恢复]" not in current_query:
                                last_input["user_query"]["query"] = current_query + "\n\n[自动恢复] 继续之前的任务。"
                                logger.info("[OpenAI Compat] 非流式已在请求中添加继续任务提示")
            except Exception as _e:
                logger.warning(f"[OpenAI Compat] 非流式添加继续任务提示失败: {_e}")

            resp2 = _post_with_packet(new_packet)
            if resp2.status_code == 429:
                try:
                    r2 = requests.post(f"{BRIDGE_BASE_URL}/api/auth/refresh", timeout=10.0)
                    logger.warning("[OpenAI Compat] Bridge returned 429 on retry. JWT refresh -> HTTP %s", getattr(r2, 'status_code', 'N/A'))
                except Exception as _e:
                    logger.warning("[OpenAI Compat] JWT refresh attempt failed on retry: %s", _e)
                resp2 = _post_with_packet(new_packet)
            if resp2.status_code == 200:
                bridge_resp = resp2.json()
                logger.info("[OpenAI Compat] 非流式 llm_unavailable 自动恢复成功")
            else:
                logger.warning(f"[OpenAI Compat] 非流式 llm_unavailable 自动恢复失败, HTTP {resp2.status_code}: {resp2.text[:200]}")

    except Exception as e:
        raise HTTPException(502, f"bridge_unreachable: {e}")

    try:
        STATE.conversation_id = bridge_resp.get("conversation_id") or STATE.conversation_id
        ret_task_id = bridge_resp.get("task_id")
        if isinstance(ret_task_id, str) and ret_task_id:
            STATE.baseline_task_id = ret_task_id
    except Exception:
        pass

    tool_calls: List[Dict[str, Any]] = []
    try:
        parsed_events = bridge_resp.get("parsed_events", []) or []
        for ev in parsed_events:
            evd = ev.get("parsed_data") or ev.get("raw_data") or {}
            client_actions = evd.get("client_actions") or evd.get("clientActions") or {}
            actions = client_actions.get("actions") or client_actions.get("Actions") or []
            for action in actions:
                add_msgs = action.get("add_messages_to_task") or action.get("addMessagesToTask") or {}
                if not isinstance(add_msgs, dict):
                    continue
                for message in add_msgs.get("messages", []) or []:
                    tc = message.get("tool_call") or message.get("toolCall") or {}
                    call_mcp = tc.get("call_mcp_tool") or tc.get("callMcpTool") or {}
                    if isinstance(call_mcp, dict) and call_mcp.get("name"):
                        try:
                            args_obj = call_mcp.get("args", {}) or {}
                            args_str = json.dumps(args_obj, ensure_ascii=False)
                        except Exception:
                            args_str = "{}"
                        tool_calls.append({
                            "id": tc.get("tool_call_id") or str(uuid.uuid4()),
                            "type": "function",
                            "function": {"name": call_mcp.get("name"), "arguments": args_str},
                        })
    except Exception:
        pass

    if tool_calls:
        msg_payload = {"role": "assistant", "content": "", "tool_calls": tool_calls}
        finish_reason = "tool_calls"
    else:
        response_text = bridge_resp.get("response", "")
        msg_payload = {"role": "assistant", "content": response_text}
        finish_reason = "stop"

    # 估算 token 使用情况
    from .sse_transform import estimate_input_tokens
    input_tokens = estimate_input_tokens(packet)
    output_tokens = max(len(response_text if not tool_calls else "") // 4, 1)  # 简单估算

    final = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model_id,
        "choices": [{"index": 0, "message": msg_payload, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens
        }
    }
    return final 