"""适配器管理器。

本模块提供适配器管理器，负责适配器的启动、停止、重启和健康检查等功能。
管理所有已启动的适配器实例，提供统一的接口进行操作。
"""

import asyncio
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import ComponentState

if TYPE_CHECKING:
    from src.core.components.base.adapter import BaseAdapter

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
        self._active_adapters: dict[str, "BaseAdapter"] = {}
        logger.info("适配器管理器初始化完成")

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

        # 获取插件实例（用于传递给适配器）
        plugin_manager = _get_plugin_manager()
        plugin_name = signature.split(":")[0]
        plugin_instance = plugin_manager.get_plugin(plugin_name)

        # 实例化适配器
        try:
            adapter_instance = adapter_cls(
                core_sink=None,  # 这里需要根据实际需求配置
                plugin=plugin_instance,
            )
        except Exception as e:
            logger.error(f"实例化适配器 '{signature}' 失败: {e}")
            return False

        # 启动适配器
        try:
            await adapter_instance.start()
            self._active_adapters[signature] = adapter_instance

            # 更新组件状态
            state_manager = get_global_state_manager()
            await state_manager.set_state_async(signature, ComponentState.ACTIVE)

            logger.info(f"✅ 适配器启动成功: {signature}")
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

            logger.info(f"✅ 适配器停止成功: {signature}")
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
        await asyncio.sleep(1)

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
        return self._active_adapters.get(signature)

    def get_all_adapters(self) -> dict[str, "BaseAdapter"]:
        """获取所有已启动的适配器。

        Returns:
            dict[str, BaseAdapter]: 适配器签名到适配器实例的字典

        Examples:
            >>> adapters = manager.get_all_adapters()
        """
        return self._active_adapters.copy()

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


# 避免循环导入的延迟导入
def _get_plugin_manager():
    """延迟导入插件管理器以避免循环导入。"""
    from src.core.managers.plugin_manager import get_plugin_manager as _get_plugin_manager
    return _get_plugin_manager()