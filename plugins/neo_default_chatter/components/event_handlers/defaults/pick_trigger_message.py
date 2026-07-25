"""``:pick_trigger_message`` 默认实现——委托 session._pick_trigger_message。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent
from ....session import _pick_trigger_message


class PickTriggerMessageDefaultHandler(BaseEventHandler):
    """:pick_trigger_message 的默认实现——选取本轮工具执行的触发消息。

    委托 ``session._pick_trigger_message``：未读消息末条 → 当前激活消息 →
    未读消息末条 → 历史末条 → 伪空消息。
    """

    name = "pick_trigger_message_default"
    description = "默认 pick_trigger_message：委托 session._pick_trigger_message"
    weight = 0
    init_subscribe = [NdfcEvent.PICK_TRIGGER_MESSAGE]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 pick_trigger_message，把选中的 Message 填入 payload。"""
        try:
            chat_stream = params["chat_stream"]
            unreads = params.get("unreads") or []
            params["trigger"] = _pick_trigger_message(chat_stream, unreads)
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["PickTriggerMessageDefaultHandler"]
