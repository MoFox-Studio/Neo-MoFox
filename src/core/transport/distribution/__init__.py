"""消息分发模块。

负责将接收到的消息分发到对应的聊天流，并启动/管理 Tick 驱动器。

核心概念：
- ConversationTick: 表示一次待处理的会话事件
- conversation_loop: 异步生成器，按需产出 Tick 事件
- StreamLoopManager: 管理所有聊天流的驱动器生命周期
- MessageDistributor: 订阅 ON_MESSAGE_RECEIVED 事件，将消息注入流并启动驱动器

公共接口：
- ``get_stream_loop_manager()``: 获取全局 StreamLoopManager 单例
- ``initialize_distribution()``: 初始化分发模块（订阅事件）
"""

from __future__ import annotations

from src.core.transport.distribution.tick import ConversationTick
from src.core.transport.distribution.stream_loop_manager import (
    StreamLoopManager,
    get_stream_loop_manager,
)
from src.core.transport.distribution.distributor import initialize_distribution

__all__ = [
    "ConversationTick",
    "StreamLoopManager",
    "get_stream_loop_manager",
    "initialize_distribution",
]
