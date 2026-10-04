"""正式记忆、来源证据、派生检索及历史候选与整理审计的 ORM 模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .enums import (
    ActorType,
    CandidateActionTargetRole,
    CandidateActionType,
    CandidateStatus,
    EvidenceSourceType,
    MemoryEventType,
    MemoryKind,
    MemoryStatus,
    OutboxObjectType,
    OutboxOperation,
    OutboxStatus,
    ParticipantKind,
    RelationType,
    RetrievalEntryType,
    RevisionChangeReason,
    SleepCandidateOutcome,
    SleepSessionStatus,
    SleepTriggerType,
    SubjectKind,
    VectorIndexStatus,
)


class Base(DeclarativeBase):
    """vNext 独立 SQLAlchemy 声明基类。"""


class UTCDateTime(TypeDecorator[datetime]):
    """以 ISO-8601 UTC 文本持久化带时区时间。"""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        """拒绝无时区时间并统一转换为 UTC。"""
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Engram vNext 时间必须包含时区")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        """将数据库 UTC 文本恢复为带时区时间。"""
        del dialect
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _enum(enum_type: type[Any], name: str) -> SqlEnum[Any]:
    """构造带数据库 CHECK 约束的字符串枚举列类型。"""
    return SqlEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class SchemaVersionModel(Base):
    """记录 vNext Schema 版本，不承载认知数据。"""

    __tablename__ = "engram_vnext_schema_version"

    schema_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EpisodeModel(Base):
    """低成本经历痕迹，不等同于已确认的正式记忆。"""

    __tablename__ = "engram_vnext_episode"

    episode_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_ref: Mapped[str | None] = mapped_column(Text, unique=True)
    stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    compressed_text: Mapped[str] = mapped_column(Text, nullable=False)
    participants: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    emotion: Mapped[str | None] = mapped_column(Text)
    scene: Mapped[str | None] = mapped_column(Text)
    salience: Mapped[float] = mapped_column(Float, nullable=False)
    certainty: Mapped[float] = mapped_column(Float, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    episode_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_episode_stream_time", "stream_id", "observed_at"),
        Index("idx_engram_vnext_episode_kind", "episode_kind", "created_at"),
    )


class EpisodeSemanticModel(Base):
    """Episode 的实体、事件和自然语言引用解析结果。"""

    __tablename__ = "engram_vnext_episode_semantic"

    semantic_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_episode.episode_id"), nullable=False
    )
    referenced_episode_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    entities: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    events: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_episode_semantic_episode", "episode_id"),
    )


class ClaimHypothesisModel(Base):
    """独立的 Claim/Hypothesis 认知对象，必须经过审核才能产生正式提案。"""

    __tablename__ = "engram_vnext_claim_hypothesis"

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    referenced_episode_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    entity_refs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    event_refs: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(Text)
    review_payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    target_memory_id: Mapped[str | None] = mapped_column(String(36))
    proposal_id: Mapped[str | None] = mapped_column(String(36))
    review_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("kind IN ('CLAIM', 'HYPOTHESIS')", name="ck_engram_vnext_claim_kind"),
        CheckConstraint(
            "status IN ('PENDING', 'REVIEWING', 'ACCEPTED', 'REJECTED', 'DEFERRED')",
            name="ck_engram_vnext_claim_status",
        ),
        Index("idx_engram_vnext_claim_stream_status", "stream_id", "status", "created_at"),
    )


class CueSetModel(Base):
    """一次上下文触发得到的硬线索与软线索。"""

    __tablename__ = "engram_vnext_cue_set"

    cue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_episode.episode_id")
    )
    stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    hard_cues: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    soft_cues: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_cue_stream_time", "stream_id", "created_at"),
    )


class EpisodeRelationModel(Base):
    """经历之间的轻量关联边，支持低成本多跳扩散。"""

    __tablename__ = "engram_vnext_episode_relation"

    relation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_episode_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_episode.episode_id"), nullable=False
    )
    target_episode_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_episode.episode_id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_episode_id",
            "target_episode_id",
            "relation_type",
            name="uq_engram_vnext_episode_relation",
        ),
        Index("idx_engram_vnext_episode_relation_source", "source_episode_id"),
        Index("idx_engram_vnext_episode_relation_target", "target_episode_id"),
    )


class WorkingMemoryModel(Base):
    """当前请求选出的有限工作记忆，不改变正式记忆正文。"""

    __tablename__ = "engram_vnext_working_memory"

    working_memory_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    cue_id: Mapped[str] = mapped_column(String(36), nullable=False)
    selected_episodes: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    conflicts: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_working_memory_stream", "stream_id", "created_at"),
    )


class MemoryUpdateProposalModel(Base):
    """LM 提出的结构化记忆更新，必须经过代码层验证后才可执行。"""

    __tablename__ = "engram_vnext_memory_update_proposal"

    proposal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_id: Mapped[str] = mapped_column(Text, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    target_memory_id: Mapped[str | None] = mapped_column(String(36))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("idx_engram_vnext_proposal_status", "status", "created_at"),
    )


class MemoryModel(Base):
    """正式记忆身份，不保存正文或认知评估副本。"""

    __tablename__ = "engram_vnext_memory"

    memory_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[MemoryStatus] = mapped_column(
        _enum(MemoryStatus, "engram_vnext_memory_status"), nullable=False
    )
    current_revision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "engram_vnext_memory_revision.revision_id",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType, "engram_vnext_memory_creator_type"), nullable=False
    )
    last_experienced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_memory_status", "status"),
        Index("idx_engram_vnext_memory_experienced", "last_experienced_at"),
    )


class MemoryRevisionModel(Base):
    """正式记忆不可变语义版本。"""

    __tablename__ = "engram_vnext_memory_revision"

    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory_revision.revision_id")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_kind: Mapped[MemoryKind] = mapped_column(
        _enum(MemoryKind, "engram_vnext_memory_kind"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    change_reason: Mapped[RevisionChangeReason] = mapped_column(
        _enum(RevisionChangeReason, "engram_vnext_revision_change_reason"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType, "engram_vnext_revision_creator_type"), nullable=False
    )
    created_by_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "memory_id", "revision_no", name="uq_engram_vnext_revision_no"
        ),
        CheckConstraint("revision_no >= 1", name="ck_engram_vnext_revision_positive"),
        Index("idx_engram_vnext_revision_memory", "memory_id"),
        Index("idx_engram_vnext_revision_kind", "memory_kind"),
    )


class MemoryRevisionSubjectModel(Base):
    """每个记忆版本唯一的主体。"""

    __tablename__ = "engram_vnext_memory_revision_subject"

    revision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_memory_revision.revision_id"),
        primary_key=True,
    )
    subject_kind: Mapped[SubjectKind] = mapped_column(
        _enum(SubjectKind, "engram_vnext_subject_kind"), nullable=False
    )
    person_id: Mapped[str | None] = mapped_column(Text)
    subject_key: Mapped[str | None] = mapped_column(Text)
    subject_label: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "subject_kind != 'PERSON' OR person_id IS NOT NULL",
            name="ck_engram_vnext_person_subject_id",
        ),
        Index("idx_engram_vnext_subject_person", "person_id"),
    )


class MemoryRevisionParticipantModel(Base):
    """记忆版本参与者。"""

    __tablename__ = "engram_vnext_memory_revision_participant"

    participant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_memory_revision.revision_id"),
        nullable=False,
    )
    participant_kind: Mapped[ParticipantKind] = mapped_column(
        _enum(ParticipantKind, "engram_vnext_participant_kind"), nullable=False
    )
    person_id: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "participant_kind != 'PERSON' OR person_id IS NOT NULL",
            name="ck_engram_vnext_person_participant_id",
        ),
        Index("idx_engram_vnext_participant_revision", "revision_id"),
        Index("idx_engram_vnext_participant_person", "person_id"),
    )


class EvidenceModel(Base):
    """正式记忆与历史候选归档共享的来源证据。"""

    __tablename__ = "engram_vnext_evidence"

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[EvidenceSourceType] = mapped_column(
        _enum(EvidenceSourceType, "engram_vnext_evidence_source_type"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EvidenceMessageLinkModel(Base):
    """证据到核心消息的有序软引用。"""

    __tablename__ = "engram_vnext_evidence_message_link"

    evidence_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_evidence.evidence_id"), primary_key=True
    )
    stream_id: Mapped[str] = mapped_column(Text, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_engram_vnext_evidence_ordinal"),
        Index("idx_engram_vnext_evidence_message_stream", "stream_id"),
    )


class EvidenceMessageSnapshotModel(Base):
    """按来源流与消息标识保存首次读取的原始消息快照。"""

    __tablename__ = "engram_vnext_evidence_message_snapshot"

    stream_id: Mapped[str] = mapped_column(Text, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    redacted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class RevisionEvidenceModel(Base):
    """记忆版本与证据的多对多关系。"""

    __tablename__ = "engram_vnext_revision_evidence"

    revision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_memory_revision.revision_id"),
        primary_key=True,
    )
    evidence_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_evidence.evidence_id"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MemoryRelationModel(Base):
    """历史记忆关系、合并去向及迁移关联的审计结构。"""

    __tablename__ = "engram_vnext_memory_relation"

    relation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), nullable=False
    )
    target_memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), nullable=False
    )
    relation_type: Mapped[RelationType] = mapped_column(
        _enum(RelationType, "engram_vnext_relation_type"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType, "engram_vnext_relation_creator_type"), nullable=False
    )
    retracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    retract_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "source_memory_id != target_memory_id",
            name="ck_engram_vnext_relation_not_self",
        ),
        Index("idx_engram_vnext_relation_source", "source_memory_id"),
        Index("idx_engram_vnext_relation_target", "target_memory_id"),
        Index(
            "uq_engram_vnext_relation_active_directed",
            "source_memory_id",
            "target_memory_id",
            "relation_type",
            unique=True,
            sqlite_where=text("retracted_at IS NULL"),
            postgresql_where=text("retracted_at IS NULL"),
        ),
        Index(
            "uq_engram_vnext_relation_active_symmetric",
            func.min(source_memory_id, target_memory_id),
            func.max(source_memory_id, target_memory_id),
            "relation_type",
            unique=True,
            sqlite_where=text(
                "retracted_at IS NULL AND relation_type IN ('CONTRADICTS', 'RELATED_TO')"
            ),
            postgresql_where=text(
                "retracted_at IS NULL AND relation_type IN ('CONTRADICTS', 'RELATED_TO')"
            ),
        ),
    )


class CandidateModel(Base):
    """历史候选素材的归档结构。"""

    __tablename__ = "engram_vnext_candidate"

    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[CandidateStatus] = mapped_column(
        _enum(CandidateStatus, "engram_vnext_candidate_status"), nullable=False
    )
    rough_title: Mapped[str] = mapped_column(Text, nullable=False)
    rough_content: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_kind: Mapped[MemoryKind | None] = mapped_column(
        _enum(MemoryKind, "engram_vnext_candidate_kind")
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    processing_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_sleep_session.sleep_session_id")
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_engram_vnext_candidate_status", "status", "created_at"),
    )


class CandidateEvidenceModel(Base):
    """历史候选素材与来源证据的归档关联。"""

    __tablename__ = "engram_vnext_candidate_evidence"

    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_evidence.evidence_id"), primary_key=True
    )


class CandidateSubjectModel(Base):
    """历史候选素材主体判断的归档结构。"""

    __tablename__ = "engram_vnext_candidate_subject"

    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), primary_key=True
    )
    subject_kind: Mapped[SubjectKind] = mapped_column(
        _enum(SubjectKind, "engram_vnext_candidate_subject_kind"), nullable=False
    )
    person_id: Mapped[str | None] = mapped_column(Text)
    subject_key: Mapped[str | None] = mapped_column(Text)
    subject_label: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "subject_kind != 'PERSON' OR person_id IS NOT NULL",
            name="ck_engram_vnext_candidate_person_subject_id",
        ),
    )


class CandidateParticipantModel(Base):
    """历史候选素材参与者判断的归档结构。"""

    __tablename__ = "engram_vnext_candidate_participant"

    participant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), nullable=False
    )
    participant_kind: Mapped[ParticipantKind] = mapped_column(
        _enum(ParticipantKind, "engram_vnext_candidate_participant_kind"),
        nullable=False,
    )
    person_id: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "participant_kind != 'PERSON' OR person_id IS NOT NULL",
            name="ck_engram_vnext_candidate_person_participant_id",
        ),
        Index("idx_engram_vnext_candidate_participant", "candidate_id"),
    )


class CandidateEncoderCursorModel(Base):
    """历史经历编码位置的按流归档结构。"""

    __tablename__ = "engram_vnext_candidate_encoder_cursor"

    stream_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_processed_message_time: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    last_processed_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SleepSessionModel(Base):
    """历史候选整理会话的审计归档。"""

    __tablename__ = "engram_vnext_sleep_session"

    sleep_session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trigger_type: Mapped[SleepTriggerType] = mapped_column(
        _enum(SleepTriggerType, "engram_vnext_sleep_trigger_type"), nullable=False
    )
    status: Mapped[SleepSessionStatus] = mapped_column(
        _enum(SleepSessionStatus, "engram_vnext_sleep_session_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "candidate_count >= 0", name="ck_engram_vnext_sleep_candidate_count"
        ),
        Index("idx_engram_vnext_sleep_status", "status", "started_at"),
    )


class CandidateActionModel(Base):
    """历史候选素材处理动作的追加式审计归档。"""

    __tablename__ = "engram_vnext_candidate_action"

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), nullable=False
    )
    sleep_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_sleep_session.sleep_session_id"),
        nullable=False,
    )
    action_type: Mapped[CandidateActionType] = mapped_column(
        _enum(CandidateActionType, "engram_vnext_candidate_action_type"), nullable=False
    )
    result_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory_revision.revision_id")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (Index("idx_engram_vnext_action_candidate", "candidate_id"),)


class SleepActionOperationModel(Base):
    """历史候选处理动作的意图、执行状态与结果归档。"""

    __tablename__ = "engram_vnext_sleep_action_operation"

    operation_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), nullable=False
    )
    sleep_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_sleep_session.sleep_session_id"),
        nullable=False,
    )
    action_type: Mapped[CandidateActionType] = mapped_column(
        _enum(CandidateActionType, "engram_vnext_sleep_operation_action_type"),
        nullable=False,
    )
    intent_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED', 'EXECUTING', 'COMPLETED', 'FAILED')",
            name="ck_engram_vnext_sleep_operation_status",
        ),
        Index(
            "idx_engram_vnext_sleep_operation_resume",
            "sleep_session_id",
            "status",
            "updated_at",
        ),
        Index(
            "idx_engram_vnext_sleep_operation_candidate", "candidate_id", "created_at"
        ),
    )


class SleepActionPlanModel(Base):
    """历史候选处理计划及执行进度的归档结构。"""

    __tablename__ = "engram_vnext_sleep_action_plan"

    plan_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), nullable=False
    )
    sleep_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_sleep_session.sleep_session_id"),
        nullable=False,
    )
    intents_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    operation_keys_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False)
    next_action_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    target_memory_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED', 'EXECUTING', 'COMPLETED', 'FAILED')",
            name="ck_engram_vnext_sleep_plan_status",
        ),
        CheckConstraint(
            "action_count >= 0 AND next_action_index >= 0 AND next_action_index <= action_count",
            name="ck_engram_vnext_sleep_plan_cursor",
        ),
        Index(
            "idx_engram_vnext_sleep_plan_resume",
            "sleep_session_id",
            "status",
            "updated_at",
        ),
        Index("idx_engram_vnext_sleep_plan_candidate", "candidate_id", "created_at"),
    )


class CandidateActionTargetModel(Base):
    """历史候选处理动作所涉及正式记忆的归档关联。"""

    __tablename__ = "engram_vnext_candidate_action_target"

    action_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_candidate_action.action_id"),
        primary_key=True,
    )
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), primary_key=True
    )
    target_role: Mapped[CandidateActionTargetRole] = mapped_column(
        _enum(CandidateActionTargetRole, "engram_vnext_candidate_target_role"),
        primary_key=True,
    )


class SleepSessionCandidateModel(Base):
    """历史整理会话对候选素材的认领、释放与结果归档。"""

    __tablename__ = "engram_vnext_sleep_session_candidate"

    sleep_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_sleep_session.sleep_session_id"),
        primary_key=True,
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_candidate.candidate_id"), primary_key=True
    )
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[SleepCandidateOutcome | None] = mapped_column(
        _enum(SleepCandidateOutcome, "engram_vnext_sleep_candidate_outcome")
    )


class MemoryEventModel(Base):
    """记忆不可删除的追加式审计事件。"""

    __tablename__ = "engram_vnext_memory_event"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), nullable=False
    )
    revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory_revision.revision_id")
    )
    event_type: Mapped[MemoryEventType] = mapped_column(
        _enum(MemoryEventType, "engram_vnext_memory_event_type"), nullable=False
    )
    actor_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType, "engram_vnext_memory_event_actor_type"), nullable=False
    )
    actor_ref: Mapped[str | None] = mapped_column(Text)
    stream_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        Index("idx_engram_vnext_event_memory", "memory_id", "occurred_at"),
        Index("idx_engram_vnext_event_type", "event_type", "occurred_at"),
    )


class DomainOperationModel(Base):
    """领域写操作的原子幂等结果。"""

    __tablename__ = "engram_vnext_domain_operation"

    operation_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("idx_engram_vnext_domain_operation_type", "operation_type", "created_at"),
    )


class PersonaUpdateLogModel(Base):
    """人物印象更新审计日志。"""

    __tablename__ = "engram_vnext_persona_update_log"

    update_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(Text, nullable=False)
    sleep_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_sleep_session.sleep_session_id")
    )
    old_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    new_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    generator_version: Mapped[str | None] = mapped_column(Text)
    revision_no: Mapped[int | None] = mapped_column(Integer)
    impression_text: Mapped[str | None] = mapped_column(Text)
    seen_revision_ids: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_persona_log_person", "person_id", "created_at"),
        Index(
            "uq_engram_vnext_persona_revision", "person_id", "revision_no", unique=True
        ),
        CheckConstraint(
            "revision_no >= 1", name="ck_engram_vnext_persona_revision_positive"
        ),
    )


class PersonaUpdateMemoryModel(Base):
    """人物印象更新所参考记忆的审计关联。"""

    __tablename__ = "engram_vnext_persona_update_memory"

    update_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("engram_vnext_persona_update_log.update_id"),
        primary_key=True,
    )
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), primary_key=True
    )


class MemoryRetrievalEntryModel(Base):
    """可完全重建的文本检索入口。"""

    __tablename__ = "engram_vnext_memory_retrieval_entry"

    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory.memory_id"), nullable=False
    )
    revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("engram_vnext_memory_revision.revision_id")
    )
    entry_type: Mapped[RetrievalEntryType] = mapped_column(
        _enum(RetrievalEntryType, "engram_vnext_retrieval_entry_type"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    generator_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        Index("idx_engram_vnext_retrieval_memory", "memory_id"),
        Index("idx_engram_vnext_retrieval_revision", "revision_id"),
        Index("idx_engram_vnext_retrieval_type", "entry_type"),
    )


class VectorOutboxModel(Base):
    """正式记忆事务提交后更新派生向量索引的工作项。

    index_id 为空时，工作器将其绑定至唯一 ACTIVE 索引并锁定认领状态。
    """

    __tablename__ = "engram_vnext_vector_outbox"

    outbox_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    object_type: Mapped[OutboxObjectType] = mapped_column(
        _enum(OutboxObjectType, "engram_vnext_outbox_object_type"), nullable=False
    )
    object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[OutboxOperation] = mapped_column(
        _enum(OutboxOperation, "engram_vnext_outbox_operation"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    index_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "engram_vnext_outbox_status"), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0", name="ck_engram_vnext_outbox_attempt_count"
        ),
        Index("idx_engram_vnext_outbox_status", "status", "updated_at"),
        Index(
            "idx_engram_vnext_outbox_index_status", "index_id", "status", "updated_at"
        ),
    )


class VectorIndexManifestModel(Base):
    """可替换向量索引版本的工程清单。"""

    __tablename__ = "engram_vnext_vector_index_manifest"

    index_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    index_version: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    flashback_threshold: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[VectorIndexStatus] = mapped_column(
        _enum(VectorIndexStatus, "engram_vnext_vector_index_status"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "embedding_dimension > 0", name="ck_engram_vnext_manifest_dimension"
        ),
        Index("idx_engram_vnext_manifest_status", "status"),
        Index(
            "uq_engram_vnext_manifest_active",
            "status",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


IMMUTABLE_MODELS: ClassVar[tuple[type[Base], ...]] = (
    EpisodeModel,
    EpisodeSemanticModel,
    CueSetModel,
    EpisodeRelationModel,
    WorkingMemoryModel,
    MemoryRevisionModel,
    MemoryRevisionSubjectModel,
    MemoryRevisionParticipantModel,
    EvidenceModel,
    EvidenceMessageLinkModel,
    RevisionEvidenceModel,
    CandidateActionModel,
    CandidateActionTargetModel,
    MemoryEventModel,
    PersonaUpdateLogModel,
    PersonaUpdateMemoryModel,
)


def _reject_immutable_change(mapper: Any, connection: Any, target: Base) -> None:
    """阻止不可变历史模型被 ORM 更新或删除。"""
    del mapper, connection
    raise ValueError(f"{type(target).__name__} 是追加式历史记录，不允许修改或删除")


for _model in IMMUTABLE_MODELS:
    event.listen(_model, "before_update", _reject_immutable_change)
    event.listen(_model, "before_delete", _reject_immutable_change)


ALL_MODELS: tuple[type[Base], ...] = tuple(Base.metadata.tables) and (
    SchemaVersionModel,
    EpisodeModel,
    EpisodeSemanticModel,
    CueSetModel,
    EpisodeRelationModel,
    WorkingMemoryModel,
    MemoryUpdateProposalModel,
    ClaimHypothesisModel,
    MemoryModel,
    MemoryRevisionModel,
    MemoryRevisionSubjectModel,
    MemoryRevisionParticipantModel,
    EvidenceModel,
    EvidenceMessageLinkModel,
    EvidenceMessageSnapshotModel,
    RevisionEvidenceModel,
    MemoryRelationModel,
    CandidateModel,
    CandidateEvidenceModel,
    CandidateSubjectModel,
    CandidateParticipantModel,
    CandidateEncoderCursorModel,
    SleepSessionModel,
    CandidateActionModel,
    SleepActionOperationModel,
    SleepActionPlanModel,
    CandidateActionTargetModel,
    SleepSessionCandidateModel,
    MemoryEventModel,
    DomainOperationModel,
    PersonaUpdateLogModel,
    PersonaUpdateMemoryModel,
    MemoryRetrievalEntryModel,
    VectorOutboxModel,
    VectorIndexManifestModel,
)
