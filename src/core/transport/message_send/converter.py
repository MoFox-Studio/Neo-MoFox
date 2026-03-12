"""消息转换器（发送方向）。

复用 message_receive/converter.py 的双向转换功能。
"""

from src.core.transport.message_receive.converter import MessageConverter

# 复用同一个转换器类
__all__ = ["MessageConverter"]
