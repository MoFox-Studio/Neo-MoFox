"""配置管理器。

本模块提供 ConfigManager，负责插件配置的加载、重新加载和缓存管理。
每个插件的配置通过插件名称进行标识和缓存。
"""

from __future__ import annotations

from typing import Type


from src.kernel.logger import get_logger
from src.core.components.base.config import BaseConfig


logger = get_logger("config_manager")


class ConfigManager:
    """配置管理器。

    负责管理所有插件的配置实例，提供加载、重新加载和查询接口。
    使用 _configs 字典缓存已加载的配置实例，避免重复加载。

    Attributes:
        _configs: 已加载的配置缓存，格式：{plugin_name: config_instance}

    Examples:
        >>> manager = ConfigManager()
        >>> config = manager.load_config("my_plugin", MyPluginConfig)
        >>> config = manager.get_config("my_plugin")
    """

    def __init__(self) -> None:
        """初始化配置管理器。"""
        self._configs: dict[str, BaseConfig] = {}
        logger.info("配置管理器初始化完成")

    def load_config(
        self,
        plugin_name: str,
        config_class: Type[BaseConfig],
        *,
        auto_generate: bool = True,
        auto_update: bool = True,
    ) -> BaseConfig:
        """加载插件配置。

        加载指定插件的配置，如果未找到配置文件且 auto_generate 为 True，
        则自动生成默认配置文件。

        Args:
            plugin_name: 插件名称
            config_class: 配置类
            auto_generate: 如果为 True，在文件不存在时生成默认配置
            auto_update: 如果为 True，自动使用新字段更新配置文件

        Returns:
            加载的配置实例

        Raises:
            FileNotFoundError: 如果配置文件不存在且 auto_generate 为 False

        Examples:
            >>> manager = ConfigManager()
            >>> config = manager.load_config("my_plugin", MyPluginConfig)
        """
        # 检查是否已加载
        if plugin_name in self._configs:
            logger.debug(f"插件 '{plugin_name}' 配置已缓存，直接返回")
            return self._configs[plugin_name]

        # 使用 load_for_plugin 方法加载配置
        config = config_class.load_for_plugin(
            plugin_name,
            auto_generate=auto_generate,
            auto_update=auto_update,
        )

        # 缓存配置实例
        self._configs[plugin_name] = config
        logger.debug(f"已加载并缓存插件 '{plugin_name}' 配置")

        return config

    def reload_config(
        self,
        plugin_name: str,
        config_class: Type[BaseConfig],
        *,
        auto_update: bool = True,
    ) -> BaseConfig:
        """重新加载插件配置。

        重新加载指定插件的配置，丢弃之前的缓存。

        Args:
            plugin_name: 插件名称
            config_class: 配置类

        Returns:
            重新加载的配置实例

        Raises:
            FileNotFoundError: 如果配置文件不存在

        Examples:
            >>> manager = ConfigManager()
            >>> config = manager.reload_config("my_plugin", MyPluginConfig)
        """
        # 从缓存中移除现有配置
        if plugin_name in self._configs:
            del self._configs[plugin_name]
            logger.debug(f"已移除插件 '{plugin_name}' 配置缓存")

        # 使用 BaseConfig.reload 方法重新加载配置
        config = config_class.reload()

        # 更新缓存
        self._configs[plugin_name] = config
        logger.debug(f"已重新加载并缓存插件 '{plugin_name}' 配置")

        return config

    def get_config(self, plugin_name: str) -> BaseConfig | None:
        """获取已加载的配置实例。

        从缓存中获取指定插件的配置实例。

        Args:
            plugin_name: 插件名称

        Returns:
            已加载的配置实例，如果未找到返回 None

        Examples:
            >>> manager = ConfigManager()
            >>> config = manager.load_config("my_plugin", MyPluginConfig)
            >>> config = manager.get_config("my_plugin")
        """
        config = self._configs.get(plugin_name)
        if config is None:
            logger.debug(f"插件 '{plugin_name}' 配置未加载")
        else:
            logger.debug(f"从缓存获取插件 '{plugin_name}' 配置")

        return config

    def remove_config(self, plugin_name: str) -> bool:
        """移除指定插件的配置缓存。

        从缓存中移除指定插件的配置。

        Args:
            plugin_name: 插件名称

        Returns:
            bool: 如果成功移除返回 True，如果插件未加载返回 False

        Examples:
            >>> manager = ConfigManager()
            >>> success = manager.remove_config("my_plugin")
        """
        if plugin_name in self._configs:
            del self._configs[plugin_name]
            logger.debug(f"已移除插件 '{plugin_name}' 配置缓存")
            return True
        else:
            logger.debug(f"插件 '{plugin_name}' 配置未加载，无需移除")
            return False

    def get_loaded_plugins(self) -> list[str]:
        """获取所有已加载配置的插件名称列表。

        Returns:
            list[str]: 已加载配置的插件名称列表

        Examples:
            >>> manager = ConfigManager()
            >>> plugins = manager.get_loaded_plugins()
        """
        return list(self._configs.keys())
    
    def initialize_all_configs(self) -> None:
        """初始化所有包含Config组件的插件配置。
        遍历已注册的插件，加载其配置组件。

        Examples:
            >>> manager = ConfigManager()
            >>> manager.initialize_all_configs()
        """
        from src.core.components.registry import get_global_registry
        from src.core.components.types import ComponentType, parse_signature

        registry = get_global_registry()
        # 获取所有已注册的配置组件类
        config_classes = registry.get_by_type(ComponentType.CONFIG)

        if not config_classes:
            logger.debug("没有找到注册的配置组件")
            return

        for signature, config_cls in config_classes.items():
            try:
                # 解析签名获取插件名
                sig_info = parse_signature(signature)
                plugin_name = sig_info["plugin_name"]

                # 显式转换为 BaseConfig 类型以满足类型检查
                if issubclass(config_cls, BaseConfig):  # type: ignore
                    self.load_config(plugin_name, config_cls)  # type: ignore
                else:
                    logger.warning(f"组件 {signature} 不是 BaseConfig 的子类")

            except Exception as e:
                logger.error(f"初始化配置失败: {signature} - {e}")


# 全局配置管理器实例
_global_config_manager: "ConfigManager | None" = None


def get_config_manager() -> "ConfigManager":
    """获取全局配置管理器实例。

    Returns:
        ConfigManager: 全局配置管理器单例

    Examples:
        >>> manager = get_config_manager()
        >>> config = manager.load_config("my_plugin", MyPluginConfig)
    """
    global _global_config_manager
    if _global_config_manager is None:
        _global_config_manager = ConfigManager()
    return _global_config_manager


def reset_config_manager() -> None:
    """重置全局配置管理器。

    主要用于测试场景，确保测试之间不会相互影响。
    """
    global _global_config_manager
    _global_config_manager = None
