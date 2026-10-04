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
    prompt_injection: PromptInjectionSection = Field(
        default_factory=PromptInjectionSection
    )
    vector: VectorSection = Field(default_factory=VectorSection)
