"""``:fetch_unreads`` 默认实现——委托 ``BaseChatter.fetch_unreads()``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ._runtime_helper import get_runtime
from ....utils.event_publisher import NdfcEvent


class FetchUnreadsDefaultHandler(BaseEventHandler):
    """:fetch_unreads 的默认实现——委托 ``BaseChatter.fetch_unreads()``。

    weight=0 保证第三方先执行；第三方 ``STOP`` 即替换，``SUCCESS`` 即协作（一般用不上，
    因为 ``messages`` 字段是 list 不是 append 语义）。
    """

    name = "fetch_unreads_default"
    description = "默认 fetch_unreads：委托 BaseChatter.fetch_unreads()"
    weight = 0
    init_subscribe = [NdfcEvent.FETCH_UNREADS]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 fetch_unreads，把返回的 messages 列表填入 payload。"""
        try:
            runtime = get_runtime(params["stream_id"], self.plugin)
            _, messages = await runtime.fetch_unreads()
            params["messages"] = messages
            return EventDecision.SUCCESS, params
        except Exception:
            # EventBus 会自动 fail-open 为 PASS（event_manager.py:337-362），
            # 显式 try/except 让行为可预测 + 避免日志噪音。
            # fail-open 后 messages 保持 []，session 会以为没未读，可能错跳过本轮。
            return EventDecision.PASS, params


__all__ = ["FetchUnreadsDefaultHandler"]
