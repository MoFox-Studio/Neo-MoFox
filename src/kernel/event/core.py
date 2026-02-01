"""核心事件总线实现。

本模块提供kernel层的最小化Pub/Sub实现。
支持事件订阅、取消订阅和发布，以及异步处理器。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Set
from collections import defaultdict

from src.kernel.logger import get_logger, COLOR

logger = get_logger("event_bus", display="EventBus", color=COLOR.MAGENTA)


@dataclass
class Event:
    """事件数据结构。

    Attributes:
        name: 事件名称/标识符
        data: 事件负载数据（可以是任意类型）
        source: 可选的事件源标识符
    """

    name: str
    data: Any = None
    source: str | None = None

    def __post_init__(self):
        """初始化后验证事件名称。"""
        if not self.name or not isinstance(self.name, str):
            raise ValueError("事件名称必须是非空字符串")


EventHandler = Callable[[Event], Any]


class EventBus:
    """事件总线，用于发布/订阅模式。

    提供最小化的观察者模式实现，用于事件驱动架构。
    支持异步事件处理器并维护订阅跟踪。

    Example:
        >>> bus = EventBus()
        >>> async def handler(event: Event):
        ...     print(f"Received: {event.name}")
        >>> bus.subscribe("user_login", handler)
        >>> await bus.publish(Event(name="user_login", data={"user_id": "123"}))
    """

    def __init__(self, name: str = "default") -> None:
        """初始化事件总线。

        Args:
            name: 总线名称，用于日志识别
        """
        self.name = name
        # 存储处理器：event_name -> 处理器函数集合
        self._subscribers: Dict[str, Set[EventHandler]] = defaultdict(set)
        # 跟踪每个处理器订阅的所有事件，便于清理
        self._handler_subscriptions: Dict[EventHandler, Set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    def subscribe(
        self,
        event_name: str,
        handler: EventHandler,
    ) -> Callable[[], None]:
        """订阅事件。

        Args:
            event_name: 要订阅的事件名称
            handler: 接受Event参数的异步或同步可调用对象

        Returns:
            取消订阅函数，调用可移除此订阅

        Raises:
            ValueError: 如果event_name为空或handler不可调用
        """
        if not event_name:
            raise ValueError("事件名称不能为空")
        if not callable(handler):
            raise ValueError("处理器必须是可调用对象")

        self._subscribers[event_name].add(handler)
        self._handler_subscriptions[handler].add(event_name)

        logger.debug(f"已将 '{handler.__name__}' 订阅到事件 '{event_name}'")

        # 返回取消订阅函数
        def unsubscribe() -> None:
            self.unsubscribe(event_name, handler)

        return unsubscribe

    def unsubscribe(self, event_name: str, handler: EventHandler) -> bool:
        """从事件中取消订阅处理器。

        Args:
            event_name: 要取消订阅的事件名称
            handler: 要移除的处理器函数

        Returns:
            如果找到并移除处理器则返回True，否则返回False
        """
        if event_name not in self._subscribers:
            logger.warning(
                f"无法从未知事件 '{event_name}' 取消订阅"
            )
            return False

        if handler not in self._subscribers[event_name]:
            logger.warning(
                f"在事件 '{event_name}' 中未找到处理器 '{handler.__name__}'"
            )
            return False

        self._subscribers[event_name].discard(handler)
        self._handler_subscriptions[handler].discard(event_name)

        # 清理空集合
        if not self._subscribers[event_name]:
            del self._subscribers[event_name]
        if not self._handler_subscriptions[handler]:
            del self._handler_subscriptions[handler]

        logger.debug(f"已将 '{handler.__name__}' 从事件 '{event_name}' 取消订阅")
        return True

    def unsubscribe_all(self, handler: EventHandler) -> int:
        """从所有事件中取消订阅处理器。

        Args:
            handler: 要从所有订阅中移除的处理器函数

        Returns:
            移除的订阅数量
        """
        if handler not in self._handler_subscriptions:
            return 0

        event_names = list(self._handler_subscriptions[handler])
        count = 0

        for event_name in event_names:
            if self.unsubscribe(event_name, handler):
                count += 1

        logger.debug(f"已将 '{handler.__name__}' 从 {count} 个事件取消订阅")
        return count

    async def publish(self, event: Event) -> int:
        """向所有订阅者发布事件。

        所有处理器都被异步调用。如果处理器引发异常，
        会记录该异常但不会阻止其他处理器的调用。

        Args:
            event: 要发布的事件

        Returns:
            收到通知的处理器数量

        Raises:
            ValueError: 如果event不是Event实例
        """
        if not isinstance(event, Event):
            raise ValueError("必须发布Event实例")

        event_name = event.name

        if event_name not in self._subscribers:
            return 0

        handlers = list(self._subscribers[event_name])
        logger.debug(
            f"正在向 {len(handlers)} 个处理器发布事件 '{event_name}'"
        )

        # 异步执行所有处理器
        tasks = []
        for handler in handlers:
            tasks.append(self._execute_handler(handler, event))

        # 等待所有处理器完成
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 记录任何异常
        success_count = 0
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error(
                    f"处理器 '{handler.__name__}' 在事件 "
                    f"'{event_name}' 中失败: {result}",
                    exc_info=result if isinstance(result, Exception) else None,
                )
            else:
                success_count += 1

        return success_count

    async def _execute_handler(self, handler: EventHandler, event: Event) -> Any:
        """执行单个事件处理器。

        Args:
            handler: 要执行的处理器函数
            event: 要传递给处理器的事件

        Returns:
            处理器的返回值或None
        """
        try:
            result = handler(event)
            # 如果处理器返回协程，则等待它
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            logger.error(
                f"执行处理器 '{handler.__name__}' 时出错 "
                f"(事件 '{event.name}'): {e}",
                exc_info=e,
            )
            raise

    def publish_sync(self, event: Event) -> asyncio.Task[int]:
        """同步发布事件（立即返回）。

        为事件发布创建后台任务。适用于即发即弃场景。

        Args:
            event: 要发布的事件

        Returns:
            将执行发布的任务

        Example:
            >>> bus.publish_sync(Event(name="user_action", data={"action": "click"}))
        """
        task = asyncio.create_task(self.publish(event))
        return task

    @property
    def subscribed_events(self) -> Set[str]:
        """获取所有有订阅者的事件名称集合。"""
        return set(self._subscribers.keys())

    @property
    def handler_count(self) -> int:
        """获取所有事件中订阅的处理器总数。"""
        return sum(len(handlers) for handlers in self._subscribers.values())

    @property
    def event_count(self) -> int:
        """获取有订阅者的唯一事件数量。"""
        return len(self._subscribers)

    def clear(self) -> None:
        """清除所有订阅。

        用于测试或重置总线状态。
        """
        self._subscribers.clear()
        self._handler_subscriptions.clear()
        logger.debug(f"已从事件总线 '{self.name}' 清除所有订阅")

    def get_subscribers(self, event_name: str) -> List[EventHandler]:
        """获取特定事件的订阅者列表。

        Args:
            event_name: 要查询的事件名称

        Returns:
            订阅了该事件的处理器函数列表
        """
        return list(self._subscribers.get(event_name, set()))

    def __repr__(self) -> str:
        """事件总线的字符串表示。"""
        return (
            f"EventBus(name='{self.name}', "
            f"events={self.event_count}, handlers={self.handler_count})"
        )
