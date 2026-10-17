"""agent_api 模块测试。

agent_api 是 AgentManager 的薄封装，测试聚焦于参数校验与委托行为。
"""

from __future__ import annotations

from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.plugin_system.api import agent_api
from src.app.plugin_system.types import ChatType
from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool


class MockPrivateTool(BaseTool):
    """测试用私有工具。"""

    tool_name = "mock_tool"
    tool_description = "Mock tool for testing"

    async def execute(self, query: str) -> tuple[bool, str]:
        """执行模拟查询。"""
        return True, f"mock:{query}"


class MockAgent(BaseAgent):
    """测试用 Agent。"""

    agent_name = "mock_agent"
    agent_description = "Mock agent for testing"
    chatter_allow = ["demo_chatter"]
    chat_type = ChatType.PRIVATE
    associated_platforms = ["test_platform"]
    associated_types = ["text"]
    dependencies = []
    usables = [MockPrivateTool]

    async def execute(
        self,
        task: Annotated[str, "任务描述"],
    ) -> tuple[bool, str]:
        """执行 Agent 任务。"""
        return True, f"agent:{task}"


def _patch_manager(monkeypatch: pytest.MonkeyPatch, manager: MagicMock) -> None:
    """将 agent_api 的管理器入口替换为 mock。"""
    monkeypatch.setattr(agent_api, "_get_agent_manager", lambda: manager)


def test_get_all_agents_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_agents 应委托给管理器。"""
    manager = MagicMock()
    manager.get_all_agents.return_value = {"demo:agent:demo": MockAgent}
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_all_agents()

    assert result == {"demo:agent:demo": MockAgent}
    manager.get_all_agents.assert_called_once_with()


def test_get_agents_for_plugin_requires_name() -> None:
    """plugin_name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin_name 不能为空"):
        agent_api.get_agents_for_plugin("")


def test_get_agents_for_plugin_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agents_for_plugin 应委托给管理器。"""
    manager = MagicMock()
    manager.get_agents_for_plugin.return_value = {"demo:agent:demo": MockAgent}
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_agents_for_plugin("demo_plugin")

    assert "demo_plugin:agent:demo" not in result
    manager.get_agents_for_plugin.assert_called_once_with("demo_plugin")


@pytest.mark.asyncio
async def test_filter_agents_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_agents 应委托给管理器并透传筛选参数。"""
    manager = MagicMock()
    manager.filter_agents = AsyncMock(return_value=[MockAgent])
    _patch_manager(monkeypatch, manager)

    result = await agent_api.filter_agents(
        [MockAgent],
        stream_id="stream_123",
        chatter_name="my_chatter",
        chat_type=ChatType.PRIVATE,
        platform="test_platform",
    )

    assert result == [MockAgent]
    manager.filter_agents.assert_awaited_once_with(
        [MockAgent],
        stream_id="stream_123",
        chatter_name="my_chatter",
        chatter_signature="",
        chat_type=ChatType.PRIVATE,
        platform="test_platform",
    )


def test_get_agent_class_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_class("")


def test_get_agent_class_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_class 应委托给管理器。"""
    manager = MagicMock()
    manager.get_agent_class.return_value = MockAgent
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_agent_class("demo:agent:demo")

    assert result is MockAgent
    manager.get_agent_class.assert_called_once_with("demo:agent:demo")


def test_get_agent_schema_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_schema("")


def test_get_agent_schema_returns_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_schema 应委托给管理器并返回 schema。"""
    manager = MagicMock()
    schema = {"function": {"name": "agent-mock_agent"}}
    manager.get_agent_schema.return_value = schema
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_agent_schema("demo:agent:demo")

    assert result == schema
    manager.get_agent_schema.assert_called_once_with("demo:agent:demo")


def test_get_agent_schemas_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_schemas 应委托给管理器。"""
    manager = MagicMock()
    manager.get_agent_schemas.return_value = [{"function": {"name": "agent-mock_agent"}}]
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_agent_schemas([MockAgent])

    assert isinstance(result, list)
    assert len(result) == 1
    manager.get_agent_schemas.assert_called_once_with([MockAgent])


@pytest.mark.asyncio
async def test_execute_agent_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    with pytest.raises(ValueError, match="signature 不能为空"):
        await agent_api.execute_agent("", mock_plugin, "stream_123")


@pytest.mark.asyncio
async def test_execute_agent_requires_plugin() -> None:
    """plugin 为 None 时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin 不能为空"):
        await agent_api.execute_agent("demo:agent:demo", None, "stream_123")  # type: ignore


