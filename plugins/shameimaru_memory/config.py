"""Shameimaru Memory 插件配置。

四层记忆系统：
- 摘要层（summary）：周期性从群聊聊天流生成摘要，按群分别持久化。
- 新闻层（news）：周期性读取所有群聊摘要，整理出总结性的记忆条目。
- 知识层（knowledge）：Dreaming 周期对每个群聊执行知识整理，写入 booku_memory_store。
- 人物层（persona）：新闻条目被删除（上限淘汰 / 知识化）时增量维护人物背景信息。
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class ShameimaruMemoryConfig(BaseConfig):
    """Shameimaru Memory 插件配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Shameimaru Memory 四层记忆系统配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件级开关。"""

        enabled: bool = Field(
            default=True,
            description="是否启用插件",
            label="启用插件",
            tag="plugin",
        )
        run_on_start: bool = Field(
            default=False,
            description="插件加载后立即执行一次摘要/新闻/Dreaming 任务",
            label="启动立即执行",
            tag="timer",
            hint="会立即产生 LLM 调用，建议仅在需要时开启",
        )

    @config_section("storage", title="存储配置", tag="database")
    class StorageSection(SectionBase):
        """本地 JSON 持久化配置。"""

        data_dir: str = Field(
            default="data/shameimaru_memory",
            description="记忆数据目录（摘要/新闻/人物信息三个 JSON 数据库均存放于此）",
            label="数据目录",
            input_type="text",
            tag="file",
        )

    @config_section("llm", title="LLM 配置", tag="ai")
    class LLMSection(SectionBase):
        """内部子 agent 使用的模型任务配置。"""

        summary_task: str = Field(
            default="actor",
            description="摘要层子 agent 使用的模型任务名",
            label="摘要任务",
            placeholder="actor",
            tag="ai",
            hint="确保该任务在 model.toml 中已配置",
        )
        news_task: str = Field(
            default="actor",
            description="新闻层子 agent 使用的模型任务名",
            label="新闻任务",
            placeholder="actor",
            tag="ai",
        )
        knowledge_task: str = Field(
            default="actor",
            description="知识层子 agent 使用的模型任务名",
            label="知识任务",
            placeholder="actor",
            tag="ai",
        )
        persona_task: str = Field(
            default="actor",
            description="人物层子 agent 使用的模型任务名",
            label="人物任务",
            placeholder="actor",
            tag="ai",
        )

    @config_section("summary", title="摘要层配置", tag="timer")
    class SummarySection(SectionBase):
        """摘要层：从群聊聊天流生成摘要并持久化。"""

        interval_minutes: int = Field(
            default=30,
            description="摘要生成间隔（分钟）",
            label="摘要间隔（分钟）",
            ge=1,
            le=1440,
            tag="timer",
        )
        max_entries_per_group: int = Field(
            default=50,
            description="每个群聊的摘要条目数上限，达到上限时删除最早的一条",
            label="每群摘要上限",
            ge=1,
            le=500,
            tag="performance",
        )
        max_messages_per_run: int = Field(
            default=300,
            description="每次为单个群聊读取的最大消息条数",
            label="单群消息上限",
            ge=1,
            le=2000,
            tag="performance",
        )

    @config_section("news", title="新闻层配置", tag="timer")
    class NewsSection(SectionBase):
        """新闻层：从所有群聊摘要中整理总结性记忆条目。"""

        interval_minutes: int = Field(
            default=120,
            description="新闻整理间隔（分钟）",
            label="新闻间隔（分钟）",
            ge=1,
            le=1440,
            tag="timer",
        )
        max_entries: int = Field(
            default=50,
            description="新闻条目总数上限，达到上限时删除最早的一条",
            label="新闻条目上限",
            ge=1,
            le=500,
            tag="performance",
        )
        max_input_summaries: int = Field(
            default=100,
            description="单个群聊单次整理最多读取的摘要条数（按时间取最新的，不含已废弃条目）",
            label="单群摘要输入上限",
            ge=1,
            le=500,
            tag="performance",
            hint="参与处理的摘要会在整理后标记为废弃，供知识层读取",
        )

    @config_section("knowledge", title="知识层配置", tag="timer")
    class KnowledgeSection(SectionBase):
        """知识层：Dreaming 周期知识整理，写入 booku_memory_store。"""

        dream_interval_hours: int = Field(
            default=24,
            description="Dreaming 知识整理间隔（小时）",
            label="Dreaming 间隔（小时）",
            ge=1,
            le=168,
            tag="timer",
            hint="理论应小于：摘要条目上限数 × 摘要更新时间间隔",
        )
        service_signature: str = Field(
            default="booku_memory_store:service:booku_memory_store",
            description="booku_memory_store 服务组件签名（知识存储数据库）",
            label="知识库服务签名",
            placeholder="booku_memory_store:service:booku_memory_store",
            tag="general",
        )
        folder_id: str = Field(
            default="default",
            description="知识条目写入 booku_memory_store 的文件夹 ID",
            label="知识文件夹",
            placeholder="default",
            tag="general",
        )
        max_group_summaries_input: int = Field(
            default=20,
            description="Dreaming 时观察单个群聊的最近摘要条数",
            label="群摘要观察上限",
            ge=1,
            le=200,
            tag="performance",
        )

    @config_section("persona", title="人物层配置", tag="ai")
    class PersonaSection(SectionBase):
        """人物层：被删除新闻涉及人物的背景信息增量更新。"""

        max_text_length: int = Field(
            default=2000,
            description="单个人物背景信息文本的最大长度",
            label="人物信息长度上限",
            ge=100,
            le=10000,
            tag="performance",
        )

    @config_section("injection", title="回复前注入配置", tag="ai")
    class InjectionSection(SectionBase):
        """每次回复前根据 unread message 中出现的人物注入相关记忆。"""

        prompt_name: str = Field(
            default="neo_default_chatter_user_prompt",
            description="注入目标模板名",
            label="目标模板",
            placeholder="neo_default_chatter_user_prompt",
            tag="general",
        )
        bucket: str = Field(
            default="actor",
            description="system reminder 写入的 bucket 名（需与 chatter 的 with_reminder 一致）",
            label="Reminder Bucket",
            placeholder="actor",
            tag="general",
            hint="default_chatter 使用 with_reminder=\"actor\"，写入流私有 bucket 后自动拾取",
        )
        inject_news: bool = Field(
            default=True,
            description="是否在回复前注入涉及当前人物的新闻记忆",
            label="注入新闻",
            tag="ai",
        )
        inject_personas: bool = Field(
            default=True,
            description="是否在回复前注入涉及当前人物的人物背景信息",
            label="注入人物信息",
            tag="ai",
        )
        news_max_inject: int = Field(
            default=5,
            description="单次回复最多注入的新闻条数",
            label="新闻注入上限",
            ge=1,
            le=20,
            tag="performance",
        )
        persona_max_inject: int = Field(
            default=5,
            description="单次回复最多注入的人物背景条数",
            label="人物注入上限",
            ge=1,
            le=20,
            tag="performance",
        )
        person_scan_history_limit: int = Field(
            default=20,
            description="收集当前对话人物时扫描的最近历史消息条数（unread 被 flush 后兜底）",
            label="人物扫描历史条数",
            ge=1,
            le=200,
            tag="performance",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    summary: SummarySection = Field(default_factory=SummarySection)
    news: NewsSection = Field(default_factory=NewsSection)
    knowledge: KnowledgeSection = Field(default_factory=KnowledgeSection)
    persona: PersonaSection = Field(default_factory=PersonaSection)
    injection: InjectionSection = Field(default_factory=InjectionSection)
