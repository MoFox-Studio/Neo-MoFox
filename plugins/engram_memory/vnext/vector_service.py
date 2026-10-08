"""Engram Memory vNext 向量派生索引服务。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import case, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from src.app.plugin_system.api import log_api

from .domain import VectorUpsert
from .enums import (
    MemoryStatus,
    OutboxObjectType,
    OutboxOperation,
    OutboxStatus,
    VectorIndexStatus,
)
from .models import (
    MemoryModel,
    MemoryRetrievalEntryModel,
    VectorIndexManifestModel,
    VectorOutboxModel,
)
from .schema import VNextSchema

MAX_OUTBOX_ATTEMPTS = 3
PROCESSING_STALE_SECONDS = 300
DEFAULT_VECTOR_UPSERT_BATCH_SIZE = 32


@dataclass(frozen=True, slots=True)
class _PreparedOutbox:
    """已认领并完成正式检索入口校验的向量投递动作。"""

    outbox_id: str
    claim_token: str
    entry_id: str
    index_id: str
    embedding_model_id: str
    embedding_dimension: int
    upsert: VectorUpsert | None


class VectorSink:
    """向量索引写入端接口。"""

    @property
    def supports_batch_upsert(self) -> bool:
        """返回写入端是否支持通过一次后端请求写入整批入口。"""
        return False

    async def upsert(self, item: VectorUpsert) -> None:
        """写入或更新向量入口。

        参数:
            item: 待写入的入口文本与元数据。
        """
        raise NotImplementedError

    async def upsert_many(self, items: Sequence[VectorUpsert]) -> None:
        """逐条写入整批入口，写入端可覆盖此方法以使用后端批量请求。"""
        for item in items:
            await self.upsert(item)

    async def delete(self, entry_id: str) -> None:
        """删除向量入口。

        参数:
            entry_id: 检索入口标识。
        """
        raise NotImplementedError

    def for_index(
        self,
        index_id: str,
        embedding_model_id: str,
        embedding_dimension: int,
    ) -> VectorSink:
        """返回绑定指定索引清单的写入端视图。

        内存写入端可返回自身，持久化写入端需覆盖此方法，
        确保投递项写入其索引清单对应的物理集合。
        """
        del index_id, embedding_model_id, embedding_dimension
        return self

    async def entry_ids(self) -> frozenset[str] | None:
        """返回物理索引的入口 ID；写入端不支持查询时返回 None。"""
        return None


class VectorIndexService:
    """处理 Outbox、维护派生向量索引与版本清单。"""

    def __init__(
        self,
        schema: VNextSchema,
        sink: VectorSink,
        *,
        max_attempts: int = MAX_OUTBOX_ATTEMPTS,
        upsert_batch_size: int = DEFAULT_VECTOR_UPSERT_BATCH_SIZE,
    ) -> None:
        """绑定 Schema 与向量写入端。"""
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts <= 0
        ):
            raise ValueError("max_attempts 必须是正整数")
        if (
            isinstance(upsert_batch_size, bool)
            or not isinstance(upsert_batch_size, int)
            or upsert_batch_size <= 0
        ):
            raise ValueError("upsert_batch_size 必须是正整数")
        self._schema = schema
        self._sink = sink
        self._max_attempts = max_attempts
        self._upsert_batch_size = upsert_batch_size

    async def physical_entry_ids(
        self,
        index_id: str,
        embedding_model_id: str,
        embedding_dimension: int,
    ) -> frozenset[str] | None:
        """查询指定索引清单对应的物理入口 ID；不支持时返回 None。"""
        sink = self._sink.for_index(index_id, embedding_model_id, embedding_dimension)
        return await sink.entry_ids()

    async def reconcile_satisfied_outbox(self, index_id: str) -> tuple[str, ...]:
        """将已被完整 ACTIVE 物理索引覆盖的激活前工作项标记为完成。

        只有物理 ID 集合与当前全部 ACTIVE 正式记忆检索入口完全一致，
        且工作项早于索引清单激活、模型和内容摘要均匹配时才会对账。
        """
        if not index_id.strip():
            raise ValueError("index_id 不能为空")
        async with self._schema.database.session() as session:
            manifest = await session.get(VectorIndexManifestModel, index_id)
            if (
                manifest is None
                or manifest.status is not VectorIndexStatus.ACTIVE
                or manifest.activated_at is None
            ):
                return ()
            embedding_model_id = manifest.embedding_model_id
            embedding_dimension = manifest.embedding_dimension
            activated_at = manifest.activated_at
            entries = tuple(
                (
                    await session.scalars(
                        select(MemoryRetrievalEntryModel)
                        .join(
                            MemoryModel,
                            MemoryModel.memory_id
                            == MemoryRetrievalEntryModel.memory_id,
                        )
                        .where(MemoryModel.status == MemoryStatus.ACTIVE)
                    )
                ).all()
            )
        entries_by_id = {entry.entry_id: entry for entry in entries}
        physical_ids = await self.physical_entry_ids(
            index_id,
            embedding_model_id,
            embedding_dimension,
        )
        if physical_ids is None or physical_ids != frozenset(entries_by_id):
            return ()

        reconciled: list[str] = []
        async with self._schema.database.session() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(VectorOutboxModel).where(
                            VectorOutboxModel.status.in_(
                                (
                                    OutboxStatus.PENDING,
                                    OutboxStatus.PROCESSING,
                                    OutboxStatus.FAILED,
                                )
                            ),
                            VectorOutboxModel.created_at <= activated_at,
                            VectorOutboxModel.embedding_model_id == embedding_model_id,
                        )
                    )
                ).all()
            )
            now = datetime.now(UTC)
            for outbox in rows:
                if outbox.index_id not in (None, index_id):
                    continue
                entry = entries_by_id.get(outbox.object_id)
                satisfied = (
                    outbox.operation is OutboxOperation.UPSERT
                    and entry is not None
                    and outbox.content_hash == entry.content_hash
                ) or (outbox.operation is OutboxOperation.DELETE and entry is None)
                if not satisfied:
                    continue
                outbox.index_id = index_id
                outbox.status = OutboxStatus.DONE
                outbox.claim_token = None
                outbox.last_error = None
                outbox.updated_at = now
                reconciled.append(outbox.outbox_id)
        return tuple(reconciled)

    async def process_pending_outbox(
        self,
        limit: int = 20,
        *,
        index_id: str | None = None,
        outbox_ids: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        """投递待处理 Outbox，返回本轮处理成功的入口 ID。

        参数:
            limit: 单轮最多处理的工作项数量。

        返回:
            成功投递的 entry_id 元组。
        """
        if limit <= 0:
            raise ValueError("outbox limit 必须大于 0")
        if index_id is not None and not index_id.strip():
            raise ValueError("index_id 不能为空")
        if outbox_ids is not None:
            if not outbox_ids:
                return ()
            if any(
                not isinstance(item, str) or not item.strip() for item in outbox_ids
            ):
                raise ValueError("outbox_ids 只能包含非空字符串")
        succeeded: list[str] = []
        async with self._schema.database.session() as session:
            stale_before = datetime.now(UTC) - timedelta(
                seconds=PROCESSING_STALE_SECONDS
            )
            stale_statement = update(VectorOutboxModel).where(
                VectorOutboxModel.status == OutboxStatus.PROCESSING,
                VectorOutboxModel.updated_at < stale_before,
            )
            if index_id is not None:
                stale_statement = stale_statement.where(
                    VectorOutboxModel.index_id == index_id
                )
            if outbox_ids is not None:
                stale_statement = stale_statement.where(
                    VectorOutboxModel.outbox_id.in_(outbox_ids)
                )
            await session.execute(
                stale_statement.values(
                    status=OutboxStatus.PENDING,
                    claim_token=None,
                    updated_at=datetime.now(UTC),
                )
            )
            pending_statement = select(VectorOutboxModel.outbox_id).where(
                VectorOutboxModel.status == OutboxStatus.PENDING
            )
            if index_id is not None:
                pending_statement = pending_statement.where(
                    VectorOutboxModel.index_id == index_id
                )
            if outbox_ids is not None:
                pending_statement = pending_statement.where(
                    VectorOutboxModel.outbox_id.in_(outbox_ids)
                )
            pending_ids = tuple(
                (
                    await session.scalars(
                        pending_statement.order_by(
                            VectorOutboxModel.created_at, VectorOutboxModel.outbox_id
                        ).limit(limit)
                    )
                ).all()
            )
        prepared_actions: list[_PreparedOutbox] = []
        for outbox_id in pending_ids:
            claim_token = str(uuid4())
            async with self._schema.database.session() as session:
                active_manifest = (
                    await session.scalars(
                        select(VectorIndexManifestModel)
                        .where(
                            VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE
                        )
                        .order_by(VectorIndexManifestModel.created_at.desc())
                    )
                ).first()
                claimed = await session.execute(
                    update(VectorOutboxModel)
                    .where(
                        VectorOutboxModel.outbox_id == outbox_id,
                        VectorOutboxModel.status == OutboxStatus.PENDING,
                    )
                    .values(
                        status=OutboxStatus.PROCESSING,
                        claim_token=claim_token,
                        index_id=(
                            case(
                                (
                                    VectorOutboxModel.index_id.is_(None),
                                    active_manifest.index_id,
                                ),
                                else_=VectorOutboxModel.index_id,
                            )
                            if active_manifest is not None
                            else VectorOutboxModel.index_id
                        ),
                        updated_at=datetime.now(UTC),
                    )
                    .execution_options(synchronize_session=False)
                )
                if cast(CursorResult[Any], claimed).rowcount != 1:
                    continue
            async with self._schema.database.session() as session:
                outbox = await session.get(VectorOutboxModel, outbox_id)
                if outbox is None:
                    continue
                if (
                    outbox.status is not OutboxStatus.PROCESSING
                    or outbox.claim_token != claim_token
                ):
                    continue
                target_index = (
                    await session.get(VectorIndexManifestModel, outbox.index_id)
                    if outbox.index_id is not None
                    else None
                )
                if outbox.index_id is None and target_index is None:
                    outbox.status = OutboxStatus.PENDING
                    outbox.last_error = (
                        "没有 ACTIVE Vector Manifest，等待索引 bootstrap"
                    )
                    outbox.claim_token = None
                    outbox.updated_at = datetime.now(UTC)
                    continue
                if outbox.index_id is not None and target_index is None:
                    outbox.status = OutboxStatus.FAILED
                    outbox.last_error = "目标 Vector Manifest 不存在"
                    outbox.claim_token = None
                    outbox.updated_at = datetime.now(UTC)
                    continue
                if target_index is not None and target_index.status not in (
                    VectorIndexStatus.ACTIVE,
                    VectorIndexStatus.BUILDING,
                ):
                    outbox.status = OutboxStatus.FAILED
                    outbox.last_error = "目标 Vector Manifest 不可写"
                    outbox.claim_token = None
                    outbox.updated_at = datetime.now(UTC)
                    continue
                if (
                    target_index is not None
                    and outbox.embedding_model_id != target_index.embedding_model_id
                ):
                    outbox.status = OutboxStatus.FAILED
                    outbox.last_error = "Outbox 模型与目标 Vector Manifest 不匹配"
                    outbox.claim_token = None
                    outbox.updated_at = datetime.now(UTC)
                    continue
                entry = await session.get(MemoryRetrievalEntryModel, outbox.object_id)
                if entry is None and outbox.operation is OutboxOperation.UPSERT:
                    outbox.status = OutboxStatus.FAILED
                    outbox.last_error = "Retrieval Entry 不存在"
                    outbox.claim_token = None
                    outbox.updated_at = datetime.now(UTC)
                    continue
                entry_id = outbox.object_id
                upsert: VectorUpsert | None = None
                should_upsert = False
                if entry is not None:
                    memory = await session.get(MemoryModel, entry.memory_id)
                    upsert = VectorUpsert(
                        entry_id=entry.entry_id,
                        memory_id=entry.memory_id,
                        revision_id=entry.revision_id,
                        text=entry.text,
                        content_hash=entry.content_hash,
                    )
                    should_upsert = (
                        outbox.operation is OutboxOperation.UPSERT
                        and memory is not None
                        and memory.status is MemoryStatus.ACTIVE
                    )
                if target_index is None:
                    raise RuntimeError("已校验的 Outbox 缺少目标 Manifest")
                prepared_actions.append(
                    _PreparedOutbox(
                        outbox_id=outbox_id,
                        claim_token=claim_token,
                        entry_id=entry_id,
                        index_id=target_index.index_id,
                        embedding_model_id=target_index.embedding_model_id,
                        embedding_dimension=target_index.embedding_dimension,
                        upsert=upsert if should_upsert else None,
                    )
                )

        async def mark_succeeded(action: _PreparedOutbox) -> None:
            """校验认领令牌后，将对应投递项标记为完成。"""
            async with self._schema.database.session() as session:
                outbox = await session.get(VectorOutboxModel, action.outbox_id)
                if (
                    outbox is None
                    or outbox.status is not OutboxStatus.PROCESSING
                    or outbox.claim_token != action.claim_token
                ):
                    return
                outbox.status = OutboxStatus.DONE
                outbox.last_error = None
                outbox.claim_token = None
                outbox.updated_at = datetime.now(UTC)
            succeeded.append(action.entry_id)

        async def mark_failed(action: _PreparedOutbox, error: Exception) -> None:
            """记录投递失败，并按尝试次数上限决定是否重试。"""
            async with self._schema.database.session() as session:
                outbox = await session.get(VectorOutboxModel, action.outbox_id)
                if (
                    outbox is None
                    or outbox.status is not OutboxStatus.PROCESSING
                    or outbox.claim_token != action.claim_token
                ):
                    return
                outbox.attempt_count += 1
                outbox.last_error = str(error)
                outbox.updated_at = datetime.now(UTC)
                outbox.claim_token = None
                outbox.status = (
                    OutboxStatus.FAILED
                    if outbox.attempt_count >= self._max_attempts
                    else OutboxStatus.PENDING
                )

        async def deliver_upserts(batch: list[_PreparedOutbox]) -> None:
            """向同一物理索引投递一批 UPSERT 并逐条落状态。"""
            if not batch:
                return
            first = batch[0]
            target_sink = self._sink.for_index(
                first.index_id,
                first.embedding_model_id,
                first.embedding_dimension,
            )
            if not target_sink.supports_batch_upsert:
                for action in batch:
                    if action.upsert is None:
                        continue
                    try:
                        await target_sink.upsert(action.upsert)
                    except Exception as error:  # noqa: BLE001
                        await mark_failed(action, error)
                    else:
                        await mark_succeeded(action)
                return
            try:
                await target_sink.upsert_many(
                    tuple(
                        action.upsert for action in batch if action.upsert is not None
                    )
                )
            except Exception as error:  # noqa: BLE001
                for action in batch:
                    await mark_failed(action, error)
                return
            for action in batch:
                await mark_succeeded(action)

        upsert_batch: list[_PreparedOutbox] = []
        batch_key: tuple[str, str, int] | None = None
        for action in prepared_actions:
            action_key = (
                action.index_id,
                action.embedding_model_id,
                action.embedding_dimension,
            )
            if action.upsert is not None:
                if batch_key is not None and action_key != batch_key:
                    await deliver_upserts(upsert_batch)
                    upsert_batch = []
                batch_key = action_key
                upsert_batch.append(action)
                if len(upsert_batch) >= self._upsert_batch_size:
                    await deliver_upserts(upsert_batch)
                    upsert_batch = []
                    batch_key = None
                continue
            await deliver_upserts(upsert_batch)
            upsert_batch = []
            batch_key = None
            target_sink = self._sink.for_index(*action_key)
            try:
                await target_sink.delete(action.entry_id)
            except Exception as error:  # noqa: BLE001
                await mark_failed(action, error)
            else:
                await mark_succeeded(action)
        await deliver_upserts(upsert_batch)
        return tuple(succeeded)

    async def retry_failed_outbox(self, limit: int = 20) -> tuple[str, ...]:
        """重置 FAILED 工作项为 PENDING 并立即投递一轮。

        参数:
            limit: 单轮最多重置的工作项数量。

        返回:
            本轮成功投递的 entry_id 元组。
        """
        if limit <= 0:
            raise ValueError("outbox limit 必须大于 0")
        async with self._schema.database.session() as session:
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
            failed = list(
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
            selected = 0
            selected_outbox_ids: list[str] = []
            for outbox in failed:
                target = (
                    manifests.get(outbox.index_id)
                    if outbox.index_id is not None
                    else active_manifests[0]
                    if len(active_manifests) == 1
                    else None
                )
                if (
                    target is None
                    or target.status
                    not in (VectorIndexStatus.ACTIVE, VectorIndexStatus.BUILDING)
                    or target.embedding_model_id != outbox.embedding_model_id
                ):
                    continue
                if selected >= limit:
                    break
                outbox.status = OutboxStatus.PENDING
                outbox.attempt_count = 0
                outbox.last_error = None
                outbox.claim_token = None
                outbox.updated_at = datetime.now(UTC)
                selected += 1
                selected_outbox_ids.append(outbox.outbox_id)
        if not selected_outbox_ids:
            return ()
        return await self.process_pending_outbox(
            len(selected_outbox_ids), outbox_ids=tuple(selected_outbox_ids)
        )

    async def activate_manifest(
        self,
        embedding_model_id: str,
        embedding_dimension: int,
        retrieval_schema_version: str,
        flashback_threshold: float | None = None,
    ) -> str:
        """仅为没有生效索引和正式检索入口的数据库直接激活清单，否则完整重建。"""
        if not embedding_model_id.strip():
            raise ValueError("embedding_model_id 不能为空")
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension 必须大于 0")
        if not retrieval_schema_version.strip():
            raise ValueError("retrieval_schema_version 不能为空")
        async with self._schema.database.session() as session:
            has_active_manifest = (
                await session.scalar(
                    select(VectorIndexManifestModel.index_id)
                    .where(VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE)
                    .limit(1)
                )
                is not None
            )
            has_canonical_entries = (
                await session.scalar(
                    select(MemoryRetrievalEntryModel.entry_id)
                    .join(
                        MemoryModel,
                        MemoryModel.memory_id == MemoryRetrievalEntryModel.memory_id,
                    )
                    .where(MemoryModel.status == MemoryStatus.ACTIVE)
                    .limit(1)
                )
                is not None
            )
        if has_active_manifest or has_canonical_entries:
            return (
                await self.rebuild_entire_index(
                    embedding_model_id,
                    embedding_dimension,
                    retrieval_schema_version,
                    flashback_threshold,
                )
            )[0]
        now = datetime.now(UTC)
        index_id = str(uuid4())
        try:
            async with self._schema.database.session() as session:
                session.add(
                    VectorIndexManifestModel(
                        index_id=index_id,
                        index_version=str(uuid4()),
                        embedding_model_id=embedding_model_id,
                        embedding_dimension=embedding_dimension,
                        retrieval_schema_version=retrieval_schema_version,
                        flashback_threshold=flashback_threshold,
                        created_at=now,
                        activated_at=now,
                        status=VectorIndexStatus.ACTIVE,
                    )
                )
        except IntegrityError:
            async with self._schema.database.session() as session:
                active = (
                    await session.scalars(
                        select(VectorIndexManifestModel)
                        .where(
                            VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE
                        )
                        .order_by(VectorIndexManifestModel.created_at.desc())
                    )
                ).first()
            if active is not None and (
                active.embedding_model_id == embedding_model_id
                and active.embedding_dimension == embedding_dimension
                and active.retrieval_schema_version == retrieval_schema_version
            ):
                return active.index_id
            if active is not None:
                return await self.ensure_active_manifest(
                    embedding_model_id,
                    embedding_dimension,
                    retrieval_schema_version,
                    flashback_threshold,
                )
            raise
        return index_id

    async def ensure_active_manifest(
        self,
        embedding_model_id: str,
        embedding_dimension: int,
        retrieval_schema_version: str,
        flashback_threshold: float | None = None,
    ) -> str:
        """返回参数匹配的 ACTIVE 索引清单，或重建并校验索引。

        已有生效索引参数不匹配，或已有正式检索入口时，需完成
        BUILDING、校验、ACTIVE 流程，不能用空清单替代已有索引。
        ACTIVE 状态的部分唯一索引约束并发初始化。
        """
        if not embedding_model_id.strip():
            raise ValueError("embedding_model_id 不能为空")
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension 必须大于 0")
        if not retrieval_schema_version.strip():
            raise ValueError("retrieval_schema_version 不能为空")
        async with self._schema.database.session() as session:
            active = (
                await session.scalars(
                    select(VectorIndexManifestModel)
                    .where(VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE)
                    .order_by(VectorIndexManifestModel.created_at.desc())
                )
            ).first()
            if active is not None and (
                active.embedding_model_id == embedding_model_id
                and active.embedding_dimension == embedding_dimension
                and active.retrieval_schema_version == retrieval_schema_version
            ):
                return active.index_id
            has_canonical_entries = (
                await session.scalar(
                    select(MemoryRetrievalEntryModel.entry_id)
                    .join(
                        MemoryModel,
                        MemoryModel.memory_id == MemoryRetrievalEntryModel.memory_id,
                    )
                    .where(MemoryModel.status == MemoryStatus.ACTIVE)
                    .limit(1)
                )
                is not None
            )
        if active is not None or has_canonical_entries:
            return (
                await self.rebuild_entire_index(
                    embedding_model_id,
                    embedding_dimension,
                    retrieval_schema_version,
                    flashback_threshold,
                )
            )[0]
        return await self.activate_manifest(
            embedding_model_id,
            embedding_dimension,
            retrieval_schema_version,
            flashback_threshold,
        )

    async def _create_building_manifest(
        self,
        embedding_model_id: str,
        embedding_dimension: int,
        retrieval_schema_version: str,
        flashback_threshold: float | None,
    ) -> str:
        """创建尚未对外生效的 BUILDING Manifest。"""
        now = datetime.now(UTC)
        index_id = str(uuid4())
        async with self._schema.database.session() as session:
            session.add(
                VectorIndexManifestModel(
                    index_id=index_id,
                    index_version=str(uuid4()),
                    embedding_model_id=embedding_model_id,
                    embedding_dimension=embedding_dimension,
                    retrieval_schema_version=retrieval_schema_version,
                    flashback_threshold=flashback_threshold,
                    created_at=now,
                    activated_at=None,
                    status=VectorIndexStatus.BUILDING,
                )
            )
        return index_id

    async def _finish_building_manifest(
        self,
        index_id: str,
    ) -> bool:
        """校验本轮 Outbox 完成后激活 Manifest，否则标记 FAILED。"""
        async with self._schema.database.session() as session:
            manifest = await session.get(VectorIndexManifestModel, index_id)
            if manifest is None:
                raise ValueError("BUILDING Manifest 不存在")
            if manifest.status is not VectorIndexStatus.BUILDING:
                return manifest.status is VectorIndexStatus.ACTIVE
            target_rows = tuple(
                (
                    await session.scalars(
                        select(VectorOutboxModel).where(
                            VectorOutboxModel.index_id == index_id
                        )
                    )
                ).all()
            )
            if any(row.status is not OutboxStatus.DONE for row in target_rows) or any(
                row.embedding_model_id != manifest.embedding_model_id
                for row in target_rows
            ):
                manifest.status = VectorIndexStatus.FAILED
                return False
            latest_by_entry: dict[str, VectorOutboxModel] = {}
            for row in sorted(
                target_rows, key=lambda item: (item.created_at, item.outbox_id)
            ):
                latest_by_entry[row.object_id] = row
            active_entries = tuple(
                (
                    await session.scalars(
                        select(MemoryRetrievalEntryModel)
                        .join(
                            MemoryModel,
                            MemoryModel.memory_id
                            == MemoryRetrievalEntryModel.memory_id,
                        )
                        .where(MemoryModel.status == MemoryStatus.ACTIVE)
                    )
                ).all()
            )
            active_entry_ids = {entry.entry_id for entry in active_entries}
            consistent = all(
                (
                    (outbox := latest_by_entry.get(entry.entry_id)) is not None
                    and outbox.operation is OutboxOperation.UPSERT
                    and outbox.content_hash == entry.content_hash
                    and outbox.status is OutboxStatus.DONE
                )
                for entry in active_entries
            ) and all(
                row.operation is not OutboxOperation.UPSERT
                or row.object_id in active_entry_ids
                for row in latest_by_entry.values()
            )
            if not consistent:
                manifest.status = VectorIndexStatus.FAILED
                return False
            target_sink = self._sink.for_index(
                manifest.index_id,
                manifest.embedding_model_id,
                manifest.embedding_dimension,
            )
            try:
                physical_entry_ids = await target_sink.entry_ids()
            except Exception as error:  # noqa: BLE001
                logger = log_api.get_logger("engram_memory.vector_index")
                logger.error(f"无法核对新索引的物理入口，拒绝激活: {type(error).__name__}: {error}")
                physical_entry_ids = None
            if physical_entry_ids is None or frozenset(physical_entry_ids) != frozenset(
                active_entry_ids
            ):
                # 物理入口不可查询或与正式检索入口不一致时，不能激活派生索引。
                manifest.status = VectorIndexStatus.FAILED
                return False
            active = list(
                (
                    await session.scalars(
                        select(VectorIndexManifestModel).where(
                            VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE
                        )
                    )
                ).all()
            )
            for previous in active:
                previous.status = VectorIndexStatus.RETIRED
            await session.flush()
            manifest.status = VectorIndexStatus.ACTIVE
            manifest.activated_at = datetime.now(UTC)
        return True

    async def list_manifests(self) -> tuple[VectorIndexManifestModel, ...]:
        """按创建时间列出全部向量索引清单。"""
        async with self._schema.database.session() as session:
            rows = list(
                (
                    await session.scalars(
                        select(VectorIndexManifestModel).order_by(
                            VectorIndexManifestModel.created_at,
                            VectorIndexManifestModel.index_id,
                        )
                    )
                ).all()
            )
        return tuple(rows)

    async def rebuild_entire_index(
        self,
        embedding_model_id: str,
        embedding_dimension: int,
        retrieval_schema_version: str,
        flashback_threshold: float | None = None,
    ) -> tuple[str, int]:
        """登记新清单并为全部 ACTIVE Memory 的入口排队 UPSERT。

        返回:
            新索引清单 ID 与排队入口数量的元组。
        """
        index_id = await self._create_building_manifest(
            embedding_model_id,
            embedding_dimension,
            retrieval_schema_version,
            flashback_threshold,
        )
        now = datetime.now(UTC)
        outbox_ids: list[str] = []
        async with self._schema.database.session() as session:
            entries = list(
                (
                    await session.scalars(
                        select(MemoryRetrievalEntryModel)
                        .join(
                            MemoryModel,
                            MemoryModel.memory_id
                            == MemoryRetrievalEntryModel.memory_id,
                        )
                        .where(MemoryModel.status == MemoryStatus.ACTIVE)
                    )
                ).all()
            )
            for entry in entries:
                outbox_id = str(uuid4())
                outbox_ids.append(outbox_id)
                session.add(
                    VectorOutboxModel(
                        outbox_id=outbox_id,
                        object_type=OutboxObjectType.RETRIEVAL_ENTRY,
                        object_id=entry.entry_id,
                        operation=OutboxOperation.UPSERT,
                        content_hash=entry.content_hash,
                        embedding_model_id=embedding_model_id,
                        index_id=index_id,
                        status=OutboxStatus.PENDING,
                        attempt_count=0,
                        last_error=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
        tracked_outbox_ids = list(outbox_ids)
        for _ in range(3):
            if tracked_outbox_ids:
                await self.process_pending_outbox(
                    limit=max(len(tracked_outbox_ids), 20),
                    index_id=index_id,
                    outbox_ids=tuple(tracked_outbox_ids),
                )
                replay_ids = await self._requeue_building_drift(index_id)
            else:
                # 初始入口为空时，也需将激活前的正式记忆变更投递到 BUILDING 索引。
                replay_ids = await self._requeue_building_drift(index_id)
            if not replay_ids:
                break
            tracked_outbox_ids.extend(replay_ids)
        if not await self._finish_building_manifest(index_id):
            raise RuntimeError("向量索引构建或校验失败，原生效索引保持不变")
        return index_id, len(entries)

    async def _requeue_building_drift(self, index_id: str) -> list[str]:
        """为索引重建期间变化的正式检索入口补充投递项。"""
        now = datetime.now(UTC)
        replay_ids: list[str] = []
        async with self._schema.database.session() as session:
            manifest = await session.get(VectorIndexManifestModel, index_id)
            if manifest is None:
                raise ValueError("BUILDING Manifest 不存在")
            if manifest.status is not VectorIndexStatus.BUILDING:
                raise ValueError("目标 Manifest 不处于 BUILDING 状态")
            target_rows = tuple(
                (
                    await session.scalars(
                        select(VectorOutboxModel)
                        .where(VectorOutboxModel.index_id == index_id)
                        .order_by(
                            VectorOutboxModel.created_at, VectorOutboxModel.outbox_id
                        )
                    )
                ).all()
            )
            latest_by_entry: dict[str, VectorOutboxModel] = {}
            for row in target_rows:
                latest_by_entry[row.object_id] = row
            active_entries = tuple(
                (
                    await session.scalars(
                        select(MemoryRetrievalEntryModel)
                        .join(
                            MemoryModel,
                            MemoryModel.memory_id
                            == MemoryRetrievalEntryModel.memory_id,
                        )
                        .where(MemoryModel.status == MemoryStatus.ACTIVE)
                    )
                ).all()
            )
            embedding_model_id = manifest.embedding_model_id.strip()
            if not embedding_model_id or manifest.embedding_dimension <= 0:
                raise ValueError("目标 Manifest 的 embedding 参数无效")
            active_entry_ids = {entry.entry_id for entry in active_entries}
            for entry in active_entries:
                previous = latest_by_entry.get(entry.entry_id)
                if (
                    previous is not None
                    and previous.operation is OutboxOperation.UPSERT
                    and previous.content_hash == entry.content_hash
                ):
                    continue
                outbox_id = str(uuid4())
                replay_ids.append(outbox_id)
                session.add(
                    VectorOutboxModel(
                        outbox_id=outbox_id,
                        object_type=OutboxObjectType.RETRIEVAL_ENTRY,
                        object_id=entry.entry_id,
                        operation=OutboxOperation.UPSERT,
                        content_hash=entry.content_hash,
                        embedding_model_id=embedding_model_id,
                        index_id=index_id,
                        status=OutboxStatus.PENDING,
                        attempt_count=0,
                        last_error=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            for object_id, previous in latest_by_entry.items():
                if (
                    previous.operation is not OutboxOperation.UPSERT
                    or object_id in active_entry_ids
                ):
                    continue
                outbox_id = str(uuid4())
                replay_ids.append(outbox_id)
                session.add(
                    VectorOutboxModel(
                        outbox_id=outbox_id,
                        object_type=OutboxObjectType.RETRIEVAL_ENTRY,
                        object_id=object_id,
                        operation=OutboxOperation.DELETE,
                        content_hash=previous.content_hash,
                        embedding_model_id=embedding_model_id,
                        index_id=index_id,
                        status=OutboxStatus.PENDING,
                        attempt_count=0,
                        last_error=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
        return replay_ids
