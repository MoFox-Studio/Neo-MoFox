"""DefaultChatter 插件配置定义。"""

from __future__ import annotations

from typing import ClassVar, Literal

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class DefaultChatterConfig(BaseConfig):
    """DefaultChatter 配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "DefaultChatter 配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件基础配置。"""

        enabled: bool = Field(default=True, description="是否启用 DefaultChatter")
        mode: Literal["enhanced", "classical"] = Field(
            default="enhanced",
            description="执行模式: enhanced/classical",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
