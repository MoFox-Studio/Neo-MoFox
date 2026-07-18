"""兴趣值计算模块。

基于二维加权计算消息的兴趣值：语义兴趣度 + 提及分。
支持阈值调整机制（连续不回复降低、回复后连续对话增强）。

兴趣值公式：总分 = 语义兴趣度 × semantic_weight + 提及分 × mentioned_weight
总分上限 1.0。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.config import get_core_config
from src.core.models.message import Message
from src.core.models.stream import ChatStream

from .semantic_interest.runtime_scorer import SemanticInterestScorer

logger = get_logger("default_chatter.interest")


@dataclass(slots=True)
class InterestResult:
    """兴趣值计算结果。"""

    interest_value: float
    should_reply: bool
    should_take_action: bool
    semantic_score: float
    mentioned_score: float
    reason: str
    calculation_time: float = 0.0


@dataclass(slots=True)
class InterestConfig:
    """兴趣值计算配置。"""

    reply_threshold: float = 0.72
    action_threshold: float = 0.55
    semantic_weight: float = 0.6
    mentioned_weight: float = 0.4
    strong_mention_score: float = 2.0
    weak_mention_score: float = 0.8
    no_reply_threshold_adjustment: float = 0.02
    max_no_reply_count: int = 5
    reply_cooldown_reduction: int = 2
    enable_post_reply_boost: bool = True
    post_reply_threshold_reduction: float = 0.2
    post_reply_boost_max_count: int = 5
    post_reply_boost_decay_rate: float = 0.8


@dataclass(slots=True)
class StreamInterestState:
    """每个 stream 的兴趣值状态。"""

    no_reply_count: int = 0
    post_reply_boost_remaining: int = 0


class InterestCalculator:
    """兴趣值计算器。

    二维加权计算：语义兴趣度 + 提及分。
    支持 per-stream 状态管理和阈值动态调整。
    """

    def __init__(
        self,
        config: InterestConfig | None = None,
        semantic_scorer: SemanticInterestScorer | None = None,
    ) -> None:
        """初始化兴趣值计算器。

        Args:
            config: 兴趣值配置，None 则使用默认配置
            semantic_scorer: 语义兴趣度评分器，None 则不使用语义评分
        """
        self.config = config or InterestConfig()
        self._semantic_scorer = semantic_scorer

        self._stream_states: dict[str, StreamInterestState] = {}

        if self._semantic_scorer:
            logger.info("[兴趣值计算器] 语义评分器已加载")
        else:
            logger.warning("[兴趣值计算器] 语义评分器未加载，语义分将返回 0.0")

        logger.info(
            f"[兴趣值计算器] 初始化完成: "
            f"权重=语义{self.config.semantic_weight:.1f}+提及{self.config.mentioned_weight:.1f}, "
            f"回复阈值={self.config.reply_threshold}"
        )

    def get_stream_state(self, stream_id: str) -> StreamInterestState:
        """获取指定 stream 的兴趣值状态。

        Args:
            stream_id: 会话流 ID

        Returns:
            该 stream 的状态对象
        """
        if stream_id not in self._stream_states:
            self._stream_states[stream_id] = StreamInterestState()
        return self._stream_states[stream_id]

    async def calculate(
        self,
        message: Message,
        chat_stream: ChatStream,
        *,
        precomputed_thresholds: tuple[float, float] | None = None,
    ) -> InterestResult:
        """计算单条消息的兴趣值。

        Args:
            message: 待计算的消息
            chat_stream: 当前会话流
            precomputed_thresholds: 预计算的 (回复阈值, 动作阈值)，
                传入则跳过阈值调整计算（批量场景避免重复调用）

        Returns:
            兴趣值计算结果
        """
        start_time = time.time()
        stream_id = chat_stream.stream_id

        content = self._extract_text(message)

        semantic_score = await self._calculate_semantic_score(content, message)
        mentioned_score = self._calculate_mentioned_score(message, chat_stream)

        raw_total = (
            semantic_score * self.config.semantic_weight
            + mentioned_score * self.config.mentioned_weight
        )
        total_score = min(raw_total, 1.0)

        if precomputed_thresholds is not None:
            adjusted_reply_threshold, adjusted_action_threshold = precomputed_thresholds
        else:
            adjusted_reply_threshold, adjusted_action_threshold = (
                self._apply_threshold_adjustment(stream_id)
            )

        should_reply = total_score >= adjusted_reply_threshold
        should_take_action = total_score >= adjusted_action_threshold

        calculation_time = time.time() - start_time

        reason_parts = [
            f"语义={semantic_score:.3f}×{self.config.semantic_weight:.1f}",
            f"提及={mentioned_score:.3f}×{self.config.mentioned_weight:.1f}",
            f"总分={total_score:.3f}",
            f"阈值={adjusted_reply_threshold:.3f}",
        ]

        return InterestResult(
            interest_value=total_score,
            should_reply=should_reply,
            should_take_action=should_take_action,
            semantic_score=semantic_score,
            mentioned_score=mentioned_score,
            reason="，".join(reason_parts),
            calculation_time=calculation_time,
        )

    async def calculate_batch(
        self,
        messages: list[Message],
        chat_stream: ChatStream,
    ) -> list[InterestResult]:
        """批量计算消息的兴趣值。

        Args:
            messages: 待计算的消息列表
            chat_stream: 当前会话流

        Returns:
            兴趣值计算结果列表
        """
        if not messages:
            return []

        return [await self.calculate(msg, chat_stream) for msg in messages]

    def on_message_processed(self, stream_id: str, replied: bool) -> None:
        """消息处理完成后调用，更新计数器。

        Args:
            stream_id: 会话流 ID
            replied: 是否回复了此消息
        """
        state = self.get_stream_state(stream_id)

        if replied:
            state.no_reply_count = 0
            self._on_reply_sent(state)
        else:
            state.no_reply_count = min(
                state.no_reply_count + 1, self.config.max_no_reply_count
            )
            if state.post_reply_boost_remaining > 0:
                state.post_reply_boost_remaining -= 1

    def _on_reply_sent(self, state: StreamInterestState) -> None:
        """回复后激活阈值降低机制。

        每次回复都重置 boost 剩余次数，确保连续对话期间阈值持续降低。

        Args:
            state: stream 状态
        """
        if self.config.enable_post_reply_boost:
            state.post_reply_boost_remaining = self.config.post_reply_boost_max_count
            logger.debug(
                f"[回复后机制] 重置连续对话模式，阈值将在接下来 "
                f"{self.config.post_reply_boost_max_count} 条消息中降低"
            )

        if self.config.reply_cooldown_reduction > 0:
            old_count = state.no_reply_count
            state.no_reply_count = max(
                0, state.no_reply_count - self.config.reply_cooldown_reduction
            )
            logger.debug(
                f"[回复后机制] 不回复计数 {old_count} → {state.no_reply_count}"
            )

    def _apply_threshold_adjustment(
        self, stream_id: str
    ) -> tuple[float, float]:
        """应用阈值调整（连续不回复 + 回复后降低）。

        Args:
            stream_id: 会话流 ID

        Returns:
            (调整后的回复阈值, 调整后的动作阈值)
        """
        state = self.get_stream_state(stream_id)

        base_reply = self.config.reply_threshold
        base_action = self.config.action_threshold

        total_reduction = 0.0

        if 0 < state.no_reply_count < self.config.max_no_reply_count:
            boost_per_no_reply = (
                self.config.no_reply_threshold_adjustment
                / self.config.max_no_reply_count
                if self.config.max_no_reply_count > 0
                else 0.0
            )
            no_reply_reduction = state.no_reply_count * boost_per_no_reply
            total_reduction += no_reply_reduction
            logger.debug(
                f"[阈值调整] 连续不回复降低: {no_reply_reduction:.3f} "
                f"(计数: {state.no_reply_count})"
            )

        if self.config.enable_post_reply_boost and state.post_reply_boost_remaining > 0:
            decay_factor = self.config.post_reply_boost_decay_rate ** (
                self.config.post_reply_boost_max_count - state.post_reply_boost_remaining
            )
            post_reply_reduction = self.config.post_reply_threshold_reduction * decay_factor
            total_reduction += post_reply_reduction
            logger.debug(
                f"[阈值调整] 回复后降低: {post_reply_reduction:.3f} "
                f"(剩余: {state.post_reply_boost_remaining}, 衰减: {decay_factor:.2f})"
            )

        adjusted_reply = max(0.0, base_reply - total_reduction)
        adjusted_action = max(0.0, base_action - total_reduction)

        if total_reduction > 0:
            logger.debug(
                f"[阈值调整] 回复阈值 {base_reply:.3f}→{adjusted_reply:.3f} "
                f"(降低{total_reduction:.3f}, no_reply={state.no_reply_count}, "
                f"boost={state.post_reply_boost_remaining})"
            )

        return adjusted_reply, adjusted_action

    async def _calculate_semantic_score(self, content: str, message: Message) -> float:
        """计算语义兴趣度分数。

        仅对纯文本消息计算语义分；表情包/图片/语音等非文本消息
        返回中立值 0.5，避免 VLM 描述文本干扰语义模型判断。

        Args:
            content: 消息文本
            message: 原始消息对象（用于判断消息类型）

        Returns:
            语义兴趣度分数 [0.0, 1.0]
        """
        if not self._semantic_scorer or not self._semantic_scorer.is_loaded:
            return 0.0

        msg_type = getattr(message, "message_type", None)
        if msg_type is not None:
            type_str = str(msg_type).lower()
            if type_str not in ("text", "messagetype.text", ""):
                return 0.5

        if not content or not content.strip():
            return 0.0

        try:
            score = await self._semantic_scorer.score_async(content, timeout=2.0)
            return float(score)
        except Exception as e:
            logger.warning(f"[语义评分] 计算失败: {e}")
            return 0.0

    def _calculate_mentioned_score(self, message: Message, chat_stream: ChatStream) -> float:
        """计算提及分。

        强提及（被@、被回复、私聊）：strong_mention_score
        弱提及（文本匹配名字/别名）：weak_mention_score
        未提及：0.0

        Args:
            message: 消息对象
            chat_stream: 当前会话流

        Returns:
            提及分
        """
        chat_type = str(chat_stream.chat_type).lower()
        if chat_type == "private":
            return self.config.strong_mention_score

        bot_id = chat_stream.bot_id or ""
        if bot_id and self._is_strong_mention(message, bot_id):
            return self.config.strong_mention_score

        nickname, alias_names = self._get_bot_names(chat_stream)
        if nickname and self._text_contains(message, nickname):
            return self.config.weak_mention_score

        for alias in alias_names:
            if alias and self._text_contains(message, alias):
                return self.config.weak_mention_score

        return 0.0

    @staticmethod
    def _is_strong_mention(message: Message, bot_id: str) -> bool:
        """检查是否为强提及（被@或被回复）。

        Args:
            message: 消息对象
            bot_id: 机器人 ID

        Returns:
            True 如果是强提及
        """
        extra = message.extra or {}
        at_users = extra.get("at_users")
        if isinstance(at_users, list):
            for user in at_users:
                if isinstance(user, dict) and str(user.get("user_id", "")) == bot_id:
                    return True
                if isinstance(user, str) and user == bot_id:
                    return True

        text = message.processed_plain_text or ""
        if isinstance(text, str) and f":{bot_id}>" in text:
            return True

        return False

    @staticmethod
    def _text_contains(message: Message, keyword: str) -> bool:
        """检查消息文本是否包含关键词。

        Args:
            message: 消息对象
            keyword: 关键词

        Returns:
            True 如果包含
        """
        keyword = keyword.strip().lower()
        if not keyword:
            return False

        text = message.processed_plain_text
        if not isinstance(text, str) or not text:
            text = str(message.content) if message.content else ""

        return keyword in text.lower()

    @staticmethod
    def _get_bot_names(chat_stream: ChatStream) -> tuple[str, list[str]]:
        """获取 bot 的昵称和别名。

        Args:
            chat_stream: 当前会话流

        Returns:
            (昵称, 别名列表)
        """
        fallback_nickname = (
            chat_stream.bot_nickname.strip()
            if isinstance(chat_stream.bot_nickname, str)
            else ""
        )

        try:
            personality = get_core_config().personality
        except RuntimeError:
            return fallback_nickname, []

        nickname = (
            personality.nickname.strip()
            if isinstance(personality.nickname, str) and personality.nickname.strip()
            else fallback_nickname
        )
        alias_names = [
            alias.strip()
            for alias in personality.alias_names
            if isinstance(alias, str) and alias.strip()
        ]
        return nickname, alias_names

    @staticmethod
    def _extract_text(message: Message) -> str:
        """提取消息文本。

        Args:
            message: 消息对象

        Returns:
            消息文本
        """
        if isinstance(message.processed_plain_text, str) and message.processed_plain_text:
            return message.processed_plain_text
        if isinstance(message.content, str):
            return message.content
        return str(message.content) if message.content else ""

    def get_current_persona_info(self) -> dict[str, Any]:
        """获取当前人设信息，用于语义模型训练。

        Returns:
            人设信息字典
        """
        try:
            personality = get_core_config().personality
        except RuntimeError:
            return {"name": "unknown", "personality": ""}

        return {
            "name": personality.nickname,
            "personality_core": personality.personality_core,
            "personality_side": personality.personality_side,
            "identity": personality.identity,
        }
