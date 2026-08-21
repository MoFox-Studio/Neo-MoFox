"""ToolManager 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.components.base.tool import BaseTool
from src.core.components.types import ComponentType
from src.core.managers.tool_manager import ToolManager, get_tool_manager


class MockTool(BaseTool):
    """测试用 Tool。"""

    name = "mock_tool"
    description = "Mock tool for testing"

    async def execute(self, query: str) -> tuple[bool, str]:
        """执行模拟查询。"""
        return True, f"mock:{query}"


def _patch_registry(monkeypatch: pytest.MonkeyPatch, registry: MagicMock) -> None:
    monkeypatch.setattr(
        "src.core.managers.tool_manager.manager.get_global_registry",
        lambda: registry,
    )


def test_get_tool_manager_singleton() -> None:
    """get_tool_manager 应返回同一实例。"""
    assert get_tool_manager() is get_tool_manager()


def test_get_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_tools 应委托给注册表。"""
    registry = MagicMock()
    registry.get_by_type.return_value = {"plugin:tool:mock": MockTool}
    _patch_registry(monkeypatch, registry)

    manager = ToolManager()
    result = manager.get_all_tools()

    assert result == {"plugin:tool:mock": MockTool}
    registry.get_by_type.assert_called_once_with(ComponentType.TOOL)


def test_get_tools_for_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tools_for_plugin 应委托给注册表。"""
    registry = MagicMock()
    registry.get_by_plugin_and_type.return_value = {"mock": MockTool}
    _patch_registry(monkeypatch, registry)

    manager = ToolManager()
    result = manager.get_tools_for_plugin("demo_plugin")

    assert result == {"mock": MockTool}
    registry.get_by_plugin_and_type.assert_called_once_with(
        "demo_plugin", ComponentType.TOOL
    )


def test_get_tool_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tool_class 应委托给注册表。"""
    registry = MagicMock()
    registry.get.return_value = MockTool
    _patch_registry(monkeypatch, registry)

    manager = ToolManager()
    assert manager.get_tool_class("plugin:tool:mock") is MockTool


def test_get_tool_schema_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tool_schema 应生成并缓存 schema。"""
    registry = MagicMock()
    registry.get.return_value = MockTool
    _patch_registry(monkeypatch, registry)

    manager = ToolManager()
    schema = manager.get_tool_schema("plugin:tool:mock")

    assert schema is not None
    assert schema["function"]["name"] == "tool-mock_tool"
    assert "plugin:tool:mock" in manager._schema_cache


def test_get_tool_schema_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tool_schema 未找到时返回 None。"""
    registry = MagicMock()
    registry.get.return_value = None
    _patch_registry(monkeypatch, registry)

    manager = ToolManager()
    assert manager.get_tool_schema("missing") is None


def test_get_tool_schemas() -> None:
    """get_tool_schemas 应返回组件类列表对应的 schema。"""
    manager = ToolManager()
    schemas = manager.get_tool_schemas([MockTool])

    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "tool-mock_tool"


@pytest.mark.asyncio
async def test_filter_tools_empty_returns_empty() -> None:
    """空输入直接返回空列表。"""
    manager = ToolManager()
    with patch("src.core.managers.get_stream_manager") as mock_sm:
        result = await manager.filter_tools([])
    assert result == []
    mock_sm.assert_not_called()


@pytest.mark.asyncio
async def test_filter_tools_publishes_event() -> None:
    """筛选前应发布 BEFORE_TOOL_FILTER 事件。"""
    from src.core.components.types import EventType
    from src.kernel.event import EventDecision, get_event_bus

    manager = ToolManager()
    event_bus = get_event_bus()
    received = {}

    async def _handler(event_name: str, params: dict) -> tuple[EventDecision, dict]:
        received.update(params)
        return EventDecision.SUCCESS, params

    unsubscribe = event_bus.subscribe(EventType.BEFORE_TOOL_FILTER, _handler)
    try:
        result = await manager.filter_tools(
            [MockTool], stream_id="stream_123", chatter_name="demo_chatter"
        )
    finally:
        unsubscribe()

    assert result == [MockTool]
    assert received["stream_id"] == "stream_123"
    assert received["chatter_name"] == "demo_chatter"
    assert received["component_classes"] == [MockTool]


def test_clear_schema_cache() -> None:
    """clear_schema_cache 应支持指定或全部清除。"""
    manager = ToolManager()
    manager._schema_cache["a"] = {}
    manager._schema_cache["b"] = {}

    manager.clear_schema_cache("a")
    assert "a" not in manager._schema_cache
    assert "b" in manager._schema_cache

    manager.clear_schema_cache()
    assert manager._schema_cache == {}