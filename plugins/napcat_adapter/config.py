"""Napcat Adapter 配置定义"""
from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class NapcatAdapterConfig(BaseConfig):
    """Napcat 适配器配置"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "Napcat/OneBot 11 适配器配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件基本配置"""

        enabled: bool = Field(default=True, description="是否启用 Napcat 适配器")
        config_version: str = Field(default="2.0.0", description="配置文件版本")

    @config_section("bot")
    class BotSection(SectionBase):
        """Bot 基本配置"""

        qq_id: str = Field(default="", description="Bot 的 QQ 账号 ID")
        qq_nickname: str = Field(default="", description="Bot 的 QQ 昵称")

    @config_section("napcat_server")
    class NapcatServerSection(SectionBase):
        """Napcat WebSocket 服务器配置"""

        mode: str = Field(
            default="reverse",
            description="ws 连接模式: reverse/direct",
        )
        host: str = Field(default="localhost", description="Napcat WebSocket 服务地址")
        port: int = Field(default=8095, description="Napcat WebSocket 服务端口")
        access_token: str = Field(default="", description="Napcat API 访问令牌（可选）")

    @config_section("features")
    class FeaturesSection(SectionBase):
        """功能特性配置"""

        group_list_type: str = Field(
            default="blacklist",
            description="群聊名单模式: blacklist/whitelist",
        )
        group_list: list[str | int] = Field(
            default_factory=list,
            description="群聊名单；根据名单模式过滤",
        )
        private_list_type: str = Field(
            default="blacklist",
            description="私聊名单模式: blacklist/whitelist",
        )
        private_list: list[str | int] = Field(
            default_factory=list,
            description="私聊名单；根据名单模式过滤",
        )
        ban_user_id: list[str | int] = Field(
            default_factory=list,
            description="全局封禁的用户 ID 列表",
        )
        ban_qq_bot: bool = Field(default=False, description="是否屏蔽其他 QQ 机器人消息")
        enable_poke: bool = Field(default=True, description="是否启用戳一戳消息处理")
        ignore_non_self_poke: bool = Field(
            default=False,
            description="是否忽略不是针对自己的戳一戳消息",
        )
        poke_debounce_seconds: float = Field(default=2.0, description="戳一戳防抖时间（秒）")
        enable_emoji_like: bool = Field(default=True, description="是否启用群聊表情回复处理")
        enable_reply_at: bool = Field(default=True, description="是否在回复时自动@原消息发送者")
        reply_at_rate: float = Field(default=0.5, description="回复时@的概率（0.0-1.0）")
        enable_video_processing: bool = Field(
            default=True,
            description="是否启用视频消息处理（下载和解析）",
        )
        video_max_size_mb: int = Field(
            default=100,
            description="允许下载的视频文件最大大小（MB）",
        )
        video_download_timeout: int = Field(
            default=60,
            description="视频下载超时时间（秒）",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    bot: BotSection = Field(default_factory=BotSection)
    napcat_server: NapcatServerSection = Field(default_factory=NapcatServerSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
