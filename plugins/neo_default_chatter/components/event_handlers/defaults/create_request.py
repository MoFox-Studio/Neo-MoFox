"""``:create_request`` 默认实现——委托 ``BaseChatter.create_request``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ._runtime_helper import get_runtime
from ....utils.event_publisher import NdfcEvent


class CreateRequestDefaultHandler(BaseEventHandler):
    """:create_request 的默认实现——委托 ``BaseChatter.create_request``。

    注意 ``create_request`` 是同步方法（依据 ``BaseChatter`` 实现），handler 内
    直接调用即可。
    """

    name = "create_request_default"
    description = "默认 create_request：委托 BaseChatter.create_request()"
    weight = 0
    init_subscribe = [NdfcEvent.CREATE_REQUEST]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 create_request，把 LLMRequest 填入 payload。"""
        try:
            runtime = get_runtime(params["stream_id"], self.plugin)
            params["request"] = runtime.create_request(
                params["task_name"],
                params.get("request_name") or "",
                params.get("with_reminder"),
            )
            return EventDecision.SUCCESS, params
        except Exception:
            # 让异常向上抛——EventBus 的 safe_execute 会降级为 PASS，
            # session 会拿到 None 的 request，触发既有的 KeyError/Failure 分支。
            raise


__all__ = ["CreateRequestDefaultHandler"]
