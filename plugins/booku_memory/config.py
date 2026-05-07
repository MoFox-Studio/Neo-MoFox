"""Booku Memory Agent 插件配置。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class BookuMemoryConfig(BaseConfig):
    """Booku Memory Agent 插件配置模型。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "Booku Memory Agent 配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件级开关。"""

        enabled: bool = Field(
            default=True,
            description="是否启用插件",
            label="启用插件",
            tag="plugin"
        )
        inject_system_prompt: bool = Field(
            default=True,
            description="是否将记忆引导语同步到 default_chatter 的 actor system reminder",
            label="注入系统提示",
            tag="ai",
            hint="开启后会在 AI 系统提示中添加记忆相关引导"
        )

    @config_section("storage", title="存储配置", tag="database")
    class StorageSection(SectionBase):
        """存储层配置。"""

        metadata_db_path: str = Field(
            default="data/booku_memory/metadata.db",
            description="SQLite 元数据数据库路径",
            label="元数据数据库",
            input_type="text",
            tag="file"
        )
        vector_db_path: str = Field(
            default="data/chroma_db/booku_memory",
            description="向量数据库路径",
            label="向量数据库",
            input_type="text",
            tag="file"
        )
        default_folder_id: str = Field(
            default="default",
            description="默认活动记忆文件夹 ID",
            label="默认文件夹",
            placeholder="default",
            tag="general"
        )

    @config_section("retrieval", title="检索配置", tag="ai")
    class RetrievalSection(SectionBase):
        """检索与重塑配置。"""

        default_top_k: int = Field(
            default=5,
            description="默认召回条数",
            label="默认召回数",
            ge=1,
            le=50,
            tag="performance"
        )
        include_archived_default: bool = Field(
            default=False,
            description="默认是否检索归档记忆",
            label="默认检索归档",
            tag="general"
        )
        include_knowledge_default: bool = Field(
            default=False,
            description="默认是否检索知识库",
            label="默认检索知识库",
            tag="general"
        )
        deduplication_threshold: float = Field(
            default=0.88,
            description="结果去重余弦阈值",
            label="去重阈值",
            ge=0.0,
            le=1.0,
            step=0.01,
            input_type="slider",
            tag="performance"
        )
        base_beta: float = Field(
            default=0.3,
            description="向量重塑基准强度",
            label="重塑基准强度",
            ge=0.0,
            le=1.0,
            step=0.05,
            input_type="slider",
            tag="ai"
        )
        logic_depth_scale: float = Field(
            default=0.5,
            description="逻辑深度对 beta 的增益系数",
            label="逻辑深度系数",
            ge=0.0,
            le=2.0,
            step=0.1,
            tag="ai"
        )
        core_boost_min: float = Field(
            default=1.2,
            description="核心标签最小增强",
            label="核心标签最小增强",
            ge=1.0,
            le=3.0,
            step=0.1,
            tag="performance"
        )
        core_boost_max: float = Field(
            default=1.4,
            description="核心标签最大增强",
            label="核心标签最大增强",
            ge=1.0,
            le=3.0,
            step=0.1,
            tag="performance"
        )
        diffusion_boost: float = Field(
            default=0.3,
            description="扩散标签增强权重",
            label="扩散增强权重",
            ge=0.0,
            le=1.0,
            step=0.05,
            tag="performance"
        )
        opposing_penalty: float = Field(
            default=0.5,
            description="对立标签惩罚权重",
            label="对立惩罚权重",
            ge=0.0,
            le=1.0,
            step=0.05,
            tag="performance"
        )

    @config_section("write_conflict", title="写入冲突检测", tag="ai")
    class WriteConflictSection(SectionBase):
        """写入冲突检测配置。"""

        top_n: int = Field(
            default=8,
            description="写入冲突检查的检索样本数",
            label="检索样本数",
            ge=1,
            le=50,
            tag="performance"
        )
        energy_cutoff: float = Field(
            default=0.1,
            description="新颖度能量阈值，低于此值触发合并",
            label="新颖度阈值",
            ge=0.0,
            le=1.0,
            step=0.05,
            input_type="slider",
            tag="ai"
        )

    @config_section("time_window", title="隐现记忆窗口", tag="timer")
    class TimeWindowSection(SectionBase):
        """隐现记忆时间窗口与晋升配置。"""

        emergent_days: int = Field(
            default=7,
            description="隐现记忆时间窗口（天）；超出窗口后进入晋升检查",
            label="时间窗口（天）",
            ge=1,
            le=30,
            tag="timer"
        )
        activation_threshold: int = Field(
            default=2,
            description="隐现记忆在时间窗口内最少激活次数，达到后晋升为归档记忆，否则丢弃",
            label="激活阈值",
            ge=1,
            le=20,
            tag="performance"
        )

    @config_section("internal_llm", title="内部 LLM 配置", tag="ai")
    class InternalLLMSection(SectionBase):
        """Agent 内部 LLM 决策配置。"""

        task_name: str = Field(
            default="tool_use",
            description="内部决策使用的模型任务名",
            label="模型任务",
            placeholder="tool_use",
            tag="ai",
            hint="确保该任务在 model.toml 中已配置"
        )
        max_reasoning_steps: int = Field(
            default=12,
            description="内部 tool-calling 最大推理轮数",
            label="最大推理轮数",
            ge=1,
            le=50,
            tag="performance"
        )

    @config_section("flashback", title="记忆闪回", tag="ai")
    class FlashbackSection(SectionBase):
        """记忆闪回配置。

        闪回机制在构建 default_chatter 的 user prompt 时生效：
        - 先按 ``trigger_probability`` 判定是否触发；
        - 触发后按 ``archived_probability`` 判定抽取归档层/隐现层；
        - 在目标层随机抽取一条记忆，激活次数越低越容易被抽到。
        """

        enabled: bool = Field(
            default=False,
            description="是否启用记忆闪回机制",
            label="启用闪回",
            tag="ai"
        )
        trigger_probability: float = Field(
            default=0.05,
            description="每次构建 user prompt 时触发闪回的概率（0~1）",
            label="触发概率",
            ge=0.0,
            le=1.0,
            step=0.01,
            input_type="slider",
            tag="performance",
            depends_on="enabled",
            depends_value=True
        )
        archived_probability: float = Field(
            default=0.6,
            description="触发闪回后抽取归档层记忆的概率（0~1）；隐现层概率为 1-该值",
            label="归档概率",
            ge=0.0,
            le=1.0,
            step=0.05,
            input_type="slider",
            tag="performance",
            depends_on="enabled",
            depends_value=True
        )
        candidate_limit: int = Field(
            default=50,
            description="每次抽取时最多加载的候选记忆数量（按 updated_at 倒序截断）",
            label="候选数量",
            ge=10,
            le=200,
            tag="performance",
            depends_on="enabled",
            depends_value=True
        )
        activation_weight_exponent: float = Field(
            default=1.0,
            description=(
                "激活次数权重指数。抽取权重为 1/(activation_count+1)^exponent；"
                "指数越大越偏向低激活记忆。"
            ),
            label="权重指数",
            ge=0.5,
            le=3.0,
            step=0.1,
            tag="performance",
            depends_on="enabled",
            depends_value=True
        )
        cooldown_seconds: int = Field(
            default=3600,
            description=(
                "闪回去重冷却时间（秒）。当某条记忆被触发闪回后，在该时间内不会再次被闪回；"
                "设为 0 表示不启用去重。"
            ),
            label="冷却时间（秒）",
            ge=0,
            le=86400,
            input_type="slider",
            tag="timer",
            depends_on="enabled",
            depends_value=True,
            hint="0 表示不启用去重"
        )

    @config_section("chunking", title="分块配置", tag="ai")
    class ChunkingSection(SectionBase):
        """文档切分参数配置。"""

        max_chunk_chars: int = Field(
            default=900,
            description="单块最大字符数",
            label="最大块长度",
            ge=300,
            le=3000,
            tag="performance",
        )
        overlap_chars: int = Field(
            default=120,
            description="相邻块重叠字符数",
            label="重叠长度",
            ge=0,
            le=500,
            tag="performance",
        )

    @config_section("startup_ingest", title="启动自动导入", tag="file")
    class StartupIngestSection(SectionBase):
        """启动阶段本地知识库导入配置。（建议仅有未读文档需要导入时启用）"""

        enabled: bool = Field(
            default=True,
            description="是否在启动时自动导入配置路径文档",
            label="启用启动导入",
            tag="plugin",
        )
        paths: list[str] = Field(
            default_factory=lambda: [r"data\booku_memory\knowledges"],
            description="启动时自动导入的文件或目录路径列表",
            label="导入路径",
            input_type="list",
            item_type="str",
            tag="file",
        )
        recursive: bool = Field(
            default=True,
            description="目录路径是否递归扫描子目录",
            label="递归扫描目录",
            tag="file",
        )
        skip_missing_paths: bool = Field(
            default=True,
            description="路径不存在时是否跳过并继续",
            label="跳过不存在路径",
            tag="file",
        )
        skip_existing_title: bool = Field(
            default=True,
            description="文档标题已存在时是否跳过导入",
            label="跳过已存在标题",
            tag="file",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    retrieval: RetrievalSection = Field(default_factory=RetrievalSection)
    write_conflict: WriteConflictSection = Field(default_factory=WriteConflictSection)
    time_window: TimeWindowSection = Field(default_factory=TimeWindowSection)
    internal_llm: InternalLLMSection = Field(default_factory=InternalLLMSection)
    flashback: FlashbackSection = Field(default_factory=FlashbackSection)
    chunking: ChunkingSection = Field(default_factory=ChunkingSection)
    startup_ingest: StartupIngestSection = Field(default_factory=StartupIngestSection)
