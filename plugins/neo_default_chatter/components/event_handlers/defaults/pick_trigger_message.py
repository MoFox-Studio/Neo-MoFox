"""``:pick_trigger_message`` 默认实现——选取本轮工具执行的触发消息。"""

from __future__ import annotations

import time
from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import ChatStream, Message
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent


def _pick_trigger_message(chat_stream: ChatStream, unreads: list[Message]) -> Message:
    """选取本轮工具执行所用的触发消息。

    工具调用需要绑定一条「触发消息」用于权限校验、回复路由、@ 解析等。
    选取顺序为：

    1. 本轮未读消息的最后一条（最常见，对应「用户发问→LLM 工具调用」）
    2. context.current_message（当前激活消息）
    3. context.unread_messages 的最后一条
    4. context.history_messages 的最后一条
    5. 全部为空时构造一个伪空消息，保证工具调用流程可以继续

    Args:
        chat_stream: 已激活的聊天流，用于回退取上下文消息。
        unreads: 本轮 ``fetch_unreads`` 返回的未读消息列表。

    Returns:
        Message: 选中的触发消息。
    """
    if unreads:
        return unreads[-1]

    context = chat_stream.context
    if context.current_message is not None:
        return context.current_message
    if context.unread_messages:
        return context.unread_messages[-1]
    if context.history_messages:
        return context.history_messages[-1]

    return Message(
        message_id=f"neo-chatter-{int(time.time() * 1000)}",
        content="",
        processed_plain_text="",
        platform=chat_stream.platform,
        chat_type=chat_stream.chat_type,
        stream_id=chat_stream.stream_id,
        sender_name="neo_default_chatter",
    )


class PickTriggerMessageDefaultHandler(BaseEventHandler):
    """:pick_trigger_message 的默认实现——选取本轮工具执行的触发消息。

    选取顺序：未读消息末条 → 当前激活消息 → 未读消息末条 → 历史末条 →
    伪空消息。
    """

    name = "pick_trigger_message_default"
    description = "默认 pick_trigger_message：选取本轮工具执行的触发消息"
    weight = 0
    init_subscribe = [NdfcEvent.PICK_TRIGGER_MESSAGE]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 pick_trigger_message，把选中的 Message 填入 payload。"""
        try:
            chat_stream = params["chat_stream"]
            unreads = params.get("unreads") or []
            params["trigger"] = _pick_trigger_message(chat_stream, unreads)
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["PickTriggerMessageDefaultHandler"]
