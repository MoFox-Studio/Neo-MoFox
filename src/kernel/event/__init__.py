"""事件总线（Pub/Sub）。

本模块提供 kernel 层的基础事件发布/订阅功能。

硬性协议：
- 订阅者：`handler(event_name, params)`
- params: `dict[str, Any]`
- 返回：`(EventDecision, next_params)`，且 next_params 的 key 集合必须与 params 完全一致

用法示例：
    >>> from src.kernel.event import event_bus, EventDecision
    >>> async def on_user_login(event_name: str, params: dict):
    ...     return (EventDecision.SUCCESS, params)
    >>> event_bus.subscribe("user_login", on_user_login, priority=10)
    >>> await event_bus.publish("user_login", {"user_id": "12345"})
"""

from src.kernel.event.core import EventBus, EventDecision

_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """获取全局事件总线（懒加载）。"""

    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(name="global")
    return _event_bus


__all__ = ["EventBus", "EventDecision", "get_event_bus"]
