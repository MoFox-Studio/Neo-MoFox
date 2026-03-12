"""测试 src.core.components.base.chatter 模块。"""

import json
from datetime import datetime
from typing import AsyncGenerator, Generator, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.chatter import BaseChatter, ChatterResult, Failure, Success, Wait
from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType
from src.core.models.message import Message
from src.core.prompt.system_reminder import get_system_reminder_store, reset_system_reminder_store
from src.kernel.llm import LLMPayload, ROLE, Text


class ConcreteChatter(BaseChatter):
    """具体的 Chatter 实现用于测试。"""

    chatter_name = "test_chatter"
    chatter_description = "Test chatter"
    associated_platforms = []
    chatter_allow = []
    chat_type = ChatType.ALL

    async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
        """执行聊天器逻辑。"""
        if not unreads:
            yield Failure("没有新消息")
            return

        yield Wait(1.0)
        yield Success("处理完成", {"count": len(unreads)})


class TestChatterResultTypes:
    """测试 Chatter 结果类型。"""

    def test_wait_creation(self):
        """测试 Wait 创建。"""
        wait = Wait(time=5.0)
        assert wait.time == 5.0
        
        wait_no_time = Wait()
        assert wait_no_time.time is None

    def test_success_creation(self):
        """测试 Success 创建。"""
        success = Success("成功消息")
        assert success.message == "成功消息"
        assert success.data is None

    def test_success_with_data(self):
        """测试带数据的 Success。"""
        data = {"key": "value", "count": 5}
        success = Success("成功", data)
        assert success.message == "成功"
        assert success.data == data

    def test_failure_creation(self):
        """测试 Failure 创建。"""
        failure = Failure("错误消息")
        assert failure.error == "错误消息"
        assert failure.exception is None

    def test_failure_with_exception(self):
        """测试带异常的 Failure。"""
        exception = ValueError("测试异常")
        failure = Failure("错误", exception)
        assert failure.error == "错误"
        assert failure.exception == exception


