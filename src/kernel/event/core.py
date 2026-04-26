"""核心事件总线实现。

本模块提供kernel层的最小化Pub/Sub实现。
支持事件订阅、取消订阅和发布，以及异步处理器。

协议约束（硬性要求）：
- 订阅者函数签名：`handler(event_name, params)`
- params 必须是 `dict[str, Any]`
- 订阅者返回：`(EventDecision, next_params)`，且 next_params 的 key 集合必须与入参 params 完全一致
- 若订阅者返回值不符合要求，则其“影响”会被丢弃：总线继续执行后续订阅者，但 params 保持不变
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger, COLOR

# 注意：EventBus 自身的日志不应再通过事件总线进行广播，否则在订阅了
# LOG_OUTPUT_EVENT 时可能出现递归发布/日志风暴。
logger = get_logger(
    "event_bus",
    display="EventBus",
    color=COLOR.MAGENTA,
    enable_event_broadcast=False,
)

EventParams = dict[str, Any]


class EventDecision(str, Enum):
    """事件订阅者的决策。

    - SUCCESS: 正常执行完，更新共享参数并交给下一个订阅者
    - STOP: 执行完后立刻终止，不再继续后续订阅者
    - PASS: 跳过（不更新共享参数），直接交给下一个订阅者
    """

    SUCCESS = "SUCCESS"
    STOP = "STOP"
    PASS = "PASS"


EventHandlerResult = tuple[EventDecision, EventParams]
# 支持同步和异步事件处理器
EventHandlerCallable = Callable[
    [str, EventParams],
    EventHandlerResult | Awaitable[EventHandlerResult],
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
        >>> async def handler(event_name: str, params: dict):
        ...     return (EventDecision.SUCCESS, params)
        >>> bus.subscribe("user_login", handler, priority=10)
        >>> await bus.publish("user_login", {"user_id": "123"})
    """

    def __init__(self, name: str = "default") -> None:
        """初始化事件总线。

        Args:
            name: 总线名称，用于日志识别
        """
        self.name = name
        # 存储处理器：event_name -> (handler -> subscriber metadata)
        # 使用 dict 便于 O(1) 取消订阅；发布时按 priority + order 做稳定排序
        self._subscribers: dict[str, dict[EventHandlerCallable, _Subscriber]] = defaultdict(dict)
        # 跟踪每个处理器订阅的所有事件，便于清理
        self._handler_subscriptions: dict[EventHandlerCallable, set[str]] = defaultdict(set)
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
            handler: 订阅者处理器，必须支持 handler(event_name, params) 调用

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
            # 非重复
            self._subscribe_order += 1
            sub = _Subscriber(
                handler=handler,
                priority=int(priority),
                order=self._subscribe_order,
            )
        else:
            # 重复
            sub = _Subscriber(
                handler=handler,
                priority=int(priority),
                order=existing.order,
            )

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

    async def publish(self, event_name: str, params: EventParams) -> EventHandlerResult:
        """按订阅顺序（priority 从高到低）链式发布事件。

        Args:
            event_name: 事件名称
            params: 事件参数字典（将被链式传递）

        Returns:
            (last_decision, final_params)

        Raises:
            ValueError: event_name 或 params 非法
        """
        if not event_name or not isinstance(event_name, str):
            raise ValueError("事件名称必须是非空字符串")
        if not isinstance(params, dict):
            raise ValueError("params 必须是 dict")
        if any(not isinstance(k, str) for k in params.keys()):
            raise ValueError("params 的 key 必须全部为 str")

        if event_name not in self._subscribers or not self._subscribers[event_name]:
            return (EventDecision.SUCCESS, dict(params))

        subs = sorted(
            self._subscribers[event_name].values(),
            key=lambda s: (-s.priority, s.order),
        )

        # 过滤一下 log_output 事件，不然太吵了
        if event_name != "log_output":
            logger.debug(
                f"正在按顺序向 {len(subs)} 个处理器发布事件 '{event_name}'"
            )

        expected_keys = set(params.keys())
        current_params: EventParams = dict(params)
        last_decision: EventDecision = EventDecision.SUCCESS

        for sub in subs:
            handler = sub.handler
            try:
                raw_result = await self._execute_handler(sub, event_name, dict(current_params))
            except Exception as e:
                logger.error(
                    f"处理器 '{getattr(handler, '__name__', repr(handler))}' 在事件 "
                    f"'{event_name}' 中失败: {e}",
                    exc_info=e,
                )
                last_decision = EventDecision.PASS
                continue

            decision, next_params = self._normalize_handler_result(
                raw_result,
                current_params=current_params,
                expected_keys=expected_keys,
                handler_name=getattr(handler, "__name__", repr(handler)),
            )
            last_decision = decision

            if decision == EventDecision.PASS:
                continue

            current_params = next_params

            if decision == EventDecision.STOP:
                break

        return (last_decision, current_params)

    def _normalize_handler_result(
        self,
        result: Any,
        *,
        current_params: EventParams,
        expected_keys: set[str],
        handler_name: str,
    ) -> EventHandlerResult:
        """将订阅者返回值规范化。

        若不满足协议（tuple、decision、params key 签名），则丢弃影响并返回 PASS + current_params。
        """

        if not (isinstance(result, tuple) and len(result) == 2):
            logger.warning(
                f"处理器 '{handler_name}' 返回值不合法（必须是二元组），已丢弃其影响"
            )
            return (EventDecision.PASS, current_params)

        raw_decision, next_params = result

        try:
            decision = raw_decision if isinstance(raw_decision, EventDecision) else EventDecision(str(raw_decision))
        except Exception:
            logger.warning(
                f"处理器 '{handler_name}' decision 不合法，已丢弃其影响"
            )
            return (EventDecision.PASS, current_params)

        if not isinstance(next_params, dict):
            logger.warning(
                f"处理器 '{handler_name}' next_params 必须是 dict，已丢弃其影响"
            )
            return (EventDecision.PASS, current_params)

        if any(not isinstance(k, str) for k in next_params.keys()):
            logger.warning(
                f"处理器 '{handler_name}' next_params 的 key 必须为 str，已丢弃其影响"
            )
            return (EventDecision.PASS, current_params)

        if set(next_params.keys()) != expected_keys:
            logger.warning(
                f"处理器 '{handler_name}' next_params 签名不一致（key 集合必须完全一致），已丢弃其影响"
            )
            return (EventDecision.PASS, current_params)

        return (decision, next_params)

    async def _execute_handler(self, sub: _Subscriber, event_name: str, params: EventParams) -> Any:
        """执行处理器并返回结果，支持同步和异步处理器。"""
        result = sub.handler(event_name, params)
        if inspect.isawaitable(result):
            return await result
        return result

    def publish_sync(self, event_name: str, params: EventParams) -> asyncio.Task[EventHandlerResult]:
        """同步发布事件（立即返回）。

        为事件发布创建后台任务。适用于即发即弃场景。

        Args:
            event_name: 事件名称
            params: 事件参数字典

        Returns:
            将执行发布的任务

        Example:
            >>> bus.publish_sync("user_action", {"action": "click"})
        """
        task = get_task_manager().create_task(self.publish(event_name, params))
        return task.task # type: ignore

    @property
    def subscribed_events(self) -> set[str]:
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

    def get_subscribers(self, event_name: str) -> list[EventHandlerCallable]:
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
