"""InterestCalculator 单元测试。

测试兴趣值计算器的二维加权计算（语义 + 提及）、阈值调整和状态管理。
"""

from __future__ import annotations

import pytest

from src.core.models.message import Message
from src.core.models.stream import ChatStream

from plugins.default_chatter.utils.interest_calculator import (
    InterestCalculator,
    InterestConfig,
    StreamInterestState,
)


@pytest.fixture
def config() -> InterestConfig:
    """创建测试用配置。"""
    return InterestConfig(
        reply_threshold=0.72,
        action_threshold=0.55,
        semantic_weight=0.6,
        mentioned_weight=0.4,
        strong_mention_score=2.0,
        weak_mention_score=0.8,
        no_reply_threshold_adjustment=0.02,
        max_no_reply_count=5,
        reply_cooldown_reduction=2,
        enable_post_reply_boost=True,
        post_reply_threshold_reduction=0.2,
        post_reply_boost_max_count=2,
        post_reply_boost_decay_rate=0.4,
    )


@pytest.fixture
def calculator(config: InterestConfig) -> InterestCalculator:
    """创建无语义评分器的兴趣值计算器。"""
    return InterestCalculator(config=config, semantic_scorer=None)


@pytest.fixture
def mock_message() -> Message:
    """创建普通消息。"""
    return Message(
        message_id="msg_001",
        content="你好呀",
        processed_plain_text="你好呀",
        sender_id="user_123",
        sender_name="TestUser",
        platform="test",
        chat_type="group",
        stream_id="test_stream",
    )


@pytest.fixture
def mock_plain_message() -> Message:
    """创建无提及的普通消息。"""
    return Message(
        message_id="msg_002",
        content="今天天气不错",
        processed_plain_text="今天天气不错",
        sender_id="user_456",
        sender_name="AnotherUser",
        platform="test",
        chat_type="group",
        stream_id="test_stream",
    )


@pytest.fixture
def mock_at_message() -> Message:
    """创建包含 @bot 的消息。"""
    return Message(
        message_id="msg_003",
        content="你好 :12345>",
        processed_plain_text="你好 :12345>",
        sender_id="user_789",
        sender_name="AtUser",
        platform="test",
        chat_type="group",
        stream_id="test_stream",
        at_users=[{"user_id": "12345"}],
    )


@pytest.fixture
def mock_chat_stream() -> ChatStream:
    """创建群聊会话流。"""
    return ChatStream(
        stream_id="test_stream",
        platform="test",
        bot_id="12345",
        bot_nickname="TestBot",
        chat_type="group",
    )


@pytest.fixture
def mock_private_chat_stream() -> ChatStream:
    """创建私聊会话流。"""
    return ChatStream(
        stream_id="private_stream",
        platform="test",
        bot_id="12345",
        bot_nickname="TestBot",
        chat_type="private",
    )


class TestInterestConfig:
    """InterestConfig 配置测试。"""

    def test_default_values(self) -> None:
        """测试默认配置值。"""
        cfg = InterestConfig()
        assert cfg.reply_threshold == 0.72
        assert cfg.action_threshold == 0.55
        assert cfg.semantic_weight == 0.6
        assert cfg.mentioned_weight == 0.4
        assert cfg.strong_mention_score == 2.0
        assert cfg.weak_mention_score == 0.8

    def test_custom_values(self) -> None:
        """测试自定义配置值。"""
        cfg = InterestConfig(reply_threshold=0.5, semantic_weight=0.8)
        assert cfg.reply_threshold == 0.5
        assert cfg.semantic_weight == 0.8


class TestStreamInterestState:
    """StreamInterestState 状态测试。"""

    def test_default_state(self) -> None:
        """测试默认状态。"""
        state = StreamInterestState()
        assert state.no_reply_count == 0
        assert state.post_reply_boost_remaining == 0


