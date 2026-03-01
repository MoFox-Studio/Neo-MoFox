"""事件管理器。

本模块提供事件管理器，作为 kernel/event 总线的上层封装。
负责将 EventHandler 组件注册到 EventBus 并管理其生命周期。

支持系统事件（EventType 枚举）和插件自定义事件（字符串）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    pass

from src.kernel.logger import get_logger
from src.kernel.event import get_event_bus, EventDecision
from src.kernel.concurrency import get_task_manager

from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType

logger = get_logger("event_manager")


class EventManager:
    """事件管理器。

    作为 kernel/event 总线的上层封装，负责管理 EventHandler 组件的注册和订阅。
    处理器按权重排序执行，支持消息拦截功能。

    支持系统事件（EventType 枚举）和插件自定义事件（字符串）。
    EventType 只是为常见系统事件提供便捷，插件可以自由定义和使用自己的事件名称。

    Attributes:
        _event_bus: 底层事件总线实例
        _handler_map: 处理器映射，处理器签名 -> 处理器实例
        _handler_wrappers: 处理器包装函数映射（用于取消订阅）
        _lock: 用于线程安全操作的异步锁

    Examples:
        >>> manager = EventManager()
        >>> await manager.build_subscription_map()
        >>> # 发布系统事件
        >>> await manager.publish_event(EventType.ON_MESSAGE_RECEIVED, {"message": "Hello"})
        >>> # 发布自定义事件
        >>> await manager.publish_event("my_plugin:custom_event", {"data": "value"})
    """

    def __init__(self) -> None:
        """初始化事件管理器。"""
        self._event_bus = get_event_bus()
        self._handler_map: Dict[str, BaseEventHandler] = {}
        self._handler_wrappers: Dict[str, List[Callable[[], None]]] = (
            {}
        )  # signature -> unsubscribe functions
        self._lock = asyncio.Lock()

        logger.info("事件管理器初始化完成")

    async def build_subscription_map(self) -> None:
        """构建事件订阅映射表。

        遍历所有已注册的事件处理器，根据它们的订阅信息注册到 EventBus。
        处理器按权重降序排序，权重高的优先执行。

        Examples:
            >>> await manager.build_subscription_map()
        """
        async with self._lock:
            # 清空现有映射表
            await self._clear_all_subscriptions()

            # 从全局注册表获取所有事件处理器组件
            from src.core.components.registry import get_global_registry
            from src.core.components.types import ComponentType

            registry = get_global_registry()

            # 获取所有 EVENT_HANDLER 类型的组件
            event_handler_classes: Dict[str, type[BaseEventHandler]] = (
                registry.get_by_type(ComponentType.EVENT_HANDLER)
            )

            # 需要从 plugin manager 获取实例化的插件，然后实例化事件处理器
            from src.core.managers import get_plugin_manager

            plugin_manager = get_plugin_manager()

            for signature, handler_cls in event_handler_classes.items():
                try:
                    # 解析签名获取插件名称
                    from src.core.components.types import parse_signature

                    sig = parse_signature(signature)
                    plugin_name = sig["plugin_name"]

                    # 获取插件实例
                    plugin_instance = plugin_manager.get_plugin(plugin_name)
                    if not plugin_instance:
                        logger.warning(f"未找到插件实例: {plugin_name}")
                        continue

                    # 实例化事件处理器
                    handler = handler_cls(plugin_instance)
                    handler.signature = signature  # 设置签名属性

                    # 添加到处理器映射表
                    self._handler_map[signature] = handler

                    # 获取处理器订阅的事件
                    subscribed_events = handler.get_subscribed_events()

                    # 将处理器添加到每个订阅事件的映射表中
                    for event in subscribed_events:
                        # 支持 EventType 枚举和字符串事件名称
                        event_name = (
                            event.value if isinstance(event, EventType) else str(event)
                        )
                        # 直接注册处理器 execute，以薄异常保护包装
                        unsubscribe = self._event_bus.subscribe(
                            event_name,
                            self._make_safe_wrapper(handler, signature),
                            priority=handler.weight,
                        )
                        # 保存取消订阅函数
                        if signature not in self._handler_wrappers:
                            self._handler_wrappers[signature] = []
                        self._handler_wrappers[signature].append(unsubscribe)

                    logger.debug(f"已注册事件处理器: {signature}")

                except Exception as e:
                    logger.error(f"实例化事件处理器 {signature} 失败: {e}")
                    continue

            logger.info(
                f"订阅映射表构建完成，共处理 {len(self._handler_map)} 个 " f"事件处理器"
            )

    def _make_safe_wrapper(
        self, handler: BaseEventHandler, signature: str
    ) -> Callable[[str, Dict[str, Any]], Any]:
        """为事件处理器创建异常防护包装，直接透传 EventBus 协议。

        handler.execute 与 EventBus 订阅者协议完全一致：接受 (event_name, params)
        并返回 (EventDecision, params)。本方法仅添加异常捕获，不做任何语义转换。

        Args:
            handler: 事件处理器实例
            signature: 处理器签名（用于日志）

        Returns:
            符合 EventBus 协议的异常安全包装函数
        """

        async def safe_execute(
            event_name: str, params: Dict[str, Any]
        ) -> tuple[EventDecision, Dict[str, Any]]:
            try:
                return await handler.execute(event_name, params)
            except Exception as e:
                logger.error(f"事件处理器 {signature} 执行失败: {e}")
                return (EventDecision.PASS, params)

        return safe_execute

    async def publish_event(
        self, event: EventType | str, kwargs: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """发布事件给订阅者。

        支持系统事件（EventType 枚举）和自定义事件（字符串）。

        Args:
            event: 事件类型（EventType 枚举或自定义字符串）
            kwargs: 事件参数字典

        Returns:
            Dict[str, Any]: 发布结果，包含最终决策和参数

        Examples:
            >>> # 发布系统事件
            >>> result = await manager.publish_event(
            ...     EventType.ON_MESSAGE_RECEIVED,
            ...     {"message": "Hello", "sender": "user1"}
            ... )
            >>> # 发布自定义事件
            >>> result = await manager.publish_event(
            ...     "my_plugin:user_action",
            ...     {"action": "click", "target": "button"}
            ... )
        """
        if kwargs is None:
            kwargs = {}

        logger.debug(f"发布事件: {event}")

        # 通过 EventBus 发布事件
        # 注意：不能使用 str(event)，Python 3.11 中 str(StrEnum.member) 返回
        # "EnumName.member" 而非 value，会导致与直接用枚举订阅的 key 不匹配
        event_name = event.value if isinstance(event, EventType) else str(event)
        decision, final_params = await self._event_bus.publish(event_name, kwargs)

        logger.debug(f"事件 {event} 发布完成，最终决策: {decision}")

        return {"decision": decision, "params": final_params}

    async def register_handler(self, signature: str, handler: BaseEventHandler) -> None:
        """注册单个事件处理器。

        Args:
            signature: 处理器签名
            handler: 事件处理器实例

        Examples:
            >>> await manager.register_handler("my_plugin:event_handler:log", handler)
        """
        async with self._lock:
            self._handler_map[signature] = handler

            # 获取处理器订阅的事件
            subscribed_events = handler.get_subscribed_events()

            # 将处理器注册到 EventBus
            for event in subscribed_events:
                # 支持 EventType 枚举和字符串事件名称
                event_name = event.value if isinstance(event, EventType) else str(event)
                unsubscribe = self._event_bus.subscribe(
                    event_name,
                    self._create_handler_wrapper(handler, signature),
                    priority=handler.weight,
                )
                # 保存取消订阅函数
                if signature not in self._handler_wrappers:
                    self._handler_wrappers[signature] = []
                self._handler_wrappers[signature].append(unsubscribe)

            logger.debug(f"已注册事件处理器: {signature}")

    def unregister_handler(self, signature: str) -> None:
        """注销单个事件处理器。

        Args:
            signature: 处理器签名

        Examples:
            >>> manager.unregister_handler("my_plugin:event_handler:log")
        """

        async def _unregister() -> None:
            async with self._lock:
                if signature in self._handler_map:
                    # 取消所有订阅
                    if signature in self._handler_wrappers:
                        for unsubscribe in self._handler_wrappers[signature]:
                            unsubscribe()
                        del self._handler_wrappers[signature]

                    # 从映射表移除
                    del self._handler_map[signature]

                    logger.debug(f"已注销事件处理器: {signature}")

        get_task_manager().create_task(_unregister())

    def get_handlers_for_event(
        self, event: EventType | str
    ) -> List[Tuple[BaseEventHandler, str]]:
        """获取指定事件的所有处理器。

        Args:
            event: 事件类型（EventType 枚举或字符串）

        Returns:
            List[Tuple[BaseEventHandler, str]]: 处理器列表，包含处理器实例和签名

        Examples:
            >>> handlers = manager.get_handlers_for_event(EventType.ON_MESSAGE_RECEIVED)
            >>> custom_handlers = manager.get_handlers_for_event("my_plugin:custom_event")
        """
        # 将事件转换为字符串（支持 EventType 枚举和自定义字符串）
        event_name = str(event)

        # 从已注册的处理器中查找订阅了该事件的处理器
        result = []
        for signature, handler in self._handler_map.items():
            subscribed_events = handler.get_subscribed_events()
            # 检查是否订阅了该事件（支持 EventType 和字符串）
            if any(str(e) == event_name for e in subscribed_events):
                result.append((handler, signature))

        # 按权重排序
        result.sort(key=lambda x: x[0].weight, reverse=True)
        return result

    def get_handler(self, signature: str) -> Optional[BaseEventHandler]:
        """获取指定签名的事件处理器。

        Args:
            signature: 处理器签名

        Returns:
            Optional[BaseEventHandler]: 处理器实例，不存在返回 None

        Examples:
            >>> handler = manager.get_handler("my_plugin:event_handler:log")
        """
        return self._handler_map.get(signature)

    def get_all_handlers(self) -> Dict[str, BaseEventHandler]:
        """获取所有事件处理器。

        Returns:
            Dict[str, BaseEventHandler]: 处理器映射表

        Examples:
            >>> handlers = manager.get_all_handlers()
        """
        return self._handler_map.copy()

    async def _clear_all_subscriptions(self) -> None:
        """清除所有订阅（内部使用）。"""
        # 取消所有订阅
        for signature in list(self._handler_wrappers.keys()):
            for unsubscribe in self._handler_wrappers[signature]:
                unsubscribe()

        # 清空映射表
        self._handler_map.clear()
        self._handler_wrappers.clear()

        logger.debug("已清除所有事件处理器订阅")

    def get_event_stats(self) -> Dict[str, int]:
        """获取事件统计信息。

        Returns:
            Dict[str, int]: 统计信息，包含处理器数量和事件类型数量

        Examples:
            >>> stats = manager.get_event_stats()
            >>> print(stats["handler_count"])  # 处理器总数
            >>> print(stats["event_type_count"])  # 事件类型总数
        """
        return {
            "handler_count": len(self._handler_map),
            "event_type_count": self._event_bus.event_count,
            "total_subscriptions": self._event_bus.handler_count,
        }


# 全局单例
_event_manager: Optional[EventManager] = None


def get_event_manager() -> EventManager:
    """获取全局事件管理器实例（懒加载）。

    Returns:
        EventManager: 全局事件管理器实例

    Examples:
        >>> from src.core.managers import get_event_manager
        >>> manager = get_event_manager()
    """
    global _event_manager
    if _event_manager is None:
        _event_manager = EventManager()
    return _event_manager


def reset_event_manager() -> None:
    """重置全局事件管理器。

    主要用于测试场景，确保测试之间不会相互影响。
    """
    global _event_manager
    _event_manager = None


def initialize_event_manager() -> None:
    """初始化事件管理器。

    主要用于在应用启动时进行必要的初始化操作。
    订阅插件加载完成事件，在所有插件加载完成后自动构建事件订阅映射。
    """
    from src.core.components.types import EventType

    get_event_bus().subscribe(EventType.ON_ALL_PLUGIN_LOADED, on_all_plugins_loaded)


async def on_all_plugins_loaded(
    _: str, params: Dict[str, Any]
) -> Tuple[EventDecision, Dict[str, Any]]:
    """所有插件加载完毕后，构建事件订阅映射。

    Args:
        event_name: 事件名称
        params: 事件参数字典

    Returns:
        tuple[EventDecision, dict]: (事件决策, 事件参数)
    """
    logger.info("开始构建事件订阅映射...")

    # 构建订阅映射
    manager = get_event_manager()
    try:
        await manager.build_subscription_map()
        logger.info("✅ 事件订阅映射构建完成")
    except Exception as e:
        logger.error(f"❌ 构建事件订阅映射时发生异常: {e}")
        return (EventDecision.PASS, params)

    return (EventDecision.SUCCESS, params)
