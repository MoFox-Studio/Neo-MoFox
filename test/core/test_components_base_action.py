"""测试 src.core.components.base.action 模块。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.types import ChatType


class ConcreteAction(BaseAction):
    """具体的 Action 实现用于测试。"""

    action_name = "test_action"
    action_description = "A test action"
    primary_action = False
    chatter_allow = []
    chat_type = ChatType.ALL
    associated_platforms = []
    associated_types = []

    async def execute(self, message: str) -> tuple[bool, str]:
        """执行测试动作。"""
        return True, f"Executed: {message}"


class TestBaseAction:
    """测试 BaseAction 类。"""

    @pytest.fixture(autouse=True)
    def reset_class_attributes(self):
        """在每个测试前重置类属性。"""
        # 备份原始值
        original_plugin_name = getattr(ConcreteAction, "_plugin_", None)
        yield
        # 恢复原始值
        if original_plugin_name:
            ConcreteAction._plugin_ = original_plugin_name
        elif hasattr(ConcreteAction, "_plugin_"):
            delattr(ConcreteAction, "_plugin_")

    def test_action_initialization(self, mock_chat_stream, mock_plugin):
        """测试 Action 初始化。"""
        action = ConcreteAction(mock_chat_stream, mock_plugin)
        assert action.chat_stream == mock_chat_stream
        assert action.plugin == mock_plugin
        assert action.action_name == "test_action"
        assert action.action_description == "A test action"
        assert action.primary_action is False

    def test_get_signature(self, mock_chat_stream, mock_plugin):
        """测试获取签名。"""
        # 默认情况下 plugin_name 是 unknown_plugin
        action = ConcreteAction(mock_chat_stream, mock_plugin)
        assert action.get_signature() is None

        # 设置 plugin_name 后应该返回签名
        ConcreteAction._plugin_ = "my_plugin"
        action2 = ConcreteAction(mock_chat_stream, mock_plugin)
        assert action2.get_signature() == "my_plugin:action:test_action"

    def test_execute(self, mock_chat_stream, mock_plugin):
        """测试 execute 方法。"""
        import asyncio

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        success, result = asyncio.run(action.execute("test message"))
        assert success is True
        assert result == "Executed: test message"

    def test_to_schema(self, mock_chat_stream, mock_plugin):
        """测试生成 schema。"""
        action = ConcreteAction(mock_chat_stream, mock_plugin)
        schema = action.to_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "action:test_action"
        assert schema["function"]["description"] == "A test action"
        assert "parameters" in schema["function"]
        assert schema["function"]["parameters"]["type"] == "object"
        assert "properties" in schema["function"]["parameters"]
        assert "message" in schema["function"]["parameters"]["properties"]
        assert "required" in schema["function"]["parameters"]

    def test_go_activate_default(self, mock_chat_stream, mock_plugin):
        """测试默认激活判定。"""
        import asyncio

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        result = asyncio.run(action.go_activate())
        assert result is True

    def test_random_activation(self, mock_chat_stream, mock_plugin):
        """测试随机激活。"""
        import asyncio

        action = ConcreteAction(mock_chat_stream, mock_plugin)

        # 测试概率 1.0 应该总是激活
        result = asyncio.run(action._random_activation(1.0))
        assert result is True

        # 测试概率 0.0 应该从不激活
        result = asyncio.run(action._random_activation(0.0))
        assert result is False

    def test_keyword_match(self, mock_chat_stream, mock_plugin):
        """测试关键词匹配。"""
        import asyncio

        action = ConcreteAction(mock_chat_stream, mock_plugin)

        # 设置 last message
        action._last_message = "Hello world, this is a test"

        # 测试匹配关键词
        result = asyncio.run(action._keyword_match(["hello", "world"]))
        assert result is True

        # 测试不匹配关键词
        result = asyncio.run(action._keyword_match(["goodbye"]))
        assert result is False

    def test_keyword_match_case_sensitive(self, mock_chat_stream, mock_plugin):
        """测试关键词匹配（区分大小写）。"""
        import asyncio

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        action._last_message = "Hello World"

        # 不区分大小写（默认）
        result = asyncio.run(action._keyword_match(["hello"], case_sensitive=False))
        assert result is True

        # 区分大小写
        result = asyncio.run(action._keyword_match(["hello"], case_sensitive=True))
        assert result is False

        result = asyncio.run(action._keyword_match(["Hello"], case_sensitive=True))
        assert result is True

    def test_keyword_match_no_last_message(self, mock_chat_stream, mock_plugin):
        """测试没有最后消息时的关键词匹配。"""
        import asyncio

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        action._last_message = None

        result = asyncio.run(action._keyword_match(["hello"]))
        assert result is False

    def test_get_recent_chat_content(self, mock_chat_stream, mock_plugin):
        """测试获取最近聊天内容。"""
        action = ConcreteAction(mock_chat_stream, mock_plugin)

        # 添加更多消息到历史记录
        for i in range(5):
            msg = MagicMock()
            msg.processed_plain_text = f"Message {i}"
            msg.content = f"Message {i}"
            msg.sender_name = f"User{i}"
            mock_chat_stream.context.history_messages.append(msg)

        content = action._get_recent_chat_content(max_messages=3)

        # 应该获取最后 3 条消息
        assert content is not None
        assert "User2" in content
        assert "User4" in content

    def test_get_recent_chat_content_fallback_to_content(self):
        """测试获取聊天内容时回退到 content 字段。"""
        # 创建一个没有 processed_plain_text 的消息
        stream = MagicMock()
        stream.context.history_messages = []

        msg = MagicMock()
        msg.processed_plain_text = None
        msg.content = "Raw content"
        msg.sender_name = "TestUser"
        stream.context.history_messages.append(msg)

        plugin = MagicMock()
        action = ConcreteAction(stream, plugin)
        content = action._get_recent_chat_content()

        assert "Raw content" in content

    @patch("src.kernel.llm.LLMRequest")
    @patch("src.core.config.get_model_config")
    def test_llm_judge_activation_positive(
        self, mock_get_config, mock_llm_request, mock_chat_stream, mock_plugin
    ):
        """测试 LLM 判断激活（肯定结果）。"""
        import asyncio

        # 设置 mock
        mock_model_config = MagicMock()
        mock_task = MagicMock()
        mock_model_config.get_task.return_value = mock_task
        mock_get_config.return_value = mock_model_config

        mock_request_instance = MagicMock()
        mock_request_instance.send = AsyncMock(return_value="是，应该激活")
        mock_llm_request.return_value = mock_request_instance

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        result = asyncio.run(action._llm_judge_activation("测试提示"))

        assert result is True

    @patch("src.kernel.llm.LLMRequest")
    @patch("src.core.config.get_model_config")
    def test_llm_judge_activation_negative(
        self, mock_get_config, mock_llm_request, mock_chat_stream, mock_plugin
    ):
        """测试 LLM 判断激活（否定结果）。"""
        import asyncio

        mock_model_config = MagicMock()
        mock_task = MagicMock()
        mock_model_config.get_task.return_value = mock_task
        mock_get_config.return_value = mock_model_config

        mock_request_instance = MagicMock()
        mock_request_instance.send = AsyncMock(return_value="否，不应该激活")
        mock_llm_request.return_value = mock_request_instance

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        result = asyncio.run(action._llm_judge_activation("测试提示"))

        assert result is False

    @patch("src.kernel.llm.LLMRequest")
    @patch("src.core.config.get_model_config")
    def test_llm_judge_activation_timeout(
        self, mock_get_config, mock_llm_request, mock_chat_stream, mock_plugin
    ):
        """测试 LLM 判断激活（超时）。"""
        import asyncio

        mock_model_config = MagicMock()
        mock_task = MagicMock()
        mock_model_config.get_task.return_value = mock_task
        mock_get_config.return_value = mock_model_config

        async def timeout_send(*args, **kwargs):
            await asyncio.sleep(0.1)
            return "yes"

        mock_request_instance = MagicMock()
        mock_request_instance.send = timeout_send
        mock_llm_request.return_value = mock_request_instance

        action = ConcreteAction(mock_chat_stream, mock_plugin)

        # 设置很小的超时以触发超时
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = asyncio.run(action._llm_judge_activation("测试提示"))
            # 超时时应该默认激活
            assert result is True

    @patch("src.core.transport.message_send.get_message_sender")
    def test_send_to_stream_with_string(self, mock_get_sender, mock_chat_stream, mock_plugin):
        """测试 _send_to_stream 发送字符串内容。"""
        import asyncio

        # Mock MessageSender
        mock_sender = MagicMock()
        mock_sender.send_message = AsyncMock(return_value=True)
        mock_get_sender.return_value = mock_sender

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        result = asyncio.run(action._send_to_stream("test content"))

        assert result is True
        mock_sender.send_message.assert_called_once()

    @patch("src.core.transport.message_send.get_message_sender")
    def test_send_to_stream_with_message(self, mock_get_sender, mock_chat_stream, mock_plugin):
        """测试 _send_to_stream 发送 Message 对象。"""
        import asyncio
        from src.core.models.message import Message, MessageType

        # Mock MessageSender
        mock_sender = MagicMock()
        mock_sender.send_message = AsyncMock(return_value=True)
        mock_get_sender.return_value = mock_sender

        # 创建 Message 对象
        message = Message(
            message_id="test_msg",
            content="Hello",
            message_type=MessageType.TEXT,
            platform="test",
            stream_id="test_stream",
        )

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        result = asyncio.run(action._send_to_stream(message))

        assert result is True
        mock_sender.send_message.assert_called_once_with(message)

    @patch("src.core.transport.message_send.get_message_sender")
    def test_send_to_stream_failure(self, mock_get_sender, mock_chat_stream, mock_plugin):
        """测试 _send_to_stream 发送失败的情况。"""
        import asyncio

        # Mock MessageSender 返回 False
        mock_sender = MagicMock()
        mock_sender.send_message = AsyncMock(return_value=False)
        mock_get_sender.return_value = mock_sender

        action = ConcreteAction(mock_chat_stream, mock_plugin)
        result = asyncio.run(action._send_to_stream("test content"))

        assert result is False


class TestActionAttributes:
    """测试 Action 类属性。"""

    def test_action_with_custom_attributes(self, mock_chat_stream, mock_plugin):
        """测试自定义属性的 Action。"""
        from src.core.components.types import ChatType

        class CustomAction(BaseAction):
            action_name = "custom_action"
            action_description = "Custom action description"
            primary_action = True
            chatter_allow = ["chatter1", "chatter2"]
            chat_type = ChatType.GROUP
            associated_platforms = ["telegram", "discord"]
            associated_types = ["text", "image"]
            dependencies = ["other_plugin:tool:helper"]

            async def execute(self, data: str) -> tuple[bool, str]:
                return True, "done"

        action = CustomAction(mock_chat_stream, mock_plugin)
        assert action.action_name == "custom_action"
        assert action.action_description == "Custom action description"
        assert action.primary_action is True
        assert action.chatter_allow == ["chatter1", "chatter2"]
        assert action.chat_type == ChatType.GROUP
        assert action.associated_platforms == ["telegram", "discord"]
        assert action.associated_types == ["text", "image"]
        assert action.dependencies == ["other_plugin:tool:helper"]
