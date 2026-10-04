"""Engram Memory vNext 正式记忆领域服务。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    CreateMemoryInput,
    EvidenceInput,
    MemoryChanged,
    MemoryLifecycleInput,
    MemoryWriteResult,
    ReinforceMemoryInput,
    ReviseMemoryInput,
    WriteContext,
)
from .enums import (
    ActorType,
    MemoryEventType,
    MemoryStatus,
    OutboxObjectType,
    OutboxOperation,
    OutboxStatus,
    RetrievalEntryType,
    RevisionChangeReason,
)
from .evidence_service import EvidenceService
from .models import (
    DomainOperationModel,
    EvidenceMessageLinkModel,
    EvidenceModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRetrievalEntryModel,
    MemoryRevisionModel,
    MemoryRevisionParticipantModel,
    MemoryRevisionSubjectModel,
    RevisionEvidenceModel,
    VectorOutboxModel,
)
from .schema import VNextSchema

RETRIEVAL_GENERATOR_VERSION = "vnext-2"


def _new_id() -> str:
    """生成统一 UUID 字符串。"""
    return str(uuid4())


def _content_hash(text: str) -> str:
    """计算派生检索文本的稳定内容摘要。"""
    return sha256(text.encode("utf-8")).hexdigest()


class MemoryService:
    """保证正式记忆写入的事务边界与领域不变量。"""

    def __init__(
        self,
        schema: VNextSchema,
        embedding_model_id: str,
        on_memory_changed: Callable[[MemoryChanged], Awaitable[None]] | None = None,
    ) -> None:
        """绑定正式记忆数据库、向量模型与提交后通知。"""
        if not embedding_model_id.strip():
            raise ValueError("embedding_model_id 不能为空")
        self._schema = schema
        self._embedding_model_id = embedding_model_id
        self._evidence_service = EvidenceService(schema)
        self._on_memory_changed = on_memory_changed

    async def _notify_change(self, change: MemoryChanged) -> None:
        """在数据库提交后通知记忆变化的订阅者。"""
        if self._on_memory_changed is not None:
            await self._on_memory_changed(change)

    @staticmethod
    def _input_person_ids(
        data: CreateMemoryInput | ReviseMemoryInput,
    ) -> tuple[str, ...]:
        """读取写入数据中的主要人物和次要人物。"""
        return tuple(
            dict.fromkeys(
                person_id
                for person_id in (
                    data.subject.person_id,
                    *(participant.person_id for participant in data.participants),
                )
                if person_id
            )
        )

    @staticmethod
    async def _revision_person_ids(
        session: AsyncSession,
        revision_id: str,
    ) -> tuple[str, ...]:
        """读取指定版本的人物关联，不依赖记忆当前状态。"""
        subject = await session.get(MemoryRevisionSubjectModel, revision_id)
        participants = await session.scalars(
            select(MemoryRevisionParticipantModel.person_id).where(
                MemoryRevisionParticipantModel.revision_id == revision_id,
            )
        )
        return tuple(
            dict.fromkeys(
                person_id
                for person_id in (
                    subject.person_id if subject is not None else None,
                    *participants.all(),
                )
                if person_id
            )
        )

    async def prepare_evidence(
        self, evidence: tuple[EvidenceInput, ...]
    ) -> tuple[EvidenceInput, ...]:
        """在写事务开始前读取缺少的来源快照。"""
        return await self._evidence_service.prepare_evidence(evidence)

    @staticmethod
    async def _claim_domain_operation(
        session: AsyncSession,
        operation_key: str | None,
        operation_type: str,
    ) -> dict[str, object] | None:
        """在当前写事务中原子声明领域操作并读取既有结果。"""
        if operation_key is None:
            return None
        await session.execute(
            sqlite_insert(DomainOperationModel)
            .values(
                operation_key=operation_key,
                operation_type=operation_type,
                result_json=None,
                created_at=datetime.now(UTC),
                completed_at=None,
            )
            .prefix_with("OR IGNORE")
        )
        operation = await session.get(DomainOperationModel, operation_key)
        if operation is None or operation.operation_type != operation_type:
            raise ValueError("operation_key 已绑定不同领域操作")
        if operation.result_json is not None:
            return dict(operation.result_json)
        return None

    @staticmethod
    async def _complete_domain_operation(
        session: AsyncSession,
        operation_key: str | None,
        result: dict[str, object],
    ) -> None:
        """在领域事务内固化操作结果。"""
        if operation_key is None:
            return
        operation = await session.get(DomainOperationModel, operation_key)
        if operation is None:
            raise ValueError("领域操作声明不存在")
        if operation.result_json is None:
            operation.result_json = result
            operation.completed_at = datetime.now(UTC)

    async def create_memory(
        self,
        data: CreateMemoryInput,
        context: WriteContext,
    ) -> MemoryWriteResult:
        """在同一事务中创建正文、人物、来源和检索入口。"""
        data.validate()
        data = replace(data, evidence=await self.prepare_evidence(data.evidence))
        now = datetime.now(UTC)
        memory_id = _new_id()
        revision_id = _new_id()

        memory = MemoryModel(
            memory_id=memory_id,
            status=MemoryStatus.ACTIVE,
            current_revision_id=revision_id,
            created_at=now,
            created_by_type=context.actor_type,
            last_experienced_at=data.observed_at,
            updated_at=now,
        )
        revision = MemoryRevisionModel(
            revision_id=revision_id,
            memory_id=memory_id,
            revision_no=1,
            parent_revision_id=None,
            title=data.title,
            content=data.content,
            memory_kind=data.memory_kind,
            observed_at=data.observed_at,
            change_reason=RevisionChangeReason.INITIAL,
            created_at=now,
            created_by_type=context.actor_type,
            created_by_ref=context.actor_ref,
        )
        subject = MemoryRevisionSubjectModel(
            revision_id=revision_id,
            subject_kind=data.subject.subject_kind,
            person_id=data.subject.person_id,
            subject_key=data.subject.subject_key,
            subject_label=data.subject.subject_label,
        )
        async with self._schema.database.session() as session:
            await session.execute(text("PRAGMA defer_foreign_keys = ON"))
            existing_result = await self._claim_domain_operation(
                session, context.operation_key, "CREATE"
            )
            if existing_result is not None:
                return MemoryWriteResult(
                    memory_id=str(existing_result["memory_id"]),
                    revision_id=str(existing_result["revision_id"]),
                    evidence_ids=tuple(
                        str(item) for item in existing_result.get("evidence_ids", ())
                    ),
                )
            session.add_all([memory, revision, subject])
            for participant in data.participants:
                session.add(
                    MemoryRevisionParticipantModel(
                        participant_id=_new_id(),
                        revision_id=revision_id,
                        participant_kind=participant.participant_kind,
                        person_id=participant.person_id,
                        label=participant.label,
                    )
                )
            evidence_ids, _ = await self._attach_evidence(
                session=session,
                revision_id=revision_id,
                evidence=data.evidence,
                existing_evidence_ids=data.evidence_ids,
                now=now,
            )
            self._add_retrieval_entry(
                session=session,
                memory_id=memory_id,
                revision_id=revision_id,
                entry_type=RetrievalEntryType.CURRENT_REVISION,
                text=f"{data.title}\n{data.content}",
                created_at=now,
            )
            session.add(
                MemoryEventModel(
                    event_id=_new_id(),
                    memory_id=memory_id,
                    revision_id=revision_id,
                    event_type=MemoryEventType.CREATED,
                    actor_type=context.actor_type,
                    actor_ref=context.actor_ref,
                    stream_id=context.stream_id,
                    occurred_at=now,
                    payload_json={
                        "evidence_ids": list(evidence_ids),
                        "revision_id": revision_id,
                        "operation_key": context.operation_key,
                    },
                )
            )
            await self._complete_domain_operation(
                session,
                context.operation_key,
                {
                    "memory_id": memory_id,
                    "revision_id": revision_id,
                    "evidence_ids": list(evidence_ids),
                },
            )

        await self._notify_change(
            MemoryChanged(
                memory_id=memory_id,
                change_type=MemoryEventType.CREATED,
                after_person_ids=self._input_person_ids(data),
                after_revision_id=revision_id,
                after_status=MemoryStatus.ACTIVE,
            )
        )
        return MemoryWriteResult(
            memory_id=memory_id,
            revision_id=revision_id,
            evidence_ids=evidence_ids,
        )

    async def reinforce_memory(
        self,
        data: ReinforceMemoryInput,
        context: WriteContext,
    ) -> MemoryWriteResult:
        """追加支持证据，但不创建语义 Revision。"""
        data.validate()
        data = replace(data, evidence=await self.prepare_evidence(data.evidence))
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            await session.execute(text("PRAGMA defer_foreign_keys = ON"))
            existing_result = await self._claim_domain_operation(
                session, context.operation_key, "REINFORCE"
            )
            if existing_result is not None:
                return MemoryWriteResult(
                    memory_id=str(existing_result["memory_id"]),
                    revision_id=str(existing_result["revision_id"]),
                    evidence_ids=tuple(
                        str(item) for item in existing_result.get("evidence_ids", ())
                    ),
                )
            memory = await self._get_writable_memory(session, data.memory_id)
            self._validate_current_revision(memory, data.based_on_revision_id)
            person_ids = await self._revision_person_ids(
                session, memory.current_revision_id
            )
            claim_result = await session.execute(
                update(MemoryModel)
                .where(
                    MemoryModel.memory_id == data.memory_id,
                    MemoryModel.status == MemoryStatus.ACTIVE,
                    MemoryModel.current_revision_id == data.based_on_revision_id,
                    MemoryModel.updated_at == memory.updated_at,
                )
                .values(updated_at=now)
                .execution_options(synchronize_session=False)
            )
            if claim_result.rowcount != 1:
                raise ValueError("Memory 在强化前已被并发更新")
            evidence_ids, experienced_at = await self._attach_evidence(
                session=session,
                revision_id=memory.current_revision_id,
                evidence=data.evidence,
                existing_evidence_ids=data.evidence_ids,
                now=now,
            )
            memory.last_experienced_at = max(memory.last_experienced_at, experienced_at)
            memory.updated_at = now
            session.add(
                MemoryEventModel(
                    event_id=_new_id(),
                    memory_id=memory.memory_id,
                    revision_id=memory.current_revision_id,
                    event_type=MemoryEventType.REINFORCED,
                    actor_type=context.actor_type,
                    actor_ref=context.actor_ref,
                    stream_id=context.stream_id,
                    occurred_at=now,
                    payload_json={
                        "reason": data.reason,
                        "evidence_ids": list(evidence_ids),
                        "operation_key": context.operation_key,
                    },
                )
            )
            await self._complete_domain_operation(
                session,
                context.operation_key,
                {
                    "memory_id": data.memory_id,
                    "revision_id": data.based_on_revision_id,
                    "evidence_ids": list(evidence_ids),
                },
            )
        await self._notify_change(
            MemoryChanged(
                memory_id=data.memory_id,
                change_type=MemoryEventType.REINFORCED,
                before_person_ids=person_ids,
                after_person_ids=person_ids,
                before_revision_id=data.based_on_revision_id,
                after_revision_id=data.based_on_revision_id,
                before_status=MemoryStatus.ACTIVE,
                after_status=MemoryStatus.ACTIVE,
            )
        )
        return MemoryWriteResult(
            memory_id=data.memory_id,
            revision_id=data.based_on_revision_id,
            evidence_ids=evidence_ids,
        )

    async def revise_memory(
        self,
        data: ReviseMemoryInput,
        context: WriteContext,
    ) -> MemoryWriteResult:
        """基于当前版本创建线性 Revision N+1 并切换当前指针。"""
        data.validate()
        data = replace(data, evidence=await self.prepare_evidence(data.evidence))
        now = datetime.now(UTC)
        revision_id = _new_id()
        async with self._schema.database.session() as session:
            await session.execute(text("PRAGMA defer_foreign_keys = ON"))
            existing_result = await self._claim_domain_operation(
                session, context.operation_key, "REVISE"
            )
            if existing_result is not None:
                return MemoryWriteResult(
                    memory_id=str(existing_result["memory_id"]),
                    revision_id=str(existing_result["revision_id"]),
                    evidence_ids=tuple(
                        str(item) for item in existing_result.get("evidence_ids", ())
                    ),
                )
            claim_result = await session.execute(
                update(MemoryModel)
                .where(
                    MemoryModel.memory_id == data.memory_id,
                    MemoryModel.status == MemoryStatus.ACTIVE,
                    MemoryModel.current_revision_id == data.based_on_revision_id,
                )
                .values(
                    current_revision_id=revision_id,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if claim_result.rowcount != 1:
                memory = await self._get_writable_memory(session, data.memory_id)
                self._validate_current_revision(memory, data.based_on_revision_id)
                current_revision = await session.get(
                    MemoryRevisionModel,
                    memory.current_revision_id,
                )
                if current_revision is None:
                    raise ValueError("当前 Revision 不存在")
                raise ValueError("based_on_revision_id 不是当前 Revision")
            memory = await session.get(MemoryModel, data.memory_id)
            if memory is None:  # pragma: no cover - UPDATE rowcount 已证明存在
                raise ValueError("Memory 不存在")
            current_revision = await session.get(
                MemoryRevisionModel,
                data.based_on_revision_id,
            )
            if current_revision is None:
                raise ValueError("当前 Revision 不存在")
            if current_revision.memory_id != data.memory_id:
                raise ValueError("当前 Revision 不属于目标 Memory")
            before_person_ids = await self._revision_person_ids(
                session,
                current_revision.revision_id,
            )
            session.add(
                MemoryRevisionModel(
                    revision_id=revision_id,
                    memory_id=memory.memory_id,
                    revision_no=current_revision.revision_no + 1,
                    parent_revision_id=current_revision.revision_id,
                    title=data.title,
                    content=data.content,
                    memory_kind=data.memory_kind,
                    observed_at=data.observed_at,
                    change_reason=data.change_reason,
                    created_at=now,
                    created_by_type=context.actor_type,
                    created_by_ref=context.actor_ref,
                )
            )
            session.add(
                MemoryRevisionSubjectModel(
                    revision_id=revision_id,
                    subject_kind=data.subject.subject_kind,
                    person_id=data.subject.person_id,
                    subject_key=data.subject.subject_key,
                    subject_label=data.subject.subject_label,
                )
            )
            for participant in data.participants:
                session.add(
                    MemoryRevisionParticipantModel(
                        participant_id=_new_id(),
                        revision_id=revision_id,
                        participant_kind=participant.participant_kind,
                        person_id=participant.person_id,
                        label=participant.label,
                    )
                )
            evidence_ids, experienced_at = await self._attach_evidence(
                session=session,
                revision_id=revision_id,
                evidence=data.evidence,
                existing_evidence_ids=data.evidence_ids,
                now=now,
            )
            await self._demote_current_retrieval_entry(session, memory.memory_id, now)
            self._add_retrieval_entry(
                session=session,
                memory_id=memory.memory_id,
                revision_id=revision_id,
                entry_type=RetrievalEntryType.CURRENT_REVISION,
                text=f"{data.title}\n{data.content}",
                created_at=now,
            )
            await session.execute(
                update(MemoryModel)
                .where(
                    MemoryModel.memory_id == data.memory_id,
                    MemoryModel.current_revision_id == revision_id,
                )
                .values(
                    last_experienced_at=max(memory.last_experienced_at, experienced_at),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.add(
                MemoryEventModel(
                    event_id=_new_id(),
                    memory_id=memory.memory_id,
                    revision_id=revision_id,
                    event_type=MemoryEventType.REVISED,
                    actor_type=context.actor_type,
                    actor_ref=context.actor_ref,
                    stream_id=context.stream_id,
                    occurred_at=now,
                    payload_json={
                        "change_reason": data.change_reason.value,
                        "evidence_ids": list(evidence_ids),
                        "operation_key": context.operation_key,
                    },
                )
            )
            await self._complete_domain_operation(
                session,
                context.operation_key,
                {
                    "memory_id": data.memory_id,
                    "revision_id": revision_id,
                    "evidence_ids": list(evidence_ids),
                },
            )
        await self._notify_change(
            MemoryChanged(
                memory_id=data.memory_id,
                change_type=MemoryEventType.REVISED,
                before_person_ids=before_person_ids,
                after_person_ids=self._input_person_ids(data),
                before_revision_id=data.based_on_revision_id,
                after_revision_id=revision_id,
                before_status=MemoryStatus.ACTIVE,
                after_status=MemoryStatus.ACTIVE,
            )
        )
        return MemoryWriteResult(
            memory_id=data.memory_id,
            revision_id=revision_id,
            evidence_ids=evidence_ids,
        )

    async def tombstone_memory(
        self,
        data: MemoryLifecycleInput,
        context: WriteContext,
        *,
        evidence: tuple[EvidenceInput, ...] = (),
    ) -> None:
        """保留作废依据与历史，并排队删除派生向量。"""
        data.validate()
        self._require_lifecycle_writer(context)
        evidence = await self.prepare_evidence(evidence)
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            existing = await self._claim_domain_operation(
                session, context.operation_key, "TOMBSTONE"
            )
            if existing is not None:
                return
            memory = await session.get(MemoryModel, data.memory_id)
            if memory is None:
                raise ValueError("Memory 不存在")
            if memory.status is MemoryStatus.TOMBSTONED:
                raise ValueError("Memory 已经是 TOMBSTONED")
            previous_status = memory.status
            person_ids = await self._revision_person_ids(
                session, memory.current_revision_id
            )
            revision_id = memory.current_revision_id
            evidence_ids: tuple[str, ...] = ()
            if evidence:
                evidence_ids, _ = await self._attach_evidence(
                    session,
                    revision_id,
                    evidence,
                    (),
                    now,
                )
            memory.status = MemoryStatus.TOMBSTONED
            memory.updated_at = now
            entries = list(
                (
                    await session.scalars(
                        select(MemoryRetrievalEntryModel).where(
                            MemoryRetrievalEntryModel.memory_id == memory.memory_id
                        )
                    )
                ).all()
            )
            for entry in entries:
                self._add_outbox(
                    session,
                    entry.entry_id,
                    entry.content_hash,
                    now,
                    operation=OutboxOperation.DELETE,
                )
            session.add(
                MemoryEventModel(
                    event_id=_new_id(),
                    memory_id=memory.memory_id,
                    revision_id=memory.current_revision_id,
                    event_type=MemoryEventType.TOMBSTONED,
                    actor_type=context.actor_type,
                    actor_ref=context.actor_ref,
                    stream_id=context.stream_id,
                    occurred_at=now,
                    payload_json={
                        "previous_status": previous_status.value,
                        "reason": data.reason,
                        "evidence_ids": list(evidence_ids),
                        "operation_key": context.operation_key,
                    },
                )
            )
            await self._complete_domain_operation(
                session,
                context.operation_key,
                {
                    "memory_id": memory.memory_id,
                    "revision_id": revision_id,
                },
            )

        await self._notify_change(
            MemoryChanged(
                memory_id=data.memory_id,
                change_type=MemoryEventType.TOMBSTONED,
                before_person_ids=person_ids,
                before_revision_id=revision_id,
                after_revision_id=revision_id,
                before_status=previous_status,
                after_status=MemoryStatus.TOMBSTONED,
            )
        )

    async def restore_memory(
        self,
        data: MemoryLifecycleInput,
        context: WriteContext,
    ) -> None:
        """恢复工程作废记忆并排队重建派生向量。"""
        data.validate()
        self._require_lifecycle_writer(context)
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            memory = await session.get(MemoryModel, data.memory_id)
            if memory is None:
                raise ValueError("Memory 不存在")
            if memory.status is not MemoryStatus.TOMBSTONED:
                raise ValueError("只有 TOMBSTONED Memory 可以 RESTORE")
            tombstone = (
                await session.scalars(
                    select(MemoryEventModel)
                    .where(
                        MemoryEventModel.memory_id == memory.memory_id,
                        MemoryEventModel.event_type == MemoryEventType.TOMBSTONED,
                    )
                    .order_by(
                        MemoryEventModel.occurred_at.desc(),
                        MemoryEventModel.event_id.desc(),
                    )
                )
            ).first()
            previous_status = MemoryStatus.ACTIVE
            if tombstone is not None and tombstone.payload_json is not None:
                raw_status = tombstone.payload_json.get("previous_status")
                if raw_status in {status.value for status in MemoryStatus}:
                    previous_status = MemoryStatus(raw_status)
            memory.status = previous_status
            person_ids = await self._revision_person_ids(
                session, memory.current_revision_id
            )
            revision_id = memory.current_revision_id
            memory.updated_at = now
            entries = list(
                (
                    await session.scalars(
                        select(MemoryRetrievalEntryModel).where(
                            MemoryRetrievalEntryModel.memory_id == memory.memory_id
                        )
                    )
                ).all()
            )
            for entry in entries:
                self._add_outbox(
                    session,
                    entry.entry_id,
                    entry.content_hash,
                    now,
                    operation=OutboxOperation.UPSERT,
                )
            session.add(
                MemoryEventModel(
                    event_id=_new_id(),
                    memory_id=memory.memory_id,
                    revision_id=memory.current_revision_id,
                    event_type=MemoryEventType.RESTORED,
                    actor_type=context.actor_type,
                    actor_ref=context.actor_ref,
                    stream_id=context.stream_id,
                    occurred_at=now,
                    payload_json={
                        "restored_status": previous_status.value,
                        "reason": data.reason,
                    },
                )
            )

        await self._notify_change(
            MemoryChanged(
                memory_id=data.memory_id,
                change_type=MemoryEventType.RESTORED,
                after_person_ids=person_ids,
                before_revision_id=revision_id,
                after_revision_id=revision_id,
                before_status=MemoryStatus.TOMBSTONED,
                after_status=previous_status,
            )
        )

    @staticmethod
    def _require_lifecycle_writer(context: WriteContext) -> None:
        """限制记忆作废和恢复的执行身份。"""
        if context.actor_type not in {ActorType.ACTOR, ActorType.ADMIN}:
            raise PermissionError("只有 ACTOR 或 ADMIN 可以执行 Memory 生命周期操作")

    async def _get_writable_memory(
        self,
        session: AsyncSession,
        memory_id: str,
    ) -> MemoryModel:
        """读取可写 ACTIVE 记忆。"""
        memory = await session.get(MemoryModel, memory_id)
        if memory is None:
            raise ValueError("Memory 不存在")
        if memory.status is not MemoryStatus.ACTIVE:
            raise ValueError("只有 ACTIVE Memory 可以强化或修订")
        return memory

    @staticmethod
    def _validate_current_revision(
        memory: MemoryModel, based_on_revision_id: str
    ) -> None:
        """拒绝基于旧版本的并发写入。"""
        if memory.current_revision_id != based_on_revision_id:
            raise ValueError("based_on_revision_id 不是当前 Revision")

    async def _attach_evidence(
        self,
        session: AsyncSession,
        revision_id: str,
        evidence: tuple[EvidenceInput, ...],
        existing_evidence_ids: tuple[str, ...],
        now: datetime,
    ) -> tuple[tuple[str, ...], datetime]:
        """创建或复用 Evidence，并追加到目标 Revision。"""
        new_ids = tuple(_new_id() for _ in evidence)
        existing_rows = list(
            (
                await session.scalars(
                    select(EvidenceModel).where(
                        EvidenceModel.evidence_id.in_(existing_evidence_ids)
                    )
                )
            ).all()
        )
        if len(existing_rows) != len(existing_evidence_ids):
            raise ValueError("存在无效 evidence_id")
        for evidence_id, item in zip(new_ids, evidence, strict=True):
            session.add(
                EvidenceModel(
                    evidence_id=evidence_id,
                    source_type=item.source_type,
                    observed_at=item.observed_at,
                    source_ref=item.source_ref,
                    note=item.note,
                    created_at=now,
                )
            )
        await session.flush()

        messages = tuple(message for item in evidence for message in item.messages)
        await self._evidence_service.persist_snapshots(session, messages)
        for evidence_id, item in zip(new_ids, evidence, strict=True):
            for ordinal, message in enumerate(item.messages):
                session.add(
                    EvidenceMessageLinkModel(
                        evidence_id=evidence_id,
                        message_id=message.message_id,
                        stream_id=message.stream_id,
                        ordinal=ordinal,
                    )
                )
                await session.flush()

        all_ids = new_ids + existing_evidence_ids
        linked_ids = set(
            (
                await session.scalars(
                    select(RevisionEvidenceModel.evidence_id).where(
                        RevisionEvidenceModel.revision_id == revision_id,
                        RevisionEvidenceModel.evidence_id.in_(all_ids),
                    )
                )
            ).all()
        )
        for evidence_id in all_ids:
            if evidence_id in linked_ids:
                continue
            session.add(
                RevisionEvidenceModel(
                    revision_id=revision_id,
                    evidence_id=evidence_id,
                    linked_at=now,
                )
            )
            linked_ids.add(evidence_id)
        await session.flush()
        observed_times = [item.observed_at for item in evidence]
        observed_times.extend(item.observed_at for item in existing_rows)
        return all_ids, max(observed_times)

    async def _demote_current_retrieval_entry(
        self,
        session: AsyncSession,
        memory_id: str,
        now: datetime,
    ) -> None:
        """将旧当前版本入口转换为历史版本入口并重新排队。"""
        statement = select(MemoryRetrievalEntryModel).where(
            MemoryRetrievalEntryModel.memory_id == memory_id,
            MemoryRetrievalEntryModel.entry_type == RetrievalEntryType.CURRENT_REVISION,
        )
        entry = (await session.scalars(statement)).one_or_none()
        if entry is None:
            raise ValueError("当前 Revision Retrieval Entry 不存在")
        entry.entry_type = RetrievalEntryType.HISTORICAL_REVISION
        self._add_outbox(session, entry.entry_id, entry.content_hash, now)

    def _add_retrieval_entry(
        self,
        session: AsyncSession,
        memory_id: str,
        revision_id: str | None,
        entry_type: RetrievalEntryType,
        text: str,
        created_at: datetime,
    ) -> None:
        """在记忆事务中写入检索入口及待投递向量任务。"""
        entry_id = _new_id()
        content_hash = _content_hash(text)
        session.add(
            MemoryRetrievalEntryModel(
                entry_id=entry_id,
                memory_id=memory_id,
                revision_id=revision_id,
                entry_type=entry_type,
                text=text,
                content_hash=content_hash,
                generator_version=RETRIEVAL_GENERATOR_VERSION,
                created_at=created_at,
            )
        )
        session.add(
            VectorOutboxModel(
                outbox_id=_new_id(),
                object_type=OutboxObjectType.RETRIEVAL_ENTRY,
                object_id=entry_id,
                operation=OutboxOperation.UPSERT,
                content_hash=content_hash,
                embedding_model_id=self._embedding_model_id,
                status=OutboxStatus.PENDING,
                attempt_count=0,
                last_error=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )

    def _add_outbox(
        self,
        session: AsyncSession,
        entry_id: str,
        content_hash: str,
        created_at: datetime,
        operation: OutboxOperation = OutboxOperation.UPSERT,
    ) -> None:
        """为既有检索入口追加向量更新任务。"""
        session.add(
            VectorOutboxModel(
                outbox_id=_new_id(),
                object_type=OutboxObjectType.RETRIEVAL_ENTRY,
                object_id=entry_id,
                operation=operation,
                content_hash=content_hash,
                embedding_model_id=self._embedding_model_id,
                status=OutboxStatus.PENDING,
                attempt_count=0,
                last_error=None,
                created_at=created_at,
                updated_at=created_at,
            )
        )
