"""Default Chatter 概率门模块。

封装 sub-agent 概率直通逻辑，包括：
- next_tick bonus 的读写（发送成功后给下一轮的概率加成）
- 概率计算（基础概率 + 强/弱提及 + 未读加成 + next_tick bonus）
"""

from __future__ import annotations

import random

from src.app.plugin_system.types import ChatStream, Message
from src.core.config import get_core_config

from ..components.config import DefaultChatterConfig

_SUB_AGENT_NEXT_TICK_BONUS_ATTR = "_default_chatter_next_tick_bonus"


def set_next_tick_sub_agent_bonus(chat_stream: ChatStream, bonus: float) -> None:
    """为下一次群聊 sub-agent 判定写入概率加成。

    多次写入取最大值，避免覆盖更高的加成。

    Args:
        chat_stream: 当前聊天流
        bonus: 概率加成值
    """
    current_bonus = getattr(chat_stream.context, _SUB_AGENT_NEXT_TICK_BONUS_ATTR, 0.0)
    setattr(
        chat_stream.context,
        _SUB_AGENT_NEXT_TICK_BONUS_ATTR,
        max(float(current_bonus), bonus),
    )


def consume_next_tick_sub_agent_bonus(chat_stream: ChatStream) -> float:
    """读取并清空下一次群聊 sub-agent 判定的概率加成。

    Args:
        chat_stream: 当前聊天流

    Returns:
        被消耗的概率加成值
    """
    bonus = float(getattr(chat_stream.context, _SUB_AGENT_NEXT_TICK_BONUS_ATTR, 0.0))
    setattr(chat_stream.context, _SUB_AGENT_NEXT_TICK_BONUS_ATTR, 0.0)
    return bonus


def message_text_for_probability(message: Message) -> str:
    """提取消息文本，供 sub-agent 概率门做关键词判定。

    Args:
        message: 消息对象

    Returns:
        用于概率判定的纯文本
    """
    if isinstance(message.processed_plain_text, str) and message.processed_plain_text:
        return message.processed_plain_text
    if isinstance(message.content, str):
        return message.content
    return str(message.content)


def messages_contain_any_name(
    unread_msgs: list[Message],
    names: list[str],
) -> bool:
    """判断任意未读消息是否包含指定名字或别名。

    Args:
        unread_msgs: 未读消息列表
        names: 要检查的名字/别名列表

    Returns:
        True 如果任意消息包含任一名字
    """
    normalized_names = [name.strip().lower() for name in names if name.strip()]
    if not normalized_names:
        return False

    for unread_msg in unread_msgs:
        lowered_text = message_text_for_probability(unread_msg).lower()
        if any(name in lowered_text for name in normalized_names):
            return True
    return False


def get_sub_agent_identity_names(chat_stream: ChatStream) -> tuple[str, list[str]]:
    """获取 sub-agent 概率门使用的 bot 名字与别名。

    Args:
        chat_stream: 当前聊天流

    Returns:
        (nickname, alias_names)
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
        alias_name.strip()
        for alias_name in personality.alias_names
        if isinstance(alias_name, str) and alias_name.strip()
    ]
    return nickname, alias_names


def messages_contain_strong_mention(unread_msgs: list[Message], bot_id: str) -> bool:
    """判断是否有未读消息强提及了 bot（被 @ 或被回复）。

    Args:
        unread_msgs: 未读消息列表
        bot_id: 机器人 ID

    Returns:
        True 如果有消息强提及了 bot
    """
    for msg in unread_msgs:
        # 1. 检查 @ 列表
        extra = msg.extra or {}
        at_users = extra.get("at_users")
        if isinstance(at_users, list):
            for user in at_users:
                if isinstance(user, dict) and str(user.get("user_id", "")) == bot_id:
                    return True
                if isinstance(user, str) and user == bot_id:
                    return True

        # 2. 检查文本中的 @ 标记
        text = msg.processed_plain_text or ""
        if isinstance(text, str) and f":{bot_id}>" in text:
            return True

        # 3. 检查是否回复了 Bot 的消息
        reply_to = getattr(msg, "reply_to", None)
        if reply_to:
            target_sender = getattr(reply_to, "sender_id", None)
            if str(target_sender) == bot_id:
                return True

    return False


def compute_sub_agent_bypass_probability(
    unread_msgs: list[Message],
    chat_stream: ChatStream,
    plugin_config: DefaultChatterConfig,
) -> tuple[float, str]:
    """计算本地概率直通 sub-agent 的放行概率。

    Args:
        unread_msgs: 未读消息列表
        chat_stream: 当前聊天流
        plugin_config: 插件配置

    Returns:
        (概率值 0~1, 概率构成描述)
    """
    nickname, alias_names = get_sub_agent_identity_names(chat_stream)
    bot_id = chat_stream.bot_id or ""

    prob_cfg = plugin_config.plugin.programmatic_probability

    base_prob = prob_cfg.base_bypass_probability
    name_bonus = prob_cfg.name_mention_bonus
    alias_bonus = prob_cfg.alias_mention_bonus
    unread_per_msg_bonus = prob_cfg.unread_message_bonus

    probability = base_prob
    reasons = [f"基础概率 {base_prob:.2f}"]

    # 强提及加成：被 @ 或 被回复 (应用 name_mention_bonus)
    if bot_id and messages_contain_strong_mention(unread_msgs, bot_id):
        probability += name_bonus
        reasons.append(f"强提及(at/回复) +{name_bonus:.2f}")
    # 弱提及加成：文本命中全名或别名 (应用 alias_mention_bonus)
    elif nickname or alias_names:
        search_names = ([nickname] if nickname else []) + alias_names
        if messages_contain_any_name(unread_msgs, search_names):
            probability += alias_bonus
            reasons.append(f"弱提及(名字/别名) +{alias_bonus:.2f}")

    unread_bonus = len(unread_msgs) * unread_per_msg_bonus
    if unread_bonus > 0:
        probability += unread_bonus
        reasons.append(
            f"{len(unread_msgs)} 条未读 +{unread_bonus:.2f}"
        )

    next_tick_bonus = consume_next_tick_sub_agent_bonus(chat_stream)
    if next_tick_bonus > 0:
        probability += next_tick_bonus
        reasons.append(f"上次回复后的下一 tick +{next_tick_bonus:.2f}")

    capped_probability = min(probability, 1.0)
    if capped_probability != probability:
        reasons.append("封顶 1.00")

    return capped_probability, "，".join(reasons)


def should_bypass_via_probability(
    unread_msgs: list[Message],
    chat_stream: ChatStream,
    plugin_config: DefaultChatterConfig,
) -> tuple[bool, str]:
    """概率门判定：是否直接放行（跳过 sub-agent LLM 决策）。

    Args:
        unread_msgs: 未读消息列表
        chat_stream: 当前聊天流
        plugin_config: 插件配置

    Returns:
        (是否放行, 放行原因描述)
    """
    bypass_probability, bypass_reason = compute_sub_agent_bypass_probability(
        unread_msgs, chat_stream, plugin_config
    )
    if random.random() < bypass_probability:
        return True, bypass_reason
    return False, bypass_reason
