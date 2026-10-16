"""正式记忆的人物印象、检索、闪回、提示注入与向量索引配置。"""

from __future__ import annotations

from src.app.plugin_system.base import Field, SectionBase, config_section


@config_section("persona", title="人物印象", tag="ai")
class PersonaSection(SectionBase):
    """人物印象更新与查询参数。"""

    recent_memory_limit: int = Field(
        label="近期记忆条数",
        default=10,
        ge=1,
        le=50,
        description="person_lookup 返回的近期相关记忆条数",
    )
    max_concurrency: int = Field(
        label="人物处理并发数",
        default=3,
        ge=1,
        description="补建、更新和重试共用的人物处理并发上限",
    )
    recent_chat_days: int = Field(
        label="辅助聊天范围（天）",
        default=7,
        ge=1,
        description="人物印象辅助聊天的最近天数",
    )
    recent_chat_max_messages: int = Field(
        label="辅助聊天消息上限",
        default=500,
        ge=1,
        description="辅助聊天消息总上限，包含其他参与者和 Bot",
    )


@config_section("retrieval", title="混合检索", tag="ai")
class VNextRetrievalSection(SectionBase):
    """记忆混合检索参数。"""

    default_limit: int = Field(
        label="默认检索条数",
        default=5,
        ge=1,
        le=20,
        description="memory_search 默认返回条数",
    )
    max_limit: int = Field(
        label="单次检索条数上限",
        default=20,
        ge=1,
        le=100,
        description="memory_search 单次返回条数上限",
    )
    rrf_k: int = Field(
        label="检索融合常数（RRF）",
        default=60,
        ge=1,
        description="RRF 融合常数 k",
    )
    activation_half_life_days: float = Field(
        label="时间激活半衰期（天）",
        default=30.0,
        ge=1.0,
        description="ACT-R-lite 时间激活半衰期（天）",
    )
    activation_weight: float = Field(
        label="时间激活权重",
        default=0.08,
        ge=0.0,
        le=1.0,
        description="激活分数对 RRF 排序的影响权重",
    )
    activation_noise: float = Field(
        label="候选排序扰动",
        default=0.02,
        ge=0.0,
        le=0.2,
        description="仅在已有候选之间生效的有界确定性噪声",
    )
    memory_half_life_days: float = Field(
        label="记忆强度半衰期（天）",
        default=90.0,
        ge=1.0,
        description="正式记忆强度的时间半衰期（天）",
    )
    recall_half_life_days: float = Field(
        label="召回强化半衰期（天）",
        default=30.0,
        ge=1.0,
        description="召回强化贡献的时间半衰期（天）",
    )
    forget_threshold: float = Field(
        label="遗忘候选强度阈值",
        default=0.12,
        ge=0.0,
        le=1.0,
        description="进入遗忘候选的最低强度阈值；不会直接作废记忆",
    )
    forget_min_age_days: float = Field(
        label="遗忘候选最短年龄（天）",
        default=30.0,
        ge=0.0,
        description="记忆进入遗忘候选前必须经过的最短时间（天）",
    )


