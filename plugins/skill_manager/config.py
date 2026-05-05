"""SkillManager 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base import BaseConfig
from src.kernel.config.core import Field, SectionBase, config_section


class SkillManagerConfig(BaseConfig):
    """SkillManager 配置模型。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "SkillManager 配置"

    @config_section("manager", title="技能管理", tag="plugin", order=0)
    class ManagerSection(SectionBase):
        """技能管理主配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 SkillManager",
        )
        paths: list[str] = Field(
            default_factory=lambda: ["skill"],
            description="skill 根目录路径列表；相对路径默认相对项目根目录",
        )
        inject_actor_reminder: bool = Field(
            default=True,
            description="是否注入 actor system reminder",
        )
        inject_sub_actor_reminder: bool = Field(
            default=True,
            description="是否注入 sub_actor system reminder",
        )

    manager: ManagerSection = Field(default_factory=ManagerSection)