class TestBaseChatter:
    """测试 BaseChatter 类。"""

    def test_chatter_initialization(self, mock_plugin):
        """测试 Chatter 初始化。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)
        assert chatter.stream_id == "stream_123"
        assert chatter.plugin == mock_plugin
        assert chatter.chatter_name == "test_chatter"
        assert chatter.chatter_description == "Test chatter"

    def test_get_signature(self, mock_plugin):
        """测试获取签名。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)
        assert chatter.get_signature() is None

        ConcreteChatter._plugin_ = "my_plugin"
        chatter2 = ConcreteChatter("stream_456", mock_plugin)
        assert chatter2.get_signature() == "my_plugin:chatter:test_chatter"

    def test_create_request_registers_system_reminder(self, mock_plugin):
        """测试 create_request 可登记 system reminder，且不会把 SYSTEM 挤到 USER 后面。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        reset_system_reminder_store()
        store = get_system_reminder_store()
        store.set("actor", "goal", "先给结论")

        with patch("src.core.config.get_model_config") as mock_model_config, patch(
            "src.core.config.get_core_config"
        ) as mock_core_config:
            mock_model_config.return_value.get_task.return_value = []
            mock_core_config.return_value.chat.max_context_size = 10

            request = chatter.create_request("actor", with_reminder="actor")

        request.add_payload(LLMPayload(ROLE.SYSTEM, Text("sys")))
        request.add_payload(LLMPayload(ROLE.USER, Text("hello")))

        assert request.payloads[0].role == ROLE.SYSTEM
        assert request.payloads[1].role == ROLE.USER
        assert cast(Text, request.payloads[1].content[0]).text == "<system_reminder>\n[goal]\n先给结论\n</system_reminder>"
        assert cast(Text, request.payloads[1].content[1]).text == "hello"

        reset_system_reminder_store()

    @pytest.mark.asyncio
    async def test_execute_with_messages(self, mock_plugin):
        """测试执行聊天器（有消息）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # 模拟消息
        mock_message = MagicMock()
        unreads = [mock_message]

        results = []
        async for result in chatter.execute(unreads):
            results.append(result)

        assert len(results) == 2
        assert isinstance(results[0], Wait)
        assert results[0].time == 1.0
        assert isinstance(results[1], Success)
        assert results[1].message == "处理完成"

    @pytest.mark.asyncio
    async def test_execute_without_messages(self, mock_plugin):
        """测试执行聊天器（无消息）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([]):
            results.append(result)

        assert len(results) == 1
        assert isinstance(results[0], Failure)
        assert results[0].error == "没有新消息"

    @pytest.mark.asyncio
    async def test_execute_with_multiple_messages(self, mock_plugin):
        """测试执行聊天器（多条消息）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # 创建多条消息
        unreads = [MagicMock() for _ in range(5)]

        results = []
        async for result in chatter.execute(unreads):
            results.append(result)

        assert len(results) == 2
        assert isinstance(results[1], Success)
        assert results[1].data == {"count": 5}

    @pytest.mark.asyncio
    async def test_modify_llm_usables_uses_owner_plugin_instance(self):
        """测试跨插件组件实例化时使用组件所属插件实例。"""

        class CrossPluginTool(BaseTool):
            tool_name = "cross_tool"
            tool_description = "cross tool"
            _signature_ = "plugin_b:tool:cross_tool"

            def __init__(self, plugin):
                super().__init__(plugin)
                if getattr(plugin, "plugin_name", "") != "plugin_b":
                    raise ValueError("plugin mismatch")

            async def execute(self, *args, **kwargs):
                return True, "ok"

        class CrossPluginChatter(BaseChatter):
            chatter_name = "cross_plugin_chatter"

            async def execute(self):
                if False:
                    yield Success("never")  # pragma: no cover

        chatter_plugin = MagicMock()
        chatter_plugin.plugin_name = "plugin_a"

        owner_plugin = MagicMock()
        owner_plugin.plugin_name = "plugin_b"

        chatter = CrossPluginChatter("stream_123", chatter_plugin)

        mock_stream = MagicMock()
        mock_stream.stream_id = "stream_123"
        mock_stream.context = MagicMock()
        mock_stream.context.current_message = None

        with patch("src.core.components.base.chatter.get_stream_manager") as mock_sm, patch(
            "src.core.components.base.chatter.get_plugin_manager"
        ) as mock_pm:
            mock_sm.return_value.get_or_create_stream = AsyncMock(return_value=mock_stream)
            mock_pm.return_value.get_plugin.return_value = owner_plugin

            result = await chatter.modify_llm_usables([CrossPluginTool])

        assert CrossPluginTool in result

    @pytest.mark.asyncio
    async def test_modify_llm_usables_filters_by_chatter_allow(self):
        """测试 modify_llm_usables 会按 chatter_allow 过滤组件。"""

        class AllowedTool(BaseTool):
            tool_name = "allowed_tool"
            tool_description = "allowed"
            chatter_allow = ["test_chatter"]

            async def execute(self, *args, **kwargs):
                return True, "ok"

        class RejectedTool(BaseTool):
            tool_name = "rejected_tool"
            tool_description = "rejected"
            chatter_allow = ["other_chatter"]

            async def execute(self, *args, **kwargs):
                return True, "ok"

        class OpenTool(BaseTool):
            tool_name = "open_tool"
            tool_description = "open"

            async def execute(self, *args, **kwargs):
                return True, "ok"

        chatter_plugin = MagicMock()
        chatter = ConcreteChatter("stream_123", chatter_plugin)

        mock_stream = MagicMock()
        mock_stream.stream_id = "stream_123"
        mock_stream.context = MagicMock()

        with patch("src.core.components.base.chatter.get_stream_manager") as mock_sm:
            mock_sm.return_value.get_or_create_stream = AsyncMock(return_value=mock_stream)

            result = await chatter.modify_llm_usables([AllowedTool, RejectedTool, OpenTool])

        assert AllowedTool in result
        assert OpenTool in result
        assert RejectedTool not in result

    @pytest.mark.asyncio
    async def test_exec_llm_usable_uses_owner_plugin_instance(self):
        """测试执行跨插件 Tool 时向管理器传入所属插件实例。"""

        class CrossPluginTool(BaseTool):
            tool_name = "cross_tool"
            tool_description = "cross tool"
            _signature_ = "plugin_b:tool:cross_tool"

            async def execute(self, *args, **kwargs):
                return True, "ok"

        class CrossPluginChatter(BaseChatter):
            chatter_name = "cross_plugin_chatter"

            async def execute(self):
                if False:
                    yield Success("never")  # pragma: no cover

        chatter_plugin = MagicMock()
        chatter_plugin.plugin_name = "plugin_a"
        owner_plugin = MagicMock()
        owner_plugin.plugin_name = "plugin_b"

        message = MagicMock()

        chatter = CrossPluginChatter("stream_123", chatter_plugin)

        with patch("src.core.components.base.chatter.get_plugin_manager") as mock_pm, patch(
            "src.core.components.base.chatter.get_tool_use"
        ) as mock_tool_use:
            mock_pm.return_value.get_plugin.return_value = owner_plugin
            mock_tool_use.return_value.execute_tool = AsyncMock(return_value=(True, "ok"))

            ok, payload = await chatter.exec_llm_usable(CrossPluginTool, message)

        assert ok is True
        assert payload == "ok"
        mock_tool_use.return_value.execute_tool.assert_awaited_once_with(
            "plugin_b:tool:cross_tool",
            owner_plugin,
            message,
        )

    @pytest.mark.asyncio
    async def test_exec_llm_usable_agent_without_global_managers(self):
        """测试执行 Agent 时不通过 Tool/Action 管理器。"""

        class LocalAgent(BaseAgent):
            agent_name = "local_agent"
            agent_description = "local agent"
            _signature_ = "plugin_b:agent:local_agent"

            async def execute(self, query: str) -> tuple[bool, str]:
                return True, f"agent:{query}"

        class CrossPluginChatter(BaseChatter):
            chatter_name = "cross_plugin_chatter"

            async def execute(self):
                if False:
                    yield Success("never")  # pragma: no cover

        chatter_plugin = MagicMock()
        chatter_plugin.plugin_name = "plugin_a"
        owner_plugin = MagicMock()
        owner_plugin.plugin_name = "plugin_b"
        message = MagicMock()

        chatter = CrossPluginChatter("stream_123", chatter_plugin)

        with patch("src.core.components.base.chatter.get_plugin_manager") as mock_pm, patch(
            "src.core.components.base.chatter.get_tool_use"
        ) as mock_tool_use, patch(
            "src.core.components.base.chatter.get_action_manager"
        ) as mock_action_manager:
            mock_pm.return_value.get_plugin.return_value = owner_plugin

            ok, payload = await chatter.exec_llm_usable(LocalAgent, message, query="demo")

        assert ok is True
        assert payload == "agent:demo"
        mock_tool_use.assert_not_called()
        mock_action_manager.assert_not_called()


