"""配置组件基类。

本模块提供用于插件配置管理的 BaseConfig 类。
扩展了 kernel 的 ConfigBase，提供插件特定的功能，如
自动默认路径生成和默认配置文件生成。
"""

from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING

from src.kernel.config import ConfigBase

if TYPE_CHECKING:
    pass


class BaseConfig(ABC, ConfigBase):
    """插件配置基类。

    扩展 ConfigBase，提供插件特定的配置管理。
    每个插件都应通过继承此类并定义配置节来定义其配置。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        config_name: 配置文件名称（不含 .toml 扩展名）
        config_description: 配置的人类可读描述

    Examples:
        >>> from src.core.components.base.config import BaseConfig, config_section, Field, SectionBase
        >>>
        >>> class MyPluginConfig(BaseConfig):
        ...     config_name: str = "config"
        ...     config_description: str = "我的插件配置"
        ...
        ...     @config_section("inner")
        ...     class InnerSection(SectionBase):
        ...         version: str = Field(default="1.0.0", description="配置版本")
        ...         enabled: bool = Field(default=False, description="启用插件")
        ...
        ...     inner: InnerSection = Field(default_factory=InnerSection)
    """

    # 所属插件名称（如被注入，可用于默认路径等场景）
    plugin_name: str = "unknown_plugin"

    # 这些属性应由子类覆盖
    config_name: str = "config"
    config_description: str = ""

    @classmethod
    def get_default_path(cls) -> Path:
        """获取此插件的默认配置文件路径。

        基于插件模块位置构造路径。
        默认格式：config/plugins/{plugin_name}/config.toml

        Returns:
            Path: 配置文件的默认路径

        Examples:
            >>> path = MyPluginConfig.get_default_path()
            >>> Path("config/plugins/my_plugin/config.toml")
        """

        return Path("config") / "plugins" / cls.plugin_name / f"{cls.config_name}.toml"

    @classmethod
    def get_signature(cls) -> str | None:
        """获取动作组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:action:action_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = SendEmoji.get_signature()
            >>> "my_plugin:action:send_emoji"
        """
        return f"{cls.plugin_name}:config:{cls.config_name}" if cls.plugin_name != "unknown_plugin" else None

    @classmethod
    def generate_default(cls, path: str | Path | None = None) -> None:
        """生成默认配置文件。

        基于配置模型创建包含默认值的 TOML 文件。
        如果未提供路径，使用 get_default_path() 的默认路径。

        Args:
            path: 生成配置文件的路径。如果为 None，使用默认路径

        Raises:
            OSError: 如果无法写入文件
            RuntimeError: 如果未设置 config_name

        Examples:
            >>> MyPluginConfig.generate_default()
            >>> # 创建：config/plugins/my_plugin/config.toml
            >>>
            >>> MyPluginConfig.generate_default("custom/path/config.toml")
            >>> # 创建：custom/path/config.toml
        """
        if not cls.config_name:
            raise RuntimeError(f"{cls.__name__} 必须定义 config_name")

        if path is None:
            path = cls.get_default_path()
        else:
            path = Path(path)

        # 创建父目录
        path.parent.mkdir(parents=True, exist_ok=True)

        # 获取默认配置数据
        default_data = cls.default()

        # 使用签名渲染为 TOML
        from src.kernel.config.core import _render_toml_with_signature

        toml_content = _render_toml_with_signature(cls, default_data)

        # 写入文件
        path.write_text(toml_content, encoding="utf-8")

    @classmethod
    def load_for_plugin(
        cls,
        plugin_name: str,
        *,
        auto_generate: bool = True,
        auto_update: bool = True,
    ) -> "BaseConfig":
        """为特定插件加载配置。

        加载插件配置的便捷方法，支持自动默认文件生成。

        Args:
            plugin_name: 插件名称
            auto_generate: 如果为 True，在文件不存在时生成默认配置
            auto_update: 如果为 True，自动使用新字段更新配置文件

        Returns:
            加载的配置实例

        Examples:
            >>> config = MyPluginConfig.load_for_plugin("my_plugin")
            >>> print(config.inner.enabled)
        """
        # 构造路径
        config_path = Path("config") / "plugins" / plugin_name / f"{cls.config_name}.toml"

        # 检查文件是否存在
        if not config_path.exists():
            if auto_generate:
                cls.generate_default(config_path)
            else:
                raise FileNotFoundError(f"配置文件未找到: {config_path}")

        # 加载配置
        return cls.load(config_path, auto_update=auto_update)

    @classmethod
    def reload(cls) -> "BaseConfig":
        """从默认路径重新加载配置。

        这是一个便捷方法，从默认路径加载配置并启用自动更新。

        Returns:
            重新加载的配置实例

        Examples:
            >>> config = MyPluginConfig.reload()
        """
        path = cls.get_default_path()
        if not path.exists():
            raise FileNotFoundError(f"配置文件未找到: {path}")

        return cls.load(path, auto_update=True)
