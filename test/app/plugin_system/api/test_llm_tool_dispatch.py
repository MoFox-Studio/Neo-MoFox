"""LLM tool call 到组件 Manager 的路由回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.plugin_system.api import llm_api
from src.core.components.base.action import BaseAction
from src.core.components.base.tool import BaseTool
from src.kernel.llm import ToolCall, ToolRegistry


class _DispatchTool(BaseTool):
    name = "dispatch_tool"

    @classmethod
    def get_signature(cls) -> str:
        return "demo:tool:dispatch_tool"

    async def execute(self, text: str) -> tuple[bool, str]:
        return True, text


class _DispatchAction(BaseAction):
    name = "dispatch_action"

    @classmethod
    def get_signature(cls) -> str:
        return "demo:action:dispatch_action"

    async def execute(self, text: str) -> tuple[bool, str]:
        return True, text


class _Response:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    def add_payload(self, payload: object, position: object = None) -> None:
        _ = position
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_dispatch_llm_tool_calls_routes_tool_to_tool_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MagicMock()
    manager.execute_tool = AsyncMock(return_value=(True, "tool-result"))
    monkeypatch.setattr(
        "src.core.managers.get_tool_use", lambda: manager
    )

    registry = ToolRegistry()
    registry.register(_DispatchTool, name="tool-dispatch")
    response = _Response()

    result = await llm_api.dispatch_llm_tool_calls(
        calls=[ToolCall("call-tool", "tool-dispatch", {"text": "hello"})],
        response=response,
        usable_map=registry,
        trigger_msg=MagicMock(),
        plugin=MagicMock(),
    )

    assert result == [(True, True)]
    manager.execute_tool.assert_awaited_once()
    assert response.payloads


@pytest.mark.asyncio
async def test_dispatch_llm_tool_calls_routes_action_to_action_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MagicMock()
    manager.execute_action = AsyncMock(return_value=(True, "action-result"))
    monkeypatch.setattr(
        "src.core.managers.get_action_manager", lambda: manager
    )

    registry = ToolRegistry()
    registry.register(_DispatchAction, name="action-dispatch")
    response = _Response()

    result = await llm_api.dispatch_llm_tool_calls(
        calls=[ToolCall("call-action", "action-dispatch", {"text": "hello"})],
        response=response,
        usable_map=registry,
        trigger_msg=MagicMock(),
        plugin=MagicMock(),
    )

    assert result == [(True, True)]
    manager.execute_action.assert_awaited_once()
    assert response.payloads
