"""neo_emoji_sender 插件配置。

配置文件默认路径：config/plugins/neo_emoji_sender/config.toml
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class NeoEmojiSenderConfig(BaseConfig):
    """neo_emoji_sender 插件配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "表情包领域服务插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件行为配置。"""

        enabled: bool = Field(default=True, description="是否启用插件")

        inject_system_prompt: bool = Field(
            default=True,
            description="是否将表情包使用提示同步到 default_chatter 的 actor system reminder",
        )

        interaction_mode: str = Field(
            default="direct",
            description=(
                "表情包发送模式：direct=一步直达（插件自动挑选后直接发送）；"
                "picker=两段式（先查询候选列表，由 AI 亲自挑选后按 id 发送）。"
                "修改后需重载插件生效。"
            ),
        )

    @config_section("prompt")
    class PromptSection(SectionBase):
        """自定义提示词配置。"""

        custom_instructions: str = Field(
            default="",
            description="追加到 LLM 组件描述末尾的自定义场景说明",
        )

    @config_section("storage")
    class StorageSection(SectionBase):
        """文件与数据库存储配置。"""

        data_dir: str = Field(
            default="data/neo_emoji_sender",
            description="插件数据根目录（memes / emoji.db / vector / tmp 均位于其下）",
        )

        max_memes: int = Field(
            default=200,
            description="最大可用表情包数量上限（<=0 表示不限制）",
        )

    @config_section("vector")
    class VectorSection(SectionBase):
        """向量库相关配置。"""

        collection_name: str = Field(default="neo_emoji_sender", description="向量集合名")
        top_n: int = Field(default=8, description="检索候选数量 topN")
        max_distance: float = Field(default=0.35, description="最大距离阈值（距离越小越相似）")
        temperature: float = Field(
            default=0.3,
            description="检索结果采样温度（<=0 时固定选择最相似项，越大越随机）",
        )

    @config_section("reconcile")
    class ReconcileSection(SectionBase):
        """对账配置。"""

        interval_seconds: int = Field(default=1800, description="三方对账周期（秒）")
        startup_delay_seconds: int = Field(default=0, description="启动后首次对账延迟（秒）")
        grace_seconds: int = Field(
            default=600,
            description="孤儿文件清理宽限期（秒），避免与正在进行的注册竞争",
        )

    @config_section("migration")
    class MigrationSection(SectionBase):
        """旧 emoji_sender 数据迁移配置。"""

        enabled: bool = Field(
            default=True,
            description=(
                "是否在插件加载时自动接管旧 emoji_sender 数据；"
                "旧数据路径直接读取旧插件自己的配置"
            ),
        )

    @config_section("picker")
    class PickerSection(SectionBase):
        """两段式挑选模式相关配置。"""

        page_size: int = Field(default=6, description="候选列表每页数量")

    @config_section("dedup")
    class DedupSection(SectionBase):
        """使用历史去重配置。"""

        enabled: bool = Field(
            default=True,
            description="是否过滤每个聊天流最近已发送过的表情包（无可用候选时自动回退全量）",
        )
        window: int = Field(
            default=6,
            description="每个聊天流记住的最近已发送表情包数量",
        )

    @config_section("sources")
    class SourcesSection(SectionBase):
        """来源总开关配置。"""

        scan_batch_size: int = Field(
            default=8,
            description="每轮向来源索取的候选数量上限（应大于 batch_size，用于容忍重复项）",
        )

        @config_section("manual")
        class ManualSection(SectionBase):
            """手动目录来源。"""

            enabled: bool = Field(default=True, description="是否启用手动目录来源")
            dir: str = Field(
                default="data/neo_emoji_sender/manual_memes",
                description="手动放置表情包的目录",
            )
            interval_seconds: int = Field(default=120, description="扫描间隔（秒）")
            batch_size: int = Field(default=1, description="每轮最多入库数量")
            after_register: str = Field(
                default="keep",
                description="注册成功后的处理方式：keep / move / delete",
            )

        @config_section("media")
        class MediaSection(SectionBase):
            """主库 Images 表情来源。"""

            enabled: bool = Field(default=False, description="是否启用主库媒体来源")
            interval_seconds: int = Field(default=120, description="扫描间隔（秒）")
            batch_size: int = Field(default=1, description="每轮最多入库数量")
            vlm_enabled: bool = Field(default=True, description="是否调用 VLM 做收藏决策与标注")

        manual: ManualSection = Field(default_factory=ManualSection)
        media: MediaSection = Field(default_factory=MediaSection)

    plugin: PluginSection = Field(default_factory=PluginSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    vector: VectorSection = Field(default_factory=VectorSection)
    reconcile: ReconcileSection = Field(default_factory=ReconcileSection)
    migration: MigrationSection = Field(default_factory=MigrationSection)
    picker: PickerSection = Field(default_factory=PickerSection)
    dedup: DedupSection = Field(default_factory=DedupSection)
    sources: SourcesSection = Field(default_factory=SourcesSection)
