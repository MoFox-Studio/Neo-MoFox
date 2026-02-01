"""核心事件总线实现。

本模块提供kernel层的最小化Pub/Sub实现。
支持事件订阅、取消订阅和发布，以及异步处理器。
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Set, Tuple, Union

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


class EventDecision(str, Enum):
    """事件订阅者的决策。

    - SUCCESS: 正常执行完，更新共享参数并交给下一个订阅者
    - STOP: 执行完后立刻终止，不再继续后续订阅者
    - PASS: 跳过（不更新共享参数），直接交给下一个订阅者
    """

    SUCCESS = "SUCCESS"
    STOP = "STOP"
    PASS = "PASS"


EventHandlerResult = Tuple[EventDecision, Any]
EventHandlerCallable = Union[
    Callable[[Event], Union[EventHandlerResult, Tuple[str, Any], Any, Awaitable[Any]]],
    Callable[[Event, Any], Union[EventHandlerResult, Tuple[str, Any], Any, Awaitable[Any]]],
]


@dataclass(frozen=True)
class _Subscriber:
    handler: EventHandlerCallable
    priority: int
    order: int


class EventBus:
    """事件总线，用于发布/订阅模式。

    提供最小化的观察者模式实现，用于事件驱动架构。
    支持异步事件处理器并维护订阅跟踪。

    Example:
        >>> bus = EventBus()
        >>> from src.kernel.event import EventDecision
        >>> async def handler(event: Event, shared):
        ...     print(f"Received: {event.name}")
        ...     return (EventDecision.SUCCESS, shared)
        >>> bus.subscribe("user_login", handler, priority=10)
        >>> await bus.publish(Event(name="user_login", data={"user_id": "123"}), shared={})
    """

    def __init__(self, name: str = "default") -> None:
        """初始化事件总线。

        Args:
            name: 总线名称，用于日志识别
        """
        self.name = name
        # 存储处理器：event_name -> (handler -> subscriber metadata)
        # 使用 dict 便于 O(1) 取消订阅；发布时按 priority + order 做稳定排序
        self._subscribers: Dict[str, Dict[EventHandlerCallable, _Subscriber]] = defaultdict(dict)
        # 跟踪每个处理器订阅的所有事件，便于清理
        self._handler_subscriptions: Dict[EventHandlerCallable, Set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._subscribe_order = 0

    def subscribe(
        self,
        event_name: str,
        handler: EventHandlerCallable,
        priority: int = 0,
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

        # 如果重复订阅同一个 handler，则更新 priority，保持最初 order 以稳定排序
        existing = self._subscribers[event_name].get(handler)
        if existing is None:
            self._subscribe_order += 1
            sub = _Subscriber(handler=handler, priority=int(priority), order=self._subscribe_order)
        else:
            sub = _Subscriber(handler=handler, priority=int(priority), order=existing.order)

        self._subscribers[event_name][handler] = sub
        self._handler_subscriptions[handler].add(event_name)

        handler_name = getattr(handler, "__name__", repr(handler))
        logger.debug(
            f"已将 '{handler_name}' 订阅到事件 '{event_name}' (priority={priority})"
        )

        # 返回取消订阅函数
        def unsubscribe() -> None:
            self.unsubscribe(event_name, handler)

        return unsubscribe

    def unsubscribe(self, event_name: str, handler: EventHandlerCallable) -> bool:
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
                f"在事件 '{event_name}' 中未找到处理器 '{getattr(handler, '__name__', repr(handler))}'"
            )
            return False

        self._subscribers[event_name].pop(handler, None)
        self._handler_subscriptions[handler].discard(event_name)

        # 清理空集合
        if not self._subscribers[event_name]:
            del self._subscribers[event_name]
        if not self._handler_subscriptions[handler]:
            del self._handler_subscriptions[handler]

        logger.debug(
            f"已将 '{getattr(handler, '__name__', repr(handler))}' 从事件 '{event_name}' 取消订阅"
        )
        return True

    def unsubscribe_all(self, handler: EventHandlerCallable) -> int:
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

        logger.debug(
            f"已将 '{getattr(handler, '__name__', repr(handler))}' 从 {count} 个事件取消订阅"
        )
        return count

    async def publish(self, event: Event, shared: Any = None) -> EventHandlerResult:
        """按订阅顺序（priority 从高到低）链式发布事件。

        订阅者必须返回 (decision, next_shared)。
        - SUCCESS: next_shared 会传递给下一个订阅者
        - PASS: 忽略 next_shared，继续执行下一个订阅者（共享参数保持不变）
        - STOP: 设置 shared 为 next_shared 并立刻终止链

        兼容模式：
        - 若订阅者只接受 (event) 也可；若接受 (event, shared) 将收到当前共享参数
        - 若订阅者返回 None/非二元组，按 (SUCCESS, shared) 处理（不改变共享参数）

        Args:
            event: 要发布的事件
            shared: 初始共享参数

        Returns:
            (last_decision, final_shared)

        Raises:
            ValueError: 如果 event 不是 Event 实例
        """
        if not isinstance(event, Event):
            raise ValueError("必须发布Event实例")

        event_name = event.name

        if event_name not in self._subscribers or not self._subscribers[event_name]:
            return (EventDecision.SUCCESS, shared)

        subs = sorted(
            self._subscribers[event_name].values(),
            key=lambda s: (-s.priority, s.order),
        )

        logger.debug(
            f"正在按顺序向 {len(subs)} 个处理器发布事件 '{event_name}'"
        )

        current_shared = shared
        last_decision: EventDecision = EventDecision.SUCCESS

        for sub in subs:
            handler = sub.handler
            try:
                raw_result = await self._execute_handler(handler, event, current_shared)
            except Exception as e:
                logger.error(
                    f"处理器 '{getattr(handler, '__name__', repr(handler))}' 在事件 "
                    f"'{event_name}' 中失败: {e}",
                    exc_info=e,
                )
                last_decision = EventDecision.PASS
                continue

            decision, next_shared = self._normalize_handler_result(raw_result, current_shared)
            last_decision = decision

            if decision == EventDecision.PASS:
                continue

            current_shared = next_shared

            if decision == EventDecision.STOP:
                break

        return (last_decision, current_shared)

    def _normalize_handler_result(self, result: Any, current_shared: Any) -> EventHandlerResult:
        if result is None:
            return (EventDecision.SUCCESS, current_shared)

        if isinstance(result, tuple) and len(result) == 2:
            raw_decision, next_shared = result
            if isinstance(raw_decision, EventDecision):
                return (raw_decision, next_shared)
            if isinstance(raw_decision, str):
                try:
                    return (EventDecision(raw_decision), next_shared)
                except ValueError:
                    logger.warning(f"未知 decision='{raw_decision}'，按 SUCCESS 处理")
                    return (EventDecision.SUCCESS, current_shared)
            logger.warning("decision 类型非法，按 SUCCESS 处理")
            return (EventDecision.SUCCESS, current_shared)

        # 兼容旧处理器：返回非二元组时视为不改变共享参数
        return (EventDecision.SUCCESS, current_shared)

    async def _execute_handler(self, handler: EventHandlerCallable, event: Event, shared: Any) -> Any:
        """执行单个事件处理器。

        Args:
            handler: 要执行的处理器函数
            event: 要传递给处理器的事件

        Returns:
            处理器的返回值或None
        """
        # 根据 handler 签名决定是否传 shared
        try:
            call_with_shared = False
            try:
                sig = inspect.signature(handler)
                params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                # 允许 (event, shared)
                call_with_shared = len(params) >= 2
            except Exception:
                # signature 获取失败时，保守尝试传 shared
                call_with_shared = True

            if call_with_shared:
                result = handler(event, shared)
            else:
                result = handler(event)

            if asyncio.iscoroutine(result):
                result = await result
            return result
        except TypeError:
            # 如果因参数不匹配导致 TypeError，回退到单参调用
            result = handler(event)
            if asyncio.iscoroutine(result):
                result = await result
            return result

    def publish_sync(self, event: Event, shared: Any = None) -> asyncio.Task[EventHandlerResult]:
        """同步发布事件（立即返回）。

        为事件发布创建后台任务。适用于即发即弃场景。

        Args:
            event: 要发布的事件

        Returns:
            将执行发布的任务

        Example:
            >>> bus.publish_sync(Event(name="user_action", data={"action": "click"}))
        """
        task = asyncio.create_task(self.publish(event, shared=shared))
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

    def get_subscribers(self, event_name: str) -> List[EventHandlerCallable]:
        """获取特定事件的订阅者列表。

        Args:
            event_name: 要查询的事件名称

        Returns:
            订阅了该事件的处理器函数列表
        """
        subs = self._subscribers.get(event_name)
        if not subs:
            return []
        ordered = sorted(subs.values(), key=lambda s: (-s.priority, s.order))
        return [s.handler for s in ordered]

    def __repr__(self) -> str:
        """事件总线的字符串表示。"""
        return (
            f"EventBus(name='{self.name}', "
            f"events={self.event_count}, handlers={self.handler_count})"
        )
