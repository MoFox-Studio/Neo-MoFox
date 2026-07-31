"""适配器组件基类。

本模块提供 BaseAdapter 类，定义适配器组件的基本行为。
Adapter 负责与外部平台通信，实现消息的接收和发送。
继承自 mofox_wire.AdapterBase，添加插件生命周期、自动重连等特性。
"""

import asyncio
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mofox_wire import AdapterBase
from mofox_wire import CoreSink, MessageEnvelope

from src.core.components.base.component import BaseComponent
from src.kernel.concurrency import get_task_manager

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


@dataclass(slots=True)
class PlatformSendResult:
    """适配器向平台发送消息的结果。

    与异常不同，此对象同时携带"成功/失败"状态与平台消息 ID，
    供调用方（如 MessageSender）精确区分以下三种情况：
    - 发送成功且平台返回消息 ID：``success=True, message_id=ID``
    - 发送成功但平台未返回 ID：``success=True, message_id=None``
    - 发送失败：``success=False``（``error`` 描述原因）

    Attributes:
        success: 是否发送成功
        message_id: 平台返回的消息 ID（如平台未返回则为 None）
        error: 失败原因描述（成功时为 None）
        response: 平台返回的原始响应（如有），便于排查
    """

    success: bool
    message_id: str | None = None
    error: str | None = None
    response: Any = None


class BaseAdapter(BaseComponent, AdapterBase):
    """适配器组件基类。

    Adapter 负责与外部平台通信，是 Bot 与平台之间的桥梁。
    相比 mofox_wire.AdapterBase，增加了以下特性：
    1. 插件生命周期管理 (on_adapter_loaded, on_adapter_unloaded)
    2. 自动重连与健康检查
    3. （已移除）子进程启动支持

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        name: 适配器名称
        adapter_version: 适配器版本
        description: 适配器描述
        platform: 平台标识（如 "qq", "telegram", "discord"）

    Examples:
        >>> class MyAdapter(BaseAdapter):
        ...     name = "my_adapter"
        ...     adapter_version = "1.0.0"
        ...     platform = "test"
        ...
        ...     async def from_platform_message(self, raw: Any):
        ...         # 解析平台消息并返回 MessageEnvelope
        ...         return envelope
    """
    _plugin_: str
    _signature_: str

    component_type = "adapter"
    _legacy_name_attr = "adapter_name"
    _legacy_desc_attr = "adapter_description"
    adapter_name: str = "unknown_adapter"
    adapter_description: str = "无描述"

    # 适配器元数据
    name: str = "unknown_adapter"
    adapter_version: str = "0.0.1"
    description: str = "无描述"

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

        # 在创建健康检查任务前标记运行中，避免协程抢占后立即退出。
        self._running = True

        # 启动健康检查
        tm = get_task_manager()
        self._health_check_task_info = tm.create_task(
            self._health_check_loop(),
            name=f"{self.name}_health_check",
            daemon=True,
        )

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

    async def _send_platform_message(self, envelope: MessageEnvelope) -> PlatformSendResult:
        """发送消息到平台。

        如果使用了自动传输配置，此方法会自动处理。
        否则子类需要重写此方法。

        Args:
            envelope: 要发送的消息信封

        Returns:
            PlatformSendResult: 发送结果，包含成功/失败状态与平台消息 ID

        Raises:
            NotImplementedError: 如果未配置自动传输且未重写此方法
        """
        # 如果配置了自动传输，调用父类方法
        if hasattr(self, "_transport_config") and self._transport_config:  # type: ignore
            return await super()._send_platform_message(envelope)  # type: ignore
        else:
            raise NotImplementedError(
                f"适配器 {self.name} 未配置自动传输，必须重写 _send_platform_message 方法"
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