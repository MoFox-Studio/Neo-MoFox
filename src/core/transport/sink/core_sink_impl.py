"""CoreSink 实现模块。

提供进程内和进程间的 CoreSink 实现，作为 Adapter 与核心消息系统的桥梁。

注意：这里的 CoreSink 必须与 mofox_wire.adapter_utils.CoreSink 协议保持一致，
至少实现 send/push_outgoing/close 等方法；否则 AdapterBase 会在运行时崩溃。
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Coroutine

from mofox_wire import CoreSink, MessageEnvelope

from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

logger = get_logger("core_sink_impl")

OutgoingHandler = Callable[[MessageEnvelope], Coroutine[Any, Any, None]]
IncomingHandler = Callable[[MessageEnvelope], Coroutine[Any, Any, None]]


class InProcessCoreSinkImpl(CoreSink):
    """进程内 CoreSink 实现。

    - send/send_many: 适配器 -> 核心
    - push_outgoing: 核心 -> 适配器

    Args:
        message_callback: 核心侧接收 incoming envelope 的异步回调
    """

    def __init__(self, message_callback: IncomingHandler) -> None:
        self._message_callback = message_callback
        self._outgoing_handlers: set[OutgoingHandler] = set()
        self._task_ids: set[str] = set()
        logger.debug("InProcessCoreSinkImpl 初始化完成")

    def set_outgoing_handler(self, handler: OutgoingHandler | None) -> None:
        if handler is None:
            return
        self._outgoing_handlers.add(handler)

    def remove_outgoing_handler(self, handler: OutgoingHandler) -> None:
        self._outgoing_handlers.discard(handler)

    async def send(self, message: MessageEnvelope) -> None:
        message_id = message.get("message_info", {}).get("message_id", "unknown")
        tm = get_task_manager()
        task_info = tm.create_task(
            self._message_callback(message),
            name=f"core_sink_incoming_{message_id}",
            daemon=True,
        )
        self._task_ids.add(task_info.task_id)

    async def send_many(self, messages: list[MessageEnvelope]) -> None:
        for message in messages:
            await self.send(message)

    async def push_outgoing(self, envelope: MessageEnvelope) -> None:
        if not self._outgoing_handlers:
            return
        message_id = envelope.get("message_info", {}).get("message_id", "unknown")
        tm = get_task_manager()
        for handler in list(self._outgoing_handlers):
            task_info = tm.create_task(
                handler(envelope),
                name=f"core_sink_outgoing_{message_id}",
                daemon=True,
            )
            self._task_ids.add(task_info.task_id)

    async def close(self) -> None:
        tm = get_task_manager()
        for task_id in list(self._task_ids):
            with contextlib.suppress(Exception):
                tm.cancel_task(task_id)
        self._task_ids.clear()
        self._outgoing_handlers.clear()

    async def send_upstream(self, envelope: MessageEnvelope) -> None:
        """兼容旧命名：等价于 send()."""

        await self.send(envelope)

__all__ = ["InProcessCoreSinkImpl"]
