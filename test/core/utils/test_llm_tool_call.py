from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.components.base.tool import BaseTool
from src.core.utils.llm_tool_call import run_llm_usable_executions, run_tool_call
from src.kernel.llm import LLMUsableExecution, ToolCall, ToolRegistry


class _FakeResponse:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def add_payload(self, payload: Any, position: object = None) -> None:
        _ = position
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_ready_executions_resume_in_call_order() -> None:
    events: list[str] = []

    async def first():
        events.append("first-prepare")
        yield None
        await asyncio.sleep(0.01)
        events.append("first-final")
        yield (True, "first")

    async def second():
        events.append("second-prepare")
        yield None
        events.append("second-final")
        yield (True, "second")

    executions = [LLMUsableExecution(first()), LLMUsableExecution(second())]
    await run_llm_usable_executions(executions)

    assert events.index("second-prepare") < events.index("first-final")
    assert events.index("first-final") < events.index("second-final")
    assert [execution.result for execution in executions] == [
        (True, "first"),
        (True, "second"),
    ]


@pytest.mark.asyncio
async def test_run_tool_call_runs_concurrently_and_appends_in_call_order() -> None:
    events: list[str] = []

    class SlowTool(BaseTool):
        tool_name = "slow"
        tool_description = "slow"

        async def execute(self) -> tuple[bool, str]:
            events.append("slow-start")
            await asyncio.sleep(0.02)
            events.append("slow-done")
            return True, "slow"

    class FastTool(BaseTool):
        tool_name = "fast"
        tool_description = "fast"

        async def execute(self) -> tuple[bool, str]:
            events.append("fast-start")
            events.append("fast-done")
            return True, "fast"

    registry = ToolRegistry()
    registry.register(SlowTool)
    registry.register(FastTool)
    response = _FakeResponse()

    result = await run_tool_call(
        calls=[
            ToolCall(id="1", name="tool-slow", args={}),
            ToolCall(id="2", name="tool-fast", args={}),
        ],
        response=response,
        usable_map=registry,
        trigger_msg=SimpleNamespace(message_id="m1"),
        plugin=MagicMock(),
        stream_id="s1",
    )

    assert result == [(True, True), (True, True)]
    assert events.index("fast-done") < events.index("slow-done")
    assert [payload.content[0].value for payload in response.payloads] == [
        "slow",
        "fast",
    ]
