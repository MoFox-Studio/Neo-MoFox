"""Transport 层 - 消息传输与路由系统。

负责核心与适配器之间的通信和数据传输，包括：
- MessageConverter: MessageEnvelope 与 Message 之间的双向转换
- MessageReceiver: 接收并处理来自 Adapter 的消息
- MessageSender: 发送消息到 Adapter
- SinkManager: 管理 CoreSink，连接 Adapter 和 MessageReceiver
"""

# 消息接收模块
from src.core.transport.message_receive import (
    MessageConverter,
    MessageReceiver,
    get_message_receiver,
    init_message_receiver,
)

# 消息发送模块
from src.core.transport.message_send import (
    MessageSender,
    get_message_sender,
)

# Sink 模块
from src.core.transport.sink import (
    SinkManager,
    get_sink_manager,
)

# Router 模块
from src.core.transport.router import (
    HTTPServer,
    get_http_server,
)

# 消息分发模块
from src.core.transport.distribution import (
    StreamLoopManager,
    get_stream_loop_manager,
    initialize_distribution,
)

__all__ = [
    # 消息接收
    "MessageReceiver",
    "MessageConverter",
    "get_message_receiver",
    "init_message_receiver",
    # 消息发送
    "MessageSender",
    "get_message_sender",
    # Sink 管理
    "SinkManager",
    "get_sink_manager",
    # HTTP 服务
    "HTTPServer",
    "get_http_server",
    # 消息分发
    "StreamLoopManager",
    "get_stream_loop_manager",
    "initialize_distribution",
]
