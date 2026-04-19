"""测试 Stream 相关模型。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.core.models.stream import ChatStream, StreamContext


class TestStreamContext:
    """测试 StreamContext 类。"""

    def test_create_stream_context(self):
        """测试创建流上下文。"""
        context = StreamContext(
            stream_id="test_stream_123",
            chat_type="private",
            max_context_size=100,
        )

        assert context.stream_id == "test_stream_123"
        assert context.chat_type == "private"
        assert context.max_context_size == 100
        assert context.unread_messages == []
        assert context.history_messages == []
        assert context.is_active is True
        assert context.is_chatter_processing is False
        assert context.current_message is None
        assert context.triggering_user_id is None
        assert context.processing_message_id is None

    def test_add_unread_message(self):
        """测试添加未读消息。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.message_id = "msg_1"

        context.add_unread_message(mock_message)

        assert len(context.unread_messages) == 1
        assert context.unread_messages[0] == mock_message

    def test_add_multiple_unread_messages(self):
        """测试添加多个未读消息。"""
        context = StreamContext(stream_id="test")

        for i in range(5):
            mock_message = MagicMock()
            mock_message.message_id = f"msg_{i}"
            context.add_unread_message(mock_message)

        assert len(context.unread_messages) == 5

    def test_add_history_message(self):
        """测试添加历史消息。"""
        context = StreamContext(
            stream_id="test",
            max_context_size=100,
        )
        mock_message = MagicMock()
        mock_message.message_id = "msg_1"

        context.add_history_message(mock_message)

        assert len(context.history_messages) == 1
        assert context.history_messages[0] == mock_message

    def test_history_message_size_limit(self):
        """测试历史消息大小限制。"""
        context = StreamContext(
            stream_id="test",
            max_context_size=5,
        )

        # 添加 10 条消息
        for i in range(10):
            mock_message = MagicMock()
            mock_message.message_id = f"msg_{i}"
            context.add_history_message(mock_message)

        # 应该只保留最后 5 条
        assert len(context.history_messages) == 5

    def test_check_types_without_current_message(self):
        """测试没有当前消息时检查类型。"""
        context = StreamContext(stream_id="test")

        result = context.check_types(["text", "image"])
        assert result is False

    def test_check_types_no_type_requirement(self):
        """测试没有类型要求时。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {}
        context.current_message = mock_message

        result = context.check_types([])
        assert result is True

    def test_check_types_with_empty_accept_format(self):
        """测试空的 accept_format。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {"format_info": {"accept_format": []}}
        context.current_message = mock_message

        result = context.check_types(["text", "image"])
        # 空 accept_format 默认支持所有类型
        assert result is True

    def test_check_types_with_string_accept_format(self):
        """测试字符串类型的 accept_format。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {"format_info": {"accept_format": "text"}}
        context.current_message = mock_message

        result = context.check_types(["text"])
        assert result is True

    def test_check_types_matched(self):
        """测试类型匹配。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {
            "format_info": {"accept_format": ["text", "image", "emoji"]}
        }
        context.current_message = mock_message

        result = context.check_types(["text", "image"])
        assert result is True

    def test_check_types_partial_match(self):
        """测试部分类型匹配。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {"format_info": {"accept_format": ["text", "image"]}}
        context.current_message = mock_message

        result = context.check_types(["text", "video"])
        # video 不在 accept_format 中
        assert result is False

    def test_check_types_no_match(self):
        """测试类型完全不匹配。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {"format_info": {"accept_format": ["text"]}}
        context.current_message = mock_message

        result = context.check_types(["image", "video"])
        assert result is False

    def test_check_types_without_format_info(self):
        """测试没有 format_info 时。"""
        context = StreamContext(stream_id="test")
        mock_message = MagicMock()
        mock_message.extra = {}
        context.current_message = mock_message

        result = context.check_types(["text", "image"])
        # 没有 format_info 时默认支持所有类型
        assert result is True

    def test_message_cache_initialization(self):
        """测试消息缓存初始化。"""
        context = StreamContext(stream_id="test")

        from collections import deque

        assert isinstance(context.message_cache, deque)
        assert context.is_cache_enabled is False

    def test_do_not_disturb_helpers(self):
        """测试免打扰状态的设置、判定与清空。"""
        context = StreamContext(stream_id="test")
        until = time.time() + 60

        context.set_do_not_disturb(
            until=until,
            reason="我先忙一下",
            trigger_message_id="msg-001",
        )

        assert context.is_do_not_disturb_active(until - 1) is True
        assert context.is_do_not_disturb_active(until + 1) is False
        assert context.do_not_disturb_reason == "我先忙一下"
        assert context.do_not_disturb_trigger_message_id == "msg-001"

        context.clear_do_not_disturb()

        assert context.do_not_disturb_until is None
        assert context.do_not_disturb_reason is None
        assert context.do_not_disturb_trigger_message_id is None