class TestInterestCalculator:
    """InterestCalculator 计算器测试。"""

    def test_get_stream_state_creates_new(
        self, calculator: InterestCalculator
    ) -> None:
        """测试获取不存在的 stream 状态时创建新状态。"""
        state = calculator.get_stream_state("test_stream_1")
        assert isinstance(state, StreamInterestState)
        assert state.no_reply_count == 0

    def test_get_stream_state_returns_same(
        self, calculator: InterestCalculator
    ) -> None:
        """测试相同 stream_id 返回同一状态对象。"""
        state1 = calculator.get_stream_state("test_stream_2")
        state1.no_reply_count = 3
        state2 = calculator.get_stream_state("test_stream_2")
        assert state2.no_reply_count == 3

    @pytest.mark.asyncio
    async def test_calculate_without_scorer_returns_zero_semantic(
        self,
        calculator: InterestCalculator,
        mock_message,
        mock_chat_stream,
    ) -> None:
        """测试无语义评分器时语义分返回 0.0。"""
        result = await calculator.calculate(mock_message, mock_chat_stream)
        assert result.semantic_score == 0.0
        assert result.interest_value >= 0.0
        assert isinstance(result.reason, str)

    @pytest.mark.asyncio
    async def test_calculate_private_chat_strong_mention(
        self,
        calculator: InterestCalculator,
        mock_message,
        mock_private_chat_stream,
    ) -> None:
        """测试私聊场景返回强提及分。"""
        result = await calculator.calculate(mock_message, mock_private_chat_stream)
        assert result.mentioned_score == calculator.config.strong_mention_score

    @pytest.mark.asyncio
    async def test_calculate_at_mention_strong(
        self,
        calculator: InterestCalculator,
        mock_at_message,
        mock_chat_stream,
    ) -> None:
        """测试被 @ 消息返回强提及分。"""
        result = await calculator.calculate(mock_at_message, mock_chat_stream)
        assert result.mentioned_score == calculator.config.strong_mention_score

    @pytest.mark.asyncio
    async def test_calculate_no_mention_returns_zero(
        self,
        calculator: InterestCalculator,
        mock_plain_message,
        mock_chat_stream,
    ) -> None:
        """测试无提及消息返回 0 提及分。"""
        result = await calculator.calculate(mock_plain_message, mock_chat_stream)
        assert result.mentioned_score == 0.0

    def test_on_message_processed_replied(
        self,
        calculator: InterestCalculator,
    ) -> None:
        """测试回复后状态更新。"""
        calculator.on_message_processed("test_stream", replied=True)
        state = calculator.get_stream_state("test_stream")
        assert state.no_reply_count == 0
        assert state.post_reply_boost_remaining > 0

    def test_on_message_processed_not_replied(
        self,
        calculator: InterestCalculator,
    ) -> None:
        """测试不回复后状态更新。"""
        calculator.on_message_processed("test_stream", replied=False)
        state = calculator.get_stream_state("test_stream")
        assert state.no_reply_count == 1

    def test_on_message_processed_replied_multiple_times(
        self,
        calculator: InterestCalculator,
    ) -> None:
        """测试多次不回复后计数递增。"""
        for _ in range(3):
            calculator.on_message_processed("test_stream", replied=False)
        state = calculator.get_stream_state("test_stream")
        assert state.no_reply_count == 3

    def test_on_message_processed_max_no_reply_count(
        self,
        calculator: InterestCalculator,
    ) -> None:
        """测试不回复计数上限。"""
        for _ in range(10):
            calculator.on_message_processed("test_stream", replied=False)
        state = calculator.get_stream_state("test_stream")
        assert state.no_reply_count == calculator.config.max_no_reply_count

    def test_threshold_adjustment_no_reply(
        self,
        calculator: InterestCalculator,
    ) -> None:
        """测试连续不回复降低阈值。"""
        state = calculator.get_stream_state("test_stream")
        state.no_reply_count = 2
        reply_threshold, action_threshold = calculator._apply_threshold_adjustment(
            "test_stream"
        )
        assert reply_threshold < calculator.config.reply_threshold
        assert action_threshold < calculator.config.action_threshold

    def test_threshold_adjustment_post_reply_boost(
        self,
        calculator: InterestCalculator,
    ) -> None:
        """测试回复后阈值降低。"""
        state = calculator.get_stream_state("test_stream")
        state.no_reply_count = 0
        state.post_reply_boost_remaining = 2
        reply_threshold, _ = calculator._apply_threshold_adjustment("test_stream")
        assert reply_threshold < calculator.config.reply_threshold

    def test_extract_text_plain_text(self, calculator: InterestCalculator) -> None:
        """测试提取 processed_plain_text。"""
        from src.core.models.message import Message

        msg = Message(message_id="1", content="hello", processed_plain_text="plain")
        assert calculator._extract_text(msg) == "plain"

    def test_extract_text_fallback_to_content(
        self, calculator: InterestCalculator
    ) -> None:
        """测试无 processed_plain_text 时回退到 content。"""
        from src.core.models.message import Message

        msg = Message(message_id="1", content="fallback")
        assert calculator._extract_text(msg) == "fallback"

    def test_text_contains_case_insensitive(
        self, calculator: InterestCalculator
    ) -> None:
        """测试文本匹配大小写不敏感。"""
        from src.core.models.message import Message

        msg = Message(message_id="1", content="Hello World", processed_plain_text="Hello World")
        assert calculator._text_contains(msg, "hello")
        assert calculator._text_contains(msg, "WORLD")
        assert not calculator._text_contains(msg, "missing")

    def test_is_strong_mention_at_users_dict(
        self, calculator: InterestCalculator
    ) -> None:
        """测试 @ 检测（dict 格式）。"""
        from src.core.models.message import Message

        msg = Message(
            message_id="1",
            content="hello",
            at_users=[{"user_id": "12345"}],
        )
        assert calculator._is_strong_mention(msg, "12345")
        assert not calculator._is_strong_mention(msg, "99999")

    def test_is_strong_mention_at_users_str(
        self, calculator: InterestCalculator
    ) -> None:
        """测试 @ 检测（str 格式）。"""
        from src.core.models.message import Message

        msg = Message(
            message_id="1",
            content="hello",
            at_users=["12345"],
        )
        assert calculator._is_strong_mention(msg, "12345")

    def test_is_strong_mention_reply_to(
        self, calculator: InterestCalculator
    ) -> None:
        """测试仅回复消息不再无条件算强提及（需配合 @ 或文本匹配）。"""
        from src.core.models.message import Message

        msg = Message(message_id="1", content="reply", reply_to="msg_999")
        assert not calculator._is_strong_mention(msg, "12345")

        msg_with_at = Message(
            message_id="2",
            content="reply",
            reply_to="msg_999",
            at_users=[{"user_id": "12345"}],
        )
        assert calculator._is_strong_mention(msg_with_at, "12345")

    def test_is_strong_mention_text_pattern(
        self, calculator: InterestCalculator
    ) -> None:
        """测试文本中 :bot_id> 格式的 @。"""
        from src.core.models.message import Message

        msg = Message(
            message_id="1",
            content="hello :12345> something",
            processed_plain_text="hello :12345> something",
        )
        assert calculator._is_strong_mention(msg, "12345")

    def test_is_strong_mention_none(
        self, calculator: InterestCalculator
    ) -> None:
        """测试无提及返回 False。"""
        from src.core.models.message import Message

        msg = Message(message_id="1", content="hello")
        assert not calculator._is_strong_mention(msg, "12345")

    def test_get_current_persona_info(self, calculator: InterestCalculator) -> None:
        """测试获取人设信息。"""
        info = calculator.get_current_persona_info()
        assert isinstance(info, dict)
        assert "name" in info
