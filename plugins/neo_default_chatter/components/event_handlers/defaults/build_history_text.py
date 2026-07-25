"""``:build_history_text`` 默认实现——委托 ``NeoChatterPromptBuilder.build_history_text``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseChatter, BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent
from ....utils.prompt_builder import NeoChatterPromptBuilder


class BuildHistoryTextDefaultHandler(BaseEventHandler):
    """:build_history_text 的默认实现。

    调用 :meth:`NeoChatterPromptBuilder.build_history_text`，``formatter`` 用
    :meth:`BaseChatter.format_message_line`（静态方法，无需 runtime 实例）。
    把返回的字符串按行拆成 list 填入 ``lines``。
    """

    name = "build_history_text_default"
    description = "默认 build_history_text：委托 NeoChatterPromptBuilder"
    weight = 0
    init_subscribe = [NdfcEvent.BUILD_HISTORY_TEXT]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 build_history_text，把按行拆分的 list 填入 payload。"""
        try:
            chat_stream = params["chat_stream"]
            text = NeoChatterPromptBuilder.build_history_text(
                chat_stream, formatter=BaseChatter.format_message_line
            )
            params["lines"] = text.split("\n") if text else []
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["BuildHistoryTextDefaultHandler"]
