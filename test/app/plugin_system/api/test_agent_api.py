"""agent_api 模块测试。"""

from __future__ import annotations

from typing import Annotated
from unittest.mock import MagicMock

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
    associated_types = []
    dependencies = []
    usables = [MockPrivateTool]

    async def execute(
        self,
        task: Annotated[str, "任务描述"],
    ) -> tuple[bool, str]:
        """执行 Agent 任务。"""
        return True, f"agent:{task}"


def test_get_all_agents_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_agents 应返回字典。"""

    class _FakeRegistry:
        def get_by_type(self, component_type):
            return {"demo:agent:demo": MockAgent}

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_all_agents()

    assert isinstance(result, dict)
    assert "demo:agent:demo" in result


def test_get_agents_for_plugin_requires_name() -> None:
    """plugin_name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin_name 不能为空"):
        agent_api.get_agents_for_plugin("")


def test_get_agents_for_plugin_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agents_for_plugin 应委托给 Registry。"""

    class _FakeRegistry:
        def get_by_plugin_and_type(self, plugin_name: str, component_type):
            return {f"{plugin_name}:agent:demo": MockAgent}

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agents_for_plugin("demo_plugin")

    assert "demo_plugin:agent:demo" in result


def test_get_agents_for_chat_filters_by_chat_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_agents_for_chat 应根据 chat_type 过滤。"""

    class GroupAgent(BaseAgent):
        agent_name = "group_agent"
        chat_type = ChatType.GROUP

        async def execute(self):
            return True, "ok"

    class PrivateAgent(BaseAgent):
        agent_name = "private_agent"
        chat_type = ChatType.PRIVATE

        async def execute(self):
            return True, "ok"

    class _FakeRegistry:
        def get_by_type(self, component_type):
            return {
                "plugin:agent:group": GroupAgent,
                "plugin:agent:private": PrivateAgent,
            }

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agents_for_chat(chat_type=ChatType.PRIVATE)

    assert len(result) == 1
    assert result[0] is PrivateAgent


def test_get_agents_for_chat_filters_by_chatter_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_agents_for_chat 应根据 chatter_allow 过滤。"""

    class AllowedAgent(BaseAgent):
        agent_name = "allowed_agent"
        chatter_allow = ["my_chatter"]
        chat_type = ChatType.ALL

        async def execute(self):
            return True, "ok"

    class NotAllowedAgent(BaseAgent):
        agent_name = "not_allowed_agent"
        chatter_allow = ["other_chatter"]
        chat_type = ChatType.ALL

        async def execute(self):
            return True, "ok"

    class _FakeRegistry:
        def get_by_type(self, component_type):
            return {
                "plugin:agent:allowed": AllowedAgent,
                "plugin:agent:not_allowed": NotAllowedAgent,
            }

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agents_for_chat(chatter_name="my_chatter")

    assert len(result) == 1
    assert result[0] is AllowedAgent


def test_get_agents_for_chat_filters_by_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_agents_for_chat 应根据 platform 过滤。"""

    class PlatformAgent(BaseAgent):
        agent_name = "platform_agent"
        associated_platforms = ["test_platform"]
        chat_type = ChatType.ALL

        async def execute(self):
            return True, "ok"

    class OtherAgent(BaseAgent):
        agent_name = "other_agent"
        associated_platforms = ["other_platform"]
        chat_type = ChatType.ALL

        async def execute(self):
            return True, "ok"

    class _FakeRegistry:
        def get_by_type(self, component_type):
            return {
                "plugin:agent:platform": PlatformAgent,
                "plugin:agent:other": OtherAgent,
            }

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agents_for_chat(platform="test_platform")

    assert len(result) == 1
    assert result[0] is PlatformAgent


def test_get_agent_class_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_class("")


def test_get_agent_class_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_class 应委托给 Registry。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:agent:demo":
                return MockAgent
            return None

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agent_class("demo:agent:demo")

    assert result is MockAgent


def test_get_agent_schema_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_schema("")


def test_get_agent_schema_returns_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_schema 应返回 schema。"""

    class _FakeRegistry:
        def get(self, signature: str):
            if signature == "demo:agent:demo":
                return MockAgent
            return None

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agent_schema("demo:agent:demo")

    assert result is not None
    assert "function" in result
    assert result["function"]["name"] == "agent-mock_agent"


def test_get_agent_schemas_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_schemas 应返回 schema 列表。"""

    class _FakeRegistry:
        def get_by_type(self, component_type):
            return {
                "plugin:agent:mock": MockAgent,
            }

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agent_schemas(chat_type=ChatType.PRIVATE)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["function"]["name"] == "agent-mock_agent"


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
async def test_execute_agent_raises_if_not_found() -> None:
    """Agent 未找到时应抛出 ValueError。"""
    mock_plugin = MagicMock()

    class _FakeRegistry:
        def get(self, signature: str):
            return None

    import src.app.plugin_system.api.agent_api as module

    original_func = module.get_global_registry

    try:
        module.get_global_registry = lambda: _FakeRegistry()

        with pytest.raises(ValueError, match="Agent 类未找到"):
            await agent_api.execute_agent("demo:agent:demo", mock_plugin, "stream_123")

    finally:
        module.get_global_registry = original_func


@pytest.mark.asyncio
async def test_execute_agent_calls_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute_agent 应调用 Agent 的 execute 方法。"""
    mock_plugin = MagicMock()

    executed_kwargs = {}

    class TestAgent(BaseAgent):
        agent_name = "test_agent"

        async def execute(self, **kwargs):
            nonlocal executed_kwargs
            executed_kwargs = kwargs
            return True, "success"

    class _FakeRegistry:
        def get(self, signature: str):
            return TestAgent

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    success, result = await agent_api.execute_agent(
        "demo:agent:test",
        mock_plugin,
        "stream_123",
        task="test_task",
    )

    assert success is True
    assert result == "success"
    assert executed_kwargs["task"] == "test_task"


def test_get_agent_usables_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_usables("")


def test_get_agent_usables_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_agent_usables 应返回 usables 列表。"""

    class _FakeRegistry:
        def get(self, signature: str):
            return MockAgent

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agent_usables("demo:agent:demo")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] is MockPrivateTool


def test_get_agent_usable_schemas_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        agent_api.get_agent_usable_schemas("")


def test_get_agent_usable_schemas_returns_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_agent_usable_schemas 应返回 schemas 列表。"""

    class _FakeRegistry:
        def get(self, signature: str):
            return MockAgent

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    result = agent_api.get_agent_usable_schemas("demo:agent:demo")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["function"]["name"] == "tool-mock_tool"


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
async def test_execute_agent_usable_calls_execute_local_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_agent_usable 应调用 Agent 的 execute_local_usable 方法。"""
    mock_plugin = MagicMock()

    called_with = {}

    class TestAgent(BaseAgent):
        agent_name = "test_agent"

        async def execute(self):
            return True, "ok"

        async def execute_local_usable(self, usable_name: str, **kwargs):
            nonlocal called_with
            called_with = {"usable_name": usable_name, **kwargs}
            return True, "usable_result"

    class _FakeRegistry:
        def get(self, signature: str):
            return TestAgent

    monkeypatch.setattr(agent_api, "get_global_registry", lambda: _FakeRegistry())

    success, result = await agent_api.execute_agent_usable(
        "demo:agent:test",
        mock_plugin,
        "stream_123",
        "mock_tool",
        query="test_query",
    )

    assert success is True
    assert result == "usable_result"
    assert called_with["usable_name"] == "mock_tool"
    assert called_with["query"] == "test_query"
