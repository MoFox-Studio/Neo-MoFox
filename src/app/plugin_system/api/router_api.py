"""
Router API 模块。

提供路由查询、挂载与卸载能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.components.base.router import BaseRouter
    from src.core.managers.router_manager import RouterManager


def _get_router_manager() -> "RouterManager":
    """延迟获取 RouterManager，避免循环依赖。

    Returns:
        路由管理器实例
    """
    from src.core.managers.router_manager import get_router_manager

    return get_router_manager()


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


def get_all_routers() -> dict[str, type["BaseRouter"]]:
    """获取所有已注册的 Router 组件。

    Returns:
        Router 签名到类的映射
    """
    return _get_router_manager().get_all_routers()


def get_routers_for_plugin(plugin_name: str) -> dict[str, type["BaseRouter"]]:
    """获取指定插件的所有 Router 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Router 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_router_manager().get_routers_for_plugin(plugin_name)


def get_router_class(signature: str) -> type["BaseRouter"] | None:
    """通过签名获取 Router 类。

    Args:
        signature: Router 组件签名

    Returns:
        Router 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_router_manager().get_router_class(signature)


def get_mounted_router(signature: str) -> "BaseRouter | None":
    """获取已挂载的 Router 实例。

    Args:
        signature: Router 组件签名

    Returns:
        Router 实例，未挂载则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_router_manager().get_mounted_router(signature)


def get_all_mounted_routers() -> dict[str, "BaseRouter"]:
    """获取所有已挂载的 Router 实例。

    Returns:
        Router 签名到实例的映射
    """
    return _get_router_manager().get_all_mounted_routers()


async def mount_router(signature: str, plugin: "BasePlugin") -> "BaseRouter":
    """挂载单个 Router。

    Args:
        signature: Router 组件签名
        plugin: 插件实例

    Returns:
        Router 实例
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    return await _get_router_manager().mount_router(signature=signature, plugin=plugin)


async def unmount_router(signature: str) -> None:
    """卸载单个 Router。

    Args:
        signature: Router 组件签名

    Returns:
        None
    """
    _validate_non_empty(signature, "signature")
    await _get_router_manager().unmount_router(signature)


async def mount_plugin_routers(plugin: "BasePlugin") -> list["BaseRouter"]:
    """挂载插件的所有 Router 组件。

    Args:
        plugin: 插件实例

    Returns:
        Router 实例列表
    """
    if plugin is None:
        raise ValueError("plugin 不能为空")
    return await _get_router_manager().mount_plugin_routers(plugin)


async def unmount_plugin_routers(plugin_name: str) -> None:
    """卸载插件的所有 Router 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        None
    """
    _validate_non_empty(plugin_name, "plugin_name")
    await _get_router_manager().unmount_plugin_routers(plugin_name)


async def mount_all_routers() -> None:
    """挂载所有 Router 组件。

    Returns:
        None
    """
    await _get_router_manager().mount_all_routers()


async def unmount_all_routers() -> None:
    """卸载所有 Router 组件。

    Returns:
        None
    """
    await _get_router_manager().unmount_all_routers()


def get_router_info(signature: str) -> dict[str, Any] | None:
    """获取 Router 信息。

    Args:
        signature: Router 组件签名

    Returns:
        Router 信息字典，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_router_manager().get_router_info(signature)


def get_all_router_info() -> list[dict[str, Any]]:
    """获取所有 Router 信息列表。

    Returns:
        Router 信息列表
    """
    return _get_router_manager().get_all_router_info()


async def reload_router(signature: str, plugin: "BasePlugin") -> "BaseRouter":
    """重新加载 Router。

    Args:
        signature: Router 组件签名
        plugin: 插件实例

    Returns:
        Router 实例
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    return await _get_router_manager().reload_router(signature, plugin)


__all__ = [
    "get_all_routers",
    "get_routers_for_plugin",
    "get_router_class",
    "get_mounted_router",
    "get_all_mounted_routers",
    "mount_router",
    "unmount_router",
    "mount_plugin_routers",
    "unmount_plugin_routers",
    "mount_all_routers",
    "unmount_all_routers",
    "get_router_info",
    "get_all_router_info",
    "reload_router",
]
