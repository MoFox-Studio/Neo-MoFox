"""ToolUse telemetry 测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
from unittest.mock import MagicMock

import pytest

from src.core.components.base.tool import BaseTool
from src.core.managers.tool_manager.tool_use import ToolUse
from src.kernel.telemetry import (
    TelemetryConfig,
    close_telemetry_db,
    get_telemetry_collector,
    init_telemetry,
)


class _TelemetryTool(BaseTool):
    """测试用成功工具。"""

    tool_name = "telemetry_tool"
    tool_description = "telemetry tool"

    async def execute(self, query: str) -> tuple[bool, str]:
        """返回查询参数。"""
        return True, query


class _FailingTool(BaseTool):
    """测试用失败工具。"""

    tool_name = "failing_tool"
    tool_description = "failing tool"

    async def execute(self, query: str) -> tuple[bool, str]:
        """直接抛出异常。"""
        raise ValueError(f"bad query: {query}")


@pytest.fixture(autouse=True)
async def _setup_telemetry(tmp_path) -> AsyncGenerator[None, None]:
    """初始化 telemetry。"""
    await close_telemetry_db()
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_tool_events=True,
        )
    )
    yield
    await close_telemetry_db()


def _decode_attributes(row: dict[str, object]) -> dict[str, object]:
    """解析 attributes_json。"""
    raw = row.get("attributes_json")
    if not isinstance(raw, str) or not raw:
        return {}
    return json.loads(raw)


@pytest.mark.asyncio
async def test_execute_tool_records_success_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功执行应记录 tool_invoked 事件。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:tool:telemetry_tool":
                return _TelemetryTool
            return None

    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(),
    )

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:telemetry_tool",
        MagicMock(),
        MagicMock(),
        query="weather",
    )

    assert ok is True
    assert result == "weather"

    rows = await get_telemetry_collector().get_recent(domain="tool", limit=10)
    tool_rows = [row for row in rows if row["event_name"] == "tool_invoked"]
    assert tool_rows
    attributes = _decode_attributes(tool_rows[0])
    assert attributes["tool_name"] == "telemetry_tool"
    assert attributes["status"] == "success"
    assert attributes["cache_hit"] is False


@pytest.mark.asyncio
async def test_execute_tool_records_cache_hit_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存命中应记录 cache_hit=true 的事件。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:tool:telemetry_tool":
                return _TelemetryTool
            return None

    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(),
    )

    manager = ToolUse()
    manager.enable_caching(True)
    await manager.execute_tool(
        "demo:tool:telemetry_tool",
        MagicMock(),
        MagicMock(),
        query="weather",
    )
    await manager.execute_tool(
        "demo:tool:telemetry_tool",
        MagicMock(),
        MagicMock(),
        query="weather",
    )

    rows = await get_telemetry_collector().get_recent(domain="tool", limit=10)
    tool_rows = [row for row in rows if row["event_name"] == "tool_invoked"]
    assert len(tool_rows) >= 2
    assert any(_decode_attributes(row).get("cache_hit") is True for row in tool_rows)


@pytest.mark.asyncio
async def test_execute_tool_records_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行异常应记录 error 事件。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:tool:failing_tool":
                return _FailingTool
            return None

    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(),
    )

    manager = ToolUse()
    with pytest.raises(RuntimeError):
        await manager.execute_tool(
            "demo:tool:failing_tool",
            MagicMock(),
            MagicMock(),
            query="weather",
        )

    rows = await get_telemetry_collector().get_recent(domain="tool", limit=10)
    tool_rows = [row for row in rows if row["event_name"] == "tool_invoked"]
    assert tool_rows
    attributes = _decode_attributes(tool_rows[0])
    assert attributes["tool_name"] == "failing_tool"
    assert attributes["status"] == "error"
    assert attributes["error_type"] == "ValueError"