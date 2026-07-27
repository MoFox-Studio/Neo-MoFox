"""``:flush_unreads`` 默认实现——委托 ``BaseChatter.flush_unreads``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ._runtime_helper import get_runtime
from ....utils.event_publisher import NdfcEvent


class FlushUnreadsDefaultHandler(BaseEventHandler):
    """:flush_unreads 的默认实现——委托 ``BaseChatter.flush_unreads``。"""

    name = "flush_unreads_default"
    description = "默认 flush_unreads：委托 BaseChatter.flush_unreads()"
    weight = 0
    init_subscribe = [NdfcEvent.FLUSH_UNREADS]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 flush_unreads，把返回的 int 填入 payload。"""
        try:
            runtime = get_runtime(params["stream_id"], self.plugin)
            params["flushed_count"] = await runtime.flush_unreads(params["messages"])
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["FlushUnreadsDefaultHandler"]
