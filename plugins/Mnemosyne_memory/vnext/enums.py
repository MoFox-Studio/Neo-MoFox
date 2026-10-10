"""正式记忆与历史候选、整理审计记录的持久化枚举。"""

from __future__ import annotations

from enum import StrEnum


class MemoryStatus(StrEnum):
    """正式记忆状态。"""

    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    TOMBSTONED = "TOMBSTONED"


class MemoryKind(StrEnum):
    """记忆语义类型。"""

    EVENT = "EVENT"
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    RELATIONSHIP = "RELATIONSHIP"
    COMMITMENT = "COMMITMENT"
    PERSONAL_STATE = "PERSONAL_STATE"
    SOCIAL_PATTERN = "SOCIAL_PATTERN"


class RevisionChangeReason(StrEnum):
    """记忆版本变化原因。"""

    INITIAL = "INITIAL"
    DEVELOPMENT = "DEVELOPMENT"
    CORRECTION = "CORRECTION"
    CLARIFICATION = "CLARIFICATION"
    NEW_EVIDENCE = "NEW_EVIDENCE"
    EXPLICIT_CORRECTION = "EXPLICIT_CORRECTION"
    ADMIN_CORRECTION = "ADMIN_CORRECTION"
    MIGRATION_REWRITE = "MIGRATION_REWRITE"


class SubjectKind(StrEnum):
    """记忆主体类型。"""

    PERSON = "PERSON"
    GROUP = "GROUP"
    EVENT = "EVENT"
    TOPIC = "TOPIC"
    SELF = "SELF"
    WORLD = "WORLD"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ParticipantKind(StrEnum):
    """记忆参与者类型。"""

    PERSON = "PERSON"
    SELF = "SELF"
    OTHER = "OTHER"


class EvidenceSourceType(StrEnum):
    """证据来源类型。"""

    MESSAGE_SET = "MESSAGE_SET"
    ACTOR_WRITE = "ACTOR_WRITE"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    LEGACY_RECORD = "LEGACY_RECORD"
    ADMIN = "ADMIN"
    EXTERNAL = "EXTERNAL"


class RelationType(StrEnum):
    """正式记忆关系类型。"""

    CONTINUES = "CONTINUES"
    CAUSES = "CAUSES"
    REINFORCES = "REINFORCES"
    CONTRADICTS = "CONTRADICTS"
    RELATED_TO = "RELATED_TO"
    SUPERSEDES = "SUPERSEDES"
    MERGED_INTO = "MERGED_INTO"


class CandidateStatus(StrEnum):
    """历史候选素材的处理状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DEFERRED = "DEFERRED"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"


class CandidateActionType(StrEnum):
    """历史候选素材的整理动作类型。"""

    CREATE_NEW = "CREATE_NEW"
    REINFORCE = "REINFORCE"
    REVISE = "REVISE"
    MERGE = "MERGE"
    RELATE = "RELATE"
    IGNORE = "IGNORE"
    DEFER = "DEFER"


class CandidateActionTargetRole(StrEnum):
    """历史候选动作所关联记忆的角色。"""

    SOURCE = "SOURCE"
    TARGET = "TARGET"
    CANONICAL = "CANONICAL"
    RESULT = "RESULT"


class SleepTriggerType(StrEnum):
    """历史候选整理会话的触发类型。"""

    DAILY = "DAILY"
    PRESSURE = "PRESSURE"
    MANUAL = "MANUAL"
    RECOVERY = "RECOVERY"


class SleepSessionStatus(StrEnum):
    """历史候选整理会话的运行状态。"""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class SleepCandidateOutcome(StrEnum):
    """历史整理会话处理候选素材的结果。"""

    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"
    FAILED = "FAILED"


class MemoryEventType(StrEnum):
    """记忆追加式事件类型。"""

    CREATED = "CREATED"
    REINFORCED = "REINFORCED"
    REVISED = "REVISED"
    ANCHOR_CHANGED = "ANCHOR_CHANGED"
    RELATED = "RELATED"
    RELATION_RETRACTED = "RELATION_RETRACTED"
    MERGED = "MERGED"
    RECALLED = "RECALLED"
    READ = "READ"
    FLASHBACK_EXPOSED = "FLASHBACK_EXPOSED"
    TOMBSTONED = "TOMBSTONED"
    RESTORED = "RESTORED"


class ActorType(StrEnum):
    """正式记忆及历史审计记录的动作执行主体。"""

    ACTOR = "ACTOR"
    SLEEP_AGENT = "SLEEP_AGENT"
    SYSTEM = "SYSTEM"
    ADMIN = "ADMIN"
    MIGRATION = "MIGRATION"


class RetrievalEntryType(StrEnum):
    """可重建检索入口类型。"""

    CURRENT_REVISION = "CURRENT_REVISION"
    HISTORICAL_REVISION = "HISTORICAL_REVISION"
    TAG = "TAG"
    GENERATED_CUE = "GENERATED_CUE"


class OutboxObjectType(StrEnum):
    """向量 Outbox 可处理对象类型。"""

    RETRIEVAL_ENTRY = "RETRIEVAL_ENTRY"


class OutboxOperation(StrEnum):
    """向量 Outbox 操作。"""

    UPSERT = "UPSERT"
    DELETE = "DELETE"


class OutboxStatus(StrEnum):
    """向量 Outbox 处理状态。"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class VectorIndexStatus(StrEnum):
    """向量索引版本状态。"""

    BUILDING = "BUILDING"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    FAILED = "FAILED"
