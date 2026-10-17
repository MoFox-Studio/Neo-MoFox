"""``:format_unread_line`` 默认实现——委托 ``BaseChatter.format_message_line``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ._runtime_helper import get_runtime
from ....utils.event_publisher import NdfcEvent


class FormatUnreadLineDefaultHandler(BaseEventHandler):
    """:format_unread_line 的默认实现——委托 ``BaseChatter.format_message_line``。"""

    name = "format_unread_line_default"
    description = "默认 format_unread_line：委托 BaseChatter.format_message_line()"
    weight = 0
    init_subscribe = [NdfcEvent.FORMAT_UNREAD_LINE]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 format_unread_line，把格式化文本填入 payload。"""
        try:
            runtime = get_runtime(params["stream_id"], self.plugin)
            params["formatted_line"] = runtime.format_message_line(
                params["message"], params.get("time_format") or "%H:%M"
            )
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["FormatUnreadLineDefaultHandler"]
