"""``:format_tool_result`` 默认实现——按 ``kind`` 返回控制流工具结果文本。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent

_KIND_PASS = "pass"
_KIND_STOP = "stop"
_KIND_DUPLICATE = "duplicate"
_KIND_NORMAL = "normal"


class FormatToolResultDefaultHandler(BaseEventHandler):
    """:format_tool_result 的默认实现——按 ``kind`` 分发文本。

    - ``"pass"``：``"已登记等待，本轮动作完成后等待用户新消息"`` 或带秒数变体
    - ``"stop"``：``"对话已结束，将在 X 分钟后允许新对话"``
    - ``"duplicate"``：``"检测到重复工具调用，已自动跳过"``
    - ``"normal"``：空字符串（由 ``run_tool_call`` 内部写入真实结果）
    """

    name = "format_tool_result_default"
    description = "默认 format_tool_result：按 kind 返回控制流工具结果文本"
    weight = 0
    init_subscribe = [NdfcEvent.FORMAT_TOOL_RESULT]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 format_tool_result，把文本填入 ``result_text``。"""
        try:
            kind = params.get("kind") or _KIND_NORMAL
            args = params.get("args") or {}

            if kind == _KIND_PASS:
                params["result_text"] = self._format_pass(args)
            elif kind == _KIND_STOP:
                params["result_text"] = self._format_stop(args)
            elif kind == _KIND_DUPLICATE:
                params["result_text"] = "检测到重复工具调用，已自动跳过"
            else:
                # normal 或未知 kind：留空，由 run_tool_call 写入真实结果
                params["result_text"] = ""
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params

    @staticmethod
    def _format_pass(args: dict[str, Any]) -> str:
        """构造 pass_and_wait 的结果文本。

        ``seconds`` 缺省表示无限期等待新消息；否则按用户指定秒数定时唤醒。
        """
        seconds = args.get("seconds")
        try:
            wait_seconds = None if seconds is None else float(seconds)
        except (TypeError, ValueError):
            wait_seconds = None
        if wait_seconds is None:
            return "已登记等待，本轮动作完成后等待用户新消息"
        return (
            f"已登记等待，本轮动作完成后等待 {wait_seconds} 秒后继续对话"
        )

    @staticmethod
    def _format_stop(args: dict[str, Any]) -> str:
        """构造 stop_conversation 的结果文本。

        ``args["minutes"]`` 由 tool_flow.py 预解析后传入（已含 default_stop_minutes 回退）。
        """
        minutes = args.get("minutes")
        try:
            minutes_val = float(minutes) if minutes is not None else 5.0
        except (TypeError, ValueError):
            minutes_val = 5.0
        return f"对话已结束，将在 {minutes_val} 分钟后允许新对话"


__all__ = ["FormatToolResultDefaultHandler"]
