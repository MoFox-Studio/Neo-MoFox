"""``:inject_usables`` 默认实现——委托 ``BaseChatter.inject_usables``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ._runtime_helper import get_runtime
from ....utils.event_publisher import NdfcEvent


class InjectUsablesDefaultHandler(BaseEventHandler):
    """:inject_usables 的默认实现——委托 ``BaseChatter.inject_usables``。

    把返回的 :class:`ToolRegistry` 填入 ``tool_registry``；``extra_tools`` 由
    :meth:`NdfcPublisher.inject_usables` 在读回阶段统一 register。
    """

    name = "inject_usables_default"
    description = "默认 inject_usables：委托 BaseChatter.inject_usables()"
    weight = 0
    init_subscribe = [NdfcEvent.INJECT_USABLES]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 inject_usables，把 ToolRegistry 填入 payload。"""
        try:
            runtime = get_runtime(params["stream_id"], self.plugin)
            params["tool_registry"] = await runtime.inject_usables(params["request"])
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["InjectUsablesDefaultHandler"]