class TestChatterAttributes:
    """测试 Chatter 类属性。"""

    def test_chatter_with_all_attributes(self, mock_plugin):
        """测试带有所有属性的聊天器。"""
        from src.core.components.types import ChatType

        class FullChatter(BaseChatter):
            chatter_name = "full_chatter"
            chatter_description = "Full chatter description"
            associated_platforms = ["telegram", "discord"]
            chatter_allow = ["chatter1", "chatter2"]
            chat_type = ChatType.GROUP
            dependencies = ["other_plugin:service:memory"]

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Success("done")

        chatter = FullChatter("stream_123", mock_plugin)
        assert chatter.chatter_name == "full_chatter"
        assert chatter.chatter_description == "Full chatter description"
        assert chatter.associated_platforms == ["telegram", "discord"]
        assert chatter.chatter_allow == ["chatter1", "chatter2"]
        assert chatter.chat_type == ChatType.GROUP
        assert chatter.dependencies == ["other_plugin:service:memory"]

    def test_different_chat_types(self, mock_plugin):
        """测试不同聊天类型。"""
        # 分别测试每种聊天类型
        class PrivateChatter(BaseChatter):
            chatter_name = "chatter_private"
            chat_type = ChatType.PRIVATE

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Success("done")

        class GroupChatter(BaseChatter):
            chatter_name = "chatter_group"
            chat_type = ChatType.GROUP

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Success("done")

        class DiscussChatter(BaseChatter):
            chatter_name = "chatter_discuss"
            chat_type = ChatType.DISCUSS

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Success("done")

        class AllChatter(BaseChatter):
            chatter_name = "chatter_all"
            chat_type = ChatType.ALL

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Success("done")

        # 测试每种类型
        assert PrivateChatter("stream_123", mock_plugin).chat_type == ChatType.PRIVATE
        assert GroupChatter("stream_123", mock_plugin).chat_type == ChatType.GROUP
        assert DiscussChatter("stream_123", mock_plugin).chat_type == ChatType.DISCUSS
        assert AllChatter("stream_123", mock_plugin).chat_type == ChatType.ALL