class TestChatStream:
    """测试 ChatStream 类。"""

    def test_create_chat_stream(self):
        """测试创建聊天流。"""
        stream = ChatStream(
            stream_id="test_stream_123",
            platform="qq",
            chat_type="private",
        )

        assert stream.stream_id == "test_stream_123"
        assert stream.platform == "qq"
        assert stream.chat_type == "private"
        assert stream.bot_id == ""
        assert stream.bot_nickname == ""
        assert isinstance(stream.context, StreamContext)

    def test_create_chat_stream_with_bot_info(self):
        """测试创建聊天流时保存 bot 信息。"""
        stream = ChatStream(
            stream_id="test_stream_123",
            platform="qq",
            chat_type="private",
            bot_id="123456",
            bot_nickname="MoFoxBot",
        )

        assert stream.bot_id == "123456"
        assert stream.bot_nickname == "MoFoxBot"

    def test_chat_stream_context_initialization(self):
        """测试聊天流上下文初始化。"""
        stream = ChatStream(
            stream_id="test_stream",
            platform="telegram",
            chat_type="group",
        )

        assert stream.context.stream_id == "test_stream"
        assert stream.context.chat_type == "group"

    def test_update_active_time(self):
        """测试更新活跃时间。"""
        import time

        stream = ChatStream(
            stream_id="test",
            platform="qq",
        )

        old_time = stream.last_active_time
        time.sleep(0.01)  # 小延迟
        stream.update_active_time()

        assert stream.last_active_time > old_time

    def test_get_raw_id(self):
        """测试获取原始 ID。"""
        stream = ChatStream(
            stream_id="abc123",
            platform="qq",
            chat_type="private",
        )

        raw_id = stream.get_raw_id()
        assert raw_id == "qq:abc123:private"

    def test_set_context(self):
        """测试设置上下文。"""
        stream = ChatStream(
            stream_id="test",
            platform="qq",
        )
        mock_message = MagicMock()
        mock_message.message_id = "msg_123"

        import asyncio

        async def test_set():
            await stream.set_context(mock_message)
            assert stream.context.current_message == mock_message

        asyncio.run(test_set())

    def test_generate_stream_id_with_user_id(self):
        """测试使用用户 ID 生成 stream_id。"""
        stream_id = ChatStream.generate_stream_id(
            platform="qq",
            user_id="123456",
        )

        assert isinstance(stream_id, str)
        assert len(stream_id) == 64  # SHA-256 哈希长度

    def test_generate_stream_id_with_group_id(self):
        """测试使用群组 ID 生成 stream_id。"""
        stream_id = ChatStream.generate_stream_id(
            platform="qq",
            group_id="789012",
        )

        assert isinstance(stream_id, str)
        assert len(stream_id) == 64

    def test_generate_stream_id_without_id_raises(self):
        """测试没有 ID 时生成 stream_id 抛出异常。"""
        with pytest.raises(ValueError, match="user_id 或 group_id 必须提供至少一个"):
            ChatStream.generate_stream_id(platform="qq")

    def test_generate_stream_id_is_deterministic(self):
        """测试生成 stream_id 是确定性的。"""
        stream_id1 = ChatStream.generate_stream_id(
            platform="qq",
            user_id="123456",
        )
        stream_id2 = ChatStream.generate_stream_id(
            platform="qq",
            user_id="123456",
        )

        assert stream_id1 == stream_id2

    def test_generate_stream_id_different_for_different_inputs(self):
        """测试不同输入生成不同的 stream_id。"""
        stream_id1 = ChatStream.generate_stream_id(
            platform="qq",
            user_id="123456",
        )
        stream_id2 = ChatStream.generate_stream_id(
            platform="qq",
            user_id="789012",
        )

        assert stream_id1 != stream_id2

    def test_generate_stream_id_private_vs_group(self):
        """测试私聊和群聊生成不同的 stream_id。"""
        private_id = ChatStream.generate_stream_id(
            platform="qq",
            user_id="123456",
        )
        group_id = ChatStream.generate_stream_id(
            platform="qq",
            group_id="123456",
        )

        assert private_id != group_id

    def test_generate_stream_id_cached(self):
        """测试 stream_id 生成缓存。"""
        # 多次生成相同 ID 应该返回缓存结果
        id1 = ChatStream.generate_stream_id(platform="qq", user_id="123")
        id2 = ChatStream.generate_stream_id(platform="qq", user_id="123")
        id3 = ChatStream.generate_stream_id(platform="qq", user_id="123")

        assert id1 == id2 == id3


class TestChatStreamIntegration:
    """测试 ChatStream 集成场景。"""

    def test_full_stream_lifecycle(self):
        """测试完整的流生命周期。"""
        # 创建流
        stream = ChatStream(
            stream_id="test_stream",
            platform="qq",
            chat_type="group",
        )

        # 添加消息
        mock_message1 = MagicMock()
        mock_message1.message_id = "msg_1"
        stream.context.add_history_message(mock_message1)

        mock_message2 = MagicMock()
        mock_message2.message_id = "msg_2"
        stream.context.add_history_message(mock_message2)

        # 检查状态
        assert len(stream.context.history_messages) == 2
        assert stream.context.is_active is True

    def test_stream_with_context_size_limit(self):
        """测试带上下文大小限制的流。"""
        stream = ChatStream(
            stream_id="test",
            platform="qq",
        )
        stream.context.max_context_size = 3

        # 添加 5 条消息
        for i in range(5):
            mock_message = MagicMock()
            mock_message.message_id = f"msg_{i}"
            stream.context.add_history_message(mock_message)

        # 应该只保留最后 3 条
        assert len(stream.context.history_messages) == 3

    def test_stream_type_checking(self):
        """测试流类型检查。"""
        stream = ChatStream(
            stream_id="test",
            platform="qq",
        )

        # 设置当前消息
        mock_message = MagicMock()
        mock_message.extra = {"format_info": {"accept_format": ["text", "image"]}}
        stream.context.current_message = mock_message

        # 测试类型检查
        assert stream.context.check_types(["text"]) is True
        assert stream.context.check_types(["video"]) is False
