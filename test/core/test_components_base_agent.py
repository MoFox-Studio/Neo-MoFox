"""测试 src.core.components.base.agent 模块。"""

from typing import cast
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType
from src.core.prompt.system_reminder import get_system_reminder_store, reset_system_reminder_store
from src.kernel.llm import LLMPayload, Text
from src.kernel.llm import ROLE


class PrivateTool(BaseTool):
    """用于测试 Agent 私有 usables 的 Tool。"""

    tool_name = "private_lookup"
    tool_description = "私有查询工具"

    async def execute(self, query: str) -> tuple[bool, str]:
        """执行私有查询。"""
        return True, f"private:{query}"


class ConcreteAgent(BaseAgent):
    """用于测试的具体 Agent。"""

    agent_name = "task_agent"
    agent_description = "Task agent for tests"
    chatter_allow = []
    chat_type = ChatType.ALL
    associated_platforms = []
    associated_types = []
    dependencies = []
    usables = [PrivateTool]

    async def execute(
        self,
        task: Annotated[str, "任务描述"],
    ) -> tuple[bool, str]:
        """执行 Agent 任务。

        Args:
            task: 任务描述
        """
        return True, f"agent:{task}"


class TestBaseAgent:
    """测试 BaseAgent 类。"""

    @pytest.fixture(autouse=True)
    def reset_class_attributes(self):
        """在每个测试前重置类属性。"""
        original_plugin_name = getattr(ConcreteAgent, "_plugin_", None)
        yield
        if original_plugin_name:
            ConcreteAgent._plugin_ = original_plugin_name
        elif hasattr(ConcreteAgent, "_plugin_"):
            delattr(ConcreteAgent, "_plugin_")

    def test_agent_initialization(self, mock_plugin):
        """测试 Agent 初始化。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        assert agent.stream_id == "stream_123"
        assert agent.plugin == mock_plugin
        assert agent.agent_name == "task_agent"

    def test_get_signature(self, mock_plugin):
        """测试 Agent 签名。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        assert agent.get_signature() is None

        ConcreteAgent._plugin_ = "my_plugin"
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        assert agent.get_signature() == "my_plugin:agent:task_agent"

    @pytest.mark.asyncio
    async def test_execute(self, mock_plugin):
        """测试 execute。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        success, result = await agent.execute("plan")
        assert success is True
        assert result == "agent:plan"

    def test_to_schema(self):
        """测试 schema 生成。"""
        schema = ConcreteAgent.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "agent:task_agent"
        assert schema["function"]["description"] == "Task agent for tests"
        assert "task" in schema["function"]["parameters"]["properties"]

    def test_get_local_usables(self):
        """测试获取私有 usables。"""
        usables = ConcreteAgent.get_local_usables()
        assert usables == [PrivateTool]

    def test_get_local_usable_schemas(self):
        """测试获取私有 usable schema。"""
        schemas = ConcreteAgent.get_local_usable_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "private_lookup"

    def test_create_llm_request(self, mock_plugin):
        """测试创建 LLMRequest。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        request = agent.create_llm_request(model_set=[], request_name="agent_test")
        assert request.request_name == "agent_test"
        assert request.model_set == []

    def test_create_llm_request_with_usables_true(self, mock_plugin):
        """测试创建 LLMRequest 时自动注入私有 usables。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        request = agent.create_llm_request(
            model_set=[],
            request_name="agent_test",
            with_usables=True,
        )

        assert len(request.payloads) == 1
        payload = request.payloads[0]
        assert payload.role == ROLE.TOOL
        assert payload.content == [PrivateTool]

    def test_create_llm_request_with_usables_false(self, mock_plugin):
        """测试创建 LLMRequest 时不自动注入私有 usables。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        request = agent.create_llm_request(
            model_set=[],
            request_name="agent_test",
            with_usables=False,
        )

        assert request.payloads == []

    def test_create_llm_request_with_reminder(self, mock_plugin):
        """测试创建 LLMRequest 时可自动登记 system reminder。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        reset_system_reminder_store()
        store = get_system_reminder_store()
        store.set("actor", "goal", "先给结论")

        request = agent.create_llm_request(
            model_set=[],
            request_name="agent_test",
            with_reminder="actor",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text("sys")))
        request.add_payload(LLMPayload(ROLE.USER, Text("hello")))

        assert request.payloads[0].role == ROLE.SYSTEM
        assert request.payloads[1].role == ROLE.USER
        assert cast(Text, request.payloads[1].content[0]).text == "<system_reminder>\n[goal]\n先给结论\n</system_reminder>"
        assert cast(Text, request.payloads[1].content[1]).text == "hello"

        reset_system_reminder_store()

    @pytest.mark.asyncio
    async def test_execute_local_usable_success(self, mock_plugin):
        """测试执行私有 usable 成功。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        success, result = await agent.execute_local_usable(
            "private_lookup",
            query="weather",
        )
        assert success is True
        assert result == "private:weather"

    @pytest.mark.asyncio
    async def test_execute_local_usable_success_with_prefixed_name(self, mock_plugin):
        """测试执行私有 usable（带 schema 前缀名）成功。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        success, result = await agent.execute_local_usable(
            "tool-private_lookup",
            query="weather",
        )
        assert success is True
        assert result == "private:weather"

    @pytest.mark.asyncio
    async def test_execute_local_usable_not_found(self, mock_plugin):
        """测试执行不存在的私有 usable。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        with pytest.raises(ValueError, match="私有 usable 不存在"):
            await agent.execute_local_usable("non_existing_tool")

    @pytest.mark.asyncio
    async def test_execute_local_usable_ignores_global_registry(self, mock_plugin):
        """测试 Agent 不读取全局注册表。"""
        agent = ConcreteAgent(stream_id="stream_123", plugin=mock_plugin)
        with patch("src.core.components.registry.get_global_registry") as mock_registry:
            with pytest.raises(ValueError, match="私有 usable 不存在"):
                await agent.execute_local_usable("global_tool")

        mock_registry.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_local_usable_action_path(self, mock_plugin):
        """测试私有 Action usable 路径。"""
        from src.core.components.base.action import BaseAction

        class PrivateAction(BaseAction):
            action_name = "private_action"
            action_description = "private action"

            async def execute(self, content: str) -> tuple[bool, str]:
                return True, f"action:{content}"

        class ActionAgent(ConcreteAgent):
            usables = [PrivateAction]

        mock_stream = MagicMock()
        mock_stream.context = MagicMock()
        mock_stream.context.current_message = None

        with patch("src.core.managers.stream_manager.get_stream_manager") as mock_sm:
            mock_sm.return_value.get_or_create_stream = AsyncMock(return_value=mock_stream)
            agent = ActionAgent(stream_id="stream_123", plugin=mock_plugin)
            success, result = await agent.execute_local_usable(
                "private_action",
                content="hello",
            )

        assert success is True
        assert result == "action:hello"
