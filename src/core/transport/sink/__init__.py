"""Sink 模块。

提供 CoreSink 实现和管理器，连接 Adapter 和 MessageReceiver。
"""

from src.core.transport.sink.core_sink_impl import InProcessCoreSinkImpl
from src.core.transport.sink.sink_factory import create_sink_for_adapter
from src.core.transport.sink.sink_manager import (
    SinkManager,
    get_sink_manager,
    reset_sink_manager,
    set_sink_manager,
)

__all__ = [
    # CoreSink 实现
    "InProcessCoreSinkImpl",
    # Sink 工厂
    "create_sink_for_adapter",
    # Sink 管理器
    "SinkManager",
    "get_sink_manager",
    "set_sink_manager",
    "reset_sink_manager",
]
