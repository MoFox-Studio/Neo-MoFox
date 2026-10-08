"""检查正式记忆、证据、检索入口及向量派生数据的一致性。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import (
    MemoryStatus,
    OutboxObjectType,
    OutboxOperation,
    OutboxStatus,
    RelationType,
    RetrievalEntryType,
    SubjectKind,
    VectorIndexStatus,
)
from .memory_service import RETRIEVAL_GENERATOR_VERSION
from .models import (
    EvidenceMessageLinkModel,
    EvidenceMessageSnapshotModel,
    EvidenceModel,
    MemoryModel,
    MemoryRelationModel,
    MemoryRetrievalEntryModel,
    MemoryRevisionModel,
    MemoryRevisionSubjectModel,
    RevisionEvidenceModel,
    VectorIndexManifestModel,
    VectorOutboxModel,
)
from .schema import VNextSchema
from .vector_service import VectorIndexService

_ModelT = TypeVar("_ModelT")

_CORE_ENTRY_TYPES = frozenset(
    {
        RetrievalEntryType.CURRENT_REVISION,
        RetrievalEntryType.HISTORICAL_REVISION,
    }
)
_REVISION_ENTRY_TYPES = frozenset(
    {
        RetrievalEntryType.CURRENT_REVISION,
        RetrievalEntryType.HISTORICAL_REVISION,
    }
)
_ACTIVE_OUTBOX_STATUSES = frozenset(
    {OutboxStatus.PENDING, OutboxStatus.PROCESSING, OutboxStatus.FAILED}
)


@dataclass(frozen=True, slots=True)
class DoctorIssue:
    """描述一次可定位的 vNext 一致性问题。

    属性:
        code: 稳定的问题代码。
        object_id: 受影响对象标识；数据库级问题使用固定描述。
        repairable: Doctor 是否能在不猜测认知事实的前提下修复。
        details: 面向工程诊断的具体说明。
    """

    code: str
    object_id: str
    repairable: bool
    details: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """一次只读 Doctor 检查的结果。"""

    issues: tuple[DoctorIssue, ...]

    @property
    def healthy(self) -> bool:
        """返回报告是否没有发现问题。"""
        return not self.issues


@dataclass(frozen=True, slots=True)
class DoctorRepairResult:
    """一次 Doctor 修复前后状态与已执行动作。"""

    before: DoctorReport
    after: DoctorReport
    retried_outbox_entry_ids: tuple[str, ...]
    unrepaired_issue_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CanonicalSnapshot:
    """供一致性检查使用的 Canonical 与派生表快照。"""

    memories: tuple[MemoryModel, ...]
    revisions: tuple[MemoryRevisionModel, ...]
    subjects: tuple[MemoryRevisionSubjectModel, ...]
    evidence: tuple[EvidenceModel, ...]
    revision_evidence: tuple[RevisionEvidenceModel, ...]
    evidence_links: tuple[EvidenceMessageLinkModel, ...]
    evidence_snapshots: tuple[EvidenceMessageSnapshotModel, ...]
    relations: tuple[MemoryRelationModel, ...]
    retrieval_entries: tuple[MemoryRetrievalEntryModel, ...]
    outbox: tuple[VectorOutboxModel, ...]
    manifests: tuple[VectorIndexManifestModel, ...]


class DoctorService:
    """检查 vNext Canonical/Derived 一致性并执行明确的工程修复。"""

    def __init__(
        self,
        schema: VNextSchema,
        *,
        vector_service: VectorIndexService,
        embedding_model_id: str,
        embedding_dimension: int,
        retrieval_schema_version: str,
    ) -> None:
        """绑定 Schema、向量服务和当前索引参数。

        参数:
            schema: vNext Canonical 数据库。
            vector_service: 负责 Outbox 投递和向量索引重建的服务。
            embedding_model_id: 当前期望的向量模型标识。
            embedding_dimension: 当前期望的向量维度。
            retrieval_schema_version: 当前期望的检索结构版本。

        异常:
            ValueError: 当前向量清单参数非法。
        """
        if not embedding_model_id.strip():
            raise ValueError("embedding_model_id 不能为空")
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension 必须大于 0")
        if not retrieval_schema_version.strip():
            raise ValueError("retrieval_schema_version 不能为空")
        self._schema = schema
        self._vector_service = vector_service
        self._embedding_model_id = embedding_model_id
        self._embedding_dimension = embedding_dimension
        self._retrieval_schema_version = retrieval_schema_version

    async def check(self) -> DoctorReport:
        """只读检查 Canonical、派生检索、Outbox 和 Manifest 一致性。"""
        async with self._schema.database.session() as session:
            snapshot = await self._load_snapshot(session)
        issues = self._check_snapshot(snapshot)
        issues.extend(await self._check_physical_vector(snapshot))
        return DoctorReport(tuple(issues))

    async def repair(self, outbox_limit: int = 20) -> DoctorRepairResult:
        """执行检索入口、Outbox 和向量索引的确定性修复并复查。

        Canonical 认知损坏不会被修改。Retrieval Entry 会从可用的
        Canonical Memory/Revision 重建；FAILED Outbox 只会重试仍能定位到
        Retrieval Entry 的工作项。

        参数:
            outbox_limit: 本轮最多重新激活的 FAILED Outbox 数量。

        异常:
            ValueError: ``outbox_limit`` 不是正数。
        """
        if outbox_limit <= 0:
            raise ValueError("outbox_limit 必须大于 0")
        before = await self.check()
        await self.rebuild_retrieval_entries()
        await self._repair_outbox_metadata()
        retried = await self.retry_failed_outbox(outbox_limit)
        await self._repair_manifest_drift()
        await self._repair_physical_drift()
        after = await self.check()
        return DoctorRepairResult(
            before=before,
            after=after,
            retried_outbox_entry_ids=retried,
            unrepaired_issue_codes=_unique_strings(
                issue.code for issue in after.issues
            ),
        )

    async def rebuild_retrieval_entries(self) -> tuple[str, ...]:
        """从 Canonical Memory/Revision 重建可确定的核心 Retrieval Entry。

        返回:
            被创建、校正或清理的 Memory ID 元组。

        说明:
            当前指针不完整时不会推测替代版本。TAG 和 GENERATED_CUE
            没有本地 Canonical 生成源，因此保留而不猜测其内容；核心
            Revision 入口按稳定键原位校正或补建。
        """
        changed_memory_ids: list[str] = []
        async with self._schema.database.session() as session:
            snapshot = await self._load_snapshot(session)
            memories = {row.memory_id: row for row in snapshot.memories}
            revisions_by_id = {row.revision_id: row for row in snapshot.revisions}
            revisions_by_memory = _revisions_by_memory(snapshot.revisions)
            current_by_memory = {
                memory_id: _owned_revision(
                    memory,
                    revisions_by_memory.get(memory_id, ()),
                )
                for memory_id, memory in memories.items()
            }

            entries_by_memory: dict[str, list[MemoryRetrievalEntryModel]] = {}
            for entry in snapshot.retrieval_entries:
                owner_id = _canonical_entry_memory_id(
                    entry,
                    memories,
                    revisions_by_id,
                )
                entries_by_memory.setdefault(owner_id, []).append(entry)

            expected_memory_ids = set(memories)
            for memory_id, memory in memories.items():
                current = current_by_memory[memory_id]
                if current is None:
                    continue
                expected = _expected_entries(
                    revisions_by_memory.get(memory_id, ()),
                    current,
                )
                entries = entries_by_memory.setdefault(memory_id, [])
                if await self._repair_memory_entries(
                    session,
                    memory,
                    entries,
                    expected,
                ):
                    changed_memory_ids.append(memory_id)

            for owner_id, entries in entries_by_memory.items():
                if owner_id in expected_memory_ids:
                    continue
                for entry in entries:
                    await self._remove_derived_entry(session, entry)
                    changed_memory_ids.append(owner_id)

            await session.flush()
            await self._remove_completed_dangling_outbox(session)

        return _unique_strings(changed_memory_ids)

    async def retry_failed_outbox(self, limit: int = 20) -> tuple[str, ...]:
        """重试可定位的 FAILED Outbox，并返回成功投递的入口 ID。

        指向不存在 Retrieval Entry 的 FAILED 行不会被重置或删除，因而
        仍会在下一次检查中报告；这避免 Doctor 凭空制造派生或认知对象。
        """
        if limit <= 0:
            return ()
        selected_outbox_ids: list[str] = []
        selected_entry_ids: list[str] = []
        async with self._schema.database.session() as session:
            failed = tuple(
                (
                    await session.scalars(
                        select(VectorOutboxModel)
                        .where(VectorOutboxModel.status == OutboxStatus.FAILED)
                        .order_by(
                            VectorOutboxModel.updated_at, VectorOutboxModel.outbox_id
                        )
                    )
                ).all()
            )
            entries = {
                entry.entry_id: entry
                for entry in (
                    await session.scalars(select(MemoryRetrievalEntryModel))
                ).all()
            }
            now = datetime.now(UTC)
            for outbox in failed:
                if len(selected_outbox_ids) >= limit:
                    break
                if outbox.object_type is not OutboxObjectType.RETRIEVAL_ENTRY:
                    continue
                entry = entries.get(outbox.object_id)
                if entry is None:
                    continue
                outbox.content_hash = entry.content_hash
                outbox.embedding_model_id = self._embedding_model_id
                outbox.status = OutboxStatus.PENDING
                outbox.attempt_count = 0
                outbox.last_error = None
                outbox.updated_at = now
                selected_outbox_ids.append(outbox.outbox_id)
                selected_entry_ids.append(entry.entry_id)

        if not selected_outbox_ids:
            return ()
        succeeded = await self._process_selected_outbox(selected_outbox_ids)
        selected = set(selected_entry_ids)
        return _unique_strings(
            entry_id for entry_id in succeeded if entry_id in selected
        )

    async def _check_physical_vector(
        self,
        snapshot: _CanonicalSnapshot,
    ) -> list[DoctorIssue]:
        """检查可读取的物理向量索引与 ACTIVE Canonical 入口是否一致。"""
        active_manifests = tuple(
            row for row in snapshot.manifests if row.status is VectorIndexStatus.ACTIVE
        )
        if len(active_manifests) != 1:
            return []
        manifest = active_manifests[0]
        physical_ids = await self._vector_service.physical_entry_ids(
            manifest.index_id,
            manifest.embedding_model_id,
            manifest.embedding_dimension,
        )
        if physical_ids is None:
            return [
                _issue(
                    "VECTOR_PHYSICAL_UNCHECKABLE",
                    manifest.index_id,
                    "无法检查 ACTIVE Manifest 绑定的物理向量索引",
                    False,
                )
            ]
        active_memory_ids = {
            row.memory_id
            for row in snapshot.memories
            if row.status is MemoryStatus.ACTIVE
        }
        expected_ids = {
            row.entry_id
            for row in snapshot.retrieval_entries
            if row.memory_id in active_memory_ids
        }
        if physical_ids == expected_ids:
            return []
        missing = sorted(expected_ids - physical_ids)
        extra = sorted(physical_ids - expected_ids)
        return [
            _issue(
                "VECTOR_PHYSICAL_DRIFT",
                manifest.index_id,
                f"物理向量入口漂移: missing={missing[:20]!r}, extra={extra[:20]!r}",
                True,
            )
        ]

    async def _load_snapshot(self, session: AsyncSession) -> _CanonicalSnapshot:
        """在一个数据库会话中读取 Doctor 所需的全部表行。"""
        return _CanonicalSnapshot(
            memories=await _all(session, MemoryModel),
            revisions=await _all(session, MemoryRevisionModel),
            subjects=await _all(session, MemoryRevisionSubjectModel),
            evidence=await _all(session, EvidenceModel),
            revision_evidence=await _all(session, RevisionEvidenceModel),
            evidence_links=await _all(session, EvidenceMessageLinkModel),
            evidence_snapshots=await _all(session, EvidenceMessageSnapshotModel),
            relations=await _all(session, MemoryRelationModel),
            retrieval_entries=await _all(session, MemoryRetrievalEntryModel),
            outbox=await _all(session, VectorOutboxModel),
            manifests=await _all(session, VectorIndexManifestModel),
        )

    def _check_snapshot(self, snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
        """按固定顺序检查 Canonical 与派生数据。"""
        issues: list[DoctorIssue] = []
        issues.extend(_check_current_pointers(snapshot))
        issues.extend(_check_revisions(snapshot))
        issues.extend(_check_evidence(snapshot))
        issues.extend(_check_evidence_snapshots(snapshot))
        issues.extend(_check_merge_cycles(snapshot))
        issues.extend(_check_retrieval_entries(snapshot))
        issues.extend(_check_outbox(snapshot, self._embedding_model_id))
        issues.extend(_check_manifests(snapshot, self._manifest_parameters()))
        return issues

    async def _repair_memory_entries(
        self,
        session: AsyncSession,
        memory: MemoryModel,
        entries: list[MemoryRetrievalEntryModel],
        expected: tuple[tuple[RetrievalEntryType, str | None, str], ...],
    ) -> bool:
        """按逻辑入口键原位修复一条 Memory 的核心入口集合。"""
        changed = False
        core_entries = [
            entry for entry in entries if entry.entry_type in _CORE_ENTRY_TYPES
        ]
        entries_by_key: dict[
            tuple[RetrievalEntryType, str | None],
            list[MemoryRetrievalEntryModel],
        ] = {}
        for entry in core_entries:
            entries_by_key.setdefault(_entry_key(entry), []).append(entry)
        used_ids: set[str] = set()

        for entry_type, revision_id, text_value in expected:
            key = (entry_type, revision_id)
            candidates = sorted(
                entries_by_key.get(key, []),
                key=lambda item: item.entry_id,
            )
            entry = next(
                (item for item in candidates if item.entry_id not in used_ids),
                None,
            )
            if entry is None and revision_id is not None:
                entry = next(
                    (
                        item
                        for item in core_entries
                        if item.entry_id not in used_ids
                        and item.revision_id == revision_id
                    ),
                    None,
                )
            if entry is None:
                entry = _new_retrieval_entry(
                    memory.memory_id,
                    entry_type,
                    revision_id,
                    text_value,
                )
                session.add(entry)
                await session.flush()
                await self._ensure_upsert(session, entry)
                entries.append(entry)
                changed = True
                continue

            used_ids.add(entry.entry_id)
            old_vector_state = (
                entry.memory_id,
                entry.revision_id,
                entry.text,
                entry.content_hash,
            )
            old_generator_version = entry.generator_version
            entry.memory_id = memory.memory_id
            entry.revision_id = revision_id
            entry.entry_type = entry_type
            entry.text = text_value
            entry.content_hash = _content_hash(text_value)
            entry.generator_version = RETRIEVAL_GENERATOR_VERSION
            new_vector_state = (
                entry.memory_id,
                entry.revision_id,
                entry.text,
                entry.content_hash,
            )
            if old_vector_state != new_vector_state:
                await self._ensure_upsert(session, entry)
                changed = True
            elif old_generator_version != RETRIEVAL_GENERATOR_VERSION:
                changed = True

        for entry in core_entries:
            if entry.entry_id not in used_ids:
                await self._remove_derived_entry(session, entry)
                changed = True
        return changed

    async def _ensure_upsert(
        self,
        session: AsyncSession,
        entry: MemoryRetrievalEntryModel,
    ) -> None:
        """为需要向量同步的入口确保存在一条 UPSERT Outbox。"""
        rows = tuple(
            (
                await session.scalars(
                    select(VectorOutboxModel).where(
                        VectorOutboxModel.object_type
                        == OutboxObjectType.RETRIEVAL_ENTRY,
                        VectorOutboxModel.object_id == entry.entry_id,
                        VectorOutboxModel.operation == OutboxOperation.UPSERT,
                    )
                )
            ).all()
        )
        active = next(
            (
                row
                for row in rows
                if row.status
                in {
                    OutboxStatus.PENDING,
                    OutboxStatus.PROCESSING,
                    OutboxStatus.FAILED,
                }
            ),
            None,
        )
        if active is not None:
            active.content_hash = entry.content_hash
            active.embedding_model_id = self._embedding_model_id
            if active.status is not OutboxStatus.PROCESSING:
                active.last_error = (
                    None if active.status is OutboxStatus.PENDING else active.last_error
                )
            active.updated_at = datetime.now(UTC)
            return
        now = datetime.now(UTC)
        session.add(
            VectorOutboxModel(
                outbox_id=str(uuid4()),
                object_type=OutboxObjectType.RETRIEVAL_ENTRY,
                object_id=entry.entry_id,
                operation=OutboxOperation.UPSERT,
                content_hash=entry.content_hash,
                embedding_model_id=self._embedding_model_id,
                status=OutboxStatus.PENDING,
                attempt_count=0,
                last_error=None,
                created_at=now,
                updated_at=now,
            )
        )

    async def _remove_derived_entry(
        self,
        session: AsyncSession,
        entry: MemoryRetrievalEntryModel,
    ) -> None:
        """删除没有 Canonical 对应物的派生入口及其数据库 Outbox 行。"""
        rows = tuple(
            (
                await session.scalars(
                    select(VectorOutboxModel).where(
                        VectorOutboxModel.object_type
                        == OutboxObjectType.RETRIEVAL_ENTRY,
                        VectorOutboxModel.object_id == entry.entry_id,
                    )
                )
            ).all()
        )
        for row in rows:
            await session.delete(row)
        await session.delete(entry)

    async def _remove_completed_dangling_outbox(
        self,
        session: AsyncSession,
    ) -> None:
        """清理目标已不存在且已完成投递的派生 Outbox 行。"""
        entry_ids = set(
            (await session.scalars(select(MemoryRetrievalEntryModel.entry_id))).all()
        )
        rows = tuple(
            (
                await session.scalars(
                    select(VectorOutboxModel).where(
                        VectorOutboxModel.object_type
                        == OutboxObjectType.RETRIEVAL_ENTRY,
                        VectorOutboxModel.status == OutboxStatus.DONE,
                    )
                )
            ).all()
        )
        for row in rows:
            if row.object_id not in entry_ids:
                await session.delete(row)

    async def _repair_outbox_metadata(self) -> tuple[str, ...]:
        """把当前工作 Outbox 的元数据恢复为入口参数。"""
        repaired: list[str] = []
        async with self._schema.database.session() as session:
            entries = {
                entry.entry_id: entry
                for entry in (
                    await session.scalars(select(MemoryRetrievalEntryModel))
                ).all()
            }
            rows = tuple((await session.scalars(select(VectorOutboxModel))).all())
            manifests = {
                manifest.index_id: manifest
                for manifest in (
                    await session.scalars(select(VectorIndexManifestModel))
                ).all()
            }
            active_manifests = tuple(
                manifest
                for manifest in manifests.values()
                if manifest.status is VectorIndexStatus.ACTIVE
            )
            for outbox in rows:
                if outbox.object_type is not OutboxObjectType.RETRIEVAL_ENTRY:
                    continue
                if outbox.status is OutboxStatus.PROCESSING:
                    continue
                target = (
                    manifests.get(outbox.index_id)
                    if outbox.index_id is not None
                    else active_manifests[0]
                    if len(active_manifests) == 1
                    else None
                )
                if (
                    target is None
                    or target.status is VectorIndexStatus.BUILDING
                    or target.status is not VectorIndexStatus.ACTIVE
                    or target.embedding_model_id != self._embedding_model_id
                ):
                    continue
                entry = entries.get(outbox.object_id)
                if entry is None:
                    continue
                if outbox.status is OutboxStatus.DONE:
                    continue
                changed = False
                if outbox.content_hash != entry.content_hash:
                    outbox.content_hash = entry.content_hash
                    changed = True
                if outbox.embedding_model_id != self._embedding_model_id:
                    outbox.embedding_model_id = self._embedding_model_id
                    changed = True
                if changed:
                    outbox.updated_at = datetime.now(UTC)
                    repaired.append(entry.entry_id)
        return _unique_strings(repaired)

    async def _process_selected_outbox(
        self,
        selected_outbox_ids: list[str],
    ) -> tuple[str, ...]:
        """仅投递选中的 Outbox，不修改其他工作项的处理状态。"""
        selected = tuple(dict.fromkeys(selected_outbox_ids))
        return await self._vector_service.process_pending_outbox(
            len(selected), outbox_ids=selected
        )

    async def _repair_manifest_drift(self) -> str | None:
        """发现清单漂移时从 Canonical 入口重新建立当前索引。"""
        async with self._schema.database.session() as session:
            manifests = tuple(
                (await session.scalars(select(VectorIndexManifestModel))).all()
            )
        if not _manifest_drift(manifests, self._manifest_parameters()):
            return None
        active_manifest = next(
            (
                manifest
                for manifest in manifests
                if manifest.status is VectorIndexStatus.ACTIVE
            ),
            None,
        )
        index_id, _ = await self._vector_service.rebuild_entire_index(
            self._embedding_model_id,
            self._embedding_dimension,
            self._retrieval_schema_version,
            active_manifest.flashback_threshold
            if active_manifest is not None
            else None,
        )
        return index_id

    def _manifest_parameters(self) -> tuple[str, int, str]:
        """返回当前向量清单的模型、维度和检索结构参数。"""
        return (
            self._embedding_model_id,
            self._embedding_dimension,
            self._retrieval_schema_version,
        )

    async def _repair_physical_drift(self) -> str | None:
        """物理入口 ID 漂移时重建当前派生索引。"""
        report = await self.check()
        if not any(issue.code == "VECTOR_PHYSICAL_DRIFT" for issue in report.issues):
            return None
        async with self._schema.database.session() as session:
            manifest = (
                await session.scalars(
                    select(VectorIndexManifestModel)
                    .where(VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE)
                    .order_by(VectorIndexManifestModel.created_at.desc())
                )
            ).first()
        if manifest is None:
            return None
        index_id, _ = await self._vector_service.rebuild_entire_index(
            self._embedding_model_id,
            self._embedding_dimension,
            self._retrieval_schema_version,
            manifest.flashback_threshold,
        )
        return index_id


async def _all(session: AsyncSession, model: type[_ModelT]) -> tuple[_ModelT, ...]:
    """读取模型全部行并保持数据库返回顺序。"""
    return tuple((await session.scalars(select(model))).all())


def _check_current_pointers(snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
    """检查 Memory 当前 Revision 指针及归属。"""
    revisions = {row.revision_id: row for row in snapshot.revisions}
    issues: list[DoctorIssue] = []
    for memory in snapshot.memories:
        revision = revisions.get(memory.current_revision_id)
        if revision is None:
            issues.append(
                _issue(
                    "CURRENT_REVISION_DANGLING",
                    memory.memory_id,
                    f"current_revision_id={memory.current_revision_id!r} 不存在",
                )
            )
        elif revision.memory_id != memory.memory_id:
            issues.append(
                _issue(
                    "CURRENT_REVISION_OWNERSHIP",
                    memory.memory_id,
                    f"当前 Revision {revision.revision_id} 属于 Memory {revision.memory_id}",
                )
            )

    return issues


def _check_revisions(snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
    """检查 Revision 所属 Memory、父链、Subject 和 Evidence 关系。"""
    memory_ids = {row.memory_id for row in snapshot.memories}
    revisions = {row.revision_id: row for row in snapshot.revisions}
    subjects = _group_by(snapshot.subjects, lambda row: row.revision_id)
    evidence_by_revision = _group_by(
        snapshot.revision_evidence,
        lambda row: row.revision_id,
    )
    current_claimants: dict[str, set[str]] = {}
    for memory in snapshot.memories:
        current_claimants.setdefault(memory.current_revision_id, set()).add(
            memory.memory_id
        )

    issues: list[DoctorIssue] = []
    cycle_nodes = _revision_cycle_nodes(revisions)
    revisions_by_memory = _revisions_by_memory(snapshot.revisions)
    for memory_id, rows in revisions_by_memory.items():
        ordered = tuple(
            sorted(rows, key=lambda row: (row.revision_no, row.revision_id))
        )
        actual_numbers = tuple(row.revision_no for row in ordered)
        if len(set(actual_numbers)) != len(actual_numbers):
            issues.append(
                _issue(
                    "REVISION_NUMBER_DUPLICATE",
                    memory_id,
                    f"Revision No 序列包含重复值: {actual_numbers!r}",
                )
            )
        if actual_numbers != tuple(range(1, len(ordered) + 1)):
            issues.append(
                _issue(
                    "REVISION_NUMBER_SEQUENCE",
                    memory_id,
                    f"Revision No 序列为 {actual_numbers!r}，期望从 1 连续递增",
                )
            )

    for revision in snapshot.revisions:
        claimants = current_claimants.get(revision.revision_id, set())
        if revision.memory_id not in memory_ids or any(
            owner_id != revision.memory_id for owner_id in claimants
        ):
            owner_details = ", ".join(sorted(claimants)) or "无反向 current 指针"
            issues.append(
                _issue(
                    "REVISION_MEMORY_OWNERSHIP",
                    revision.revision_id,
                    f"Revision memory_id={revision.memory_id!r}，"
                    f"反向 current owner={owner_details}",
                )
            )

        parent_id = revision.parent_revision_id
        if parent_id is None:
            if revision.revision_no != 1:
                issues.append(
                    _issue(
                        "REVISION_PARENT_MISSING",
                        revision.revision_id,
                        f"Revision No={revision.revision_no} 不是根版本但没有 parent_revision_id",
                    )
                )
        else:
            parent = revisions.get(parent_id)
            if parent is None:
                issues.append(
                    _issue(
                        "REVISION_PARENT_DANGLING",
                        revision.revision_id,
                        f"parent_revision_id={parent_id!r} 不存在",
                    )
                )
            else:
                if parent.memory_id != revision.memory_id:
                    issues.append(
                        _issue(
                            "REVISION_PARENT_OWNERSHIP",
                            revision.revision_id,
                            f"父 Revision 属于 Memory {parent.memory_id}",
                        )
                    )
                elif parent.revision_no != revision.revision_no - 1:
                    issues.append(
                        _issue(
                            "REVISION_PARENT_ORDER",
                            revision.revision_id,
                            f"父版本号 {parent.revision_no} 不是当前版本的紧邻前一版本 "
                            f"{revision.revision_no - 1}",
                        )
                    )
        if revision.revision_id in cycle_nodes:
            issues.append(
                _issue(
                    "REVISION_PARENT_CYCLE",
                    revision.revision_id,
                    "Revision parent chain 包含环",
                )
            )

        subject_rows = subjects.get(revision.revision_id, [])
        if not subject_rows:
            issues.append(
                _issue(
                    "REVISION_SUBJECT_MISSING",
                    revision.revision_id,
                    "Revision 没有唯一 Subject",
                )
            )
        elif len(subject_rows) != 1:
            issues.append(
                _issue(
                    "REVISION_SUBJECT_MULTIPLE",
                    revision.revision_id,
                    f"Revision 拥有 {len(subject_rows)} 个 Subject",
                )
            )
        elif (
            subject_rows[0].subject_kind == SubjectKind.PERSON
            and not subject_rows[0].person_id
        ):
            issues.append(
                _issue(
                    "REVISION_SUBJECT_PERSON_MISSING",
                    revision.revision_id,
                    "PERSON Subject 缺少 person_id",
                )
            )

        if not evidence_by_revision.get(revision.revision_id):
            issues.append(
                _issue(
                    "REVISION_EVIDENCE_MISSING",
                    revision.revision_id,
                    "Revision 没有 Evidence 关联",
                )
            )

    for subject in snapshot.subjects:
        if subject.revision_id not in revisions:
            issues.append(
                _issue(
                    "REVISION_SUBJECT_DANGLING",
                    subject.revision_id,
                    "Subject 指向不存在的 Revision",
                )
            )
    return issues


def _check_evidence(snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
    """检查 Evidence 关系行和消息软引用的内部完整性。"""
    evidence_ids = {row.evidence_id for row in snapshot.evidence}
    revision_ids = {row.revision_id for row in snapshot.revisions}
    issues: list[DoctorIssue] = []

    for link in snapshot.revision_evidence:
        if link.revision_id not in revision_ids:
            issues.append(
                _issue(
                    "REVISION_EVIDENCE_REVISION_DANGLING",
                    link.revision_id,
                    "RevisionEvidence 指向不存在的 Revision",
                )
            )
        if link.evidence_id not in evidence_ids:
            issues.append(
                _issue(
                    "REVISION_EVIDENCE_DANGLING",
                    link.revision_id,
                    f"Evidence {link.evidence_id} 不存在",
                )
            )

    for link in snapshot.evidence_links:
        if link.evidence_id not in evidence_ids:
            # message_id / stream_id 是外部 Message 的 soft reference；只
            # 检查本地 Evidence 所有权，不尝试加载或修复外部消息。
            issues.append(
                _issue(
                    "EVIDENCE_MESSAGE_LINK_DANGLING",
                    link.evidence_id,
                    "消息软引用所属 Evidence 不存在",
                )
            )
    return issues


def _check_evidence_snapshots(snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
    """检查正式记忆来源快照的可用性、结构和引用标识。

    未被正式版本引用的历史证据不参与快照可用性检查。
    本地快照结构不能证明外部消息或其正文真实。
    """
    formal_evidence_ids = {row.evidence_id for row in snapshot.revision_evidence}
    snapshots = {
        (row.stream_id, row.message_id): row for row in snapshot.evidence_snapshots
    }
    issues: list[DoctorIssue] = []
    for link in snapshot.evidence_links:
        if link.evidence_id not in formal_evidence_ids:
            continue
        source = snapshots.get((link.stream_id, link.message_id))
        if source is None:
            issues.append(
                _issue(
                    "EVIDENCE_SNAPSHOT_MISSING",
                    link.evidence_id,
                    "消息软引用没有对应的长期来源快照",
                )
            )
            continue
        payload = source.payload
        if source.redacted_at is not None or (
            isinstance(payload, dict) and payload.get("redacted") is True
        ):
            issues.append(
                _issue(
                    "EVIDENCE_SNAPSHOT_REDACTED",
                    link.evidence_id,
                    "来源快照已标记隐私删除，内容不可读",
                )
            )
            continue
        if not isinstance(payload, dict):
            issues.append(
                _issue(
                    "EVIDENCE_SNAPSHOT_INVALID",
                    link.evidence_id,
                    "来源快照不是 JSON 对象",
                )
            )
            continue
        reference = (
            str(payload.get("stream_id") or ""),
            str(payload.get("message_id") or ""),
        )
        if reference != (link.stream_id, link.message_id):
            issues.append(
                _issue(
                    "EVIDENCE_SNAPSHOT_REFERENCE_MISMATCH",
                    link.evidence_id,
                    "来源快照标识与消息软引用不一致",
                )
            )
            continue
        missing_fields: list[str] = []
        if payload.get("time") is None:
            missing_fields.append("time")
        if not any(
            payload.get(field)
            for field in ("sender_id", "person_id", "sender_name", "speaker")
        ):
            missing_fields.append("sender")
        if not any(
            payload.get(field) for field in ("processed_plain_text", "content", "text")
        ):
            missing_fields.append("message_text")
        if missing_fields:
            issues.append(
                _issue(
                    "EVIDENCE_SNAPSHOT_FIELDS_MISSING",
                    link.evidence_id,
                    "来源快照缺少必要字段: " + ", ".join(missing_fields),
                )
            )
    return issues


def _check_merge_cycles(snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
    """检查未撤回 MERGED_INTO 关系是否形成有向环。"""
    memory_ids = {row.memory_id for row in snapshot.memories}
    active = tuple(
        row
        for row in snapshot.relations
        if row.relation_type == RelationType.MERGED_INTO and row.retracted_at is None
    )
    issues: list[DoctorIssue] = []
    for relation in active:
        if relation.source_memory_id not in memory_ids:
            issues.append(
                _issue(
                    "MERGE_ENDPOINT_DANGLING",
                    relation.relation_id,
                    f"source Memory {relation.source_memory_id} 不存在",
                )
            )
        if relation.target_memory_id not in memory_ids:
            issues.append(
                _issue(
                    "MERGE_ENDPOINT_DANGLING",
                    relation.relation_id,
                    f"target Memory {relation.target_memory_id} 不存在",
                )
            )

    components = _strongly_connected_components(active)
    component_by_memory = {
        memory_id: component for component in components for memory_id in component
    }
    for relation in active:
        source_component = component_by_memory.get(relation.source_memory_id)
        target_component = component_by_memory.get(relation.target_memory_id)
        if source_component is None or source_component != target_component:
            continue
        if (
            len(source_component) == 1
            and relation.source_memory_id != relation.target_memory_id
        ):
            continue
        issues.append(
            _issue(
                "MERGE_CYCLE",
                relation.relation_id,
                f"MERGED_INTO 关系形成环: {relation.source_memory_id} -> {relation.target_memory_id}",
            )
        )
    return issues


def _check_retrieval_entries(snapshot: _CanonicalSnapshot) -> list[DoctorIssue]:
    """检查核心 Retrieval Entry 是否能由 Canonical 内容确定重建。"""
    memories = {row.memory_id: row for row in snapshot.memories}
    revisions_by_id = {row.revision_id: row for row in snapshot.revisions}
    revisions_by_memory = _revisions_by_memory(snapshot.revisions)
    current_by_memory = {
        memory_id: _owned_revision(memory, revisions_by_memory.get(memory_id, ()))
        for memory_id, memory in memories.items()
    }
    entries_by_memory = _group_by(
        snapshot.retrieval_entries,
        lambda row: row.memory_id,
    )
    issues: list[DoctorIssue] = []

    for entry in snapshot.retrieval_entries:
        memory = memories.get(entry.memory_id)
        if memory is None:
            issues.append(
                _issue(
                    "RETRIEVAL_ENTRY_DANGLING",
                    entry.entry_id,
                    f"Entry 指向不存在的 Memory {entry.memory_id}",
                    True,
                )
            )
            continue

        structural_issue = False
        if entry.entry_type in _REVISION_ENTRY_TYPES:
            if entry.revision_id is None:
                structural_issue = True
            else:
                revision = revisions_by_id.get(entry.revision_id)
                if revision is None or revision.memory_id != entry.memory_id:
                    structural_issue = True
        if structural_issue:
            issues.append(
                _issue(
                    "RETRIEVAL_ENTRY_OWNERSHIP",
                    entry.entry_id,
                    "Entry 的 memory_id、revision_id 或 entry_type 归属不一致",
                    True,
                )
            )

        if entry.content_hash != _content_hash(entry.text):
            issues.append(
                _issue(
                    "RETRIEVAL_ENTRY_CONTENT_DRIFT",
                    entry.entry_id,
                    "Entry content_hash 与自身文本不一致",
                    True,
                )
            )
        if (
            entry.entry_type in _CORE_ENTRY_TYPES
            and entry.generator_version != RETRIEVAL_GENERATOR_VERSION
        ):
            issues.append(
                _issue(
                    "RETRIEVAL_ENTRY_GENERATOR_DRIFT",
                    entry.entry_id,
                    f"generator_version={entry.generator_version!r} 不是当前版本",
                    True,
                )
            )

        current = current_by_memory.get(memory.memory_id)
        if current is None or structural_issue:
            continue
        expected_by_key = {
            (item[0], item[1]): item[2]
            for item in _expected_entries(
                revisions_by_memory.get(memory.memory_id, ()),
                current,
            )
        }
        expected_text = expected_by_key.get((entry.entry_type, entry.revision_id))
        if expected_text is None and entry.entry_type in _CORE_ENTRY_TYPES:
            issues.append(
                _issue(
                    "RETRIEVAL_ENTRY_CONTENT_DRIFT",
                    entry.entry_id,
                    "Entry 类型或 Revision 键与 Canonical 入口不一致",
                    True,
                )
            )
        elif expected_text is not None and (
            entry.text != expected_text
            or entry.content_hash != _content_hash(expected_text)
        ):
            issues.append(
                _issue(
                    "RETRIEVAL_ENTRY_CONTENT_DRIFT",
                    entry.entry_id,
                    "Entry 文本或 content_hash 与 Canonical 内容不一致",
                    True,
                )
            )

    for memory_id, memory in memories.items():
        current = current_by_memory[memory_id]
        if current is None:
            continue
        expected = _expected_entries(
            revisions_by_memory.get(memory_id, ()),
            current,
        )
        entries = entries_by_memory.get(memory_id, [])
        for entry_type, revision_id, _text_value in expected:
            keyed = [
                entry
                for entry in entries
                if entry.entry_type == entry_type and entry.revision_id == revision_id
            ]
            if not keyed:
                issues.append(
                    _issue(
                        "RETRIEVAL_ENTRY_MISSING",
                        memory_id,
                        f"缺少 {entry_type.value} Retrieval Entry",
                        True,
                    )
                )
            elif len(keyed) > 1:
                for duplicate in sorted(keyed, key=lambda item: item.entry_id)[1:]:
                    issues.append(
                        _issue(
                            "RETRIEVAL_ENTRY_DUPLICATE",
                            duplicate.entry_id,
                            f"重复的 {entry_type.value} Retrieval Entry",
                            True,
                        )
                    )
    return issues


def _check_outbox(
    snapshot: _CanonicalSnapshot,
    embedding_model_id: str,
) -> list[DoctorIssue]:
    """检查 Outbox 目标、当前工作项和最新同步记录。"""
    entries = {row.entry_id: row for row in snapshot.retrieval_entries}
    latest_by_entry = _latest_outbox_by_entry(snapshot.outbox)
    issues: list[DoctorIssue] = []
    for outbox in snapshot.outbox:
        if outbox.object_type is not OutboxObjectType.RETRIEVAL_ENTRY:
            issues.append(
                _issue(
                    "OUTBOX_OBJECT_TYPE_UNSUPPORTED",
                    outbox.outbox_id,
                    f"不支持的 Outbox object_type={outbox.object_type.value}",
                )
            )
            continue
        entry = entries.get(outbox.object_id)
        if entry is None:
            issues.append(
                _issue(
                    "OUTBOX_ENTRY_DANGLING",
                    outbox.outbox_id,
                    f"Outbox 指向不存在的 Retrieval Entry {outbox.object_id}",
                )
            )
            continue
        if outbox.status == OutboxStatus.FAILED:
            issues.append(
                _issue(
                    "OUTBOX_FAILED",
                    outbox.outbox_id,
                    f"Outbox 已失败 {outbox.attempt_count} 次: {outbox.last_error or '无错误详情'}",
                    True,
                )
            )

        is_current_work = outbox.status in _ACTIVE_OUTBOX_STATUSES
        is_latest_done_upsert = (
            outbox.status is OutboxStatus.DONE
            and outbox.operation is OutboxOperation.UPSERT
            and latest_by_entry.get(outbox.object_id) is outbox
        )
        if not is_current_work and not is_latest_done_upsert:
            continue
        if outbox.content_hash != entry.content_hash:
            issues.append(
                _issue(
                    "OUTBOX_CONTENT_DRIFT",
                    outbox.outbox_id,
                    f"Outbox hash={outbox.content_hash!r}，Entry hash={entry.content_hash!r}",
                    True,
                )
            )
        if outbox.embedding_model_id != embedding_model_id:
            issues.append(
                _issue(
                    "OUTBOX_MODEL_DRIFT",
                    outbox.outbox_id,
                    f"Outbox embedding_model_id={outbox.embedding_model_id!r}，"
                    f"当前期望={embedding_model_id!r}",
                    True,
                )
            )
    return issues


def _latest_outbox_by_entry(
    outbox_rows: Iterable[VectorOutboxModel],
) -> dict[str, VectorOutboxModel]:
    """返回每个 Retrieval Entry 最近一次 Outbox 操作。"""
    latest: dict[str, VectorOutboxModel] = {}
    for row in outbox_rows:
        if row.object_type is not OutboxObjectType.RETRIEVAL_ENTRY:
            continue
        previous = latest.get(row.object_id)
        if previous is None or _outbox_order_key(row) > _outbox_order_key(previous):
            latest[row.object_id] = row
    return latest


def _outbox_order_key(
    row: VectorOutboxModel,
) -> tuple[datetime, datetime, str]:
    """返回用于判断 Outbox 新旧的稳定排序键。"""
    return row.updated_at, row.created_at, row.outbox_id


def _check_manifests(
    snapshot: _CanonicalSnapshot,
    expected: tuple[str, int, str],
) -> list[DoctorIssue]:
    """检查向量索引清单是否存在唯一且参数匹配的 ACTIVE 版本。"""
    if not _manifest_drift(snapshot.manifests, expected):
        return []
    return [
        _issue(
            "MANIFEST_DRIFT",
            "vector-index-manifest",
            "ACTIVE Manifest 缺失、重复、未激活或参数与当前运行时不一致",
            True,
        )
    ]


def _manifest_drift(
    manifests: Iterable[VectorIndexManifestModel],
    expected: tuple[str, int, str] | None,
) -> bool:
    """判断 Manifest 是否偏离给定期望；None 仅检查结构性漂移。"""
    rows = tuple(manifests)
    active = tuple(row for row in rows if row.status == VectorIndexStatus.ACTIVE)
    if len(active) != 1:
        return True
    current = active[0]
    if current.activated_at is None or current.embedding_dimension <= 0:
        return True
    if expected is None:
        return False
    return (
        current.embedding_model_id,
        current.embedding_dimension,
        current.retrieval_schema_version,
    ) != expected


def _revisions_by_memory(
    revisions: Iterable[MemoryRevisionModel],
) -> dict[str, tuple[MemoryRevisionModel, ...]]:
    """按 Memory 聚合并稳定排序 Revision。"""
    grouped: dict[str, list[MemoryRevisionModel]] = {}
    for revision in revisions:
        grouped.setdefault(revision.memory_id, []).append(revision)
    return {
        memory_id: tuple(
            sorted(rows, key=lambda row: (row.revision_no, row.revision_id))
        )
        for memory_id, rows in grouped.items()
    }


def _expected_entries(
    revisions: Iterable[MemoryRevisionModel],
    current: MemoryRevisionModel,
) -> tuple[tuple[RetrievalEntryType, str | None, str], ...]:
    """从完整 Revision 历史生成核心入口集合。"""
    expected: list[tuple[RetrievalEntryType, str | None, str]] = []
    for revision in revisions:
        entry_type = (
            RetrievalEntryType.CURRENT_REVISION
            if revision.revision_id == current.revision_id
            else RetrievalEntryType.HISTORICAL_REVISION
        )
        expected.append(
            (
                entry_type,
                revision.revision_id,
                f"{revision.title}\n{revision.content}",
            )
        )
    return tuple(expected)


def _owned_revision(
    memory: MemoryModel,
    revisions: Iterable[MemoryRevisionModel],
) -> MemoryRevisionModel | None:
    """返回指针存在且归属于 Memory 的当前 Revision。"""
    return next(
        (
            revision
            for revision in revisions
            if revision.revision_id == memory.current_revision_id
            and revision.memory_id == memory.memory_id
        ),
        None,
    )


def _canonical_entry_memory_id(
    entry: MemoryRetrievalEntryModel,
    memories: dict[str, MemoryModel],
    revisions: dict[str, MemoryRevisionModel],
) -> str:
    """按可验证的 Revision owner 返回派生入口的 Canonical Memory ID。"""
    if entry.revision_id is not None:
        revision = revisions.get(entry.revision_id)
        if revision is not None and revision.memory_id in memories:
            return revision.memory_id
    return entry.memory_id


def _entry_key(
    entry: MemoryRetrievalEntryModel,
) -> tuple[RetrievalEntryType, str | None]:
    """返回派生入口的稳定逻辑键。"""
    return entry.entry_type, entry.revision_id


def _new_retrieval_entry(
    memory_id: str,
    entry_type: RetrievalEntryType,
    revision_id: str | None,
    text_value: str,
) -> MemoryRetrievalEntryModel:
    """创建一条带确定性正文摘要的 Retrieval Entry。"""
    return MemoryRetrievalEntryModel(
        entry_id=str(uuid4()),
        memory_id=memory_id,
        revision_id=revision_id,
        entry_type=entry_type,
        text=text_value,
        content_hash=_content_hash(text_value),
        generator_version=RETRIEVAL_GENERATOR_VERSION,
        created_at=datetime.now(UTC),
    )


def _revision_cycle_nodes(
    revisions: dict[str, MemoryRevisionModel],
) -> set[str]:
    """返回参与 parent_revision_id 环的 Revision 标识。"""
    cycle_nodes: set[str] = set()
    for start_id in revisions:
        path: list[str] = []
        positions: dict[str, int] = {}
        current_id: str | None = start_id
        while current_id is not None and current_id in revisions:
            if current_id in positions:
                cycle_nodes.update(path[positions[current_id] :])
                break
            positions[current_id] = len(path)
            path.append(current_id)
            current_id = revisions[current_id].parent_revision_id
    return cycle_nodes


def _strongly_connected_components(
    relations: tuple[MemoryRelationModel, ...],
) -> tuple[frozenset[str], ...]:
    """计算活动合并图的强连通分量。"""
    adjacency: dict[str, list[str]] = {}
    for relation in relations:
        adjacency.setdefault(relation.source_memory_id, []).append(
            relation.target_memory_id
        )
        adjacency.setdefault(relation.target_memory_id, [])

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[frozenset[str]] = []

    def visit(node: str) -> None:
        """深度优先访问一个合并图节点。"""
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency.get(node, []):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        members: set[str] = set()
        while True:
            member = stack.pop()
            on_stack.remove(member)
            members.add(member)
            if member == node:
                break
        components.append(frozenset(members))

    for node in adjacency:
        if node not in indices:
            visit(node)
    return tuple(components)


def _group_by(
    items: Iterable[_ModelT],
    key: Callable[[_ModelT], str],
) -> dict[str, list[_ModelT]]:
    """按字符串键聚合 ORM 行。"""
    result: dict[str, list[_ModelT]] = {}
    for item in items:
        result.setdefault(key(item), []).append(item)
    return result


def _issue(
    code: str,
    object_id: str,
    details: str,
    repairable: bool = False,
) -> DoctorIssue:
    """构造一致性问题。"""
    return DoctorIssue(code, object_id, repairable, details)


def _content_hash(text_value: str) -> str:
    """计算 Retrieval Entry 的确定性内容摘要。"""
    return sha256(text_value.encode("utf-8")).hexdigest()


def _unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    """按首次出现顺序去重字符串。"""
    return tuple(dict.fromkeys(values))
