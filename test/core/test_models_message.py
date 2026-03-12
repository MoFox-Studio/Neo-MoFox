"""测试 src.core.models.message 模块。"""

from datetime import datetime


from src.core.models.message import Message, MessageType


class TestMessageType:
    """测试 MessageType 枚举。"""

    def test_message_type_values(self):
        """测试 MessageType 枚举值。"""
        assert MessageType.TEXT.value == "text"
        assert MessageType.IMAGE.value == "image"
        assert MessageType.VOICE.value == "voice"
        assert MessageType.VIDEO.value == "video"
        assert MessageType.FILE.value == "file"
        assert MessageType.LOCATION.value == "location"
        assert MessageType.EMOJI.value == "emoji"
        assert MessageType.NOTICE.value == "notice"
        assert MessageType.UNKNOWN.value == "unknown"


class TestMessage:
    """测试 Message 类。"""

    def test_message_minimal_initialization(self):
        """测试最小初始化。"""
        message = Message()
        assert message.message_id == ""
        assert message.content == ""
        assert message.sender_id == ""
        assert message.sender_name == ""
        assert message.platform == ""
        assert message.chat_type == ""
        assert message.stream_id == ""

    def test_message_full_initialization(self):
        """测试完整初始化。"""
        now = datetime.now()
        message = Message(
            message_id="msg_123",
            time=now,
            reply_to="msg_456",
            content="Hello world",
            processed_plain_text="Hello",
            message_type=MessageType.TEXT,
            sender_id="user_001",
            sender_name="Alice",
            sender_cardname="Alice_Card",
            platform="telegram",
            chat_type="private",
            stream_id="stream_789",
            extra={"key": "value"},
        )

        assert message.message_id == "msg_123"
        assert message.reply_to == "msg_456"
        assert message.content == "Hello world"
        assert message.processed_plain_text == "Hello"
        assert message.message_type == MessageType.TEXT
        assert message.sender_id == "user_001"
        assert message.sender_name == "Alice"
        assert message.sender_cardname == "Alice_Card"
        assert message.platform == "telegram"
        assert message.chat_type == "private"
        assert message.stream_id == "stream_789"
        # extra 参数被收集到 **extra 中，所以会变成 {'extra': {'key': 'value'}}
        assert message.extra == {"extra": {"key": "value"}}

    def test_message_time_conversion(self):
        """测试时间转换为时间戳。"""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        message = Message(time=dt)
        assert isinstance(message.time, float)
        assert message.time == dt.timestamp()

    def test_message_time_none_uses_now(self):
        """测试 time 为 None 时使用当前时间。"""
        message = Message(time=None)
        assert isinstance(message.time, float)
        assert message.time > 0

    def test_message_repr(self):
        """测试 __repr__ 方法。"""
        message = Message(
            message_id="msg_123",
            content="This is a long message that should be truncated in repr",
            sender_name="TestUser",
            message_type=MessageType.TEXT,
        )
        repr_str = repr(message)
        assert "msg_123" in repr_str
        assert "TestUser" in repr_str
        assert "text" in repr_str

    def test_message_to_dict(self):
        """测试 to_dict 方法。"""
        message = Message(
            message_id="msg_123",
            content="Test content",
            sender_id="user_001",
            sender_name="Alice",
            platform="telegram",
        )
        result = message.to_dict()

        assert isinstance(result, dict)
        assert result["message_id"] == "msg_123"
        assert result["content"] == "Test content"
        assert result["sender_id"] == "user_001"
        assert result["sender_name"] == "Alice"
        assert result["platform"] == "telegram"

    def test_message_with_raw_data(self):
        """测试带原始数据的消息。"""
        raw_data = {"original_json": {"key": "value"}}
        message = Message(
            message_id="msg_raw",
            raw_data=raw_data,
        )
        assert message.raw_data == raw_data

    def test_message_different_types(self):
        """测试不同类型的消息。"""
        types_and_content = [
            (MessageType.TEXT, "Text message"),
            (MessageType.IMAGE, {"url": "http://example.com/image.jpg"}),
            (MessageType.VOICE, {"url": "http://example.com/voice.mp3"}),
            (MessageType.FILE, {"filename": "document.pdf", "size": 1024}),
        ]

        for msg_type, content in types_and_content:
            message = Message(message_type=msg_type, content=content)
            assert message.message_type == msg_type
            assert message.content == content

    def test_message_extra_metadata(self):
        """测试额外的元数据。"""
        message = Message(
            message_id="msg_extra",
            reply_count=5,
            forward_count=2,
            edit_count=1,
            custom_field="custom_value",
        )
        # 额外的关键字参数被收集到 extra 字典中
        assert message.extra["reply_count"] == 5
        assert message.extra["forward_count"] == 2
        assert message.extra["edit_count"] == 1
        assert message.extra["custom_field"] == "custom_value"

    def test_message_processed_plain_text_priority(self):
        """测试 processed_plain_text 优先于 content。"""
        message = Message(
            content="<b>Raw HTML</b>",
            processed_plain_text="Raw HTML",
        )
        assert message.content == "<b>Raw HTML</b>"
        assert message.processed_plain_text == "Raw HTML"


class TestMessageReplyChain:
    """测试消息回复链。"""

    def test_message_with_reply_to(self):
        """测试带回复的消息。"""
        parent = Message(message_id="parent_123", content="Parent message")
        child = Message(message_id="child_456", content="Child message", reply_to=parent.message_id)

        assert child.reply_to == "parent_123"
        assert parent.message_id == "parent_123"

    def test_message_reply_to_none(self):
        """测试 reply_to 为 None。"""
        message = Message(message_id="msg_no_reply")
        assert message.reply_to is None


class TestMessageUserFields:
    """测试消息用户字段。"""

    def test_sender_cardname(self):
        """测试 sender_cardname 字段。"""
        # 有名片名
        message1 = Message(
            sender_name="RealName",
            sender_cardname="Nickname",
        )
        assert message1.sender_name == "RealName"
        assert message1.sender_cardname == "Nickname"

        # 无名片名
        message2 = Message(
            sender_name="RealName",
            sender_cardname=None,
        )
        assert message2.sender_name == "RealName"
        assert message2.sender_cardname is None

    def test_sender_fields_combinations(self):
        """测试用户字段组合。"""
        test_cases = [
            {"sender_id": "id1", "sender_name": "name1"},
            {"sender_id": "id2", "sender_name": "name2", "sender_cardname": "card2"},
            {"sender_id": "id3", "sender_name": "name3", "sender_cardname": None},
        ]

        for i, fields in enumerate(test_cases, 1):
            message = Message(**fields)
            assert message.sender_id == fields["sender_id"]
            assert message.sender_name == fields["sender_name"]
            if "sender_cardname" in fields:
                assert message.sender_cardname == fields["sender_cardname"]


class TestMessageContextFields:
    """测试消息上下文字段。"""

    def test_chat_context(self):
        """测试聊天上下文字段。"""
        contexts = [
            ("private", "telegram", "stream_private_123"),
            ("group", "discord", "stream_group_456"),
            ("discuss", "qq", "stream_discuss_789"),
        ]

        for chat_type, platform, stream_id in contexts:
            message = Message(
                chat_type=chat_type,
                platform=platform,
                stream_id=stream_id,
            )
            assert message.chat_type == chat_type
            assert message.platform == platform
            assert message.stream_id == stream_id
