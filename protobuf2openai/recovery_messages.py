#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恢复提示文案统一管理模块

集中管理所有自动恢复相关的提示文案，便于维护和国际化。
"""
from typing import Optional


class RecoveryMessages:
    """恢复提示文案管理类"""

    # ==================== Internal Error 相关文案 ====================

    @staticmethod
    def internal_error_recovery_prompt(tool_name: Optional[str] = None) -> str:
        """生成 internal_error 恢复提示（添加到用户请求中）

        Args:
            tool_name: 被限制的工具名称

        Returns:
            恢复提示文本
        """
        if tool_name:
            return f"\n\n[系统自动恢复] 请继续之前的任务，但不要使用 {tool_name} 工具。可用的工具包括：Read、Write、Edit、Bash、Glob、Grep 等 MCP 工具。"
        else:
            return "\n\n[系统自动恢复] 请继续之前的任务，使用可用的 MCP 工具完成。"

    @staticmethod
    def internal_error_recovery_notice() -> str:
        """生成 internal_error 恢复通知（显示给用户）

        Returns:
            恢复通知文本
        """
        return "\n\n🔄 **正在自动恢复...**\n\n检测到工具限制冲突，系统正在重新尝试任务。\n"

    @staticmethod
    def internal_error_max_retry_exceeded(tool_name: Optional[str] = None) -> str:
        """生成 internal_error 达到最大重试次数的错误提示

        Args:
            tool_name: 被限制的工具名称

        Returns:
            错误提示文本
        """
        return (
            f"\n\n⚠️ **服务内部错误（无法自动恢复）**\n\n"
            f"AI 多次尝试调用被限制的工具：`{tool_name}`\n\n"
            f"**建议解决方案：**\n"
            f"• 🔄 换个方式描述你的需求\n"
            f"• 💡 简化请求范围\n"
            f"• 📝 明确说明避免某些操作\n"
        )

    # ==================== LLM Unavailable 相关文案 ====================

    @staticmethod
    def llm_unavailable_recovery_prompt() -> str:
        """生成 llm_unavailable 恢复提示（添加到用户请求中）

        Returns:
            恢复提示文本
        """
        return "\n\n[自动恢复] 继续之前的任务。"

    @staticmethod
    def llm_unavailable_recovery_notice() -> str:
        """生成 llm_unavailable 恢复通知（显示给用户）

        Returns:
            恢复通知文本
        """
        return "\n\n🔄 **LLM 服务暂时不可用，正在自动重试...**\n\n"

    @staticmethod
    def llm_unavailable_max_retry_exceeded() -> str:
        """生成 llm_unavailable 达到最大重试次数的错误提示

        Returns:
            错误提示文本
        """
        return "\n\n⚠️ **LLM 服务暂时不可用**\n\n请稍后重试。\n"

    # ==================== Timeout 相关文案 ====================

    @staticmethod
    def timeout_recovery_prompt() -> str:
        """生成超时恢复提示（添加到用户请求中）

        Returns:
            恢复提示文本
        """
        return "\n\n[自动恢复] 继续之前的任务。"

    # ==================== 检测标记 ====================

    # 用于检测是否已添加恢复提示，避免重复
    RECOVERY_MARKERS = [
        "[系统自动恢复]",
        "[自动恢复]",
        "继续任务",
    ]

    @staticmethod
    def has_recovery_marker(text: str) -> bool:
        """检查文本中是否已包含恢复标记

        Args:
            text: 待检查的文本

        Returns:
            是否包含恢复标记
        """
        return any(marker in text for marker in RecoveryMessages.RECOVERY_MARKERS)


# 导出单例
recovery_messages = RecoveryMessages()