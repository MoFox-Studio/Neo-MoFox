"""tool_api 模块测试。

tool_api 是 ToolManager 的薄封装，测试聚焦于参数校验与委托行为。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.plugin_system.api import tool_api
from src.app.plugin_system.types import ChatType
from src.core.components.base.tool import BaseTool


class MockTool(BaseTool):
    """测试用 Tool。"""

    name = "mock_tool"
    description = "Mock tool for testing"

    async def execute(self, query: str) -> tuple[bool, str]:
        """执行模拟查询。"""
        return True, f"mock:{query}"


def _patch_manager(monkeypatch: pytest.MonkeyPatch, manager: MagicMock) -> None:
    monkeypatch.setattr(tool_api, "_get_tool_manager", lambda: manager)


def test_get_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_tools 应委托给管理器。"""
    manager = MagicMock()
    manager.get_all_tools.return_value = {"demo:tool:demo": MockTool}
    _patch_manager(monkeypatch, manager)

    result = tool_api.get_all_tools()

    assert result == {"demo:tool:demo": MockTool}
    manager.get_all_tools.assert_called_once_with()


def test_get_tools_for_plugin_requires_name() -> None:
    """plugin_name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin_name 不能为空"):
        tool_api.get_tools_for_plugin("")


def test_get_tools_for_plugin_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tools_for_plugin 应委托给管理器。"""
    manager = MagicMock()
    manager.get_tools_for_plugin.return_value = {"demo": MockTool}
    _patch_manager(monkeypatch, manager)

    result = tool_api.get_tools_for_plugin("demo_plugin")

    assert result == {"demo": MockTool}
    manager.get_tools_for_plugin.assert_called_once_with("demo_plugin")


@pytest.mark.asyncio
async def test_filter_tools_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_tools 应委托给管理器并透传筛选参数。"""
    manager = MagicMock()
    manager.filter_tools = AsyncMock(return_value=[MockTool])
    _patch_manager(monkeypatch, manager)

    result = await tool_api.filter_tools(
        [MockTool],
        stream_id="stream_123",
        chatter_name="my_chatter",
        chat_type=ChatType.PRIVATE,
        platform="qq",
    )

    assert result == [MockTool]
    manager.filter_tools.assert_awaited_once_with(
        [MockTool],
        stream_id="stream_123",
        chatter_name="my_chatter",
        chatter_signature="",
        chat_type=ChatType.PRIVATE,
        platform="qq",
    )


def test_get_tool_class_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        tool_api.get_tool_class("")


def test_get_tool_class_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tool_class 应委托给管理器。"""
    manager = MagicMock()
    manager.get_tool_class.return_value = MockTool
    _patch_manager(monkeypatch, manager)

    result = tool_api.get_tool_class("demo:tool:demo")

    assert result is MockTool
    manager.get_tool_class.assert_called_once_with("demo:tool:demo")


def test_get_tool_schema_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        tool_api.get_tool_schema("")


def test_get_tool_schema_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tool_schema 应委托给管理器。"""
    manager = MagicMock()
    schema = {"function": {"name": "tool-mock_tool"}}
    manager.get_tool_schema.return_value = schema
    _patch_manager(monkeypatch, manager)

    result = tool_api.get_tool_schema("demo:tool:demo")

    assert result == schema
    manager.get_tool_schema.assert_called_once_with("demo:tool:demo")


def test_get_tool_schemas_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tool_schemas 应委托给管理器。"""
    manager = MagicMock()
    manager.get_tool_schemas.return_value = [{"function": {"name": "tool-mock_tool"}}]
    _patch_manager(monkeypatch, manager)

    result = tool_api.get_tool_schemas([MockTool])

    assert result == [{"function": {"name": "tool-mock_tool"}}]
    manager.get_tool_schemas.assert_called_once_with([MockTool])


@pytest.mark.asyncio
async def test_execute_tool_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    mock_message = MagicMock()
    with pytest.raises(ValueError, match="signature 不能为空"):
        await tool_api.execute_tool("", mock_plugin, mock_message)


@pytest.mark.asyncio
async def test_execute_tool_requires_plugin() -> None:
    """plugin 为 None 时应抛出 ValueError。"""
    mock_message = MagicMock()
    with pytest.raises(ValueError, match="plugin 不能为空"):
        await tool_api.execute_tool("demo:tool:demo", None, mock_message)  # type: ignore


@pytest.mark.asyncio
async def test_execute_tool_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_tool 应委托给 ToolUse 管理器。"""
    mock_plugin = MagicMock()
    mock_message = MagicMock()
    mock_tool_use = MagicMock()
    mock_tool_use.execute_tool = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(
        "src.core.managers.tool_manager.get_tool_use", lambda: mock_tool_use
    )

    success, result = await tool_api.execute_tool(
        "demo:tool:demo", mock_plugin, mock_message, query="hi"
    )

    assert success is True
    assert result == "ok"
    mock_tool_use.execute_tool.assert_awaited_once_with(
        signature="demo:tool:demo",
        plugin=mock_plugin,
        message=mock_message,
        query="hi",
    )


def test_clear_schema_cache_requires_signature() -> None:
    """空签名时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        tool_api.clear_schema_cache("")


def test_clear_schema_cache_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """clear_schema_cache 应委托给管理器。"""
    manager = MagicMock()
    _patch_manager(monkeypatch, manager)

    tool_api.clear_schema_cache("demo:tool:demo")

    manager.clear_schema_cache.assert_called_once_with("demo:tool:demo")