"""
Plugin API 模块。

提供插件加载、卸载与查询能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.components.loader import PluginManifest
    from src.core.managers.plugin_manager import PluginManager


def _get_plugin_manager() -> "PluginManager":
    """延迟获取 PluginManager，避免循环依赖。

    Returns:
        插件管理器实例
    """
    from src.core.managers.plugin_manager import get_plugin_manager

    return get_plugin_manager()


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


async def load_plugin_from_manifest(
    plugin_path: str,
    manifest: "PluginManifest",
) -> bool:
    """加载单个插件（manifest 由 loader 提供）。

    Args:
        plugin_path: 插件路径
        manifest: 插件清单

    Returns:
        是否加载成功
    """
    _validate_non_empty(plugin_path, "plugin_path")
    if manifest is None:
        raise ValueError("manifest 不能为空")
    return await _get_plugin_manager().load_plugin_from_manifest(plugin_path, manifest)


async def load_plugin(plugin_path: str) -> bool:
    """按路径加载插件。

    Args:
        plugin_path: 插件路径

    Returns:
        是否加载成功
    """
    _validate_non_empty(plugin_path, "plugin_path")
    return await _get_plugin_manager().load_plugin(plugin_path)


async def unload_plugin(plugin_name: str) -> bool:
    """卸载插件。

    Args:
        plugin_name: 插件名称

    Returns:
        是否卸载成功
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return await _get_plugin_manager().unload_plugin(plugin_name)


async def reload_plugin(plugin_name: str) -> bool:
    """重载插件。

    Args:
        plugin_name: 插件名称

    Returns:
        是否重载成功
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return await _get_plugin_manager().reload_plugin(plugin_name)


def get_plugin(plugin_name: str) -> "BasePlugin | None":
    """获取插件实例。

    Args:
        plugin_name: 插件名称

    Returns:
        插件实例，未找到则返回 None
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_plugin_manager().get_plugin(plugin_name)


def get_all_plugins() -> dict[str, "BasePlugin"]:
    """获取所有已加载插件。

    Returns:
        插件名称到实例的映射
    """
    return _get_plugin_manager().get_all_plugins()


def list_loaded_plugins() -> list[str]:
    """列出所有已加载插件名称。

    Returns:
        插件名称列表
    """
    return _get_plugin_manager().list_loaded_plugins()


def get_manifest(plugin_name: str) -> "PluginManifest | None":
    """获取插件清单。

    Args:
        plugin_name: 插件名称

    Returns:
        插件清单，未找到则返回 None
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_plugin_manager().get_manifest(plugin_name)


def is_plugin_loaded(plugin_name: str) -> bool:
    """检查插件是否已加载。

    Args:
        plugin_name: 插件名称

    Returns:
        是否已加载
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_plugin_manager().is_plugin_loaded(plugin_name)


__all__ = [
    "load_plugin_from_manifest",
    "load_plugin",
    "unload_plugin",
    "reload_plugin",
    "get_plugin",
    "get_all_plugins",
    "list_loaded_plugins",
    "get_manifest",
    "is_plugin_loaded",
]
