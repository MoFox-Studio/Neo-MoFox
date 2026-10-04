"""正式记忆的人物印象、检索、闪回、提示注入与向量索引配置。"""

from __future__ import annotations

from src.app.plugin_system.base import Field, SectionBase, config_section


@config_section("persona", title="人物印象", tag="ai")
class PersonaSection(SectionBase):
    """Persona 更新与查询参数。"""

    recent_memory_limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="person_lookup 返回的近期相关记忆条数",
    )
    max_concurrency: int = Field(
        default=3,
        ge=1,
        description="补建、更新和重试共用的人物处理并发上限",
    )
    recent_chat_days: int = Field(
        default=7,
        ge=1,
        description="人物印象辅助聊天的最近天数",
    )
    recent_chat_max_messages: int = Field(
        default=500,
        ge=1,
        description="辅助聊天消息总上限，包含其他参与者和 Bot",
    )


@config_section("retrieval", title="混合检索", tag="ai")
class VNextRetrievalSection(SectionBase):
    """vNext 混合检索参数。"""

    default_limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="memory_search 默认返回条数",
    )
    max_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="memory_search 单次返回条数上限",
    )
    rrf_k: int = Field(
        default=60,
        ge=1,
        description="RRF 融合常数 k",
    )
    activation_half_life_days: float = Field(
        default=30.0,
        ge=1.0,
        description="ACT-R-lite 时间激活半衰期（天）",
    )
    activation_weight: float = Field(
        default=0.08,
        ge=0.0,
        le=1.0,
        description="激活分数对 RRF 排序的影响权重",
    )
    activation_noise: float = Field(
        default=0.02,
        ge=0.0,
        le=0.2,
        description="仅在已有候选之间生效的有界确定性噪声",
    )


@config_section("flashback", title="自然闪回", tag="ai")
class VNextFlashbackSection(SectionBase):
    """Flashback 自动联想参数。"""

    enabled: bool = Field(
        default=True,
        description="是否启用回复前自然闪回",
        label="启用闪回",
    )
    trigger_probability: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        step=0.01,
        input_type="slider",
        description="回复前自然闪回的触发概率；0 关闭，1 每轮尝试，仍受相关性与冷却限制",
    )
    context_turns: int = Field(
        default=6,
        ge=1,
        le=30,
        description="闪回查询使用的最近聊天消息条数",
    )
    latency_budget_ms: int = Field(
        default=1200,
        ge=100,
        description="闪回延迟预算（毫秒），超时本轮直接跳过",
    )
    max_memories: int = Field(
        default=2,
        ge=0,
        le=2,
        description="单轮闪回最多注入的记忆条数（0-2）",
    )
    cooldown_turns: int = Field(
        default=3,
        ge=0,
        description="同一记忆在会话内闪回冷却轮数",
    )
    max_working_memory: int = Field(
        default=3,
        ge=1,
        le=8,
        description="单轮 Episode 工作记忆最多装载条数",
    )
    relation_hops: int = Field(
        default=2,
        ge=1,
        le=2,
        description="Episode 关联扩散最大跳数",
    )
    reconstruction_noise: float = Field(
        default=0.06,
        ge=0.0,
        le=0.2,
        description="只影响已有候选选择的受控重构扰动",
    )
    working_memory_ttl_seconds: int = Field(
        default=900,
        ge=30,
        le=7200,
        description="当前工作记忆在流中的有效时长（秒）",
    )


@config_section("claim_review", title="Claim/Hypothesis 审核", tag="ai")
class ClaimReviewSection(SectionBase):
    """经历巩固候选的自动审核参数。"""

    enabled: bool = Field(
        default=True,
        description="是否自动审核待处理 Claim/Hypothesis；关闭时只保存待审候选",
        label="启用自动审核",
    )
    model_task: str = Field(
        default="actor",
        description="用于 Claim/Hypothesis 审核的模型任务名",
        input_type="text",
    )
    max_per_run: int = Field(
        default=2,
        ge=1,
        le=10,
        description="每次后台审核最多处理的候选数量",
    )


@config_section("neo4j", title="Neo4j Episode 图", tag="database")
class Neo4jSection(SectionBase):
    """可选 Neo4j Episode 关系镜像参数。"""

    enabled: bool = Field(default=False, description="是否启用 Neo4j Episode 图镜像")
    uri: str = Field(default="bolt://127.0.0.1:7687", input_type="text")
    user: str = Field(default="neo4j", input_type="text")
    password: str = Field(default="", input_type="password")
    database: str = Field(default="neo4j", input_type="text")


@config_section("prompt_injection", title="提示注入", tag="ai")
class PromptInjectionSection(SectionBase):
    """SystemReminder 注入参数。"""

    reminder_at_end: bool = Field(
        default=True,
        description="是否将记忆使用指引放到最新一轮输入；开启为动态 SystemReminder，关闭为固定 SystemReminder",
    )


@config_section("vector", title="向量索引", tag="database")
class VectorSection(SectionBase):
    """向量派生索引参数。"""

    worker_retry_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Outbox 工作项失败重试上限，达到后转 FAILED",
    )


@config_section(
    "vnext",
    title="Engram Memory vNext",
    description="Engram Memory 正式记忆、人物印象与自然闪回",
    tag="ai",
)
class VNextConfig(SectionBase):
    """Engram Memory vNext 认知记忆配置模型。"""

    persona: PersonaSection = Field(default_factory=PersonaSection)
    retrieval: VNextRetrievalSection = Field(default_factory=VNextRetrievalSection)
    flashback: VNextFlashbackSection = Field(default_factory=VNextFlashbackSection)
    claim_review: ClaimReviewSection = Field(default_factory=ClaimReviewSection)
    neo4j: Neo4jSection = Field(default_factory=Neo4jSection)
    prompt_injection: PromptInjectionSection = Field(
        default_factory=PromptInjectionSection
    )
    vector: VectorSection = Field(default_factory=VectorSection)
