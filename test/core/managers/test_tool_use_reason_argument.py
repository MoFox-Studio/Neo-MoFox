"""测试 ToolUse 对 reason 参数的处理逻辑。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.components.base.tool import BaseTool
from src.core.managers.tool_manager.tool_use import ToolUse


class ReasonAwareTool(BaseTool):
    """显式声明 reason 参数的测试工具。"""

    tool_name = "reason_aware"
    tool_description = "reason-aware tool"

    async def execute(self, query: str, reason: str) -> tuple[bool, str]:
        """返回 query 与 reason，用于断言参数是否保留。"""
        return True, f"{query}:{reason}"


class PlainTool(BaseTool):
    """不声明 reason 参数的测试工具。"""

    tool_name = "plain_tool"
    tool_description = "plain tool"

    async def execute(self, query: str) -> tuple[bool, str]:
        """仅接收 query 参数。"""
        return True, query


@pytest.mark.asyncio
async def test_execute_tool_keeps_declared_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """当工具 execute 声明 reason 时，ToolUse 不应剥离该参数。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:tool:reason_aware":
                return ReasonAwareTool
            return None

    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(),
    )

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:reason_aware",
        MagicMock(),
        MagicMock(),
        query="weather",
        reason="context needed",
    )

    assert ok is True
    assert result == "weather:context needed"


@pytest.mark.asyncio
async def test_execute_tool_strips_auto_reason_for_plain_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当工具 execute 未声明 reason 时，ToolUse 应剥离自动注入参数。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:tool:plain_tool":
                return PlainTool
            return None

    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(),
    )

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:plain_tool",
        MagicMock(),
        MagicMock(),
        query="weather",
        reason="auto injected",
    )

    assert ok is True
    assert result == "weather"
