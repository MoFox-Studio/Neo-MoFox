"""适配器组件基类。

本模块提供 BaseAdapter 类，定义适配器组件的基本行为。
Adapter 负责与外部平台通信，实现消息的接收和发送。
继承自 mofox_wire.AdapterBase，添加插件生命周期、自动重连等特性。
"""

import asyncio
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from mofox_wire import AdapterBase
from mofox_wire import CoreSink, MessageEnvelope

from src.kernel.concurrency import get_task_manager

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


class BaseAdapter(AdapterBase):
    """适配器组件基类。

    Adapter 负责与外部平台通信，是 Bot 与平台之间的桥梁。
    相比 mofox_wire.AdapterBase，增加了以下特性：
    1. 插件生命周期管理 (on_adapter_loaded, on_adapter_unloaded)
    2. 自动重连与健康检查
    3. （已移除）子进程启动支持

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        adapter_name: 适配器名称
        adapter_version: 适配器版本
        adapter_description: 适配器描述
        platform: 平台标识（如 "qq", "telegram", "discord"）

    Examples:
        >>> class MyAdapter(BaseAdapter):
        ...     adapter_name = "my_adapter"
        ...     adapter_version = "1.0.0"
        ...     platform = "test"
        ...
        ...     async def from_platform_message(self, raw: Any):
        ...         # 解析平台消息并返回 MessageEnvelope
        ...         return envelope
    """
    _plugin_: str
    _signature_: str

    # 适配器元数据
    adapter_name: str = "unknown_adapter"
    adapter_version: str = "0.0.1"
    adapter_description: str = "无描述"

    platform: str = ""

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:service:message_queue"]

    def __init__(
        self,
        core_sink: CoreSink,
        plugin: "BasePlugin | None" = None,
        **kwargs: Any,
    ) -> None:
        """初始化适配器组件。

        Args:
            core_sink: 核心消息接收器
            plugin: 所属插件实例（可选）
            **kwargs: 传递给 AdapterBase 的其他参数
        """
        super().__init__(core_sink, **kwargs)
        self.plugin = plugin
        self._health_check_task_info: Any | None = None
        self._running = False

    @classmethod
    def get_signature(cls) -> str | None:
        """获取适配器组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:adapter:adapter_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = MyAdapter.get_signature()
            >>> "my_plugin:adapter:my_adapter"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.adapter_name:
            return f"{cls._plugin_}:adapter:{cls.adapter_name}"
        return None

    async def start(self) -> None:
        """启动适配器。

        调用生命周期钩子并启动健康检查。

        Examples:
            >>> await adapter.start()
        """
        # 调用生命周期钩子
        await self.on_adapter_loaded()

        # 调用父类启动
        await super().start()

        # 启动健康检查
        tm = get_task_manager()
        self._health_check_task_info = tm.create_task(
            self._health_check_loop(),
            name=f"{self.adapter_name}_health_check",
            daemon=True,
        )

        self._running = True

    async def stop(self) -> None:
        """停止适配器。

        停止健康检查并调用生命周期钩子。

        Examples:
            >>> await adapter.stop()
        """
        self._running = False

        # 停止健康检查
        if self._health_check_task_info:
            tm = get_task_manager()
            try:
                tm.cancel_task(self._health_check_task_info.task_id)
            except Exception:
                pass
            self._health_check_task_info = None

        # 调用父类停止
        await super().stop()

        # 调用生命周期钩子
        await self.on_adapter_unloaded()

    async def on_adapter_loaded(self) -> None:
        """适配器加载时的钩子。

        子类可重写以执行初始化逻辑。

        Examples:
            >>> async def on_adapter_loaded(self) -> None:
            ...     # 初始化连接
            ...     pass
        """
        pass

    async def on_adapter_unloaded(self) -> None:
        """适配器卸载时的钩子。

        子类可重写以执行清理逻辑。

        Examples:
            >>> async def on_adapter_unloaded(self) -> None:
            ...     # 清理资源
            ...     pass
        """
        pass

    async def _health_check_loop(self) -> None:
        """健康检查循环。

        定期执行健康检查，失败时尝试重连。
        """
        interval = 30  # 默认 30 秒

        while self._running:
            try:
                await asyncio.sleep(interval)

                # 执行健康检查
                is_healthy = await self.health_check()

                if not is_healthy:
                    await self.reconnect()

            except asyncio.CancelledError:
                break
            except Exception:
                # 忽略健康检查异常
                pass

    async def health_check(self) -> bool:
        """健康检查。

        子类可重写以实现自定义检查逻辑。
        默认检查连接状态。

        Returns:
            bool: 是否健康

        Examples:
            >>> async def health_check(self) -> bool:
            ...     # 检查 WebSocket 连接状态
            ...     return self.is_connected()
        """
        return self.is_connected()

    async def reconnect(self) -> None:
        """重新连接。

        子类可重写以实现自定义重连逻辑。
        默认停止后重新启动。

        Examples:
            >>> async def reconnect(self) -> None:
            ...     await self.stop()
            ...     await asyncio.sleep(2)
            ...     await self.start()
        """
        await self.stop()
        await asyncio.sleep(2)
        await self.start()

    @abstractmethod
    async def from_platform_message(self, raw: Any) -> MessageEnvelope:
        """将平台原始消息转换为 MessageEnvelope。

        此方法由 mofox_wire.AdapterBase 定义，必须实现。

        Args:
            raw: 平台原始消息对象

        Returns:
            MessageEnvelope: 符合 mofox_wire 标准的消息信封

        Examples:
            >>> async def from_platform_message(self, raw: Any):
            ...     from mofox_wire import MessageEnvelope, MessageDirection
            ...
            ...     # 解析平台消息
            ...     message_id = raw.get("message_id")
            ...     user_id = raw.get("user_id")
            ...     content = raw.get("content")
            ...
            ...     return MessageEnvelope(
            ...         direction=MessageDirection.UPWARD,
            ...         message_info={
            ...             "platform": self.platform,
            ...             "user_id": user_id,
            ...             "message_id": message_id,
            ...         },
            ...         message_segment=[{"type": "text", "data": content}],
            ...         raw_message=raw,
            ...     )
        """
        ...

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """发送消息到平台。

        如果使用了自动传输配置，此方法会自动处理。
        否则子类需要重写此方法。

        Args:
            envelope: 要发送的消息信封

        Raises:
            NotImplementedError: 如果未配置自动传输且未重写此方法
        """
        # 如果配置了自动传输，调用父类方法
        if hasattr(self, "_transport_config") and self._transport_config:  # type: ignore
            await super()._send_platform_message(envelope)  # type: ignore
        else:
            raise NotImplementedError(
                f"适配器 {self.adapter_name} 未配置自动传输，必须重写 _send_platform_message 方法"
            )

    @abstractmethod
    async def get_bot_info(self) -> dict[str, Any]:
        """获取 Bot 信息。

        子类可重写以返回平台特定的 Bot 信息。

        Returns:
            dict[str, Any]: 包含 bot_id、bot_name、platform 等信息的字典

        Examples:
            >>> async def get_bot_info(self) -> dict:
            ...     return {
            ...         "bot_id": "123456",
            ...         "bot_name": "MyBot",
            ...         "platform": self.platform,
            ...     }
        """
        return {
            "bot_id": "unknown_bot",
            "bot_name": "Unknown Bot",
            "platform": self.platform,
        }