class TestChatterExecutePatterns:
    """测试 Chatter 执行模式。"""

    @pytest.mark.asyncio
    async def test_multiple_waits(self, mock_plugin):
        """测试多个 Wait。"""
        class MultiWaitChatter(BaseChatter):
            chatter_name = "multi_wait"

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Wait(time=1.0)
                yield Wait(time=2.0)
                yield Wait(time=3.0)
                yield Success("完成")

        chatter = MultiWaitChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([MagicMock()]):
            results.append(result)

        assert len(results) == 4
        assert all(isinstance(r, Wait) for r in results[:3])
        assert results[0].time == 1.0
        assert results[1].time == 2.0
        assert results[2].time == 3.0
        assert isinstance(results[3], Success)

    @pytest.mark.asyncio
    async def test_immediate_success(self, mock_plugin):
        """测试立即成功。"""
        class ImmediateSuccessChatter(BaseChatter):
            chatter_name = "immediate_success"

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Success("立即完成")

        chatter = ImmediateSuccessChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([MagicMock()]):
            results.append(result)

        assert len(results) == 1
        assert isinstance(results[0], Success)
        assert results[0].message == "立即完成"

    @pytest.mark.asyncio
    async def test_immediate_failure(self, mock_plugin):
        """测试立即失败。"""
        class ImmediateFailureChatter(BaseChatter):
            chatter_name = "immediate_failure"

            async def execute(self, unreads: list) -> AsyncGenerator[ChatterResult, None]:
                yield Failure("立即失败")

        chatter = ImmediateFailureChatter("stream_123", mock_plugin)

        results = []
        async for result in chatter.execute([MagicMock()]):
            results.append(result)

        assert len(results) == 1
        assert isinstance(results[0], Failure)
        assert results[0].error == "立即失败"