@config_section("flashback", title="自然闪回", tag="ai")
class VNextFlashbackSection(SectionBase):
    """记忆自动联想参数。"""

    enabled: bool = Field(
        default=True,
        description="是否启用回复前自然闪回",
        label="启用闪回",
    )
    trigger_probability: float = Field(
        label="闪回触发概率",
        default=0.25,
        ge=0.0,
        le=1.0,
        step=0.01,
        input_type="slider",
        description="回复前自然闪回的触发概率；0 关闭，1 每轮尝试，仍受相关性与冷却限制",
    )
    context_turns: int = Field(
        label="查询上下文消息数",
        default=6,
        ge=1,
        le=30,
        description="闪回查询使用的最近聊天消息条数",
    )
    latency_budget_ms: int = Field(
        label="闪回耗时上限（毫秒）",
        default=1200,
        ge=100,
        description="闪回延迟预算（毫秒），超时本轮直接跳过",
    )
    max_memories: int = Field(
        label="单轮闪回条数上限",
        default=2,
        ge=0,
        le=2,
        description="单轮闪回最多注入的记忆条数（0-2）",
    )
    cooldown_turns: int = Field(
        label="重复闪回冷却轮数",
        default=3,
        ge=0,
        description="同一记忆在会话内闪回冷却轮数",
    )
    max_working_memory: int = Field(
        label="经历工作记忆容量",
        default=3,
        ge=1,
        le=8,
        description="单轮 Episode 工作记忆最多装载条数",
    )
    relation_hops: int = Field(
        label="经历关联扩散跳数",
        default=2,
        ge=1,
        le=2,
        description="Episode 关联扩散最大跳数",
    )
    reconstruction_noise: float = Field(
        label="回忆选择扰动",
        default=0.06,
        ge=0.0,
        le=0.2,
        description="只影响已有候选选择的受控重构扰动",
    )
    working_memory_ttl_seconds: int = Field(
        label="工作记忆有效期（秒）",
        default=900,
        ge=30,
        le=7200,
        description="当前工作记忆在流中的有效时长（秒）",
    )
    semantic_recall_enabled: bool = Field(
        label="启用经历语义召回",
        default=True,
        description="是否使用 Embedding 补充 Episode 语义关联",
    )
    semantic_candidate_limit: int = Field(
        label="语义召回候选上限",
        default=50,
        ge=1,
        le=200,
        description="Episode 语义召回最多编码的近期候选数",
    )
    semantic_min_similarity: float = Field(
        label="最低语义相似度",
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
        label="单轮记忆条数上限",
        default=3, ge=0, le=8, description="每轮最多注入的正式记忆条数"
    )
    candidate_limit: int = Field(
        label="检索候选条数上限",
        default=24, ge=1, le=100, description="混合检索为前馈提供的候选上限"
    )
    hot_candidates: int = Field(
        label="热缓存候选条数",
        default=8, ge=0, le=64, description="L2 热缓存直接进入候选池的条数"
    )
    feature_dim: int = Field(
        label="特征维度",
        default=64, ge=8, le=512, description="特征哈希与 KDA 状态矩阵维度"
    )
    retrieval_threshold: float = Field(
        label="记忆激活阈值",
        default=-2.5, ge=-10.0, le=5.0, description="ACT-R 检索阈值 τ，低于此激活不注入"
    )
    temperature: float = Field(
        label="检索采样温度",
        default=0.4, ge=0.05, le=5.0, description="Boltzmann 检索概率温度 s"
    )
    noise_scale: float = Field(
        label="激活噪声强度",
        default=0.15, ge=0.0, le=1.0, description="ACT-R logistic 噪声尺度"
    )
    mismatch_penalty: float = Field(
        label="人物不匹配惩罚",
        default=0.6, ge=0.0, le=5.0, description="人物不匹配时的部分匹配惩罚 P"
    )
    working_weight: float = Field(
        label="工作记忆扩散权重",
        default=0.8, ge=0.0, le=5.0, description="L1 工作记忆扩散权重"
    )
    kda_weight: float = Field(
        label="联想状态扩散权重（KDA）",
        default=0.6, ge=0.0, le=5.0, description="KDA 线索读出扩散权重"
    )
    recency_weight: float = Field(
        label="最新输入检索权重",
        default=1.5, ge=0.0, le=5.0, description="最新一条输入的单独检索扩散权重"
    )
    inhibition_weight: float = Field(
        label="重复回忆抑制强度",
        default=0.6, ge=0.0, le=5.0, description="连续复述但本轮未被新线索指向时的返回抑制强度"
    )
    latency_budget_ms: int = Field(
        label="前馈耗时上限（毫秒）",
        default=1500, ge=100, description="单轮前馈耗时预算（毫秒），超时本轮不注入"
    )
    kda_learning_rate: float = Field(
        label="联想状态学习率（KDA）",
        default=0.5, ge=0.05, le=1.0, description="KDA delta 规则学习率 β"
    )
    private_decay: float = Field(
        label="私聊激活衰减率",
        default=0.35, ge=0.05, le=0.95, description="私聊通道 ACT-R 衰减率 d"
    )
    private_retention_per_day: float = Field(
        label="私聊状态每日保留率",
        default=0.98, ge=0.5, le=1.0, description="私聊通道 KDA 每日保留率 γ"
    )
    group_decay: float = Field(
        label="群聊激活衰减率",
        default=0.6, ge=0.05, le=0.95, description="群聊通道 ACT-R 衰减率 d"
    )
    group_retention_per_day: float = Field(
        label="群聊状态每日保留率",
        default=0.9, ge=0.5, le=1.0, description="群聊通道 KDA 每日保留率 γ"
    )
    emotional_decay: float = Field(
        label="情感激活衰减率",
        default=0.3, ge=0.05, le=0.95, description="情感通道 ACT-R 衰减率 d"
    )
    emotional_retention_per_day: float = Field(
        label="情感状态每日保留率",
        default=0.99, ge=0.5, le=1.0, description="情感通道 KDA 每日保留率 γ"
    )
    factual_decay: float = Field(
        label="事实激活衰减率",
        default=0.25, ge=0.05, le=0.95, description="事实通道 ACT-R 衰减率 d"
    )
    factual_retention_per_day: float = Field(
        label="事实状态每日保留率",
        default=0.995, ge=0.5, le=1.0, description="事实通道 KDA 每日保留率 γ"
    )
    sensory_capacity: int = Field(
        label="感知缓冲消息容量",
        default=8, ge=1, le=64, description="L0 感知缓冲每流保留的输入条数"
    )
    sensory_ttl_seconds: int = Field(
        label="感知缓冲有效期（秒）",
        default=600, ge=30, le=86400, description="L0 感知缓冲有效秒数"
    )
    working_capacity: int = Field(
        label="工作集容量",
        default=7, ge=1, le=32, description="L1 工作集每流容量"
    )
    working_half_life_turns: float = Field(
        label="工作记忆半衰期（轮）",
        default=4.0, ge=0.5, le=64.0, description="L1 工作记忆按回复轮数的半衰期"
    )
    hot_capacity: int = Field(
        label="热缓存容量",
        default=256, ge=1, le=10000, description="L2 热缓存全局条数"
    )
    hot_ttl_seconds: int = Field(
        label="热缓存有效期（秒）",
        default=3600, ge=60, le=86400, description="L2 热缓存有效秒数"
    )


