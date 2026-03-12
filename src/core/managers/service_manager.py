"""Service 管理器。

本模块提供 Service 管理器，负责 Service 组件的注册、发现和方法调用。
Service 是"暴露的功能"，供其他插件或组件调用。
管理器提供动态实例创建和方法调用接口，支持同步和异步调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

from src.core.components.registry import get_global_registry
from src.core.components.types import ComponentType

if TYPE_CHECKING:
    from src.core.components.base.service import BaseService


logger = get_logger("service_manager")


class ServiceManager:
    """Service 管理器。

    负责管理所有 Service 组件，提供查询和动态实例创建接口。
    每次调用 get_service() 都会创建新的 Service 实例。

    Examples:
        >>> manager = ServiceManager()
        >>> service = manager.get_service("my_plugin:service:calculator")
        >>> result = await manager.call_service_async("my_plugin:service:calculator", "add", 1, 2)
        >>> result = manager.call_service("my_plugin:service:calculator", "add", 1, 2)
    """

    def __init__(self) -> None:
        """初始化 Service 管理器。"""
        logger.info("Service 管理器初始化完成")

    def get_all_services(self) -> dict[str, type[BaseService]]:
        """获取所有已注册的 Service 组件。

        Returns:
            dict[str, type[BaseService]]: 将签名映射到 Service 类的字典

        Examples:
            >>> services = manager.get_all_services()
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.SERVICE)

    def get_services_for_plugin(
        self, plugin_name: str
    ) -> dict[str, type[BaseService]]:
        """获取指定插件的所有 Service 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseService]]: 将签名映射到 Service 类的字典

        Examples:
            >>> services = manager.get_services_for_plugin("my_plugin")
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.SERVICE)

    def get_service_class(self, signature: str) -> type[BaseService] | None:
        """通过签名获取 Service 类。

        Args:
            signature: Service 组件签名

        Returns:
            type[BaseService] | None: Service 类，如果未找到则返回 None

        Examples:
            >>> service_cls = manager.get_service_class("my_plugin:service:calculator")
        """
        registry = get_global_registry()
        return registry.get(signature)

    def get_service(self, signature: str) -> BaseService | None:
        """获取 Service 实例。

        创建新的 Service 实例（非单例模式）。

        Args:
            signature: Service 组件签名

        Returns:
            BaseService | None: Service 实例，如果未找到则返回 None

        Examples:
            >>> service = manager.get_service("my_plugin:service:calculator")
        """
        service_cls = self.get_service_class(signature)
        if not service_cls:
            logger.warning(f"Service 类未找到: {signature}")
            return None

        # 获取插件实例
        sig_info = self._parse_signature(signature)
        if not sig_info:
            return None

        from src.core.managers import get_plugin_manager

        plugin_manager = get_plugin_manager()
        plugin = plugin_manager.get_plugin(sig_info["plugin_name"])

        if not plugin:
            logger.warning(f"插件未加载: {sig_info['plugin_name']}")
            return None

        # 创建新的 Service 实例
        try:
            service_instance = service_cls(plugin=plugin)
            logger.debug(f"创建 Service 实例: {signature}")
            return service_instance
        except Exception as e:
            logger.error(f"创建 Service 实例失败 ({signature}): {e}")
            return None

    def _parse_signature(self, signature: str) -> dict[str, str] | None:
        """解析 Service 签名。

        Args:
            signature: Service 组件签名

        Returns:
            dict[str, str] | None: 解析后的签名信息

        Examples:
            >>> sig_info = manager._parse_signature("my_plugin:service:calculator")
            >>> {"plugin_name": "my_plugin", "component_type": "service", "component_name": "calculator"}
        """
        try:
            from src.core.components.types import parse_signature

            sig_info = parse_signature(signature)
            if sig_info["component_type"] != ComponentType.SERVICE:
                logger.warning(f"组件类型不是 Service: {signature}")
                return None

            return {
                "plugin_name": sig_info["plugin_name"],
                "component_type": sig_info["component_type"].value,
                "component_name": sig_info["component_name"],
            }
        except ValueError as e:
            logger.error(f"解析签名失败: {signature} - {e}")
            return None


# 全局 Service 管理器实例
_global_service_manager: ServiceManager | None = None


def get_service_manager() -> ServiceManager:
    """获取全局 Service 管理器实例。

    Returns:
        ServiceManager: 全局 Service 管理器单例

    Examples:
        >>> manager = get_service_manager()
        >>> service = manager.get_service("my_plugin:service:calculator")
    """
    global _global_service_manager
    if _global_service_manager is None:
        _global_service_manager = ServiceManager()
    return _global_service_manager
