#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Warp API客户端模块

处理与Warp API的通信，包括protobuf数据发送和SSE响应解析。
"""
import httpx
import os
import base64
import binascii
from typing import Optional, Any, Dict
from urllib.parse import urlparse
import socket

from ..core.logging import logger
from ..core.protobuf_utils import protobuf_to_dict
from ..core.pool_auth import get_pool_manager
from ..config.settings import WARP_URL as CONFIG_WARP_URL


def _get(d: Dict[str, Any], *names: str) -> Any:
    """Return the first matching key value (camelCase/snake_case tolerant)."""
    for name in names:
        if name in d:
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


async def send_protobuf_to_warp_api(
    protobuf_bytes: bytes, show_all_events: bool = True
) -> tuple[str, Optional[str], Optional[str]]:
    """发送protobuf数据到Warp API并获取响应"""
    _pool_session_id = None
    warp_url: Optional[str] = None
    try:
        logger.info(f"发送 {len(protobuf_bytes)} 字节到Warp API")
        logger.info(f"数据包前32字节 (hex): {protobuf_bytes[:32].hex()}")

        warp_url = CONFIG_WARP_URL

        logger.info(f"发送请求到: {warp_url}")

        # 从账号池获取会话并获取访问令牌
        manager = get_pool_manager()
        session = await manager.acquire_session()
        if not session or not session.get("access_token"):
            raise RuntimeError("账号池未返回有效的 access_token")
        jwt = session["access_token"]
        _pool_session_id = session.get("session_id")

        conversation_id = None
        task_id = None
        complete_response = []
        all_events = []
        event_count = 0
        
        verify_opt = True
        insecure_env = os.getenv("WARP_INSECURE_TLS", "").lower()
        if insecure_env in ("1", "true", "yes"):
            verify_opt = False
            logger.warning("TLS verification disabled via WARP_INSECURE_TLS for Warp API client")

        async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(120.0), verify=verify_opt, trust_env=True) as client:
            # 最多尝试两次：第一次失败且为配额429时申请匿名token并重试一次
            for attempt in range(2):
                if attempt == 0:
                    pass
                else:
                    _sess = await manager.acquire_session()
                    if not _sess or not _sess.get("access_token"):
                        logger.error("重试时账号池未返回有效 access_token")
                        return f"❌ Warp API Error: unable to acquire session for retry", None, None
                    jwt = _sess["access_token"]
                    _pool_session_id = _sess.get("session_id")
                headers = {
                    "accept": "text/event-stream",
                    "content-type": "application/x-protobuf", 
                    "x-warp-client-version": "v0.2025.08.06.08.12.stable_02",
                    "x-warp-os-category": "Windows",
                    "x-warp-os-name": "Windows", 
                    "x-warp-os-version": "11 (26100)",
                    "authorization": f"Bearer {jwt}",
                    "content-length": str(len(protobuf_bytes)),
                }
                async with client.stream("POST", warp_url, headers=headers, content=protobuf_bytes) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        error_content = error_text.decode('utf-8') if error_text else "No error content"
                    if response.status_code == 429 and attempt == 0:
                        # 429：上报封禁并释放当前会话，随后进入下一轮尝试获取新会话
                        try:
                            await manager.mark_blocked(jwt_token=jwt, email=(session.get("account") or {}).get("email"))
                        except Exception:
                            pass
                        try:
                            if _pool_session_id:
                                await manager.release_session(_pool_session_id)
                        except Exception:
                            pass
                        continue
                    else:
                        # 其他错误或第二次失败：记录并释放本轮会话
                        logger.error(f"WARP API HTTP ERROR {response.status_code}: {error_content}")
                        try:
                            if _pool_session_id:
                                await manager.release_session(_pool_session_id)
                        except Exception:
                            pass
                        return f"❌ Warp API Error (HTTP {response.status_code}): {error_content}", None, None
                    
                    logger.info(f"✅ 收到HTTP {response.status_code}响应")
                    logger.info("开始处理SSE事件流...")
                    
                    import re as _re
                    import base64 as _b64
                    def _decode_payload_bytes(data_str: str):
                        s = _re.sub(r"\s+", "", data_str or "")
                        if not s:
                            return None
                        if _re.fullmatch(r"[0-9a-fA-F]+", s or ""):
                            try:
                                return bytes.fromhex(s)
                            except Exception:
                                pass
                        pad = "=" * ((4 - (len(s) % 4)) % 4)
                        try:
                            return _b64.urlsafe_b64decode(s + pad)
                        except Exception:
                            try:
                                return _b64.b64decode(s + pad)
                            except Exception:
                                return None
                    
                    current_data = ""
                    
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            if not payload:
                                continue
                            if payload == "[DONE]":
                                logger.info("收到[DONE]标记，结束处理")
                                break
                            current_data += payload
                            continue
                        
                        if (line.strip() == "") and current_data:
                            raw_bytes = _decode_payload_bytes(current_data)
                            current_data = ""
                            if raw_bytes is None:
                                logger.debug("跳过无法解析的SSE数据块（非hex/base64或不完整）")
                                continue
                            try:
                                event_data = protobuf_to_dict(raw_bytes, "warp.multi_agent.v1.ResponseEvent")
                            except Exception as parse_error:
                                logger.debug(f"解析事件失败，跳过: {str(parse_error)[:100]}")
                                continue
                            event_count += 1
                            
                            def _get(d: Dict[str, Any], *names: str) -> Any:
                                for n in names:
                                    if isinstance(d, dict) and n in d:
                                        return d[n]
                                return None
                            
                            event_type = _get_event_type(event_data)
                            if show_all_events:
                                all_events.append({"event_number": event_count, "event_type": event_type, "raw_data": event_data})
                            logger.info(f"🔄 Event #{event_count}: {event_type}")
                            if show_all_events:
                                logger.info(f"   📋 Event data: {str(event_data)}...")
                            
                            if "init" in event_data:
                                init_data = event_data["init"]
                                conversation_id = init_data.get("conversation_id", conversation_id)
                                task_id = init_data.get("task_id", task_id)
                                logger.info(f"会话初始化: {conversation_id}")
                                client_actions = _get(event_data, "client_actions", "clientActions")
                                if isinstance(client_actions, dict):
                                    actions = _get(client_actions, "actions", "Actions") or []
                                    for i, action in enumerate(actions):
                                        logger.info(f"   🎯 Action #{i+1}: {list(action.keys())}")
                                        append_data = _get(action, "append_to_message_content", "appendToMessageContent")
                                        if isinstance(append_data, dict):
                                            message = append_data.get("message", {})
                                            agent_output = _get(message, "agent_output", "agentOutput") or {}
                                            text_content = agent_output.get("text", "")
                                            if text_content:
                                                complete_response.append(text_content)
                                                logger.info(f"   📝 Text Fragment: {text_content[:100]}...")
                                        messages_data = _get(action, "add_messages_to_task", "addMessagesToTask")
                                        if isinstance(messages_data, dict):
                                            messages = messages_data.get("messages", [])
                                            task_id = messages_data.get("task_id", messages_data.get("taskId", task_id))
                                            for j, message in enumerate(messages):
                                                logger.info(f"   📨 Message #{j+1}: {list(message.keys())}")
                                                if _get(message, "agent_output", "agentOutput") is not None:
                                                    agent_output = _get(message, "agent_output", "agentOutput") or {}
                                                    text_content = agent_output.get("text", "")
                                                    if text_content:
                                                        complete_response.append(text_content)
                                                        logger.info(f"   📝 Complete Message: {text_content[:100]}...")
                    
                    full_response = "".join(complete_response)
                    logger.info("="*60)
                    logger.info("📊 SSE STREAM SUMMARY")
                    logger.info("="*60)
                    logger.info(f"📈 Total Events Processed: {event_count}")
                    logger.info(f"🆔 Conversation ID: {conversation_id}")
                    logger.info(f"🆔 Task ID: {task_id}")
                    logger.info(f"📝 Response Length: {len(full_response)} characters")
                    logger.info("="*60)
                    # 释放本轮会话
                    try:
                        if _pool_session_id:
                            await manager.release_session(_pool_session_id)
                    except Exception as e:
                        logger.warning(f"release_session failed: {e}")
                    if full_response:
                        logger.info(f"✅ Stream processing completed successfully")
                        return full_response, conversation_id, task_id
                    else:
                        logger.warning("⚠️ No text content received in response")
                        return "Warning: No response content received", conversation_id, task_id
    except Exception as e:
        import traceback
        logger.error("="*60)
        logger.error("WARP API CLIENT EXCEPTION")
        logger.error("="*60)
        logger.error(f"Exception Type: {type(e).__name__}")
        logger.error(f"Exception Message: {str(e)}")
        logger.error(f"Request URL: {warp_url if 'warp_url' in locals() else 'Unknown'}")
        logger.error(f"Request Size: {len(protobuf_bytes) if 'protobuf_bytes' in locals() else 'Unknown'}")
        logger.error("Python Traceback:")
        logger.error(traceback.format_exc())
        logger.error("="*60)
        raise


async def send_protobuf_to_warp_api_parsed(protobuf_bytes: bytes) -> tuple[str, Optional[str], Optional[str], list]:
    """发送protobuf数据到Warp API并获取解析后的SSE事件数据

    支持超时自动恢复机制：当请求超时时，自动附加继续任务提示并重试一次
    """
    try:
        return await _send_protobuf_to_warp_api_parsed_impl(protobuf_bytes)
    except httpx.TimeoutException as timeout_err:
        # 超时自动恢复：模拟用户发送继续任务
        logger.warning(f"请求超时，正在自动恢复... (超时类型: {type(timeout_err).__name__})")
        logger.info("模拟用户发送继续任务提示，重新尝试...")

        try:
            # 重新构造请求，添加继续任务提示
            from ..core.protobuf_utils import protobuf_to_dict, dict_to_protobuf_bytes

            try:
                # 解析原始请求
                original_data = protobuf_to_dict(protobuf_bytes, "warp.multi_agent.v1.Request")

                # 在 user_inputs 中的最后一个 user_query 添加继续任务提示
                if "input" in original_data and "user_inputs" in original_data["input"]:
                    inputs = original_data["input"]["user_inputs"].get("inputs", [])
                    if inputs:
                        last_input = inputs[-1]
                        if "user_query" in last_input and isinstance(last_input["user_query"], dict):
                            current_query = last_input["user_query"].get("query", "")
                            # 避免重复附加
                            if "继续任务" not in current_query and "[自动恢复]" not in current_query:
                                recovery_prompt = "\n\n[自动恢复] 继续之前的任务。"
                                last_input["user_query"]["query"] = current_query + recovery_prompt
                                logger.info("已在请求中添加继续任务提示")

                # 重新编码为 protobuf
                new_protobuf_bytes = dict_to_protobuf_bytes(original_data, "warp.multi_agent.v1.Request")

                # 重试一次
                logger.info("正在重新发送请求 (附带继续任务提示)...")
                return await _send_protobuf_to_warp_api_parsed_impl(new_protobuf_bytes)

            except Exception as parse_err:
                logger.error(f"解析/重构 protobuf 失败: {parse_err}")
                # 如果解析失败，直接抛出原始超时异常
                raise timeout_err

        except httpx.TimeoutException as second_timeout:
            logger.error("重试后仍然超时，放弃自动恢复")
            raise second_timeout
        except Exception as retry_err:
            logger.error(f"自动恢复过程失败: {retry_err}")
            raise timeout_err
    except Exception as e:
        raise


async def _send_protobuf_to_warp_api_parsed_impl(protobuf_bytes: bytes) -> tuple[str, Optional[str], Optional[str], list]:
    """发送protobuf数据到Warp API并获取解析后的SSE事件数据（实际实现）"""
    warp_url: Optional[str] = None
    try:
        logger.info(f"发送 {len(protobuf_bytes)} 字节到Warp API (解析模式)")
        logger.info(f"数据包前32字节 (hex): {protobuf_bytes[:32].hex()}")

        warp_url = CONFIG_WARP_URL

        logger.info(f"发送请求到: {warp_url}")

        # 从账号池获取会话并获取访问令牌
        manager = get_pool_manager()
        session = await manager.acquire_session()
        if not session or not session.get("access_token"):
            raise RuntimeError("账号池未返回有效的 access_token")
        jwt = session["access_token"]
        _pool_session_id = session.get("session_id")

        conversation_id = None
        task_id = None
        complete_response = []
        parsed_events = []
        event_count = 0
        
        verify_opt = True
        insecure_env = os.getenv("WARP_INSECURE_TLS", "").lower()
        if insecure_env in ("1", "true", "yes"):
            verify_opt = False
            logger.warning("TLS verification disabled via WARP_INSECURE_TLS for Warp API client")

        async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(120.0), verify=verify_opt, trust_env=True) as client:
            # 最多尝试两次：429 时上报并换新会话重试一次（账号池-only）
            for attempt in range(2):
                if attempt == 0:
                    pass
                else:
                    _sess = await manager.acquire_session()
                    if not _sess or not _sess.get("access_token"):
                        logger.error("重试时账号池未返回有效 access_token")
                        return f"❌ Warp API Error: unable to acquire session for retry", None, None
                    jwt = _sess["access_token"]
                    _pool_session_id = _sess.get("session_id")
                    session = _sess
                headers = {
                    "accept": "text/event-stream",
                    "content-type": "application/x-protobuf",
                    "x-warp-client-version": "v0.2025.08.06.08.12.stable_02",
                    "x-warp-os-category": "Windows",
                    "x-warp-os-name": "Windows",
                    "x-warp-os-version": "11 (26100)",
                    "authorization": f"Bearer {jwt}",
                    "content-length": str(len(protobuf_bytes)),
                }
                async with client.stream("POST", warp_url, headers=headers, content=protobuf_bytes) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        error_content = error_text.decode('utf-8') if error_text else "No error content"
                        if response.status_code == 429 and attempt == 0:
                            # 429：上报封禁并释放当前会话，随后进入下一轮尝试获取新会话
                            try:
                                await manager.mark_blocked(jwt_token=jwt, email=(session.get("account") or {}).get("email"))
                            except Exception:
                                pass
                            try:
                                if _pool_session_id:
                                    await manager.release_session(_pool_session_id)
                            except Exception:
                                pass
                            continue
                        # 其他错误或第二次失败
                        logger.error(f"WARP API HTTP ERROR (解析模式) {response.status_code}: {error_content}")
                        try:
                            if _pool_session_id:
                                await manager.release_session(_pool_session_id)
                        except Exception:
                            pass
                        return f"❌ Warp API Error (HTTP {response.status_code}): {error_content}", None, None, []
                    
                    logger.info(f"✅ 收到HTTP {response.status_code}响应 (解析模式)")
                    logger.info("开始处理SSE事件流...")
                    
                    import re as _re2
                    def _decode_payload_bytes(data_str: str):
                        s = _re2.sub(r"\s+", "", data_str or "")
                        if not s:
                            return None
                        if _re2.fullmatch(r"[0-9a-fA-F]+", s or ""):
                            try:
                                return bytes.fromhex(s)
                            except Exception:
                                pass
                        pad = "=" * ((4 - (len(s) % 4)) % 4)
                        try:
                            import base64 as _b64
                            return _b64.urlsafe_b64decode(s + pad)
                        except Exception:
                            try:
                                return _b64.b64decode(s + pad)
                            except Exception:
                                return None
                    
                    current_data = ""
                    
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            if not payload:
                                continue
                            if payload == "[DONE]":
                                logger.info("收到[DONE]标记，结束处理")
                                break
                            current_data += payload
                            continue
                        
                        if (line.strip() == "") and current_data:
                            raw_bytes = _decode_payload_bytes(current_data)
                            current_data = ""
                            if raw_bytes is None:
                                logger.debug("跳过无法解析的SSE数据块（非hex/base64或不完整）")
                                continue
                            try:
                                event_data = protobuf_to_dict(raw_bytes, "warp.multi_agent.v1.ResponseEvent")
                                event_count += 1
                                event_type = _get_event_type(event_data)
                                parsed_event = {"event_number": event_count, "event_type": event_type, "parsed_data": event_data}
                                parsed_events.append(parsed_event)
                                logger.info(f"🔄 Event #{event_count}: {event_type}")
                                logger.debug(f"   📋 Event data: {str(event_data)}...")
                                
                                def _get(d: Dict[str, Any], *names: str) -> Any:
                                    for n in names:
                                        if isinstance(d, dict) and n in d:
                                            return d[n]
                                    return None
                                
                                if "init" in event_data:
                                    init_data = event_data["init"]
                                    conversation_id = init_data.get("conversation_id", conversation_id)
                                    task_id = init_data.get("task_id", task_id)
                                    logger.info(f"会话初始化: {conversation_id}")
                                
                                client_actions = _get(event_data, "client_actions", "clientActions")
                                if isinstance(client_actions, dict):
                                    actions = _get(client_actions, "actions", "Actions") or []
                                    for i, action in enumerate(actions):
                                        logger.info(f"   🎯 Action #{i+1}: {list(action.keys())}")
                                        append_data = _get(action, "append_to_message_content", "appendToMessageContent")
                                        if isinstance(append_data, dict):
                                            message = append_data.get("message", {})
                                            agent_output = _get(message, "agent_output", "agentOutput") or {}
                                            text_content = agent_output.get("text", "")
                                            if text_content:
                                                complete_response.append(text_content)
                                                logger.info(f"   📝 Text Fragment: {text_content[:100]}...")
                                        messages_data = _get(action, "add_messages_to_task", "addMessagesToTask")
                                        if isinstance(messages_data, dict):
                                            messages = messages_data.get("messages", [])
                                            task_id = messages_data.get("task_id", messages_data.get("taskId", task_id))
                                            for j, message in enumerate(messages):
                                                logger.info(f"   📨 Message #{j+1}: {list(message.keys())}")
                                                if _get(message, "agent_output", "agentOutput") is not None:
                                                    agent_output = _get(message, "agent_output", "agentOutput") or {}
                                                    text_content = agent_output.get("text", "")
                                                    if text_content:
                                                        complete_response.append(text_content)
                                                        logger.info(f"   📝 Complete Message: {text_content[:100]}...")
                            except Exception as parse_err:
                                logger.debug(f"解析事件失败，跳过: {str(parse_err)[:100]}")
                                continue
                    
                    full_response = "".join(complete_response)
                    logger.info("="*60)
                    logger.info("📊 SSE STREAM SUMMARY (解析模式)")
                    logger.info("="*60)
                    logger.info(f"📈 Total Events Processed: {event_count}")
                    logger.info(f"🆔 Conversation ID: {conversation_id}")
                    logger.info(f"🆔 Task ID: {task_id}")
                    logger.info(f"📝 Response Length: {len(full_response)} characters")
                    logger.info(f"🎯 Parsed Events Count: {len(parsed_events)}")
                    logger.info("="*60)
                    
                    logger.info(f"✅ Stream processing completed successfully (解析模式)")
                    # 释放本轮会话（解析模式）
                    try:
                        if _pool_session_id:
                            await manager.release_session(_pool_session_id)
                    except Exception:
                        pass
                    return full_response, conversation_id, task_id, parsed_events
    except Exception as e:
        import traceback
        logger.error("="*60)
        logger.error("WARP API CLIENT EXCEPTION (解析模式)")
        logger.error("="*60)
        logger.error(f"Exception Type: {type(e).__name__}")
        logger.error(f"Exception Message: {str(e)}")
        logger.error(f"Request URL: {warp_url if 'warp_url' in locals() else 'Unknown'}")
        logger.error(f"Request Size: {len(protobuf_bytes) if 'protobuf_bytes' in locals() else 'Unknown'}")
        logger.error("Python Traceback:")
        logger.error(traceback.format_exc())
        logger.error("="*60)
        raise