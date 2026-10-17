"""AgentManager 单元测试。"""

from __future__ import annotations

from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType, ComponentType
from src.core.managers.agent_manager import AgentManager, get_agent_manager


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
    usables = [MockPrivateTool]

    async def execute(
        self,
        task: Annotated[str, "任务描述"],
    ) -> tuple[bool, str]:
        """执行 Agent 任务。"""
        return True, f"agent:{task}"


def _patch_registry(monkeypatch: pytest.MonkeyPatch, registry: MagicMock) -> None:
    monkeypatch.setattr(
        "src.core.managers.agent_manager.get_global_registry",
        lambda: registry,
    )


def test_get_agent_manager_singleton() -> None:
    """get_agent_manager 应返回同一实例。"""
    assert get_agent_manager() is get_agent_manager()


def test_get_all_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_agents 应委托给注册表。"""
    registry = MagicMock()
    registry.get_by_type.return_value = {"plugin:agent:mock": MockAgent}
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    result = manager.get_all_agents()

    assert result == {"plugin:agent:mock": MockAgent}
    registry.get_by_type.assert_called_once_with(ComponentType.AGENT)


def test_get_agents_for_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agents_for_plugin 应委托给注册表。"""
    registry = MagicMock()
    registry.get_by_plugin_and_type.return_value = {"mock": MockAgent}
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    result = manager.get_agents_for_plugin("demo_plugin")

    assert result == {"mock": MockAgent}
    registry.get_by_plugin_and_type.assert_called_once_with(
        "demo_plugin", ComponentType.AGENT
    )


def test_get_agent_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_class 应委托给注册表。"""
    registry = MagicMock()
    registry.get.return_value = MockAgent
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    assert manager.get_agent_class("plugin:agent:mock") is MockAgent
    registry.get.assert_called_once_with("plugin:agent:mock")


def test_get_agent_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_schema 应返回 schema。"""
    registry = MagicMock()
    registry.get.return_value = MockAgent
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    schema = manager.get_agent_schema("plugin:agent:mock")

    assert schema is not None
    assert schema["function"]["name"] == "agent-mock_agent"


def test_get_agent_schema_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_schema 未找到时返回 None。"""
    registry = MagicMock()
    registry.get.return_value = None
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    assert manager.get_agent_schema("missing") is None


def test_get_agent_schemas() -> None:
    """get_agent_schemas 应返回组件类列表对应的 schema。"""
    manager = AgentManager()
    schemas = manager.get_agent_schemas([MockAgent])

    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "agent-mock_agent"


@pytest.mark.asyncio
async def test_filter_agents_empty_returns_empty() -> None:
    """空输入直接返回空列表。"""
    manager = AgentManager()
    with patch("src.core.managers.get_stream_manager") as mock_sm:
        result = await manager.filter_agents([])
    assert result == []
    mock_sm.assert_not_called()


@pytest.mark.asyncio
async def test_filter_agents_publishes_event() -> None:
    """筛选前应发布 BEFORE_AGENT_FILTER 事件。"""
    from src.core.components.types import EventType
    from src.kernel.event import EventDecision, get_event_bus

    manager = AgentManager()
    event_bus = get_event_bus()
    received = {}

    async def _handler(event_name: str, params: dict) -> tuple[EventDecision, dict]:
        received.update(params)
        return EventDecision.SUCCESS, params

    unsubscribe = event_bus.subscribe(EventType.BEFORE_AGENT_FILTER, _handler)
    try:
        with patch.object(
            manager,
            "_apply_agent_activation",
            new=AsyncMock(return_value=[MockAgent]),
        ) as mock_activate:
            result = await manager.filter_agents(
                [MockAgent],
                stream_id="stream_123",
                chatter_name="demo_chatter",
                chat_type=ChatType.PRIVATE,
            )
    finally:
        unsubscribe()

    assert result == [MockAgent]
    assert received["stream_id"] == "stream_123"
    assert received["chatter_name"] == "demo_chatter"
    assert received["component_classes"] == [MockAgent]
    mock_activate.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_agent_activation_removes_deactivated_agent() -> None:
    """go_activate 返回 False 的 Agent 应被剔除。"""

    class _DeactivatedAgent(BaseAgent):
        name = "deactivated_agent"
        description = "deactivated"
        _signature_ = "plugin:agent:deactivated_agent"
        associated_types = ["text"]

        async def execute(self) -> tuple[bool, str]:
            return True, "ok"

        async def go_activate(self) -> bool:
            return False

    manager = AgentManager()

    mock_stream = MagicMock()
    mock_stream.stream_id = "stream_123"
    mock_stream.context = MagicMock()
    mock_stream.context.current_message = None

    plugin = MagicMock()
    plugin.plugin_name = "plugin"

    with patch("src.core.managers.get_stream_manager") as mock_sm, patch(
        "src.core.managers.get_plugin_manager"
    ) as mock_pm:
        mock_sm.return_value.get_or_create_stream = AsyncMock(return_value=mock_stream)
        mock_pm.return_value.get_plugin.return_value = plugin

        result = await manager._apply_agent_activation(
            [_DeactivatedAgent], stream_id="stream_123"
        )

    assert _DeactivatedAgent not in result


@pytest.mark.asyncio
async def test_execute_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_agent 应创建实例并执行。"""
    registry = MagicMock()
    registry.get.return_value = MockAgent
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    plugin = MagicMock()
    success, result = await manager.execute_agent(
        "plugin:agent:mock", plugin, "stream_123", task="hello"
    )

    assert success is True
    assert result == "agent:hello"


