"""disturbance_guard 事件处理器。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.core.components.types import EventType
from src.core.models.message import Message
from src.kernel.event import EventDecision

from .service import DisturbanceGuardService

logger = get_logger("disturbance_guard_event_handler")


class DisturbanceGuardMessageHandler(BaseEventHandler):
    """在消息分发前执行打扰感知判定。"""

    handler_name: str = "disturbance_guard_message_handler"
    handler_description: str = "识别免打扰/唤醒意图并在需要时短路消息分发"
    weight: int = 50
    intercept_message: bool = True
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE_RECEIVED]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 ON_MESSAGE_RECEIVED 事件。"""
        _ = event_name
        message = params.get("message")
        if not isinstance(message, Message):
            return EventDecision.PASS, params

        decision = await DisturbanceGuardService(self.plugin).handle_message(message)
        if not decision.should_suppress:
            return EventDecision.PASS, params

        logger.info(
            "打扰感知已静默当前消息: "
            f"stream={message.stream_id[:8]}, reason={decision.reason}"
        )
        return EventDecision.STOP, params

