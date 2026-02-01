"""事件总线（Pub/Sub）。

本模块提供kernel层的基础事件发布/订阅功能。
对外接口：`event_bus`（全局实例）、`Event`（事件类）

用法示例：
    >>> from src.kernel.event import event_bus, Event
    >>> from src.kernel.event import EventDecision
    >>> async def on_user_login(event: Event, shared):
    ...     print(f"User {event.data['user_id']} logged in")
    ...     return (EventDecision.SUCCESS, shared)
    >>> event_bus.subscribe("user_login", on_user_login, priority=10)
    >>> await event_bus.publish(Event(name="user_login", data={"user_id": "12345"}), shared={})
"""

from src.kernel.event.core import Event, EventBus, EventDecision

# 全局事件总线实例
event_bus: EventBus = EventBus(name="global")

__all__ = ["Event", "EventBus", "EventDecision", "event_bus"]
