"""action_api 模块测试。

action_api 是 ActionManager 的薄封装，测试聚焦于参数校验与委托行为。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.plugin_system.api import action_api
from src.app.plugin_system.types import ChatType
from src.core.components.base.action import BaseAction


class MockAction(BaseAction):
    """测试用 Action。"""

    name = "mock_action"
    description = "Mock action for testing"
    associated_types = ["text"]

    async def execute(self, content: str) -> tuple[bool, str]:
        """执行模拟动作。"""
        return True, f"action:{content}"


def _patch_manager(monkeypatch: pytest.MonkeyPatch, manager: MagicMock) -> None:
    monkeypatch.setattr(action_api, "_get_action_manager", lambda: manager)


def test_get_all_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_actions 应委托给管理器。"""
    manager = MagicMock()
    manager.get_all_actions.return_value = {"demo:action:demo": MockAction}
    _patch_manager(monkeypatch, manager)

    result = action_api.get_all_actions()

    assert result == {"demo:action:demo": MockAction}
    manager.get_all_actions.assert_called_once_with()


def test_get_actions_for_plugin_requires_name() -> None:
    """plugin_name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin_name 不能为空"):
        action_api.get_actions_for_plugin("")


def test_get_actions_for_plugin_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_actions_for_plugin 应委托给管理器。"""
    manager = MagicMock()
    manager.get_actions_for_plugin.return_value = {"demo": MockAction}
    _patch_manager(monkeypatch, manager)

    result = action_api.get_actions_for_plugin("demo_plugin")

    assert result == {"demo": MockAction}
    manager.get_actions_for_plugin.assert_called_once_with("demo_plugin")


@pytest.mark.asyncio
async def test_filter_actions_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_actions 应委托给管理器并透传筛选参数。"""
    manager = MagicMock()
    manager.filter_actions = AsyncMock(return_value=[MockAction])
    _patch_manager(monkeypatch, manager)

    result = await action_api.filter_actions(
        [MockAction],
        stream_id="stream_123",
        chatter_name="my_chatter",
        chat_type=ChatType.PRIVATE,
        platform="qq",
    )

    assert result == [MockAction]
    manager.filter_actions.assert_awaited_once_with(
        [MockAction],
        stream_id="stream_123",
        chatter_name="my_chatter",
        chatter_signature="",
        chat_type=ChatType.PRIVATE,
        platform="qq",
    )


def test_get_action_class_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        action_api.get_action_class("")


def test_get_action_class_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_action_class 应委托给管理器。"""
    manager = MagicMock()
    manager.get_action_class.return_value = MockAction
    _patch_manager(monkeypatch, manager)

    result = action_api.get_action_class("demo:action:demo")

    assert result is MockAction
    manager.get_action_class.assert_called_once_with("demo:action:demo")


def test_get_action_schema_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        action_api.get_action_schema("")


def test_get_action_schema_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_action_schema 应委托给管理器。"""
    manager = MagicMock()
    schema = {"function": {"name": "action-mock_action"}}
    manager.get_action_schema.return_value = schema
    _patch_manager(monkeypatch, manager)

    result = action_api.get_action_schema("demo:action:demo")

    assert result == schema
    manager.get_action_schema.assert_called_once_with("demo:action:demo")


def test_get_action_schemas_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_action_schemas 应委托给管理器。"""
    manager = MagicMock()
    manager.get_action_schemas.return_value = [{"function": {"name": "action-mock_action"}}]
    _patch_manager(monkeypatch, manager)

    result = action_api.get_action_schemas([MockAction])

    assert result == [{"function": {"name": "action-mock_action"}}]
    manager.get_action_schemas.assert_called_once_with([MockAction])


@pytest.mark.asyncio
async def test_execute_action_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    mock_message = MagicMock()
    with pytest.raises(ValueError, match="signature 不能为空"):
        await action_api.execute_action("", mock_plugin, mock_message)


@pytest.mark.asyncio
async def test_execute_action_requires_plugin() -> None:
    """plugin 为 None 时应抛出 ValueError。"""
    mock_message = MagicMock()
    with pytest.raises(ValueError, match="plugin 不能为空"):
        await action_api.execute_action("demo:action:demo", None, mock_message)  # type: ignore


@pytest.mark.asyncio
async def test_execute_action_requires_message() -> None:
    """message 为 None 时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    with pytest.raises(ValueError, match="message 不能为空"):
        await action_api.execute_action("demo:action:demo", mock_plugin, None)  # type: ignore


@pytest.mark.asyncio
async def test_execute_action_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_action 应委托给管理器。"""
    mock_plugin = MagicMock()
    mock_message = MagicMock()
    manager = MagicMock()
    manager.execute_action = AsyncMock(return_value=(True, "ok"))
    _patch_manager(monkeypatch, manager)

    success, result = await action_api.execute_action(
        "demo:action:demo", mock_plugin, mock_message, content="hi"
    )

    assert success is True
    assert result == "ok"
    manager.execute_action.assert_awaited_once_with(
        signature="demo:action:demo",
        plugin=mock_plugin,
        message=mock_message,
        content="hi",
    )


def test_clear_schema_cache_requires_signature() -> None:
    """空签名时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        action_api.clear_schema_cache("")


def test_clear_schema_cache_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """clear_schema_cache 应委托给管理器。"""
    manager = MagicMock()
    _patch_manager(monkeypatch, manager)

    action_api.clear_schema_cache("demo:action:demo")

    manager.clear_schema_cache.assert_called_once_with("demo:action:demo")