class TestUnreadsFlow:
    """测试 fetch_unreads 与 flush_unreads 方法。"""

    @pytest.mark.asyncio
    async def test_fetch_unreads_only_does_not_mutate_context(self, mock_plugin):
        """测试 fetch_unreads 仅读取，不会清空未读或写入历史。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        msg = Message(
            message_id="msg_1",
            time=datetime.now().timestamp(),
            content="测试",
            sender_id="user_1",
            sender_name="Test",
        )

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            text, messages = await chatter.fetch_unreads()

            payload = json.loads(text)
            assert len(payload) == 1
            assert len(messages) == 1
            assert len(mock_stream.context.unread_messages) == 1
            mock_stream.context.add_history_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_unreads_only_moves_specified_messages(self, mock_plugin):
        """测试 flush_unreads 仅搬运指定未读消息，不影响新增未读。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        msg1 = Message(message_id="msg_1", content="1", sender_id="u1", sender_name="A")
        msg2 = Message(message_id="msg_2", content="2", sender_id="u2", sender_name="B")

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg1, msg2]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            flushed = await chatter.flush_unreads([msg1])

            assert flushed == 1
            assert len(mock_stream.context.unread_messages) == 1
            assert mock_stream.context.unread_messages[0].message_id == "msg_2"
            mock_stream.context.add_history_message.assert_called_once_with(msg1)

    @pytest.mark.asyncio
    async def test_fetch_empty_unreads(self, mock_plugin):
        """测试获取空的未读消息。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # Mock stream manager
        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = []
            # 确保 _streams 是一个字典，支持 .get() 方法
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            text, messages = await chatter.fetch_unreads()

            assert text == ""
            assert messages == []

    @pytest.mark.asyncio
    async def test_fetch_single_message(self, mock_plugin):
        """测试获取单条消息。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        # 创建测试消息
        msg = Message(
            message_id="msg_1",
            time=datetime.now().timestamp(),
            content="你好",
            sender_id="user_1",
            sender_name="Alice"
        )

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            # 需要让 unread_messages 是一个真实的列表
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            # 设置 _streams 为字典
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            text, messages = await chatter.fetch_unreads()
            flushed = await chatter.flush_unreads(messages)

            payload = json.loads(text)
            assert len(payload) == 1
            assert payload[0]["sender_name"] == "Alice"
            assert payload[0]["message_id"] == "msg_1"
            assert payload[0]["message_type"] == "text"
            assert len(messages) == 1
            assert flushed == 1
            mock_stream.context.add_history_message.assert_called_once_with(msg)
            assert len(mock_stream.context.unread_messages) == 0

    @pytest.mark.asyncio
    async def test_fetch_multiple_messages_grouped(self, mock_plugin):
        """测试获取多条消息（分组模式）。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        messages = [
            Message(
                message_id=f"msg_{i}",
                time=datetime.now().timestamp(),
                content=f"消息{i}",
                sender_id=f"user_{i}",
                sender_name=f"User{i}"
            )
            for i in range(3)
        ]

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = messages
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            text, fetched = await chatter.fetch_unreads(format_as_group=True)
            flushed = await chatter.flush_unreads(fetched)

            # 验证 JSON 格式
            payload = json.loads(text)
            assert len(payload) == 3
            assert payload[0]["sender_name"] == "User0"
            assert payload[0]["message_id"] == "msg_0"
            assert payload[0]["message_type"] == "text"

            # 验证flush
            assert len(fetched) == 3
            assert flushed == 3
            assert mock_stream.context.add_history_message.call_count == 3
            assert len(mock_stream.context.unread_messages) == 0

    @pytest.mark.asyncio
    async def test_fetch_non_grouped(self, mock_plugin):
        """测试非分组模式。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        msg = Message(
            message_id="msg_1",
            time=datetime.now().timestamp(),
            content="测试",
            sender_id="user_1",
            sender_name="Test"
        )

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            text, messages = await chatter.fetch_unreads(format_as_group=False)
            flushed = await chatter.flush_unreads(messages)

            assert text == ""  # 非分组模式不返回格式化文本
            assert len(messages) == 1
            assert flushed == 1

    @pytest.mark.asyncio
    async def test_fetch_with_missing_stream(self, mock_plugin):
        """测试流不存在的情况。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_sm.return_value._streams.get.return_value = None

            text, messages = await chatter.fetch_unreads()

            assert text == ""
            assert messages == []

    @pytest.mark.asyncio
    async def test_custom_time_format(self, mock_plugin):
        """测试自定义时间格式。"""
        chatter = ConcreteChatter("stream_123", mock_plugin)

        msg = Message(
            message_id="msg_1",
            time=datetime(2024, 1, 1, 14, 30).timestamp(),
            content="测试",
            sender_id="user_1",
            sender_name="Test"
        )

        with patch('src.core.components.base.chatter.get_stream_manager') as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context.unread_messages = [msg]
            mock_stream.context.add_history_message = MagicMock()
            mock_sm.return_value._streams = {"stream_123": mock_stream}

            # 使用完整时间格式
            text, messages = await chatter.fetch_unreads(time_format="%Y-%m-%d %H:%M")
            await chatter.flush_unreads(messages)

            payload = json.loads(text)
            assert payload[0]["time"] == "2024-01-01 14:30"
