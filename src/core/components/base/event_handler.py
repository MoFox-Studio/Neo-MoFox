"""事件处理器组件基类。

本模块提供 BaseEventHandler 类，定义事件处理器组件的基本行为。
EventHandler 订阅系统事件并做出响应，支持权重排序和消息拦截。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from src.core.components.types import EventType
from src.kernel.event import EventDecision

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


class BaseEventHandler(ABC):
    """事件处理器组件基类。

    EventHandler 订阅系统事件并在事件触发时执行响应逻辑。
    支持权重排序和消息拦截控制。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        handler_name: 处理器名称
        handler_description: 处理器描述
        weight: 处理器权重（影响执行顺序，数值越大优先级越高）
        intercept_message: 是否拦截消息（拦截后消息不再传递给后续处理器）
        init_subscribe: 初始订阅的事件类型列表

    Examples:
        >>> class MyEventHandler(BaseEventHandler):
        ...     handler_name = "my_handler"
        ...     weight = 10
        ...     intercept_message = False
        ...     init_subscribe = [EventType.MESSAGE_RECEIVED, EventType.USER_JOIN]
        ...
        ...     async def execute(
        ...         self, event_name: str, params: dict[str, Any]
        ...     ) -> tuple[EventDecision, dict[str, Any]]:
        ...         # 处理事件，继续执行后续处理器
        ...         return EventDecision.SUCCESS, params
    """
    _plugin_: str
    _signature_: str

    # 处理器元数据
    handler_name: str = ""
    handler_description: str = ""

    weight: int = 0
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = []

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:service:log"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化事件处理器组件。

        Args:
            plugin: 所属插件实例
        """
        self.plugin = plugin
        self._subscribed_events: set[EventType] = set()
        self.signature = ""  # 组件签名，由管理器设置

        # 初始化订阅
        for event in self.init_subscribe:
            self.subscribe(event)

    @classmethod
    def get_signature(cls) -> str | None:
        """获取事件处理器组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:event_handler:handler_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = MyEventHandler.get_signature()
            >>> "my_plugin:event_handler:my_handler"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.handler_name:
            return f"{cls._plugin_}:event_handler:{cls.handler_name}"
        return None
    
    @abstractmethod
    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行事件处理的主要逻辑。

        与 kernel EventBus 订阅者协议保持一致：接受事件名称和参数字典，
        返回决策枚举与（可能已修改的）参数字典。

        Args:
            event_name: 触发本处理器的事件名称（由 EventBus 传入）
            params: 事件参数字典（即 EventBus publish 时的 params，可就地修改）

        Returns:
            tuple[EventDecision, dict[str, Any]]:
                - ``EventDecision.SUCCESS`` — 执行完成，继续后续处理器
                - ``EventDecision.STOP``    — 拦截，阻止后续处理器执行
                - ``EventDecision.PASS``    — 跳过本处理器，不传播参数变更

        Examples:
            >>> async def execute(
            ...     self, event_name: str, params: dict[str, Any]
            ... ) -> tuple[EventDecision, dict[str, Any]]:
            ...     # 正常处理，继续后续处理器
            ...     return EventDecision.SUCCESS, params
            ...
            ...     # 拦截，阻止后续处理器执行
            ...     params["reason"] = "已拦截"
            ...     return EventDecision.STOP, params
            ...
            ...     # 跳过，不传播本处理器对 params 的变更
            ...     return EventDecision.PASS, params
        """
        ...

    def subscribe(self, event: EventType | str) -> None:
        """订阅事件。

        Args:
            event: 事件类型（EventType 枚举或字符串）

        Examples:
            >>> self.subscribe(EventType.MESSAGE_RECEIVED)
            >>> self.subscribe("user_join")
        """
        if isinstance(event, str):
            try:
                event = EventType(event)
            except ValueError:
                # 如果是无效的事件类型字符串，仍然保存
                pass

        self._subscribed_events.add(event)  # type: ignore

    def unsubscribe(self, event: EventType | str) -> None:
        """取消订阅事件。

        Args:
            event: 事件类型（EventType 枚举或字符串）

        Examples:
            >>> self.unsubscribe(EventType.MESSAGE_RECEIVED)
            >>> self.unsubscribe("user_join")
        """
        if isinstance(event, str):
            try:
                event = EventType(event)
            except ValueError:
                return

        self._subscribed_events.discard(event)

    def get_subscribed_events(self) -> list[EventType | str]:
        """获取已订阅的事件列表。

        Returns:
            list[EventType | str]: 已订阅的事件列表

        Examples:
            >>> events = self.get_subscribed_events()
            >>> [EventType.MESSAGE_RECEIVED, EventType.USER_JOIN]
        """
        return list(self._subscribed_events)

    def is_subscribed(self, event: EventType | str) -> bool:
        """检查是否订阅了特定事件。

        Args:
            event: 事件类型

        Returns:
            bool: 是否已订阅

        Examples:
            >>> if self.is_subscribed(EventType.MESSAGE_RECEIVED):
            ...     print("已订阅消息接收事件")
        """
        if isinstance(event, str):
            try:
                event = EventType(event)
            except ValueError:
                return False

        return event in self._subscribed_events
