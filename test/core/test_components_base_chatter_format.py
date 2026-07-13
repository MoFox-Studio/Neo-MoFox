"""BaseChatter 消息格式化测试。"""

from __future__ import annotations

from datetime import datetime

from src.core.components.base.chatter import BaseChatter
from src.core.models.message import Message


def test_format_message_line_uses_message_fields_directly() -> None:
    """测试格式化消息行时读取 Message 的明确字段。"""
    message = Message(
        message_id="msg_1",
        time=datetime(2024, 1, 1, 9, 5).timestamp(),
        content="原始内容",
        processed_plain_text="处理后内容",
        sender_id="user_1",
        sender_name="Alice",
        sender_cardname="A-card",
        sender_role="member",
    )

    line = BaseChatter.format_message_line(message, time_format="%H:%M")

    assert line == "【09:05】<成员> [user_1] 昵称:Alice$群名片:A-card [msg_1]： 处理后内容"
