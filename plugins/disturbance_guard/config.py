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
        model_task: str = Field(
            default="utils_small",
            description="用于意图判定的模型任务名称",
            label="判定模型",
            tag="ai",
            order=3,
        )
        llm_timeout: float = Field(
            default=7.0,
            description="LLM 意图判定的超时时间（秒）",
            label="判定超时",
            tag="ai",
            order=4,
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    guard: GuardSection = Field(default_factory=GuardSection)

