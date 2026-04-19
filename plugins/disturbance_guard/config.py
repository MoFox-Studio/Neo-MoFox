"""disturbance_guard 配置定义。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import (
    BaseConfig,
    Field,
    SectionBase,
    config_section,
)


class DisturbanceGuardConfig(BaseConfig):
    """打扰感知插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "打扰感知配置"

    @config_section("plugin", title="插件设置", tag="plugin", order=0)
    class PluginSection(SectionBase):
        """插件基础配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 disturbance_guard 插件",
            label="启用插件",
            tag="plugin",
            order=0,
        )

    @config_section("guard", title="打扰感知", tag="ai", order=10)
    class GuardSection(SectionBase):
        """打扰感知规则配置。"""

        apply_to_private_chat: bool = Field(
            default=True,
            description="是否在私聊场景启用打扰感知",
            label="私聊启用",
            tag="scope",
            order=0,
        )
        apply_to_group_chat: bool = Field(
            default=False,
            description="是否在群聊场景启用打扰感知",
            label="群聊启用",
            tag="scope",
            order=1,
        )
        quiet_minutes: float = Field(
            default=30.0,
            description="命中打扰感知后进入免打扰的时长（分钟）",
            label="免打扰时长",
            tag="behavior",
            order=2,
        )
        quiet_intent_patterns: list[str] = Field(
            default_factory=lambda: [
                r"我先忙(?:一会|一下)?",
                r"晚点聊",
                r"回头聊",
                r"有空再聊",
                r"先不聊了",
                r"先这样吧",
                r"别吵",
                r"不要打扰我",
                r"先别烦我",
                r"我去忙了",
            ],
            description="触发免打扰的正则表达式列表",
            label="免打扰触发词",
            tag="behavior",
            order=3,
        )
        wake_intent_patterns: list[str] = Field(
            default_factory=lambda: [
                r"我回来了",
                r"继续聊",
                r"继续说",
                r"现在有空",
                r"聊聊",
                r"在吗",
                r"可以聊了",
            ],
            description="解除免打扰的正则表达式列表",
            label="免打扰唤醒词",
            tag="behavior",
            order=4,
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    guard: GuardSection = Field(default_factory=GuardSection)

