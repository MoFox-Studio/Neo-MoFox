"""适配器管理器。

本模块提供适配器管理器，负责适配器的启动、停止、重启和健康检查等功能。
管理所有已启动的适配器实例，提供统一的接口进行操作。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.kernel.event import get_event_bus, EventDecision
from src.kernel.logger import get_logger
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import (
    ComponentState,
    EventType,
    ComponentType,
)

if TYPE_CHECKING:
    from src.core.components.base.adapter import BaseAdapter
    from src.core.managers.plugin_manager import PluginManager

logger = get_logger("adapter_manager")


class AdapterManager:
    """适配器管理器。

    负责适配器的启动、停止、重启和批量健康检查。
    使用 _active_adapters 字典管理已启动的适配器实例。

    Attributes:
        _active_adapters: 已启动的适配器实例字典，格式为 {signature: adapter_instance}

    Examples:
        >>> manager = AdapterManager()
        >>> await manager.start_adapter("my_plugin:adapter:qq")
        >>> await manager.stop_adapter("my_plugin:adapter:qq")
        >>> health_status = await manager.health_check_all()
    """

    def __init__(self) -> None:
        """初始化适配器管理器。"""
        self._active_adapters: dict[str, BaseAdapter] = {}

    async def start_adapter(self, signature: str) -> bool:
        """启动适配器。

        从全局注册表中获取适配器组件，实例化并启动。

        Args:
            signature: 适配器组件签名，格式为 'plugin_name:adapter:adapter_name'

        Returns:
            bool: 是否启动成功

        Examples:
            >>> success = await manager.start_adapter("my_plugin:adapter:qq")
            >>> True
        """
        # 检查是否已经启动
        if signature in self._active_adapters:
            logger.warning(f"适配器 '{signature}' 已经启动")
            return True

        # 从全局注册表获取适配器类
        registry = get_global_registry()
        adapter_cls = registry.get(signature)

        if not adapter_cls:
            logger.error(f"未找到适配器组件: {signature}")
            return False

        # 子进程模式已移除：如果适配器仍声明 run_in_subprocess=True，则拒绝启动。
        if getattr(adapter_cls, "run_in_subprocess", False):
            logger.error(
                f"适配器 '{signature}' 声明 run_in_subprocess=True，但子进程适配器支持已移除；"
                "请改为进程内运行或使用独立进程/容器方式部署该适配器。"
            )
            return False

        # 获取插件实例（用于传递给适配器）
        plugin_manager = _get_plugin_manager()
        plugin_name = signature.split(":")[0]
        plugin_instance = plugin_manager.get_plugin(plugin_name)

        # 实例化适配器
        try:
            # 尝试获取 SinkManager 并创建 CoreSink
            core_sink = None
            try:
                from src.core.transport.sink.sink_manager import get_sink_manager

                sink_mgr = get_sink_manager()
                # 创建消息回调（稍后设置）
                core_sink = None  # SinkManager 会在 setup_adapter_sink 中设置
            except RuntimeError:
                # SinkManager 未初始化，延迟设置 CoreSink
                logger.debug(f"SinkManager 未初始化，延迟设置 CoreSink: {signature}")
                core_sink = None

            adapter_instance = adapter_cls(
                core_sink=core_sink,
                plugin=plugin_instance,
            )
        except Exception as e:
            logger.error(f"实例化适配器 '{signature}' 失败: {e}")
            return False

        # 设置 CoreSink（如果 SinkManager 可用）
        if core_sink is None:
            try:
                from src.core.transport.sink.sink_manager import get_sink_manager

                sink_mgr = get_sink_manager()
                # 直接传递适配器实例给 setup_adapter_sink
                await sink_mgr.setup_adapter_sink(signature, adapter_instance)
                # 获取设置后的 CoreSink
                core_sink = sink_mgr.get_sink(signature)
                if core_sink:
                    adapter_instance.core_sink = core_sink
                    logger.debug(f"为适配器 {signature} 设置 CoreSink")
            except RuntimeError:
                logger.debug(f"SinkManager 仍未可用，跳过 CoreSink 设置: {signature}")
            except Exception as e:
                logger.warning(f"设置 CoreSink 失败: {e}")

        # 启动适配器
        try:
            await adapter_instance.start()
            self._active_adapters[signature] = adapter_instance

            # 更新组件状态
            state_manager = get_global_state_manager()
            await state_manager.set_state_async(signature, ComponentState.ACTIVE)

            logger.info(f"适配器启动成功: {signature}")
            return True

        except Exception as e:
            logger.error(f"启动适配器 '{signature}' 失败: {e}")
            return False

    async def stop_adapter(self, signature: str) -> bool:
        """停止适配器。

        停止指定适配器并清理资源。

        Args:
            signature: 适配器组件签名

        Returns:
            bool: 是否停止成功

        Examples:
            >>> success = await manager.stop_adapter("my_plugin:adapter:qq")
            >>> True
        """
        # 检查是否已启动
        if signature not in self._active_adapters:
            logger.warning(f"适配器 '{signature}' 未启动")
            return False

        adapter_instance = self._active_adapters[signature]

        try:
            # 停止适配器
            await adapter_instance.stop()

            # 从活跃列表中移除
            del self._active_adapters[signature]

            # 更新组件状态
            state_manager = get_global_state_manager()
            await state_manager.set_state_async(signature, ComponentState.INACTIVE)

            logger.info(f"适配器停止成功: {signature}")
            return True

        except Exception as e:
            logger.error(f"停止适配器 '{signature}' 失败: {e}")
            # Don't remove from active adapters since stop failed
            return False

    async def restart_adapter(self, signature: str) -> bool:
        """重启适配器。

        先停止适配器，然后重新启动。

        Args:
            signature: 适配器组件签名

        Returns:
            bool: 是否重启成功

        Examples:
            >>> success = await manager.restart_adapter("my_plugin:adapter:qq")
            >>> True
        """
        # 先停止适配器
        if signature in self._active_adapters:
            stop_success = await self.stop_adapter(signature)
            if not stop_success:
                logger.error(f"重启适配器 '{signature}' 失败: 停止阶段失败")
                return False

        # 等待一小段时间确保完全停止
        await asyncio.sleep(3)

        # 重新启动适配器（即使还在_active_adapters中，也要重新启动）
        # 先从_active_adapters中移除旧的实例
        if signature in self._active_adapters:
            del self._active_adapters[signature]

        # 重新启动适配器
        return await self.start_adapter(signature)

    def get_adapter(self, signature: str) -> "BaseAdapter | None":
        """获取适配器实例。

        Args:
            signature: 适配器组件签名

        Returns:
            BaseAdapter | None: 适配器实例，如果未找到则返回 None

        Examples:
            >>> adapter = manager.get_adapter("my_plugin:adapter:qq")
        """
        return self._active_adapters.get(signature)  # type: ignore[return-value]

    def get_all_adapters(self) -> dict[str, "BaseAdapter"]:
        """获取所有已启动的适配器。

        Returns:
            dict[str, BaseAdapter]: 适配器签名到适配器实例的字典

        Examples:
            >>> adapters = manager.get_all_adapters()
        """
        return self._active_adapters.copy()  # type: ignore[return-value]

    def list_active_adapters(self) -> list[str]:
        """列出所有已启动的适配器签名。

        Returns:
            list[str]: 已启动适配器签名列表

        Examples:
            >>> signatures = manager.list_active_adapters()
            >>> ['my_plugin:adapter:qq', 'other_plugin:adapter:telegram']
        """
        return list(self._active_adapters.keys())

    def is_adapter_active(self, signature: str) -> bool:
        """检查适配器是否已启动。

        Args:
            signature: 适配器组件签名

        Returns:
            bool: 适配器是否已启动

        Examples:
            >>> if manager.is_adapter_active("my_plugin:adapter:qq"):
            ...     print("适配器已启动")
        """
        return signature in self._active_adapters

    async def stop_all_adapters(self) -> dict[str, bool]:
        """停止所有适配器。

        Returns:
            dict[str, bool]: 适配器签名到停止状态的映射

        Examples:
            >>> results = await manager.stop_all_adapters()
        """
        results = {}

        for signature in list(self._active_adapters.keys()):
            results[signature] = await self.stop_adapter(signature)

        return results

    async def get_bot_info_by_platform(self, platform: str) -> dict[str, str] | None:
        """根据平台获取 Bot 信息。

        Args:
            platform: 平台名称

        Returns:
            dict[str, str] | None: 包含 'bot_id' 和 'bot_nickname' 的字典，如果未找到则返回 None

        Examples:
            >>> bot_info = await manager.get_bot_info_by_platform("napcat")
            >>> {'bot_id': '12345678', 'bot_nickname': 'MyBot'}
        """
        for adapter in self._active_adapters.values():
            if adapter.platform == platform:
                return await adapter.get_bot_info()
        return None

    async def send_adapter_command(
        self, adapter_sign: str, command_name: str, command_data: dict
    ) -> dict:
        """向指定适配器发送命令。

        通过适配器签名定位适配器，然后调用其 send_adapter_command 方法。

        Args:
            adapter_sign: 适配器组件签名，格式为 'plugin_name:adapter:adapter_name'
            command_name: 命令名称
            command_data: 命令参数字典

        Returns:
            dict: 命令执行结果，格式为:
                - 成功: {"status": "ok", "data": {...}, "message": "..."}
                - 失败: {"status": "failed", "message": "错误信息"}
                - 错误: {"status": "error", "message": "错误信息"}

        Examples:
            >>> result = await manager.send_adapter_command(
            ...     "napcat_plugin:adapter:napcat",
            ...     "get_group_list",
            ...     {}
            ... )
            >>> {'status': 'ok', 'data': [...]}
        """
        # 检查适配器是否已启动
        if adapter_sign not in self._active_adapters:
            logger.warning(f"适配器 '{adapter_sign}' 未启动或不存在")
            return {
                "status": "error",
                "message": f"适配器 '{adapter_sign}' 未启动或不存在",
                "data": None,
            }

        adapter = self._active_adapters[adapter_sign]

        try:
            # 调用适配器的 send_adapter_command 方法
            result = await adapter.send_adapter_command(command_name, command_data)
            return result
        except Exception as e:
            logger.error(
                f"向适配器 '{adapter_sign}' 发送命令 '{command_name}' 时发生异常: {e}"
            )
            return {
                "status": "error",
                "message": f"发送命令时发生异常: {str(e)}",
                "data": None,
            }


# 全局适配器管理器实例
_global_adapter_manager: "AdapterManager | None" = None


def get_adapter_manager() -> "AdapterManager":
    """获取全局适配器管理器实例。

    Returns:
        AdapterManager: 全局适配器管理器单例

    Examples:
        >>> manager = get_adapter_manager()
        >>> await manager.start_adapter("my_plugin:adapter:qq")
    """
    global _global_adapter_manager
    if _global_adapter_manager is None:
        _global_adapter_manager = AdapterManager()
    return _global_adapter_manager


def reset_adapter_manager() -> None:
    """重置全局适配器管理器。

    主要用于测试场景，确保测试之间不会相互影响。
    """
    global _global_adapter_manager
    _global_adapter_manager = None


def initialize_adapter_manager() -> None:
    """初始化适配器管理器。

    主要用于在应用启动时进行必要的初始化操作。
    """
    get_event_bus().subscribe(EventType.ON_ALL_PLUGIN_LOADED, on_all_plugins_loaded)


async def on_all_plugins_loaded(_: str, params: dict) -> tuple[EventDecision, dict]:
    """所有插件加载完毕后，启动所有注册的适配器。

    Args:
        event_name: 事件名称
        params: 事件参数字典

    Returns:
        tuple[EventDecision, dict]: (事件决策, 事件参数)
    """
    # 通过 ComponentRegistry 获取所有类型为 ADAPTER 的组件
    registry = get_global_registry()
    adapter_components = registry.get_by_type(ComponentType.ADAPTER)

    if not adapter_components:
        logger.info("没有注册任何适配器")
        return (EventDecision.SUCCESS, params)

    logger.info(f"发现 {len(adapter_components)} 个适配器，开始启动...")

    # 启动所有适配器
    manager = get_adapter_manager()
    started_adapters = []
    failed_adapters = []

    for adapter_signature in adapter_components.keys():
        try:
            success = await manager.start_adapter(adapter_signature)
            if success:
                started_adapters.append(adapter_signature)
            else:
                failed_adapters.append(adapter_signature)
                logger.error(f"❌ 自动启动适配器失败: {adapter_signature}")
        except Exception as e:
            failed_adapters.append(adapter_signature)
            logger.error(f"❌ 启动适配器 '{adapter_signature}' 时发生异常: {e}")

    # 记录结果
    total = len(adapter_components)
    success_count = len(started_adapters)
    logger.info(f"适配器启动完成: 成功 {success_count}/{total}")

    if failed_adapters:
        logger.warning(f"以下适配器启动失败: {', '.join(failed_adapters)}")

    return (EventDecision.SUCCESS, params)


# 避免循环导入的延迟导入
def _get_plugin_manager() -> "PluginManager":
    """延迟导入插件管理器以避免循环导入。"""
    from src.core.managers import get_plugin_manager as _get_plugin_manager

    return _get_plugin_manager()
