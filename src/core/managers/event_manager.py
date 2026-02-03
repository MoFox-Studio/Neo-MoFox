"""事件管理器。

本模块提供事件管理器，作为 kernel/event 总线的上层封装。
负责将 EventHandler 组件注册到 EventBus 并管理其生命周期。

支持系统事件（EventType 枚举）和插件自定义事件（字符串）。
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.kernel.logger import get_logger
from src.kernel.event import get_event_bus, EventDecision

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
        self._handler_wrappers: Dict[str, List[Callable[[], None]]] = {}  # signature -> unsubscribe functions
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
            event_handler_classes: Dict[str, type[BaseEventHandler]] = registry.get_by_type(ComponentType.EVENT_HANDLER)

            # 需要从 plugin manager 获取实例化的插件，然后实例化事件处理器
            from src.core.managers.plugin_manager import get_plugin_manager
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
                        event_name = str(event)
                        # 创建包装函数适配 EventBus 协议
                        unsubscribe = self._event_bus.subscribe(
                            event_name,
                            self._create_handler_wrapper(handler, signature),
                            priority=handler.weight
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
                f"订阅映射表构建完成，共处理 {len(self._handler_map)} 个 "
                f"事件处理器"
            )

    def _create_handler_wrapper(
        self, handler: BaseEventHandler, signature: str
    ) -> Callable[[str, Dict[str, Any]], Any]:
        """创建处理器包装函数，将 BaseEventHandler 适配到 EventBus 协议。

        Args:
            handler: 事件处理器实例
            signature: 处理器签名

        Returns:
            符合 EventBus 协议的包装函数
        """
        async def wrapper(event_name: str, params: Dict[str, Any]) -> Tuple[EventDecision, Dict[str, Any]]:
            """包装函数，适配 EventBus 协议。

            Args:
                event_name: 事件名称（由 EventBus 传入，未使用）
                params: 事件参数字典

            Returns:
                (EventDecision, params): 决策和参数字典
            """
            try:
                logger.debug(f"执行事件处理器: {signature}")

                # 执行处理器
                result = await handler.execute(params)
                success = result[0]
                intercepted = result[1]

                # 将结果转换为 EventDecision
                if intercepted:
                    # 拦截消息，停止后续处理器
                    logger.info(f"事件被处理器 {signature} 拦截，停止执行后续处理器")
                    return (EventDecision.STOP, params)
                elif success:
                    # 成功执行，继续后续处理器
                    return (EventDecision.SUCCESS, params)
                else:
                    # 失败，跳过当前处理器的影响
                    return (EventDecision.PASS, params)

            except Exception as e:
                logger.error(f"事件处理器 {signature} 执行失败: {e}")
                # 异常情况，跳过当前处理器的影响
                return (EventDecision.PASS, params)

        return wrapper

    async def publish_event(
        self,
        event: EventType | str,
        kwargs: Optional[Dict[str, Any]] = None
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
        decision, final_params = await self._event_bus.publish(str(event), kwargs)

        logger.debug(f"事件 {event} 发布完成，最终决策: {decision}")

        return {
            "decision": decision,
            "params": final_params
        }

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
                event_name = str(event)
                unsubscribe = self._event_bus.subscribe(
                    event_name,
                    self._create_handler_wrapper(handler, signature),
                    priority=handler.weight
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

        asyncio.create_task(_unregister())

    def get_handlers_for_event(self, event: EventType | str) -> List[Tuple[BaseEventHandler, str]]:
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
