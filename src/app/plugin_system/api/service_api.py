"""
Service API 模块。

提供 Service 查询与实例创建能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.components.base.service import BaseService
    from src.core.managers.service_manager import ServiceManager


def _get_service_manager() -> "ServiceManager":
    """延迟获取 ServiceManager，避免循环依赖。

    Returns:
        Service 管理器实例
    """
    from src.core.managers.service_manager import get_service_manager

    return get_service_manager()


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def get_all_services() -> dict[str, type["BaseService"]]:
    """获取所有已注册的 Service 组件。

    Returns:
        Service 签名到类的映射
    """
    return _get_service_manager().get_all_services()


def get_services_for_plugin(plugin_name: str) -> dict[str, type["BaseService"]]:
    """获取指定插件的所有 Service 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Service 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_service_manager().get_services_for_plugin(plugin_name)


def get_service_class(signature: str) -> type["BaseService"] | None:
    """通过签名获取 Service 类。

    Args:
        signature: Service 组件签名

    Returns:
        Service 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_service_manager().get_service_class(signature)


def get_service(signature: str) -> "BaseService | None":
    """获取 Service 实例。

    Args:
        signature: Service 组件签名

    Returns:
        Service 实例，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_service_manager().get_service(signature)


__all__ = [
    "get_all_services",
    "get_services_for_plugin",
    "get_service_class",
    "get_service",
]
