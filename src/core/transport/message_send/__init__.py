"""消息发送模块。

负责将 Message 发送到正确的 Adapter。
"""

from src.core.transport.message_send.message_sender import (
    MessageSender,
    get_message_sender,
    reset_message_sender,
    set_message_sender,
)

__all__ = [
    "MessageSender",
    "get_message_sender",
    "set_message_sender",
    "reset_message_sender",
]
