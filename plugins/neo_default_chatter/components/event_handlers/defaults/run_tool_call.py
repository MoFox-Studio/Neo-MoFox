"""``:run_tool_call`` 默认实现——委托 ``BaseChatter.run_tool_call``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ._runtime_helper import get_runtime
from ....utils.event_publisher import NdfcEvent


class RunToolCallDefaultHandler(BaseEventHandler):
    """:run_tool_call 的默认实现——委托 ``BaseChatter.run_tool_call``。

    把返回的 ``list[tuple[bool, bool]]`` 填入 ``results``。
    """

    name = "run_tool_call_default"
    description = "默认 run_tool_call：委托 BaseChatter.run_tool_call()"
    weight = 0
    init_subscribe = [NdfcEvent.RUN_TOOL_CALL]

    # run_tool_call 内部会执行 agent 工具调用，agent.execute 可能做多次 LLM
    # 编排（远超 30s 全局默认超时）。这里禁用订阅者级超时，避免在执行
    # agent 期间被 EventBus 强制 cancel 导致 results 静默丢失。
    timeout: float | None = 0

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 run_tool_call，把结果列表填入 payload。"""
        try:
            runtime = get_runtime(params["stream_id"], self.plugin)
            params["results"] = await runtime.run_tool_call(
                params["calls"],
                params["response"],
                params["usable_map"],
                params.get("trigger_msg"),
            )
            return EventDecision.SUCCESS, params
        except Exception:
            # 让异常向上抛——run_tool_call 失败应让上层感知（results 为空会导致
            # 后续 follow_up 决策出错）。EventBus safe_execute 会降级为 PASS，
            # results 保持 []。
            return EventDecision.PASS, params


__all__ = ["RunToolCallDefaultHandler"]
