"""扁平化事件 API

为插件提供简洁的事件操作接口，将 EventManager 的类方法扁平化为独立函数。
"""
from typing import Any

from src.core.components import EventType, BaseEventHandler
from src.core.managers import get_event_manager


# =============================================================================
# 事件发布操作
# =============================================================================


async def publish_event(
    event: EventType | str,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """发布事件给订阅者。

    支持系统事件（EventType 枚举）和自定义事件（字符串）。

    Args:
        event: 事件类型（EventType 枚举或自定义字符串）
        kwargs: 事件参数字典

    Returns:
        dict[str, Any]: 发布结果，包含最终决策和参数

    Examples:
        >>> # 发布系统事件
        >>> result = await publish_event(
        ...     EventType.ON_MESSAGE_RECEIVED,
        ...     {"message": "Hello", "sender": "user1"}
        ... )
        >>> # 发布自定义事件
        >>> result = await publish_event(
        ...     "my_plugin:user_action",
        ...     {"action": "click", "target": "button"}
        ... )
    """
    manager = get_event_manager()
    return await manager.publish_event(event, kwargs)


# =============================================================================
# 事件处理器注册操作
# =============================================================================


async def register_handler(signature: str, handler: BaseEventHandler) -> None:
    """注册单个事件处理器。

    Args:
        signature: 处理器签名
        handler: 事件处理器实例

    Examples:
        >>> from my_plugin.handlers import MyEventHandler
        >>> handler = MyEventHandler(plugin_instance)
        >>> await register_handler("my_plugin:event_handler:log", handler)
    """
    manager = get_event_manager()
    await manager.register_handler(signature, handler)


def unregister_handler(signature: str) -> None:
    """注销单个事件处理器。

    Args:
        signature: 处理器签名

    Examples:
        >>> unregister_handler("my_plugin:event_handler:log")
    """
    manager = get_event_manager()
    manager.unregister_handler(signature)


async def build_subscription_map() -> None:
    """构建事件订阅映射表。

    遍历所有已注册的事件处理器，根据它们的订阅信息注册到 EventBus。
    处理器按权重降序排序，权重高的优先执行。

    Examples:
        >>> await build_subscription_map()
    """
    manager = get_event_manager()
    await manager.build_subscription_map()

# =============================================================================
# 事件统计操作
# =============================================================================


def get_event_stats() -> dict[str, int]:
    """获取事件统计信息。

    Returns:
        Dict[str, int]: 统计信息，包含：
            - handler_count: 处理器总数
            - event_type_count: 事件类型总数
            - total_subscriptions: 总订阅数

    Examples:
        >>> stats = get_event_stats()
        >>> print(f"处理器数量: {stats['handler_count']}")
        >>> print(f"事件类型数量: {stats['event_type_count']}")
        >>> print(f"总订阅数: {stats['total_subscriptions']}")
    """
    manager = get_event_manager()
    return manager.get_event_stats()
