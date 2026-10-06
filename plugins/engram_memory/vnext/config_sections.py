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
    memory_half_life_days: float = Field(
        default=90.0,
        ge=1.0,
        description="正式记忆强度的时间半衰期（天）",
    )
    recall_half_life_days: float = Field(
        default=30.0,
        ge=1.0,
        description="召回强化贡献的时间半衰期（天）",
    )
    forget_threshold: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        description="进入遗忘候选的最低强度阈值；不会直接作废记忆",
    )
    forget_min_age_days: float = Field(
        default=30.0,
        ge=0.0,
        description="记忆进入遗忘候选前必须经过的最短时间（天）",
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
    semantic_recall_enabled: bool = Field(
        default=True,
        description="是否使用 Embedding 补充 Episode 语义关联",
    )
    semantic_candidate_limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Episode 语义召回最多编码的近期候选数",
    )
    semantic_min_similarity: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="Episode 语义关联的最低余弦相似度",
    )


@config_section("feedforward", title="前馈式记忆检索", tag="ai")
class FeedForwardSection(SectionBase):
    """ACT-R × KDA × LayerSplit × Transformer 每轮前馈检索参数。"""

    enabled: bool = Field(
        default=True,
        description="每轮回复前必然执行前馈检索，替代概率触发的自然闪回；关闭后回到原闪回",
        label="启用前馈检索",
    )
    max_memories: int = Field(
        default=3, ge=0, le=8, description="每轮最多注入的正式记忆条数"
    )
    candidate_limit: int = Field(
        default=24, ge=1, le=100, description="混合检索为前馈提供的候选上限"
    )
    hot_candidates: int = Field(
        default=8, ge=0, le=64, description="L2 热缓存直接进入候选池的条数"
    )
    feature_dim: int = Field(
        default=64, ge=8, le=512, description="特征哈希与 KDA 状态矩阵维度"
    )
    retrieval_threshold: float = Field(
        default=-2.5, ge=-10.0, le=5.0, description="ACT-R 检索阈值 τ，低于此激活不注入"
    )
    temperature: float = Field(
        default=0.4, ge=0.05, le=5.0, description="Boltzmann 检索概率温度 s"
    )
    noise_scale: float = Field(
        default=0.15, ge=0.0, le=1.0, description="ACT-R logistic 噪声尺度"
    )
    mismatch_penalty: float = Field(
        default=0.6, ge=0.0, le=5.0, description="人物不匹配时的部分匹配惩罚 P"
    )
    working_weight: float = Field(
        default=0.8, ge=0.0, le=5.0, description="L1 工作记忆扩散权重"
    )
    kda_weight: float = Field(
        default=0.6, ge=0.0, le=5.0, description="KDA 线索读出扩散权重"
    )
    latency_budget_ms: int = Field(
        default=1500, ge=100, description="单轮前馈耗时预算（毫秒），超时本轮不注入"
    )
    kda_learning_rate: float = Field(
        default=0.5, ge=0.05, le=1.0, description="KDA delta 规则学习率 β"
    )
    private_decay: float = Field(
        default=0.35, ge=0.05, le=0.95, description="私聊通道 ACT-R 衰减率 d"
    )
    private_retention_per_day: float = Field(
        default=0.98, ge=0.5, le=1.0, description="私聊通道 KDA 每日保留率 γ"
    )
    group_decay: float = Field(
        default=0.6, ge=0.05, le=0.95, description="群聊通道 ACT-R 衰减率 d"
    )
    group_retention_per_day: float = Field(
        default=0.9, ge=0.5, le=1.0, description="群聊通道 KDA 每日保留率 γ"
    )
    emotional_decay: float = Field(
        default=0.3, ge=0.05, le=0.95, description="情感通道 ACT-R 衰减率 d"
    )
    emotional_retention_per_day: float = Field(
        default=0.99, ge=0.5, le=1.0, description="情感通道 KDA 每日保留率 γ"
    )
    factual_decay: float = Field(
        default=0.25, ge=0.05, le=0.95, description="事实通道 ACT-R 衰减率 d"
    )
    factual_retention_per_day: float = Field(
        default=0.995, ge=0.5, le=1.0, description="事实通道 KDA 每日保留率 γ"
    )
    sensory_capacity: int = Field(
        default=8, ge=1, le=64, description="L0 感知缓冲每流保留的输入条数"
    )
    sensory_ttl_seconds: int = Field(
        default=600, ge=30, le=86400, description="L0 感知缓冲有效秒数"
    )
    working_capacity: int = Field(
        default=7, ge=1, le=32, description="L1 工作集每流容量"
    )
    working_half_life_turns: float = Field(
        default=4.0, ge=0.5, le=64.0, description="L1 工作记忆按回复轮数的半衰期"
    )
    hot_capacity: int = Field(
        default=256, ge=1, le=10000, description="L2 热缓存全局条数"
    )
    hot_ttl_seconds: int = Field(
        default=3600, ge=60, le=86400, description="L2 热缓存有效秒数"
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
    background_enabled: bool = Field(
        default=True,
        description="是否定期扫描已观测聊天流并收集待审巩固候选",
    )
    background_interval_seconds: int = Field(
        default=900,
        ge=60,
        le=86400,
        description="后台巩固扫描间隔（秒）",
    )
    background_stream_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="每轮后台巩固最多扫描的聊天流数量",
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
    feedforward: FeedForwardSection = Field(default_factory=FeedForwardSection)
    claim_review: ClaimReviewSection = Field(default_factory=ClaimReviewSection)
    neo4j: Neo4jSection = Field(default_factory=Neo4jSection)
    prompt_injection: PromptInjectionSection = Field(
        default_factory=PromptInjectionSection
    )
    vector: VectorSection = Field(default_factory=VectorSection)
