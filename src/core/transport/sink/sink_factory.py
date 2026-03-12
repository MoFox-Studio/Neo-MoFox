"""CoreSink 工厂模块。

根据 Adapter 配置创建合适的 CoreSink 实例。

注意：AdapterBase 运行时会调用 core_sink.send/push_outgoing/close 等方法，
因此 factory 返回的实例必须实现 mofox_wire.CoreSink 协议。
"""

from typing import TYPE_CHECKING, Any, Callable, Coroutine

from mofox_wire import CoreSink, MessageEnvelope

from src.kernel.logger import get_logger
from src.core.transport.sink.core_sink_impl import InProcessCoreSinkImpl

if TYPE_CHECKING:
    from src.core.components.base.adapter import BaseAdapter

logger = get_logger("sink_factory")


def create_sink_for_adapter(
    adapter: "BaseAdapter",
    message_callback: Callable[[MessageEnvelope], Coroutine[Any, Any, None]],
) -> CoreSink:
    """为 Adapter 创建合适的 CoreSink。

    当前仅支持进程内运行的 Adapter。

    Args:
        adapter: 适配器实例
        message_callback: 消息接收回调函数

    Returns:
        CoreSink: CoreSink 实例

    Raises:
        NotImplementedError: 如果适配器声明 run_in_subprocess=True（该能力已移除）

    Examples:
        >>> sink = create_sink_for_adapter(adapter, callback)
        >>> adapter.core_sink = sink
    """
    if getattr(adapter, "run_in_subprocess", False):
        raise NotImplementedError(
            f"适配器 {adapter.adapter_name} 声明 run_in_subprocess=True，但该能力已移除；"
            "请改为进程内运行或将适配器拆分为独立进程/服务。"
        )

    logger.debug(f"为适配器 {adapter.adapter_name} 创建进程内 CoreSink")
    return InProcessCoreSinkImpl(message_callback)


__all__ = ["create_sink_for_adapter"]