@pytest.mark.asyncio
async def test_execute_agent_not_found() -> None:
    """execute_agent 未找到类时抛出 ValueError。"""
    manager = AgentManager()
    with patch.object(manager, "get_agent_class", return_value=None):
        with pytest.raises(ValueError, match="Agent 类未找到"):
            await manager.execute_agent("missing", MagicMock(), "stream_123")


@pytest.mark.asyncio
async def test_execute_agent_raises_runtime_error() -> None:
    """execute_agent 执行失败时抛出 RuntimeError。"""

    class _FailAgent(BaseAgent):
        agent_name = "fail_agent"
        associated_types = ["text"]

        async def execute(self) -> tuple[bool, str]:
            raise ValueError("boom")

    manager = AgentManager()
    with patch.object(manager, "get_agent_class", return_value=_FailAgent):
        with pytest.raises(RuntimeError, match="Agent 执行失败"):
            await manager.execute_agent("plugin:agent:fail", MagicMock(), "stream_123")


def test_get_agent_usables(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_usables 应返回私有 usables。"""
    registry = MagicMock()
    registry.get.return_value = MockAgent
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    usables = manager.get_agent_usables("plugin:agent:mock")

    assert usables == [MockPrivateTool]


def test_get_agent_usables_not_found() -> None:
    """get_agent_usables 未找到类时返回空列表。"""
    manager = AgentManager()
    with patch.object(manager, "get_agent_class", return_value=None):
        assert manager.get_agent_usables("missing") == []


def test_get_agent_usable_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_usable_schemas 应返回私有 usables 的 schema。"""
    registry = MagicMock()
    registry.get.return_value = MockAgent
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    schemas = manager.get_agent_usable_schemas("plugin:agent:mock")

    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "tool-mock_tool"


@pytest.mark.asyncio
async def test_execute_agent_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_agent_usable 应执行 Agent 私有 usable。"""
    registry = MagicMock()
    registry.get.return_value = MockAgent
    _patch_registry(monkeypatch, registry)

    manager = AgentManager()
    plugin = MagicMock()
    success, result = await manager.execute_agent_usable(
        "plugin:agent:mock", plugin, "stream_123", "mock_tool", query="hi"
    )

    assert success is True
    assert result == "mock:hi"