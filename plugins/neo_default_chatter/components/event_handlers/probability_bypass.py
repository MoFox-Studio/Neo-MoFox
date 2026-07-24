"""Neo-Default-Chatter 预处理事件处理器：概率直通。

订阅 ``neo_default_chatter:preprocess`` 事件，按移植自 default_chatter ``probability_gate``
的概率门逻辑决定是否直接放行给主 chatter，跳过后续 sub_agent LLM 决策处理器。

命中放行概率 → 设置 ``proceed=True`` 并返回 ``EventDecision.STOP``，
让 EventBus 不再调用后续（sub_agent）处理器；未命中 → 返回
``EventDecision.SUCCESS`` 保持 ``proceed`` 不变，让 sub_agent 处理器继续判定。

所有概率计算与提及识别逻辑均自包含在本处理器内部，不依赖额外工具模块。
"""

from __future__ import annotations

import random
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import ChatStream, Message
from src.core.config import get_core_config
from src.kernel.event import EventDecision

from ..config import NeoChatterConfig

logger = get_logger("neo_default_chatter.preprocess.probability_bypass")

#: NFC 预处理事件名。
_PREPROCESS_EVENT = "neo_default_chatter:preprocess"


class ProbabilityBypassHandler(BaseEventHandler):
    """概率直通处理器。

    在 ``neo_default_chatter:preprocess`` 事件中按概率决定是否直接放行给主 chatter，
    跳过后续 sub_agent LLM 决策处理器。

    概率构成（移植自 default_chatter ``probability_gate``）：

    - 基础概率 ``base_bypass_probability``
    - 强提及加成 ``name_mention_bonus``（未读消息精准 @ 机器人）
    - 弱提及加成 ``alias_mention_bonus``（文本命中机器人昵称 / 别名）
    - 未读数加成 ``unread_message_bonus * len(unreads)``

    最终概率封顶 ``1.0``。

    Class Attributes:
        weight: 100，高于 sub_agent 处理器，确保先执行。
        init_subscribe: 订阅 ``neo_default_chatter:preprocess`` 事件。
    """

    name = "probability_bypass"
    description = "概率直通处理器 - 按概率跳过 sub_agent LLM 决策直接放行"
    weight = 100
    init_subscribe = [_PREPROCESS_EVENT]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行概率直通判定。

        Args:
            event_name: 事件名称（由 EventBus 传入）。
            params: 事件参数字典，包含 ``unreads`` / ``chat_stream`` / ``config``
                以及预填的决策字段 ``proceed`` / ``reason`` 等。

        Returns:
            ``(EventDecision, params)``：

            - 命中放行概率 → ``(STOP, params)``，``proceed=True``、``reason`` 写入概率构成；
            - 未命中 / 未启用 / 无未读 → ``(SUCCESS, params)``，``proceed`` 保持默认 ``False``，
              让后续 sub_agent 处理器继续判定。
        """
        cfg = self._get_bypass_config(params)
        if cfg is None or not bool(cfg.enabled):
            return EventDecision.SUCCESS, params

        unreads = self._extract_unreads(params)
        chat_stream = params.get("chat_stream")
        if not unreads or not isinstance(chat_stream, ChatStream):
            return EventDecision.SUCCESS, params

        probability, reason = self._compute_probability(unreads, chat_stream, cfg)

        if random.random() < probability:
            params["proceed"] = True
            params["reason"] = f"概率直通命中 (prob={probability:.2f}): {reason}"
            logger.info(
                f"[概率直通] 概率直通命中 stream={chat_stream.stream_id[:8]} "
                f"prob={probability:.2f} ({reason})"
            )
            return EventDecision.STOP, params

        logger.debug(
            f"[概率直通] 概率直通未命中 stream={chat_stream.stream_id[:8]} "
            f"prob={probability:.2f} ({reason}) → 交由 sub_agent 判定"
        )
        return EventDecision.SUCCESS, params

    # ==================== 私有辅助：所有处理逻辑自包含 ====================

    @staticmethod
    def _get_bypass_config(
        params: dict[str, Any]
    ) -> "NeoChatterConfig.PluginSection.PreprocessProbabilityBypassSection | None":
        """从 ``params['config']`` 读取 概率直通配置子节。

        Args:
            params: 事件参数字典。

        Returns:
            概率直通配置子节实例；配置缺失或类型不匹配时返回 ``None``。
        """
        config = params.get("config")
        if not isinstance(config, NeoChatterConfig):
            return None
        return config.plugin.preprocess_probability_bypass

    @staticmethod
    def _extract_unreads(params: dict[str, Any]) -> list[Message]:
        """从 ``params['unreads']`` 取未读消息列表。

        Args:
            params: 事件参数字典。

        Returns:
            合法的 :class:`Message` 列表；类型不匹配时返回空列表。
        """
        raw = params.get("unreads")
        if not isinstance(raw, list):
            return []
        return [msg for msg in raw if isinstance(msg, Message)]

    def _compute_probability(
        self,
        unreads: list[Message],
        chat_stream: ChatStream,
        cfg: "NeoChatterConfig.PluginSection.PreprocessProbabilityBypassSection",
    ) -> tuple[float, str]:
        """计算放行概率。

        Args:
            unreads: 本轮未读消息。
            chat_stream: 当前聊天流。
            cfg: 概率直通配置子节。

        Returns:
            ``(概率 0~1, 概率构成描述)``。
        """
        probability = float(cfg.base_bypass_probability)
        reasons: list[str] = [f"基础 {cfg.base_bypass_probability:.2f}"]

        bot_id = (chat_stream.bot_id or "").strip()
        nickname, alias_names = self._get_identity_names(chat_stream)

        if bot_id and self._has_strong_mention(unreads, bot_id):
            probability += float(cfg.name_mention_bonus)
            reasons.append(f"强提及(at) +{cfg.name_mention_bonus:.2f}")
        elif (nickname or alias_names) and self._has_weak_mention(
            unreads, nickname, alias_names
        ):
            probability += float(cfg.alias_mention_bonus)
            reasons.append(f"弱提及(名字/别名) +{cfg.alias_mention_bonus:.2f}")

        unread_bonus = len(unreads) * float(cfg.unread_message_bonus)
        if unread_bonus > 0:
            probability += unread_bonus
            reasons.append(f"{len(unreads)} 条未读 +{unread_bonus:.2f}")

        capped = min(probability, 1.0)
        if capped != probability:
            reasons.append("封顶 1.00")
        return capped, "，".join(reasons)

    @staticmethod
    def _get_identity_names(chat_stream: ChatStream) -> tuple[str, list[str]]:
        """获取 bot 昵称与别名。

        优先用 ``personality`` 配置，缺失时回退到 ``chat_stream.bot_nickname``。

        Args:
            chat_stream: 当前聊天流。

        Returns:
            ``(nickname, alias_names)``。
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
    def _message_text(message: Message) -> str:
        """提取消息文本，供关键词判定。

        Args:
            message: 消息对象。

        Returns:
            用于概率判定的纯文本。
        """
        if isinstance(message.processed_plain_text, str) and message.processed_plain_text:
            return message.processed_plain_text
        if isinstance(message.content, str):
            return message.content
        return str(message.content)

    @staticmethod
    def _has_strong_mention(unreads: list[Message], bot_id: str) -> bool:
        """判断未读消息是否强提及了 bot。

        强提及判定：``extra.at_users`` 命中 ``bot_id``，或文本中出现 ``:<bot_id>>``
        形式的 @ 标记。

        Args:
            unreads: 未读消息列表。
            bot_id: 机器人 ID。

        Returns:
            任一消息强提及了 bot 时返回 ``True``。
        """
        for msg in unreads:
            extra = msg.extra or {}
            at_users = extra.get("at_users")
            if isinstance(at_users, list):
                for user in at_users:
                    if isinstance(user, dict) and str(user.get("user_id", "")) == bot_id:
                        return True
                    if isinstance(user, str) and user == bot_id:
                        return True
            text = msg.processed_plain_text or ""
            if isinstance(text, str) and f":{bot_id}>" in text:
                return True
        return False

    @staticmethod
    def _has_weak_mention(
        unreads: list[Message],
        nickname: str,
        alias_names: list[str],
    ) -> bool:
        """判断未读消息是否弱提及了 bot。

        弱提及判定：消息文本（小写）命中机器人昵称或任一别名（小写）。

        Args:
            unreads: 未读消息列表。
            nickname: 机器人昵称。
            alias_names: 机器人别名列表。

        Returns:
            任一消息文本命中任一名字时返回 ``True``。
        """
        names = [
            name.lower()
            for name in ([nickname] if nickname else []) + alias_names
            if isinstance(name, str) and name.strip()
        ]
        if not names:
            return False
        for msg in unreads:
            text = ProbabilityBypassHandler._message_text(msg).lower()
            if any(name in text for name in names):
                return True
        return False
