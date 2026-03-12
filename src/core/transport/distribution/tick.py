"""Tick 数据模型。

定义 ``ConversationTick``，表示一次待处理的会话事件。
这是一个轻量级的事件信号，不存储消息数据本身。
"""

import time
from dataclasses import dataclass, field


@dataclass
class ConversationTick:
    """会话事件标记 — 表示一次待处理的会话事件。

    作为异步生成器 ``conversation_loop`` 产出的最小事件单元，
    由驱动器 ``run_chat_stream`` 消费并触发 Chatter 处理。

    Attributes:
        stream_id: 所属聊天流 ID
        tick_time: Tick 产出的时间戳
        force_dispatch: 是否为强制分发（未读消息超阈值）
        tick_count: 当前流的累计 Tick 计数

    Examples:
        >>> tick = ConversationTick(stream_id="abc123", tick_count=1)
    """

    stream_id: str
    tick_time: float = field(default_factory=time.time)
    force_dispatch: bool = False
    tick_count: int = 0