@pytest.mark.asyncio
async def test_execute_agent_requires_stream_id() -> None:
    """stream_id 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    with pytest.raises(ValueError, match="stream_id 不能为空"):
        await agent_api.execute_agent("demo:agent:demo", mock_plugin, "")


@pytest.mark.asyncio
async def test_execute_agent_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_agent 应委托给管理器。"""
    mock_plugin = MagicMock()
    manager = MagicMock()
    manager.execute_agent = AsyncMock(return_value=(True, "success"))
    _patch_manager(monkeypatch, manager)

    success, result = await agent_api.execute_agent(
        "demo:agent:demo",
        mock_plugin,
        "stream_123",
        task="test_task",
    )

    assert success is True
    assert result == "success"
    manager.execute_agent.assert_awaited_once_with(
        signature="demo:agent:demo",
        plugin=mock_plugin,
        stream_id="stream_123",
        task="test_task",
    )


def test_get_agent_usables_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_usables("")


def test_get_agent_usables_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_usables 应委托给管理器。"""
    manager = MagicMock()
    manager.get_agent_usables.return_value = [MockPrivateTool]
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_agent_usables("demo:agent:demo")

    assert isinstance(result, list)
    assert result[0] is MockPrivateTool
    manager.get_agent_usables.assert_called_once_with("demo:agent:demo")


def test_get_agent_usable_schemas_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_usable_schemas("")


def test_get_agent_usable_schemas_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_usable_schemas 应委托给管理器。"""
    manager = MagicMock()
    manager.get_agent_usable_schemas.return_value = [
        {"function": {"name": "tool-mock_tool"}}
    ]
    _patch_manager(monkeypatch, manager)

    result = agent_api.get_agent_usable_schemas("demo:agent:demo")

    assert isinstance(result, list)
    assert result[0]["function"]["name"] == "tool-mock_tool"
    manager.get_agent_usable_schemas.assert_called_once_with("demo:agent:demo")


@pytest.mark.asyncio
async def test_execute_agent_usable_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    with pytest.raises(ValueError, match="signature 不能为空"):
        await agent_api.execute_agent_usable("", mock_plugin, "stream_123", "tool")


@pytest.mark.asyncio
async def test_execute_agent_usable_requires_plugin() -> None:
    """plugin 为 None 时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin 不能为空"):
        await agent_api.execute_agent_usable(
            "demo:agent:demo", None, "stream_123", "tool"  # type: ignore
        )


@pytest.mark.asyncio
async def test_execute_agent_usable_requires_stream_id() -> None:
    """stream_id 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    with pytest.raises(ValueError, match="stream_id 不能为空"):
        await agent_api.execute_agent_usable(
            "demo:agent:demo", mock_plugin, "", "tool"
        )


@pytest.mark.asyncio
async def test_execute_agent_usable_requires_usable_name() -> None:
    """usable_name 为空时应抛出 ValueError。"""
    mock_plugin = MagicMock()
    with pytest.raises(ValueError, match="usable_name 不能为空"):
        await agent_api.execute_agent_usable(
            "demo:agent:demo", mock_plugin, "stream_123", ""
        )


@pytest.mark.asyncio
async def test_execute_agent_usable_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_agent_usable 应委托给管理器。"""
    mock_plugin = MagicMock()
    manager = MagicMock()
    manager.execute_agent_usable = AsyncMock(return_value=(True, "usable_result"))
    _patch_manager(monkeypatch, manager)

    success, result = await agent_api.execute_agent_usable(
        "demo:agent:demo",
        mock_plugin,
        "stream_123",
        "mock_tool",
        query="test_query",
    )

    assert success is True
    assert result == "usable_result"
    manager.execute_agent_usable.assert_awaited_once_with(
        signature="demo:agent:demo",
        plugin=mock_plugin,
        stream_id="stream_123",
        usable_name="mock_tool",
        query="test_query",
    )