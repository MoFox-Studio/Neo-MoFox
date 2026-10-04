"""正式记忆查询与写操作的服务门面，校验调用身份、人物及来源。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import or_, select

from .domain import (
    CreateMemoryInput,
    EvidenceInput,
    MemoryChanged,
    MemoryLifecycleInput,
    RetrievalQuery,
    ReviseMemoryInput,
    WriteContext,
)
from .enums import (
    ActorType,
    EvidenceSourceType,
    MemoryEventType,
    MemoryKind,
    MemoryStatus,
    ParticipantKind,
    RelationType,
    SubjectKind,
)
from .evidence_service import EvidenceService
from .memory_service import MemoryService
from .models import (
    EvidenceMessageLinkModel,
    EvidenceModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRelationModel,
    MemoryRevisionModel,
    MemoryRevisionParticipantModel,
    MemoryRevisionSubjectModel,
)
from .persona_service import PersonaService
from .repository import MemoryRepository
from .retrieval_service import RetrievalService, VectorSearchBackend
from .schema import VNextSchema

MEMORY_OPERATIONS = frozenset(
    {
        "memory_search",
        "memory_read",
        "memory_write",
        "memory_revise",
        "memory_invalidate",
        "person_lookup",
    }
)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """由运行组件绑定的调用身份、聊天来源和操作标识。"""

    actor_type: ActorType
    actor_ref: str | None = None
    stream_id: str | None = None
    evidence_message_ids: tuple[str, ...] = ()
    operation_key: str | None = None

    def to_write_context(self) -> WriteContext:
        """转换为领域写上下文。"""
        return WriteContext(
            actor_type=self.actor_type,
            actor_ref=self.actor_ref,
            stream_id=self.stream_id,
            operation_key=self.operation_key,
        )


class VNextToolService:
    """组织正式记忆检索、版本来源读取和带证据的写操作。"""

    def __init__(
        self,
        schema: VNextSchema,
        vector_backend: VectorSearchBackend,
        *,
        embedding_model_id: str = "tool-embedding",
        recent_memory_limit: int = 10,
        default_search_limit: int = 5,
        max_search_limit: int = 20,
        rrf_k: int = 60,
        on_memory_changed: Callable[[MemoryChanged], Awaitable[None]] | None = None,
    ) -> None:
        """装配共享数据库上的领域服务及查询数量限制。"""
        if recent_memory_limit <= 0:
            raise ValueError("recent_memory_limit 必须大于 0")
        if default_search_limit <= 0 or max_search_limit < default_search_limit:
            raise ValueError("default_search_limit/max_search_limit 参数非法")
        self._schema = schema
        self._memory = MemoryService(schema, embedding_model_id, on_memory_changed)
        self._repository = MemoryRepository(schema)
        self._evidence = EvidenceService(schema)
        self._retrieval = RetrievalService(schema, vector_backend, rrf_k=rrf_k)
        self._persona = PersonaService(schema)
        self._recent_memory_limit = recent_memory_limit
        self._default_search_limit = default_search_limit
        self._max_search_limit = max_search_limit

    def require_permission(self, operation: str, context: ToolContext) -> None:
        """只接受运行组件绑定的 Actor 或管理员身份。"""
        if (
            context.actor_type not in {ActorType.ACTOR, ActorType.ADMIN}
            or operation not in MEMORY_OPERATIONS
        ):
            raise PermissionError(f"{context.actor_type.value} 无权调用 {operation}")

    @staticmethod
    def _validate_people(data: CreateMemoryInput | ReviseMemoryInput) -> None:
        """要求一个明确主要人物及不重复的次要人物。"""
        if (
            data.subject.subject_kind is not SubjectKind.PERSON
            or not data.subject.person_id
        ):
            raise ValueError("正式记忆必须有一个主要人物 ID")
        people = [data.subject.person_id]
        for participant in data.participants:
            if (
                participant.participant_kind is not ParticipantKind.PERSON
                or not participant.person_id
            ):
                raise ValueError("次要人物必须提供准确人物 ID")
            people.append(participant.person_id)
        if len(set(people)) != len(people):
            raise ValueError("主次人物不能重复")

    @staticmethod
    def _validate_sources(
        evidence: tuple[EvidenceInput, ...], context: ToolContext
    ) -> None:
        """Actor 写入只接受当前流中已验证的准确消息快照。"""
        if context.actor_type is not ActorType.ACTOR:
            return
        if not context.stream_id or not context.evidence_message_ids or not evidence:
            raise ValueError("写操作必须引用当前聊天已验证的来源消息")
        references = set()
        for item in evidence:
            if (
                item.source_type is not EvidenceSourceType.ACTOR_WRITE
                or not item.messages
            ):
                raise ValueError("Actor 证据必须包含真实聊天来源")
            for message in item.messages:
                snapshot = message.snapshot
                if (
                    message.stream_id != context.stream_id
                    or message.message_id not in context.evidence_message_ids
                    or not snapshot
                    or snapshot.get("message_id") != message.message_id
                    or snapshot.get("stream_id") != message.stream_id
                    or not snapshot.get("time")
                    or not (
                        snapshot.get("processed_plain_text") or snapshot.get("content")
                    )
                    or snapshot.get("chat_type") not in {"private", "group", "discuss"}
                ):
                    raise ValueError("写操作来源缺少准确消息、时间、聊天类型或正文快照")
                references.add(message.message_id)
        if references != set(context.evidence_message_ids):
            raise ValueError("证据与已选择的来源消息不一致")

    async def memory_write(
        self, data: CreateMemoryInput, context: ToolContext
    ) -> dict[str, str]:
        """保存新记忆，由领域服务在提交后通知相关人物。"""
        self.require_permission("memory_write", context)
        self._validate_people(data)
        self._validate_sources(data.evidence, context)
        result = await self._memory.create_memory(data, context.to_write_context())
        return {"memory_id": result.memory_id, "revision_id": result.revision_id}

    async def memory_revise(
        self, data: ReviseMemoryInput, context: ToolContext
    ) -> dict[str, str]:
        """基于当前版本更新正文和人物关联，旧版本仍可读取。"""
        self.require_permission("memory_revise", context)
        self._validate_people(data)
        self._validate_sources(data.evidence, context)
        result = await self._memory.revise_memory(data, context.to_write_context())
        return {"memory_id": result.memory_id, "revision_id": result.revision_id}

    async def memory_invalidate(
        self,
        memory_id: str,
        reason: str,
        context: ToolContext,
        *,
        evidence: tuple[EvidenceInput, ...] = (),
    ) -> dict[str, str]:
        """保存作废依据，撤回当前记忆但不删除历史或来源。"""
        self.require_permission("memory_invalidate", context)
        self._validate_sources(evidence, context)
        await self._memory.tombstone_memory(
            MemoryLifecycleInput(memory_id, reason),
            context.to_write_context(),
            evidence=evidence,
        )
        return {"memory_id": memory_id, "status": MemoryStatus.TOMBSTONED.value}

    async def memory_search(
        self,
        query: str,
        context: ToolContext,
        *,
        person_ids: tuple[str, ...] = (),
        memory_kinds: tuple[str, ...] = (),
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[dict[str, object], ...]:
        """执行混合检索并返回当前版本及主次人物目录。"""
        self.require_permission("memory_search", context)
        if limit is not None and limit <= 0:
            raise ValueError("limit 必须大于 0")
        results = await self._retrieval.search(
            RetrievalQuery(
                text=query,
                top_k=min(limit or self._default_search_limit, self._max_search_limit),
                person_ids=person_ids,
                memory_kinds=tuple(MemoryKind(kind) for kind in memory_kinds),
                start_time=start_time,
                end_time=end_time,
            ),
            context.to_write_context(),
        )
        views = []
        for result in results:
            memory = await self._repository.get_memory(result.memory_id)
            revision = await self._repository.get_current_revision(result.memory_id)
            if (
                memory is None
                or revision is None
                or memory.status is not MemoryStatus.ACTIVE
            ):
                continue
            people = await self._people_view(revision.revision_id)
            views.append(
                {
                    "memory_id": memory.memory_id,
                    "status": memory.status.value,
                    "title": revision.title,
                    "current_content_preview": revision.content[:160],
                    "memory_kind": revision.memory_kind.value,
                    **people,
                    "last_experienced_at": memory.last_experienced_at,
                    "matched_by": list(result.matched_by),
                    "rrf_score": result.rrf_score,
                }
            )
        return tuple(views)

    async def memory_read(
        self, memory_id: str, view: str, context: ToolContext
    ) -> dict[str, object]:
        """读取当前记忆、各版本人物与来源，或完整审计记录。"""
        self.require_permission("memory_read", context)
        if view not in {"current", "history", "full"}:
            raise ValueError("view 只能是 current/history/full")
        memory = await self._repository.get_memory(memory_id)
        if memory is None:
            raise ValueError(f"Memory {memory_id} 不存在")
        revision = await self._repository.get_current_revision(memory_id)
        if revision is None:
            raise ValueError("当前版本缺失")
        await self._record_read_event(memory_id, context)
        people = await self._people_view(revision.revision_id)
        current: dict[str, object] = {
            "memory_id": memory_id,
            "status": memory.status.value,
            "merged_into": await self._resolve_merged_into(memory_id, memory.status),
            "created_at": memory.created_at,
            "last_experienced_at": memory.last_experienced_at,
            "current_revision": self._revision_view(revision),
            **people,
            "current_subject": people["subject"],
            "current_participants": people["participants"],
            "evidence_summary": await self._evidence_summary(
                memory_id, revision.revision_id, full=False
            ),
        }
        if view == "current":
            return current
        revisions = await self._repository.list_revisions(memory_id)
        history = []
        evidence_metadata = []
        for item in revisions:
            sources = await self._evidence_summary(
                memory_id, item.revision_id, full=view == "full"
            )
            history.append(
                {
                    **self._revision_view(item),
                    **await self._people_view(item.revision_id),
                    "evidence_summary": sources,
                }
            )
            evidence_metadata.extend(sources)
        if view == "history":
            return {**current, "history": history}
        return {
            **current,
            "history": history,
            "evidence_metadata": evidence_metadata,
            "events": [
                {
                    "event_id": item.event_id,
                    "event_type": item.event_type.value,
                    "revision_id": item.revision_id,
                    "actor_type": item.actor_type.value,
                    "occurred_at": item.occurred_at,
                    "payload": item.payload_json,
                }
                for item in await self._repository.list_events(memory_id)
            ],
        }

    async def _people_view(self, revision_id: str) -> dict[str, object]:
        """读取指定版本的人物关联，保留非人物历史主体信息。"""
        async with self._schema.database.session() as session:
            subject = await session.get(MemoryRevisionSubjectModel, revision_id)
            participants = tuple(
                (
                    await session.scalars(
                        select(MemoryRevisionParticipantModel).where(
                            MemoryRevisionParticipantModel.revision_id == revision_id,
                        )
                    )
                ).all()
            )
        return {
            "primary_person_id": subject.person_id if subject else None,
            "secondary_person_ids": [
                item.person_id for item in participants if item.person_id
            ],
            "subject": (
                {
                    "subject_kind": subject.subject_kind.value,
                    "person_id": subject.person_id,
                    "subject_key": subject.subject_key,
                    "subject_label": subject.subject_label,
                }
                if subject
                else None
            ),
            "participants": [
                {
                    "participant_kind": item.participant_kind.value,
                    "person_id": item.person_id,
                    "label": item.label,
                }
                for item in participants
            ],
        }

    @staticmethod
    def _revision_view(revision: MemoryRevisionModel) -> dict[str, object]:
        """构造包含程序时间的不可变版本视图。"""
        return {
            "revision_id": revision.revision_id,
            "revision_no": revision.revision_no,
            "title": revision.title,
            "content": revision.content,
            "memory_kind": revision.memory_kind.value,
            "observed_at": revision.observed_at,
            "created_at": revision.created_at,
            "change_reason": revision.change_reason.value,
        }

    async def _evidence_summary(
        self, memory_id: str, revision_id: str, *, full: bool
    ) -> list[dict[str, object]]:
        """读取指定版本的证据元数据及可选长期消息快照。"""
        evidence = await self._repository.list_evidence(
            memory_id, revision_id=revision_id
        )
        if full:
            return list(
                await self._read_evidence_records(
                    tuple(item.evidence_id for item in evidence), revision_id
                )
            )
        return [
            {
                "evidence_id": item.evidence_id,
                "source_type": item.source_type.value,
                "observed_at": item.observed_at,
                "note": item.note,
            }
            for item in evidence
        ]

    async def _read_evidence_records(
        self, evidence_ids: tuple[str, ...], revision_id: str
    ) -> tuple[dict[str, object], ...]:
        """按消息身份和聊天流读取证据快照，显式标记缺失或已脱敏来源。"""
        if not evidence_ids:
            return ()
        async with self._schema.database.session() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(EvidenceModel).where(
                            EvidenceModel.evidence_id.in_(evidence_ids),
                        )
                    )
                ).all()
            )
            links = tuple(
                (
                    await session.scalars(
                        select(EvidenceMessageLinkModel)
                        .where(
                            EvidenceMessageLinkModel.evidence_id.in_(evidence_ids),
                        )
                        .order_by(
                            EvidenceMessageLinkModel.evidence_id,
                            EvidenceMessageLinkModel.ordinal,
                        )
                    )
                ).all()
            )
        snapshots = await self._evidence.read_messages(
            tuple(dict.fromkeys((link.stream_id, link.message_id) for link in links))
        )
        by_ref = {
            (str(item.get("stream_id")), str(item.get("message_id"))): item
            for item in snapshots
        }
        records = []
        for row in rows:
            messages = []
            for link in links:
                if link.evidence_id != row.evidence_id:
                    continue
                snapshot = by_ref.get((link.stream_id, link.message_id))
                status = (
                    "MISSING"
                    if snapshot is None
                    else "REDACTED"
                    if snapshot.get("redacted")
                    else "AVAILABLE"
                )
                message: dict[str, object] = {
                    "stream_id": link.stream_id,
                    "message_id": link.message_id,
                    "ordinal": link.ordinal,
                    "source_status": status,
                }
                if status == "AVAILABLE":
                    message["snapshot"] = snapshot
                messages.append(message)
            records.append(
                {
                    "evidence_id": row.evidence_id,
                    "source_type": row.source_type.value,
                    "observed_at": row.observed_at,
                    "source_ref": row.source_ref,
                    "note": row.note,
                    "created_at": row.created_at,
                    "revision_id": revision_id,
                    "messages": messages,
                    "missing_messages": [
                        item
                        for item in messages
                        if item["source_status"] != "AVAILABLE"
                    ],
                }
            )
        return tuple(records)

    async def person_lookup(
        self,
        person_id: str,
        context: ToolContext,
        *,
        view: str = "current",
        revision_no: int | None = None,
    ) -> dict[str, object]:
        """只读当前印象、历史目录或指定历史版本，不触发人物生成。"""
        self.require_permission("person_lookup", context)
        if not person_id.strip():
            raise ValueError("person_id 不能为空")
        if view not in {"current", "history", "revision"}:
            raise ValueError("人物查询 view 必须为 current、history 或 revision")
        if view == "revision" and (revision_no is None or revision_no < 1):
            raise ValueError("读取历史人物印象必须提供正整数 revision_no")
        if view != "revision" and revision_no is not None:
            raise ValueError("revision_no 仅用于 revision 查询")
        person = await self._persona.get_core_person(person_id)
        persona = await self._persona.get_persona(person_id) if person else None
        aliases = await self._repository.resolve_person_aliases(person_id)
        async with self._schema.database.session() as session:
            revisions = tuple(
                (
                    await session.scalars(
                        select(MemoryRevisionModel)
                        .join(
                            MemoryModel,
                            MemoryModel.current_revision_id
                            == MemoryRevisionModel.revision_id,
                        )
                        .outerjoin(
                            MemoryRevisionSubjectModel,
                            MemoryRevisionSubjectModel.revision_id
                            == MemoryRevisionModel.revision_id,
                        )
                        .outerjoin(
                            MemoryRevisionParticipantModel,
                            MemoryRevisionParticipantModel.revision_id
                            == MemoryRevisionModel.revision_id,
                        )
                        .where(
                            MemoryModel.status == MemoryStatus.ACTIVE,
                            or_(
                                MemoryRevisionSubjectModel.person_id.in_(aliases),
                                MemoryRevisionParticipantModel.person_id.in_(aliases),
                            ),
                        )
                        .distinct()
                        .order_by(
                            MemoryModel.last_experienced_at.desc(),
                            MemoryModel.memory_id,
                        )
                        .limit(self._recent_memory_limit)
                    )
                ).all()
            )
        recent = [
            {
                "memory_id": item.memory_id,
                "title": item.title,
                "current_content_preview": item.content[:160],
                "observed_at": item.observed_at,
                **await self._people_view(item.revision_id),
            }
            for item in revisions
        ]
        impression = persona.impression_text if persona and revisions else ""
        result: dict[str, object] = {
            "person_id": person_id,
            "core_person_id": person.person_id if person else None,
            "basic_person_info": (
                {
                    "platform": person.platform,
                    "user_id": person.user_id,
                    "nickname": person.nickname,
                    "cardname": person.cardname,
                }
                if person
                else await self._repository.get_person_metadata(person_id)
            ),
            "persona_impression": impression or "暂无人物印象",
            "persona_updated_at": persona.updated_at
            if impression and persona
            else None,
            "recent_memories": recent,
            "view": view,
        }
        if view != "current":
            history = await self._persona.get_history(
                person.person_id if person else person_id,
                revision_no if view == "revision" else None,
            )
            result["history_notice"] = (
                "以下是当时的主观印象，不代表当前事实，也不是正式记忆依据。"
            )
            if view == "history":
                result["persona_history"] = history
            elif history:
                result["persona_revision"] = history[0]
            else:
                raise ValueError("人物印象历史版本不存在")
        return result

    async def _record_read_event(self, memory_id: str, context: ToolContext) -> None:
        """记录读取事件，不改变记忆正文或人物印象。"""
        async with self._schema.database.session() as session:
            session.add(
                MemoryEventModel(
                    event_id=str(uuid4()),
                    memory_id=memory_id,
                    revision_id=None,
                    event_type=MemoryEventType.READ,
                    actor_type=context.actor_type,
                    actor_ref=context.actor_ref,
                    stream_id=context.stream_id,
                    occurred_at=datetime.now(UTC),
                    payload_json=None,
                )
            )

    async def _resolve_merged_into(
        self, memory_id: str, status: MemoryStatus
    ) -> str | None:
        """读取历史合并记忆仍有效的目标指向。"""
        if status is not MemoryStatus.MERGED:
            return None
        async with self._schema.database.session() as session:
            return (
                await session.scalars(
                    select(MemoryRelationModel.target_memory_id).where(
                        MemoryRelationModel.source_memory_id == memory_id,
                        MemoryRelationModel.relation_type == RelationType.MERGED_INTO,
                        MemoryRelationModel.retracted_at.is_(None),
                    )
                )
            ).first()
