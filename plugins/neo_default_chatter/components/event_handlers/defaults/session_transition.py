"""``:session_transition`` 默认实现——纯观察，仅写 DEBUG 日志。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent

logger = get_logger("neo_default_chatter.session_transition")


class SessionTransitionDefaultHandler(BaseEventHandler):
    """:session_transition 的默认实现——纯观察事件。

    默认行为仅写 ``DEBUG`` 日志，不修改 params；返回 ``PASS`` 让第三方观察者继续执行。
    典型第三方用途：统计、审计、telemetry。
    """

    name = "session_transition_default"
    description = "默认 session_transition：纯观察，仅写 DEBUG 日志"
    weight = 0
    init_subscribe = [NdfcEvent.SESSION_TRANSITION]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """记录 FSM 阶段切换，返回 PASS 让后续观察者继续。"""
        stream_id = params.get("stream_id") or ""
        from_phase = params.get("from_phase") or ""
        to_phase = params.get("to_phase") or ""
        logger.debug(
            f"[FSM 观察] stream={stream_id[:8]} {from_phase} -> {to_phase}"
        )
        return EventDecision.PASS, params


__all__ = ["SessionTransitionDefaultHandler"]