@config_section("claim_review", title="记忆候选审核", tag="ai")
class ClaimReviewSection(SectionBase):
    """经历巩固候选的自动审核参数。"""

    enabled: bool = Field(
        default=True,
        description="是否自动审核待处理 Claim/Hypothesis；关闭时只保存待审候选",
        label="启用自动审核",
    )
    model_task: str = Field(
        label="审核模型任务",
        default="actor",
        description="用于 Claim/Hypothesis 审核的模型任务名",
        input_type="text",
    )
    max_per_run: int = Field(
        label="每轮审核候选上限",
        default=2,
        ge=1,
        le=10,
        description="每次后台审核最多处理的候选数量",
    )
    background_enabled: bool = Field(
        label="启用后台巩固扫描",
        default=True,
        description="是否定期扫描已观测聊天流并收集待审巩固候选",
    )
    background_interval_seconds: int = Field(
        label="后台扫描间隔（秒）",
        default=900,
        ge=60,
        le=86400,
        description="后台巩固扫描间隔（秒）",
    )
    background_stream_limit: int = Field(
        label="每轮扫描聊天流上限",
        default=20,
        ge=1,
        le=100,
        description="每轮后台巩固最多扫描的聊天流数量",
    )


@config_section("neo4j", title="经历关系图（Neo4j）", tag="database")
class Neo4jSection(SectionBase):
    """可选 Neo4j Episode 关系镜像参数。"""

    enabled: bool = Field(
        label="启用经历关系图镜像",
        default=True,
        description="是否启用 Neo4j Episode 图镜像；连接失败只记录警告，主库照常工作",
    )
    uri: str = Field(
        label="数据库连接地址",
        default="bolt://127.0.0.1:7687",
        input_type="text",
        description="设置环境变量 MNEMOSYNE_NEO4J_URI 时优先使用环境变量",
    )
    user: str = Field(
        label="数据库用户名", default="neo4j", input_type="text",
        description="连接经历关系图数据库使用的用户名",
    )
    password: str = Field(
        label="数据库密码",
        default="",
        input_type="password",
        description="设置环境变量 MNEMOSYNE_NEO4J_PASSWORD 时优先使用环境变量",
    )
    database: str = Field(
        label="数据库名称", default="neo4j", input_type="text",
        description="保存经历关系镜像的 Neo4j 数据库名称",
    )


@config_section("prompt_injection", title="提示注入", tag="ai")
class PromptInjectionSection(SectionBase):
    """SystemReminder 注入参数。"""

    reminder_at_end: bool = Field(
        label="在最新输入处注入记忆指引",
        default=True,
        description="是否将记忆使用指引放到最新一轮输入；开启为动态 SystemReminder，关闭为固定 SystemReminder",
    )
    group_persona_message_limit: int = Field(
        label="群聊参与者查询消息数",
        default=50, ge=1, le=500,
        description="群聊选取近期参与者时读取的消息条数",
    )
    group_persona_max_people: int = Field(
        label="群聊人物印象条数上限",
        default=10, ge=1, le=50,
        description="群聊每轮最多注入的人物印象数量",
    )


@config_section("vector", title="向量索引", tag="database")
class VectorSection(SectionBase):
    """向量派生索引参数。"""

    worker_retry_limit: int = Field(
        label="向量任务重试上限",
        default=3,
        ge=1,
        le=10,
        description="Outbox 工作项失败重试上限，达到后转 FAILED",
    )


