"""emoji_sender 插件配置。

只包含不会引发语义漂移的参数（不包含 persona 与情感 tag 预设）。
配置文件默认路径：config/plugins/emoji_sender/config.toml
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class EmojiSenderConfig(BaseConfig):
    """emoji_sender 插件配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "表情包收藏与发送插件配置"

    @config_section("scheduler")
    class SchedulerSection(SectionBase):
        """调度相关配置。"""

        interval_seconds: int = Field(
            default=120,
            description="入库任务执行间隔（秒）",
        )

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件行为配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用插件",
        )

        inject_system_prompt: bool = Field(
            default=True,
            description="是否将表情包使用提示同步到 default_chatter 的 actor system reminder",
        )

    @config_section("prompt")
    class PromptSection(SectionBase):
        """自定义提示词配置。"""

        custom_instructions: str = Field(
            default="",
            description=(
                "追加到 send_emoji_meme action 描述末备的自定义指令。\n"
                "可描述希望 AI 主动使用表情包的具体场景，"
                "例如：在表达强烈情感、开玩笑或缓解尴尬时主动使用。\n"
                "不会覆盖已有的触发条件，只是扩充场景说明。"
            ),
        )

    @config_section("ingest")
    class IngestSection(SectionBase):
        """入库相关配置。"""

        manual_memes_dir: str = Field(
            default="data/emoji_sender/manual_memes",
            description="手动放置表情包的目录（用法：关闭随机抽取，手动放表情包到此目录）",
        )

        sample_from_media_cache: bool = Field(
            default=True,
            description="是否从 data/media_cache/emojis 随机抽取候选表情包（关闭则使用手动目录，需要手动放置表情包）",
        )

    @config_section("vector")
    class VectorSection(SectionBase):
        """向量库相关配置。"""

        collection_name: str = Field(
            default="emoji_sender",
            description="向量集合名",
        )
        db_path: str = Field(
            default="data/emoji_sender/vector_db",
            description="向量数据库路径（ChromaDB）",
        )
        top_n: int = Field(
            default=8,
            description="检索候选数量 topN",
        )
        max_distance: float = Field(
            default=0.35,
            description="最大距离阈值（距离越小越相似）",
        )
        temperature: float = Field(
            default=0.3,
            description="检索结果采样温度（<=0 时固定选择最相似项，越大越随机）",
        )

    @config_section("storage")
    class StorageSection(SectionBase):
        """文件存储相关配置。"""

        data_dir: str = Field(
            default="data/emoji_sender/memes",
            description="插件表情包复制文件目录",
        )

        max_memes: int = Field(
            default=200,
            description="最大可用表情包数量上限（<=0 表示不限制）；达到上限后不再继续入库",
        )

    scheduler: SchedulerSection = Field(default_factory=SchedulerSection)
    plugin: PluginSection = Field(default_factory=PluginSection)
    prompt: PromptSection = Field(default_factory=PromptSection)
    ingest: IngestSection = Field(default_factory=IngestSection)
    vector: VectorSection = Field(default_factory=VectorSection)
    storage: StorageSection = Field(default_factory=StorageSection)
