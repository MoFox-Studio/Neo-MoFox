"""
Config API 模块。

提供插件配置加载、重载与查询能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Type

from src.core.components.base.config import BaseConfig

if TYPE_CHECKING:
    from src.core.managers.config_manager import ConfigManager


def _get_config_manager() -> "ConfigManager":
    """延迟获取 ConfigManager，避免循环依赖。

    Returns:
        配置管理器实例
    """
    from src.core.managers.config_manager import get_config_manager

    return get_config_manager()


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


def _validate_config_class(config_class: Type[BaseConfig]) -> None:
    """校验配置类。

    Args:
        config_class: 配置类

    Returns:
        None
    """
    if not isinstance(config_class, type) or not issubclass(config_class, BaseConfig):
        raise ValueError("config_class 必须是 BaseConfig 子类")


def load_config(
    plugin_name: str,
    config_class: Type[BaseConfig],
    *,
    auto_generate: bool = True,
    auto_update: bool = True,
) -> BaseConfig:
    """加载插件配置。

    Args:
        plugin_name: 插件名称
        config_class: 配置类
        auto_generate: 是否自动生成配置文件
        auto_update: 是否自动更新配置文件

    Returns:
        配置实例
    """
    _validate_non_empty(plugin_name, "plugin_name")
    _validate_config_class(config_class)
    return _get_config_manager().load_config(
        plugin_name=plugin_name,
        config_class=config_class,
        auto_generate=auto_generate,
        auto_update=auto_update,
    )


def reload_config(
    plugin_name: str,
    config_class: Type[BaseConfig],
    *,
    auto_update: bool = True,
) -> BaseConfig:
    """重新加载插件配置。

    Args:
        plugin_name: 插件名称
        config_class: 配置类
        auto_update: 是否自动更新配置文件

    Returns:
        配置实例
    """
    _validate_non_empty(plugin_name, "plugin_name")
    _validate_config_class(config_class)
    return _get_config_manager().reload_config(
        plugin_name=plugin_name,
        config_class=config_class,
        auto_update=auto_update,
    )


def get_config(plugin_name: str) -> BaseConfig | None:
    """获取已加载的配置实例。

    Args:
        plugin_name: 插件名称

    Returns:
        配置实例，未找到则返回 None
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_config_manager().get_config(plugin_name)


def remove_config(plugin_name: str) -> bool:
    """移除指定插件的配置缓存。

    Args:
        plugin_name: 插件名称

    Returns:
        是否移除成功
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_config_manager().remove_config(plugin_name)


def get_loaded_plugins() -> list[str]:
    """获取已加载配置的插件名称列表。

    Returns:
        插件名称列表
    """
    return _get_config_manager().get_loaded_plugins()


def initialize_all_configs() -> None:
    """初始化所有包含 Config 组件的插件配置。

    Returns:
        None
    """
    _get_config_manager().initialize_all_configs()


__all__ = [
    "load_config",
    "reload_config",
    "get_config",
    "remove_config",
    "get_loaded_plugins",
    "initialize_all_configs",
]
