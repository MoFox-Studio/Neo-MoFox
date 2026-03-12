"""消息接收模块。

提供 ``MessageEnvelope`` → ``Message`` 的转换以及消息接收分发功能。

公共接口：
- ``MessageConverter``: 双向转换器（也被 message_send 复用）
- ``MessageReceiver``: 接收并分发消息
- ``get_message_receiver()``: 获取全局单例
- ``init_message_receiver()``: 初始化全局单例
"""

from __future__ import annotations

from src.core.transport.message_receive.converter import MessageConverter
from src.core.transport.message_receive.receiver import MessageReceiver

# ──────────────────────────────────────────────
# 全局单例管理
# ──────────────────────────────────────────────

_global_receiver: MessageReceiver | None = None


def get_message_receiver() -> MessageReceiver:
    """获取全局 MessageReceiver 单例。

    Returns:
        MessageReceiver: 消息接收器实例

    Raises:
        RuntimeError: 未调用 ``init_message_receiver()`` 初始化

    Examples:
        >>> receiver = get_message_receiver()
        >>> await receiver.receive_envelope(envelope, "plugin:adapter:qq")
    """
    if _global_receiver is None:
        raise RuntimeError(
            "MessageReceiver 未初始化，请先调用 init_message_receiver()"
        )
    return _global_receiver


def init_message_receiver(
    converter: MessageConverter | None = None,
) -> MessageReceiver:
    """初始化全局 MessageReceiver 单例。

    如果已经初始化则直接返回现有实例。

    Args:
        converter: 可选的 MessageConverter 实例

    Returns:
        MessageReceiver: 初始化后的单例

    Examples:
        >>> receiver = init_message_receiver()
    """
    global _global_receiver

    if _global_receiver is not None:
        return _global_receiver

    _converter = converter or MessageConverter()
    _global_receiver = MessageReceiver(_converter)
    return _global_receiver


def reset_message_receiver() -> None:
    """重置全局 MessageReceiver（仅供测试使用）。"""
    global _global_receiver
    _global_receiver = None


__all__ = [
    "MessageConverter",
    "MessageReceiver",
    "get_message_receiver",
    "init_message_receiver",
    "reset_message_receiver",
]
