"""Engram Memory vNext 领域输入与结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .enums import (
    ActorType,
    EvidenceSourceType,
    MemoryEventType,
    MemoryKind,
    MemoryStatus,
    ParticipantKind,
    RevisionChangeReason,
    SubjectKind,
)


@dataclass(frozen=True, slots=True)
class SubjectInput:
    """记忆版本主体输入。"""

    subject_kind: SubjectKind
    person_id: str | None = None
    subject_key: str | None = None
    subject_label: str | None = None

    def validate(self) -> None:
        """验证人物主体具有核心人物标识。"""
        if self.subject_kind is SubjectKind.PERSON and not self.person_id:
            raise ValueError("PERSON 主体必须提供 person_id")


@dataclass(frozen=True, slots=True)
class ParticipantInput:
    """记忆版本参与者输入。"""

    participant_kind: ParticipantKind
    person_id: str | None = None
    label: str | None = None

    def validate(self) -> None:
        """验证人物参与者具有核心人物标识。"""
        if self.participant_kind is ParticipantKind.PERSON and not self.person_id:
            raise ValueError("PERSON 参与者必须提供 person_id")


@dataclass(frozen=True, slots=True)
class EvidenceMessageInput:
    """证据关联的原始消息软引用。"""

    message_id: str
    stream_id: str
    snapshot: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    """正式记忆写入所需的证据输入。"""

    source_type: EvidenceSourceType
    observed_at: datetime
    messages: tuple[EvidenceMessageInput, ...] = ()
    source_ref: str | None = None
    note: str | None = None

    def validate(self) -> None:
        """验证消息证据拥有消息引用且引用不重复。"""
        if self.source_type is EvidenceSourceType.MESSAGE_SET and not self.messages:
            raise ValueError("MESSAGE_SET Evidence 必须关联至少一条消息")
        keys = {(item.stream_id, item.message_id) for item in self.messages}
        if len(keys) != len(self.messages):
            raise ValueError("同一 Evidence 不能重复关联消息")


@dataclass(frozen=True, slots=True)
class CreateMemoryInput:
    """创建正式记忆及初始认知状态的输入。"""

    title: str
    content: str
    memory_kind: MemoryKind
    subject: SubjectInput
    observed_at: datetime
    evidence: tuple[EvidenceInput, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    participants: tuple[ParticipantInput, ...] = ()

    def validate(self) -> None:
        """验证正式记忆创建所需的领域完整性。"""
        required_text = {
            "title": self.title,
            "content": self.content,
        }
        for field_name, value in required_text.items():
            if not value.strip():
                raise ValueError(f"{field_name} 不能为空")
        if not self.evidence and not self.evidence_ids:
            raise ValueError("正式记忆必须至少关联一份 Evidence")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids 不能重复")
        self.subject.validate()
        for participant in self.participants:
            participant.validate()
        for evidence in self.evidence:
            evidence.validate()


@dataclass(frozen=True, slots=True)
class ReinforceMemoryInput:
    """正式记忆强化输入。"""

    memory_id: str
    based_on_revision_id: str
    reason: str
    evidence: tuple[EvidenceInput, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        """验证强化不缺少目标、原因或新增证据。"""
        if not self.memory_id or not self.based_on_revision_id:
            raise ValueError("强化必须指定 memory_id 与 based_on_revision_id")
        if not self.reason.strip():
            raise ValueError("reinforce reason 不能为空")
        if not self.evidence and not self.evidence_ids:
            raise ValueError("强化必须提供新 Evidence 或已有 evidence_ids")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids 不能重复")
        for evidence in self.evidence:
            evidence.validate()


@dataclass(frozen=True, slots=True)
class ReviseMemoryInput:
    """正式记忆线性修订输入。"""

    memory_id: str
    based_on_revision_id: str
    title: str
    content: str
    memory_kind: MemoryKind
    subject: SubjectInput
    observed_at: datetime
    change_reason: RevisionChangeReason
    evidence: tuple[EvidenceInput, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    participants: tuple[ParticipantInput, ...] = ()

    def validate(self) -> None:
        """验证线性修订所需的语义内容与证据。"""
        if not self.memory_id or not self.based_on_revision_id:
            raise ValueError("修订必须指定 memory_id 与 based_on_revision_id")
        for field_name, value in {
            "title": self.title,
            "content": self.content,
        }.items():
            if not value.strip():
                raise ValueError(f"{field_name} 不能为空")
        if self.change_reason is RevisionChangeReason.INITIAL:
            raise ValueError("修订不能使用 INITIAL change_reason")
        if not self.evidence and not self.evidence_ids:
            raise ValueError("修订必须提供 Evidence")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids 不能重复")
        self.subject.validate()
        for participant in self.participants:
            participant.validate()
        for evidence in self.evidence:
            evidence.validate()


@dataclass(frozen=True, slots=True)
class MemoryLifecycleInput:
    """正式记忆工程生命周期操作输入。"""

    memory_id: str
    reason: str

    def validate(self) -> None:
        """验证生命周期操作目标和原因。"""
        if not self.memory_id:
            raise ValueError("Memory Lifecycle 必须指定 memory_id")
        if not self.reason.strip():
            raise ValueError("lifecycle reason 不能为空")


@dataclass(frozen=True, slots=True)
class PersonaUpdateResult:
    """人物印象更新结果。"""

    person_id: str
    changed: bool
    update_id: str | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """混合检索查询输入。"""

    text: str
    top_k: int = 10
    person_ids: tuple[str, ...] = ()
    memory_kinds: tuple[MemoryKind, ...] = ()
    start_time: datetime | None = None
    end_time: datetime | None = None

    def validate(self) -> None:
        """验证查询文本与过滤条件。"""
        if not self.text.strip():
            raise ValueError("检索 text 不能为空")
        if self.top_k <= 0:
            raise ValueError("top_k 必须大于 0")
        if len(set(self.person_ids)) != len(self.person_ids):
            raise ValueError("person_ids 不能重复")
        if self.start_time is not None and self.end_time is not None:
            if self.start_time > self.end_time:
                raise ValueError("start_time 不能晚于 end_time")


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    """单条正式记忆按 memory_id 聚合后的融合检索结果。"""

    memory_id: str
    title: str
    matched_by: tuple[str, ...]
    rrf_score: float
    lexical_rank: int | None
    vector_rank: int | None
    vector_similarity: float | None = None
    activation_score: float = 0.0


@dataclass(frozen=True, slots=True)
class MemoryStrength:
    """正式记忆当前强度及其可解释组成。"""

    memory_id: str
    strength: float
    age_days: float
    evidence_count: int
    recall_count: int
    last_recalled_at: datetime | None
    last_experienced_at: datetime | None


@dataclass(frozen=True, slots=True)
class MemoryDecayCandidate:
    """等待人工或模型审查的低强度记忆候选。"""

    memory_id: str
    title: str
    strength: float
    reason: str
    last_experienced_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersonImpression:
    """人物印象与近期相关记忆。"""

    person_id: str
    impression_text: str | None
    recent_memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VectorUpsert:
    """向量工作项处理的输入文本与元数据。"""

    entry_id: str
    memory_id: str
    revision_id: str | None
    text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class WriteContext:
    """领域写操作的执行者与来源上下文。"""

    actor_type: ActorType
    actor_ref: str | None = None
    stream_id: str | None = None
    operation_key: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    """正式记忆写事务产生的稳定标识。"""

    memory_id: str
    revision_id: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MemoryChanged:
    """已提交的记忆变化及其前后人物关联。"""

    memory_id: str
    change_type: MemoryEventType
    before_person_ids: tuple[str, ...] = ()
    after_person_ids: tuple[str, ...] = ()
    before_revision_id: str | None = None
    after_revision_id: str | None = None
    before_status: MemoryStatus | None = None
    after_status: MemoryStatus | None = None

    @property
    def affected_person_ids(self) -> tuple[str, ...]:
        """返回变化前后关联人物的去重并集。"""
        return tuple(dict.fromkeys((*self.before_person_ids, *self.after_person_ids)))