@config_section("learning", title="可训练记忆控制器", tag="ai")
class LearningSection(SectionBase):
    """真正可反向传播 Transformer 记忆控制器的配置。"""

    enabled: bool = Field(
        default=False,
        description=(
            "可训练 Transformer 模型总开关；关闭时不初始化、推理、训练或采集训练样本，"
            "已保存的 checkpoint 文件和数据库记录会保留"
        ),
        label="启用可训练 Transformer 模型",
    )
    inference_enabled: bool = Field(
        label="启用模型推理",
        default=True,
        description="启用后对候选记忆进行模型评分；是否参与排序由影子模式开关决定",
    )
    training_enabled: bool = Field(
        label="启用模型训练",
        default=False,
        description="是否允许后台训练；训练只读取反馈和样本，不直接修改正式记忆",
    )
    shadow_mode: bool = Field(
        label="仅记录模型评分（影子模式）",
        default=True,
        description="是否只记录 Transformer 结果而不改变现有召回排序",
    )
    input_dim: int = Field(
        label="输入特征维度", default=128, ge=16, le=1024,
        description="每个查询或候选槽位的输入特征维度；修改后旧模型可能不兼容",
    )
    d_model: int = Field(
        label="模型隐层维度", default=192, ge=8, le=1024,
        description="Transformer 隐层宽度，必须能被注意力头数整除",
    )
    nhead: int = Field(
        label="注意力头数", default=6, ge=1, le=32,
        description="每层编码器使用的并行注意力头数量",
    )
    num_layers: int = Field(
        label="编码器层数", default=3, ge=1, le=12,
        description="Transformer 编码器堆叠层数",
    )
    dim_feedforward: int = Field(
        label="前馈网络维度", default=768, ge=8, le=4096,
        description="编码器内部前馈网络的中间层宽度",
    )
    dropout: float = Field(
        label="随机丢弃率", default=0.1, ge=0.0, lt=1.0,
        description="训练时随机丢弃部分激活的比例，用于减轻过拟合",
    )
    max_candidates: int = Field(
        label="模型候选条数上限", default=32, ge=1, le=128,
        description="模型每个样本可处理的候选记忆槽位数",
    )
    checkpoint_dir: str = Field(
        label="模型检查点目录",
        default="data/mnemosyne_memory/checkpoints",
        description="已训练模型权重的保存目录，关闭模型总开关不会删除其中的文件",
        input_type="text",
    )
    learning_rate: float = Field(
        label="模型学习率", default=0.0001, gt=0.0, le=1.0,
        description="优化器每次训练更新权重的步长",
    )
    batch_size: int = Field(
        label="训练批次样本数", default=8, ge=1, le=256,
        description="单次训练最多消费的待处理样本数",
    )
    training_interval_seconds: int = Field(
        label="后台训练间隔（秒）", default=86400, ge=60, le=604800,
        description="启用训练后，后台尝试执行一轮训练的时间间隔",
    )
    min_feedback_samples: int = Field(
        label="训练所需最少反馈数", default=500, ge=1, le=1000000,
        description="数据库累计反馈数量达到此值后才尝试训练",
    )
    learned_weight: float = Field(
        label="模型评分混合权重",
        default=0.2,
        ge=0.0,
        le=1.0,
        description="关闭影子模式后，已训练模型的评分参与召回排序的混合权重",
    )


@config_section(
    "vnext",
    title="记忆与召回",
    description="正式记忆、人物印象与关联回忆设置",
    tag="ai",
)
class VNextConfig(SectionBase):
    """Mnemosyne Memory vNext 认知记忆配置模型。"""

    persona: PersonaSection = Field(label="人物印象", default_factory=PersonaSection)
    retrieval: VNextRetrievalSection = Field(label="混合检索", default_factory=VNextRetrievalSection)
    flashback: VNextFlashbackSection = Field(label="自然闪回", default_factory=VNextFlashbackSection)
    feedforward: FeedForwardSection = Field(label="前馈式记忆检索", default_factory=FeedForwardSection)
    claim_review: ClaimReviewSection = Field(label="记忆候选审核", default_factory=ClaimReviewSection)
    neo4j: Neo4jSection = Field(label="经历关系图（Neo4j）", default_factory=Neo4jSection)
    prompt_injection: PromptInjectionSection = Field(
        label="提示注入",
        default_factory=PromptInjectionSection
    )
    vector: VectorSection = Field(label="向量索引", default_factory=VectorSection)
    learning: LearningSection = Field(label="可训练记忆控制器", default_factory=LearningSection)
