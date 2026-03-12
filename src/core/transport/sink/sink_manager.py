"""Sink 管理器。

管理所有 CoreSink 的生命周期和消息分发。
连接 AdapterManager 和 MessageReceiver。
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Dict, Any
from mofox_wire import MessageEnvelope

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.transport.message_receive import MessageReceiver

logger = get_logger("sink_manager")


class SinkManager:
    """Sink 管理器。

    负责创建和管理 CoreSink 实例，将 Adapter 连接到消息接收系统。

    Attributes:
        _receiver: 消息接收器引用
        _active_sinks: 活跃的 sink 字典

    Examples:
        >>> receiver = MessageReceiver()
        >>> sink_mgr = SinkManager(receiver)
        >>> await sink_mgr.setup_adapter_sink("my_plugin:adapter:qq")
    """

    def __init__(self, receiver: MessageReceiver) -> None:
        """初始化 Sink 管理器。

        Args:
            receiver: 消息接收器实例
        """
        self._receiver = receiver
        self._active_sinks: Dict[str, Any] = {}
        logger.info("SinkManager 初始化完成")

    async def setup_adapter_sink(self, adapter_signature: str, adapter: Any) -> None:
        """为 Adapter 设置 CoreSink。

        创建 CoreSink 并设置到 Adapter。

        Args:
            adapter_signature: 适配器签名，格式为 "plugin_name:adapter:adapter_name"
            adapter: 适配器实例

        Examples:
            >>> await sink_mgr.setup_adapter_sink("my_plugin:adapter:qq", adapter_instance)
        """
        from src.core.transport.sink.sink_factory import create_sink_for_adapter

        if not adapter:
            logger.error(f"Adapter 实例为空: {adapter_signature}")
            raise ValueError(f"Adapter 实例为空: {adapter_signature}")

        # 创建消息回调
        async def message_callback(envelope: MessageEnvelope) -> None:
            """消息回调函数"""
            await self._receiver.receive_envelope(envelope, adapter_signature)

        # 创建并设置 CoreSink
        try:
            sink = create_sink_for_adapter(adapter, message_callback)
            adapter.core_sink = sink

            self._active_sinks[adapter_signature] = sink
        except NotImplementedError as e:
            logger.warning(f"无法为 Adapter {adapter_signature} 设置 CoreSink: {e}")
            raise

    async def teardown_adapter_sink(self, adapter_signature: str) -> None:
        """移除 Adapter 的 CoreSink。

        Args:
            adapter_signature: 适配器签名

        Examples:
            >>> await sink_mgr.teardown_adapter_sink("my_plugin:adapter:qq")
        """
        if adapter_signature in self._active_sinks:
            sink = self._active_sinks.pop(adapter_signature)
            close = getattr(sink, "close", None)
            if callable(close):
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.error(f"关闭 Adapter {adapter_signature} 的 CoreSink 失败")
            logger.info(f"移除 Adapter {adapter_signature} 的 CoreSink")
        else:
            logger.debug(f"Adapter {adapter_signature} 没有 CoreSink 需要移除")

    def get_sink(self, adapter_signature: str) -> Any:
        """获取 Adapter 的 CoreSink。

        Args:
            adapter_signature: 适配器签名

        Returns:
            CoreSink 实例，如果不存在则返回 None

        Examples:
            >>> sink = sink_mgr.get_sink("my_plugin:adapter:qq")
        """
        return self._active_sinks.get(adapter_signature)

    def list_active_sinks(self) -> list[str]:
        """列出所有活跃的 Sink。

        Returns:
            list[str]: 适配器签名列表

        Examples:
            >>> sinks = sink_mgr.list_active_sinks()
        """
        return list(self._active_sinks.keys())


# 全局单例
_global_sink_manager: "SinkManager | None" = None


def get_sink_manager() -> "SinkManager":
    """获取全局 SinkManager 单例。

    Returns:
        SinkManager: 全局 SinkManager 单例

    Raises:
        RuntimeError: 如果 SinkManager 未初始化

    Examples:
        >>> sink_mgr = get_sink_manager()
    """
    global _global_sink_manager
    if _global_sink_manager is None:
        raise RuntimeError(
            "SinkManager 未初始化，请先初始化 MessageReceiver"
        )
    return _global_sink_manager


def set_sink_manager(sink_manager: "SinkManager") -> None:
    """设置全局 SinkManager 单例。

    Args:
        sink_manager: SinkManager 实例

    Examples:
        >>> set_sink_manager(SinkManager(receiver))
    """
    global _global_sink_manager
    _global_sink_manager = sink_manager


def reset_sink_manager() -> None:
    """重置全局 SinkManager。

    主要用于测试场景，确保测试之间不会相互影响。

    Examples:
        >>> reset_sink_manager()
    """
    global _global_sink_manager
    _global_sink_manager = None


__all__ = [
    "SinkManager",
    "get_sink_manager",
    "set_sink_manager",
    "reset_sink_manager",
]
