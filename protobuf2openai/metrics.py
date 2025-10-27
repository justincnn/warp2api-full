#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指标监控系统

提供独立的监控日志记录，用于追踪错误恢复、性能指标等关键数据。
"""
import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# 创建独立的指标监控日志记录器
_metrics_logger = logging.getLogger("warp2api.metrics")
_metrics_logger.setLevel(logging.INFO)
_metrics_logger.propagate = False  # 不传播到父 logger

# 移除已有的 handlers
for h in _metrics_logger.handlers[:]:
    _metrics_logger.removeHandler(h)

# 创建单独的监控日志文件
metrics_handler = RotatingFileHandler(
    LOG_DIR / "metrics.log",
    maxBytes=10*1024*1024,  # 10MB
    backupCount=10,
    encoding="utf-8"
)
metrics_handler.setLevel(logging.INFO)

# 使用 JSON 格式便于后续分析
metrics_formatter = logging.Formatter(
    '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": %(message)s}'
)
metrics_handler.setFormatter(metrics_formatter)

_metrics_logger.addHandler(metrics_handler)


class MetricsLogger:
    """指标监控日志记录器"""

    @staticmethod
    def log_recovery_attempt(
        recovery_type: str,
        trigger_reason: str,
        retry_count: int,
        tool_name: Optional[str] = None,
        user_query_preview: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ):
        """记录恢复尝试

        Args:
            recovery_type: 恢复类型 (internal_error, llm_unavailable, timeout)
            trigger_reason: 触发原因描述
            retry_count: 重试次数
            tool_name: 工具名称（如果适用）
            user_query_preview: 用户查询预览（前100字符）
            extra_data: 额外数据
        """
        data = {
            "event": "recovery_attempt",
            "recovery_type": recovery_type,
            "trigger_reason": trigger_reason,
            "retry_count": retry_count,
        }

        if tool_name:
            data["tool_name"] = tool_name
        if user_query_preview:
            data["user_query_preview"] = user_query_preview[:100]
        if extra_data:
            data.update(extra_data)

        _metrics_logger.info(json.dumps(data, ensure_ascii=False))

    @staticmethod
    def log_recovery_success(
        recovery_type: str,
        retry_count: int,
        duration_ms: Optional[float] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ):
        """记录恢复成功

        Args:
            recovery_type: 恢复类型
            retry_count: 重试次数
            duration_ms: 恢复耗时（毫秒）
            extra_data: 额外数据
        """
        data = {
            "event": "recovery_success",
            "recovery_type": recovery_type,
            "retry_count": retry_count,
        }

        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if extra_data:
            data.update(extra_data)

        _metrics_logger.info(json.dumps(data, ensure_ascii=False))

    @staticmethod
    def log_recovery_failure(
        recovery_type: str,
        retry_count: int,
        failure_reason: str,
        extra_data: Optional[Dict[str, Any]] = None
    ):
        """记录恢复失败

        Args:
            recovery_type: 恢复类型
            retry_count: 重试次数
            failure_reason: 失败原因
            extra_data: 额外数据
        """
        data = {
            "event": "recovery_failure",
            "recovery_type": recovery_type,
            "retry_count": retry_count,
            "failure_reason": failure_reason,
        }

        if extra_data:
            data.update(extra_data)

        _metrics_logger.info(json.dumps(data, ensure_ascii=False))

    @staticmethod
    def log_error_context(
        error_type: str,
        user_request: Optional[Dict[str, Any]] = None,
        server_response: Optional[str] = None,
        error_message: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ):
        """记录错误上下文（包括用户请求和服务器响应）

        Args:
            error_type: 错误类型
            user_request: 用户请求数据（脱敏后）
            server_response: 服务器响应（SSE 事件）
            error_message: 错误消息
            extra_data: 额外数据
        """
        data = {
            "event": "error_context",
            "error_type": error_type,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if user_request:
            # 脱敏处理：只保留结构信息，不保留敏感内容
            sanitized_request = {
                "has_user_inputs": "input" in user_request and "user_inputs" in user_request.get("input", {}),
                "has_task_context": "task_context" in user_request,
                "has_mcp_context": "mcp_context" in user_request,
                "input_count": len(user_request.get("input", {}).get("user_inputs", {}).get("inputs", [])),
            }
            # 保存用户查询的前100字符作为预览
            if "input" in user_request and "user_inputs" in user_request["input"]:
                inputs = user_request["input"]["user_inputs"].get("inputs", [])
                if inputs and len(inputs) > 0:
                    last_input = inputs[-1]
                    if "user_query" in last_input and isinstance(last_input["user_query"], dict):
                        query = last_input["user_query"].get("query", "")
                        sanitized_request["query_preview"] = query[:100] if query else ""
            data["user_request"] = sanitized_request

        if server_response:
            # 限制响应长度，避免日志过大
            data["server_response_preview"] = server_response[:500] if len(server_response) > 500 else server_response
            data["server_response_length"] = len(server_response)

        if error_message:
            data["error_message"] = error_message

        if extra_data:
            data.update(extra_data)

        _metrics_logger.info(json.dumps(data, ensure_ascii=False))

    @staticmethod
    def log_performance(
        operation: str,
        duration_ms: float,
        success: bool,
        extra_data: Optional[Dict[str, Any]] = None
    ):
        """记录性能指标

        Args:
            operation: 操作名称
            duration_ms: 耗时（毫秒）
            success: 是否成功
            extra_data: 额外数据
        """
        data = {
            "event": "performance",
            "operation": operation,
            "duration_ms": duration_ms,
            "success": success,
        }

        if extra_data:
            data.update(extra_data)

        _metrics_logger.info(json.dumps(data, ensure_ascii=False))


# 导出单例
metrics_logger = MetricsLogger()
