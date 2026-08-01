"""Booku Memory Store 元数据仓储（核心 CRUD）。

基于 PluginDatabase 与 SQLAlchemy 实现 SQLite 元数据管理。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import delete, distinct, func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.plugin_system.api.storage_api import PluginDatabase

from .dataclasses import BookuMemoryRecord
from .models import BookuMemoryRecordModel, BookuMemoryTagModel
from . import utils


class BookuMemoryMetadataRepository:
    """Booku Memory Store 的 SQLAlchemy + PluginDatabase 元数据仓储。"""

    _MEMORY_BUCKET: str = "memory"
    _KNOWLEDGE_BUCKET: str = "knowledge"

    def __init__(self, db_path: str) -> None:
        self._db = PluginDatabase(
            db_path,
            [BookuMemoryRecordModel, BookuMemoryTagModel],
        )

    async def initialize(self) -> None:
        await self._db.initialize()
        await self._ensure_schema_columns()
        await self._migrate_legacy_bucket_values()

    async def close(self) -> None:
        await self._db.close()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_bucket_value(bucket: str | None) -> str:
        normalized = str(bucket or "").strip().lower()
        if normalized == "knowledge":
            return "knowledge"
        return "memory"

    @staticmethod
    def _is_archived_status(status: str | None) -> bool:
        normalized = str(status or "").strip().lower()
        return normalized in {"archived", "expired"}

    @staticmethod
    def _parse_json_list(raw_value: Any) -> list[str]:
        if isinstance(raw_value, list):
            return [str(item) for item in raw_value if str(item).strip()]
        if not isinstance(raw_value, str) or not raw_value.strip():
            return []
        try:
            parsed = json.loads(raw_value)
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    def _to_record(
        self,
        row: BookuMemoryRecordModel,
        tags_by_id: dict[str, dict[str, list[str]]] | None = None,
    ) -> BookuMemoryRecord:
        td = (tags_by_id or {}).get(row.memory_id, {})
        relation_memory_ids = self._parse_json_list(getattr(row, "relation_memory_ids", "[]"))
        relation_aliases = self._parse_json_list(getattr(row, "relation_aliases", "[]"))
        related_people = self._parse_json_list(getattr(row, "related_people", "[]"))

        return BookuMemoryRecord(
            memory_id=row.memory_id,
            title=row.title or "",
            folder_id=row.folder_id,
            bucket=self._normalize_bucket_value(row.bucket),
            content=row.content,
            source=row.source,
            memory_type=str(getattr(row, "memory_type", "knowledge") or "knowledge"),
            status=str(getattr(row, "status", "active") or "active"),
            person_id=getattr(row, "person_id", None),
            relation_memory_ids=relation_memory_ids,
            relation_aliases=relation_aliases,
            event_start_at=float(getattr(row, "event_start_at", 0.0) or 0.0),
            event_end_at=float(getattr(row, "event_end_at", 0.0) or 0.0),
            related_people=related_people,
            knowledge_type=str(getattr(row, "knowledge_type", "") or ""),
            address_or_coord=str(getattr(row, "address_or_coord", "") or ""),
            place_type=str(getattr(row, "place_type", "") or ""),
            asset_type=str(getattr(row, "asset_type", "") or ""),
            disposition_status=str(getattr(row, "disposition_status", "") or ""),
            procedure_type=str(getattr(row, "procedure_type", "") or ""),
            novelty_energy=float(row.novelty_energy),
            is_archived=self._is_archived_status(getattr(row, "status", "active")),
            is_deleted=bool(row.is_deleted),
            deleted_at=float(row.deleted_at) if row.deleted_at else 0.0,
            created_at=float(row.created_at),
            updated_at=float(row.updated_at),
            last_activated_at=float(row.last_activated_at) if row.last_activated_at else 0.0,
            activation_count=int(row.activation_count) if row.activation_count else 0,
            tags=td.get("tag", []),
            core_tags=td.get("core", []),
            diffusion_tags=td.get("diffusion", []),
            opposing_tags=td.get("opposing", []),
        )

    async def _ensure_schema_columns(self) -> None:
        memory_required_columns: dict[str, str] = {
            "memory_type": "TEXT NOT NULL DEFAULT 'knowledge'",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "person_id": "TEXT",
            "relation_memory_ids": "TEXT NOT NULL DEFAULT '[]'",
            "relation_aliases": "TEXT NOT NULL DEFAULT '[]'",
            "event_start_at": "REAL NOT NULL DEFAULT 0",
            "event_end_at": "REAL NOT NULL DEFAULT 0",
            "related_people": "TEXT NOT NULL DEFAULT '[]'",
            "knowledge_type": "TEXT NOT NULL DEFAULT ''",
            "address_or_coord": "TEXT NOT NULL DEFAULT ''",
            "place_type": "TEXT NOT NULL DEFAULT ''",
            "asset_type": "TEXT NOT NULL DEFAULT ''",
            "disposition_status": "TEXT NOT NULL DEFAULT ''",
            "procedure_type": "TEXT NOT NULL DEFAULT ''",
        }

        async with self._db.session() as s:
            pragma_rows = (
                await s.execute(text("PRAGMA table_info(booku_memory_records)"))
            ).fetchall()
            existing = {str(row[1]) for row in pragma_rows}
            for name, ddl in memory_required_columns.items():
                if name in existing:
                    continue
                await s.execute(
                    text(f"ALTER TABLE booku_memory_records ADD COLUMN {name} {ddl}")
                )

    async def _migrate_legacy_bucket_values(self) -> None:
        async with self._db.session() as s:
            await s.execute(
                text(
                    """
                    UPDATE booku_memory_records
                    SET
                        status = CASE
                            WHEN bucket = 'archived' AND (status IS NULL OR status = '' OR status = 'active')
                                THEN 'archived'
                            ELSE status
                        END,
                        bucket = CASE
                            WHEN bucket = 'knowledge' THEN 'knowledge'
                            ELSE 'memory'
                        END,
                        is_archived = CASE
                            WHEN lower(COALESCE(
                                CASE
                                    WHEN bucket = 'archived' AND (status IS NULL OR status = '' OR status = 'active')
                                        THEN 'archived'
                                    ELSE status
                                END,
                                'active'
                            )) IN ('archived', 'expired') THEN 1
                            ELSE 0
                        END
                    WHERE bucket NOT IN ('knowledge', 'memory')
                       OR bucket IS NULL
                       OR bucket = ''
                       OR (bucket = 'archived' AND (status IS NULL OR status = '' OR status = 'active'))
                    """
                )
            )

    async def _batch_load_tags(self, memory_ids: list[str]) -> dict[str, dict[str, list[str]]]:
        """批量加载标签，返回 {memory_id: {tag_type: [tag_value, ...]}}。"""
        if not memory_ids:
            return {}
        T = BookuMemoryTagModel
        async with self._db.session() as s:
            tag_rows = (
                await s.execute(select(T).where(T.memory_id.in_(memory_ids)))
            ).scalars().all()

        tags_by_id: dict[str, dict[str, list[str]]] = {}
        for tag in tag_rows:
            mid = tag.memory_id
            if mid not in tags_by_id:
                tags_by_id[mid] = {}
            tags_by_id[mid].setdefault(tag.tag_type, []).append(tag.tag_value)
        return tags_by_id

    # ------------------------------------------------------------------
    # 写入 / upsert
    # ------------------------------------------------------------------

    async def upsert_record(
        self,
        *,
        memory_id: str,
        title: str,
        folder_id: str,
        bucket: str,
        content: str,
        source: str,
        memory_type: str = "knowledge",
        status: str = "active",
        person_id: str | None = None,
        relation_memory_ids: list[str] | None = None,
        relation_aliases: list[str] | None = None,
        event_start_at: float = 0.0,
        event_end_at: float = 0.0,
        related_people: list[str] | None = None,
        knowledge_type: str = "",
        address_or_coord: str = "",
        place_type: str = "",
        asset_type: str = "",
        disposition_status: str = "",
        procedure_type: str = "",
        novelty_energy: float = 0.0,
        tags: list[str] | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
    ) -> None:
        now = time.time()
        normalized_bucket = self._normalize_bucket_value(bucket)
        normalized_status = str(status or "active").strip().lower() or "active"
        is_archived = 1 if self._is_archived_status(normalized_status) else 0

        base = utils.build_upsert_record_dict(
            title=title, folder_id=folder_id, bucket=normalized_bucket,
            content=content, source=source, memory_type=memory_type,
            status=normalized_status, person_id=person_id,
            relation_memory_ids=relation_memory_ids,
            relation_aliases=relation_aliases,
            event_start_at=event_start_at, event_end_at=event_end_at,
            related_people=related_people, knowledge_type=knowledge_type,
            address_or_coord=address_or_coord, place_type=place_type,
            asset_type=asset_type, disposition_status=disposition_status,
            procedure_type=procedure_type, novelty_energy=novelty_energy,
            is_archived=is_archived,
        )

        async with self._db.session() as s:
            existing = await s.execute(
                select(BookuMemoryRecordModel.created_at).where(
                    BookuMemoryRecordModel.memory_id == memory_id
                )
            )
            row = existing.first()
            created_at = float(row[0]) if row else now

            stmt = sqlite_insert(BookuMemoryRecordModel).values(
                memory_id=memory_id,
                created_at=created_at,
                updated_at=now,
                last_activated_at=0.0,
                activation_count=0,
                **base,
            ).on_conflict_do_update(
                index_elements=["memory_id"],
                set_={**base, "updated_at": now},
            )
            await s.execute(stmt)

            await utils.rebuild_tags(
                s, memory_id=memory_id, tags=tags,
                core_tags=core_tags, diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
            )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_record(
        self, memory_id: str, *, include_deleted: bool = False
    ) -> BookuMemoryRecord | None:
        records = await self.get_records_map([memory_id], include_deleted=include_deleted)
        return records.get(memory_id)

    async def get_records_map(
        self, memory_ids: list[str], *, include_deleted: bool = False
    ) -> dict[str, BookuMemoryRecord]:
        if not memory_ids:
            return {}

        async with self._db.session() as s:
            R = BookuMemoryRecordModel
            stmt = select(R).where(R.memory_id.in_(memory_ids))
            if not include_deleted:
                stmt = stmt.where(R.is_deleted == 0)
            rows = (await s.execute(stmt)).scalars().all()

            if not rows:
                return {}

            found_ids = [r.memory_id for r in rows]

        tags_by_id = await self._batch_load_tags(found_ids)
        return {r.memory_id: self._to_record(r, tags_by_id) for r in rows}

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    async def update_record(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        source: str | None = None,
        bucket: str | None = None,
        folder_id: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        person_id: str | None = None,
        relation_memory_ids: list[str] | None = None,
        relation_aliases: list[str] | None = None,
        event_start_at: float | None = None,
        event_end_at: float | None = None,
        related_people: list[str] | None = None,
        knowledge_type: str | None = None,
        address_or_coord: str | None = None,
        place_type: str | None = None,
        asset_type: str | None = None,
        disposition_status: str | None = None,
        procedure_type: str | None = None,
        tags: list[str] | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
    ) -> bool:
        now = time.time()
        R = BookuMemoryRecordModel

        async with self._db.session() as s:
            existing = (
                await s.execute(
                    select(R).where(R.memory_id == memory_id, R.is_deleted == 0)
                )
            ).scalar_one_or_none()
            if existing is None:
                return False

            update_vals: dict[str, Any] = {"updated_at": now}
            utils.merge_update_fields(
                update_vals,
                normalize_bucket=self._normalize_bucket_value,
                is_archived_status=self._is_archived_status,
                title=title, content=content, source=source,
                bucket=bucket, folder_id=folder_id, memory_type=memory_type,
                status=status, person_id=person_id,
                relation_memory_ids=relation_memory_ids,
                relation_aliases=relation_aliases,
                event_start_at=event_start_at, event_end_at=event_end_at,
                related_people=related_people, knowledge_type=knowledge_type,
                address_or_coord=address_or_coord, place_type=place_type,
                asset_type=asset_type, disposition_status=disposition_status,
                procedure_type=procedure_type,
            )

            await s.execute(
                update(R).where(R.memory_id == memory_id).values(**update_vals)
            )

            if any(v is not None for v in [tags, core_tags, diffusion_tags, opposing_tags]):
                await utils.rebuild_tags(
                    s, memory_id=memory_id, tags=tags,
                    core_tags=core_tags, diffusion_tags=diffusion_tags,
                    opposing_tags=opposing_tags,
                )

        return True

    async def mark_archived(self, memory_ids: list[str]) -> int:
        if not memory_ids:
            return 0
        now = time.time()
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            stmt = (
                update(R)
                .where(R.memory_id.in_(memory_ids), R.is_deleted == 0)
                .values(bucket=self._MEMORY_BUCKET, status="archived", is_archived=1, updated_at=now)
            )
            result = await s.execute(stmt)
            return int(getattr(result, "rowcount", 0) or 0)

    async def soft_delete_records(self, memory_ids: list[str]) -> int:
        if not memory_ids:
            return 0
        now = time.time()
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            result = await s.execute(
                update(R)
                .where(R.memory_id.in_(memory_ids), R.is_deleted == 0)
                .values(is_deleted=1, deleted_at=now, updated_at=now)
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def hard_delete_records(self, memory_ids: list[str]) -> int:
        if not memory_ids:
            return 0
        T = BookuMemoryTagModel
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            await s.execute(delete(T).where(T.memory_id.in_(memory_ids)))
            result = await s.execute(delete(R).where(R.memory_id.in_(memory_ids)))
            return int(getattr(result, "rowcount", 0) or 0)

    async def update_activated(self, memory_id: str) -> None:
        now = time.time()
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            await s.execute(
                update(R)
                .where(R.memory_id == memory_id)
                .values(
                    activation_count=R.activation_count + 1,
                    last_activated_at=now,
                )
            )

    # ------------------------------------------------------------------
    # 结构化查询
    # ------------------------------------------------------------------

    async def search_records(
        self,
        *,
        keyword: str | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        person_id: str | None = None,
        relation_of: str | None = None,
        folder_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[BookuMemoryRecord]:
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            stmt = select(R)
            if folder_id is not None:
                stmt = stmt.where(R.folder_id == folder_id)
            if memory_type:
                stmt = stmt.where(R.memory_type == memory_type)
            if status:
                stmt = stmt.where(R.status == status)
            if person_id:
                stmt = stmt.where(R.person_id == person_id)
            if relation_of:
                stmt = stmt.where(R.relation_memory_ids.like(f'%"{relation_of}"%'))
            if not include_deleted:
                stmt = stmt.where(R.is_deleted == 0)

            cleaned_keyword = (keyword or "").strip()
            if cleaned_keyword:
                like_value = f"%{cleaned_keyword}%"
                stmt = stmt.where(
                    or_(
                        R.title.like(like_value),
                        R.content.like(like_value),
                        R.memory_id.like(like_value),
                    )
                )

            stmt = stmt.order_by(R.last_activated_at.desc(), R.updated_at.desc()).limit(max(1, int(limit)))
            rows = (await s.execute(stmt)).scalars().all()

        return await utils.resolve_rows_with_tags(self, rows)

    async def search_records_by_tag_triplet(
        self,
        *,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
        memory_type: str | None = None,
        status: str | None = None,
        person_id: str | None = None,
        relation_of: str | None = None,
        folder_id: str | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[BookuMemoryRecord]:
        normalized_core_tags = [str(tag).strip().lower() for tag in core_tags if str(tag).strip()]
        normalized_diffusion_tags = [
            str(tag).strip().lower() for tag in diffusion_tags if str(tag).strip()
        ]
        normalized_opposing_tags = [
            str(tag).strip().lower() for tag in opposing_tags if str(tag).strip()
        ]
        if not (normalized_core_tags and normalized_diffusion_tags and normalized_opposing_tags):
            return []

        R = BookuMemoryRecordModel
        T = BookuMemoryTagModel

        core_exists = (
            select(T.memory_id)
            .where(
                T.memory_id == R.memory_id,
                T.tag_type == "core",
                T.tag_value.in_(normalized_core_tags),
            )
            .exists()
        )
        diffusion_exists = (
            select(T.memory_id)
            .where(
                T.memory_id == R.memory_id,
                T.tag_type == "diffusion",
                T.tag_value.in_(normalized_diffusion_tags),
            )
            .exists()
        )
        opposing_exists = (
            select(T.memory_id)
            .where(
                T.memory_id == R.memory_id,
                T.tag_type == "opposing",
                T.tag_value.in_(normalized_opposing_tags),
            )
            .exists()
        )

        async with self._db.session() as s:
            stmt = select(R).where(core_exists, diffusion_exists, opposing_exists)
            if folder_id is not None:
                stmt = stmt.where(R.folder_id == folder_id)
            if memory_type:
                stmt = stmt.where(R.memory_type == memory_type)
            if status:
                stmt = stmt.where(R.status == status)
            if person_id:
                stmt = stmt.where(R.person_id == person_id)
            if relation_of:
                stmt = stmt.where(R.relation_memory_ids.like(f'%"{relation_of}"%'))
            if not include_deleted:
                stmt = stmt.where(R.is_deleted == 0)

            stmt = stmt.order_by(R.last_activated_at.desc(), R.updated_at.desc()).limit(
                max(1, int(limit))
            )
            rows = (await s.execute(stmt)).scalars().all()

        return await utils.resolve_rows_with_tags(self, rows)

    async def list_distinct_folder_ids(self) -> list[str]:
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            stmt = (
                select(distinct(R.folder_id))
                .where(R.bucket == self._MEMORY_BUCKET, R.is_deleted == 0)
                .order_by(R.folder_id)
            )
            rows = (await s.execute(stmt)).scalars().all()
        return [r for r in rows if r]

    async def list_recent_active_records(self, *, limit: int = 10) -> list[BookuMemoryRecord]:
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(R)
                    .where(R.status == "active", R.is_deleted == 0)
                    .order_by(R.last_activated_at.desc(), R.updated_at.desc())
                    .limit(max(1, int(limit)))
                )
            ).scalars().all()

        return await utils.resolve_rows_with_tags(self, rows)

    async def list_records_by_bucket(
        self,
        *,
        bucket: str,
        folder_id: str | None = None,
        limit: int = 300,
        include_deleted: bool = False,
    ) -> list[BookuMemoryRecord]:
        normalized_bucket = self._normalize_bucket_value(bucket)
        qb = self._db.query(BookuMemoryRecordModel).filter(bucket=normalized_bucket)
        if folder_id is not None:
            qb = qb.filter(folder_id=folder_id)
        if not include_deleted:
            qb = qb.filter(is_deleted=0)
        rows = await qb.order_by("-updated_at").limit(max(1, int(limit))).all()
        return [self._to_record(r) for r in rows]  # type: ignore[arg-type]

    async def get_bucket_counts(
        self,
        folder_id: str | None = None,
        *,
        include_deleted: bool = False,
    ) -> dict[str, int]:
        counts: dict[str, int] = {"memory": 0, "knowledge": 0}
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            stmt = (
                select(R.bucket, func.count())
                .select_from(R)
                .group_by(R.bucket)
            )
            if folder_id is not None:
                stmt = stmt.where(R.folder_id == folder_id)
            if not include_deleted:
                stmt = stmt.where(R.is_deleted == 0)
            for bucket, cnt in (await s.execute(stmt)).all():
                key = self._normalize_bucket_value(str(bucket))
                counts[key] = counts.get(key, 0) + int(cnt)
        return counts

    async def get_recent_records(
        self,
        *,
        limit: int = 10,
        folder_id: str | None = None,
        include_archived: bool = True,
        include_deleted: bool = False,
    ) -> list[BookuMemoryRecord]:
        qb = self._db.query(BookuMemoryRecordModel)
        if folder_id is not None:
            qb = qb.filter(folder_id=folder_id)
        if not include_archived:
            qb = qb.filter(status__ne="archived")
        if not include_deleted:
            qb = qb.filter(is_deleted=0)
        rows = await qb.order_by("-updated_at").limit(max(1, limit)).all()
        return [self._to_record(r) for r in rows]  # type: ignore[arg-type]

    async def list_knowledge_chunk_titles(self, *, folder_id: str = "default") -> list[str]:
        R = BookuMemoryRecordModel
        stmt = (
            select(distinct(R.title))
            .where(R.bucket == "knowledge")
            .where(R.folder_id == folder_id)
            .where(R.is_deleted == 0)
        )
        async with self._db.session() as s:
            result = await s.execute(stmt)
            return [str(row[0]) for row in result.fetchall()]

    async def get_stale_emergent(
        self, folder_id: str, before_timestamp: float
    ) -> list[BookuMemoryRecord]:
        rows = await (
            self._db.query(BookuMemoryRecordModel)
            .filter(
                folder_id=folder_id,
                bucket=self._MEMORY_BUCKET,
                created_at__lt=before_timestamp,
                is_deleted=0,
            )
            .all()
        )
        return [self._to_record(r) for r in rows]  # type: ignore[arg-type]

    async def search_records_grep(
        self,
        *,
        query: str,
        search_fields: list[str],
        folder_id: str | None = None,
        include_archived: bool = False,
        include_deleted: bool = False,
        limit: int = 20,
        use_regex: bool = False,
    ) -> list[str]:
        """在指定字段中匹配关键词或正则表达式，返回 memory_id 列表。"""
        keyword = query.strip()
        if not keyword:
            return []

        allowed_fields = {"title", "summary", "content", "tags", "metadata"}
        normalized_fields = [f for f in search_fields if f in allowed_fields] or ["title", "content"]

        R = BookuMemoryRecordModel
        T = BookuMemoryTagModel

        if use_regex:
            try:
                pattern = re.compile(keyword)
            except re.error as exc:
                raise ValueError(f"无效的正则表达式: {exc}") from exc

            need_tags = "tags" in normalized_fields

            async with self._db.session() as s:
                where_parts: list[Any] = []
                if folder_id is not None:
                    where_parts.append(R.folder_id == folder_id)
                if not include_archived:
                    where_parts.append(R.status.not_in(["archived", "expired"]))
                if not include_deleted:
                    where_parts.append(R.is_deleted == 0)

                cand_stmt = (
                    select(R.memory_id, R.title, R.content, R.source, R.folder_id, R.bucket)
                    .where(*where_parts)
                    .order_by(R.updated_at.desc())
                )
                cand_result = await s.execute(cand_stmt)
                rows = cand_result.all()

                tag_map: dict[str, list[str]] = {}
                if need_tags and rows:
                    all_ids = [str(row[0]) for row in rows]
                    tag_stmt = select(T.memory_id, T.tag_value).where(T.memory_id.in_(all_ids))
                    tag_result = await s.execute(tag_stmt)
                    for tmid, tval in tag_result.all():
                        tag_map.setdefault(str(tmid), []).append(str(tval))

                matched: list[str] = []
                for row in rows:
                    mid, title, content, source, fid, bucket = (
                        str(row[0]), str(row[1] or ""), str(row[2] or ""),
                        str(row[3] or ""), str(row[4] or ""), str(row[5] or ""),
                    )
                    hit = False
                    if "title" in normalized_fields and pattern.search(title):
                        hit = True
                    if not hit and ("summary" in normalized_fields or "content" in normalized_fields):
                        hit = bool(pattern.search(content))
                    if not hit and "metadata" in normalized_fields:
                        hit = any(pattern.search(v) for v in (mid, source, fid, bucket))
                    if not hit and need_tags:
                        hit = any(pattern.search(tag) for tag in tag_map.get(mid, []))
                    if hit:
                        matched.append(mid)
                        if len(matched) >= max(1, limit):
                            break

                return matched

        like_value = f"%{keyword}%"

        async with self._db.session() as s:
            where_parts_like: list[Any] = []
            if folder_id is not None:
                where_parts_like.append(R.folder_id == folder_id)
            if not include_archived:
                where_parts_like.append(R.status.not_in(["archived", "expired"]))
            if not include_deleted:
                where_parts_like.append(R.is_deleted == 0)

            matchers: list[Any] = []
            if "title" in normalized_fields:
                matchers.append(R.title.like(like_value))
            if "summary" in normalized_fields or "content" in normalized_fields:
                matchers.append(R.content.like(like_value))
            if "metadata" in normalized_fields:
                matchers.extend([
                    R.memory_id.like(like_value),
                    R.source.like(like_value),
                    R.folder_id.like(like_value),
                    R.bucket.like(like_value),
                ])
            if "tags" in normalized_fields:
                matchers.append(
                    select(T.memory_id)
                    .where(T.memory_id == R.memory_id, T.tag_value.like(like_value))
                    .exists()
                )

            if not matchers:
                return []

            stmt = (
                select(distinct(R.memory_id))
                .where(*where_parts_like, or_(*matchers))
                .order_by(R.updated_at.desc())
                .limit(max(1, limit))
            )
            result = await s.execute(stmt)
            return [str(row[0]) for row in result.all()]

    async def list_memory_ids_by_folder(
        self,
        *,
        folder_id: str,
        include_archived: bool = True,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> list[str]:
        qb = self._db.query(BookuMemoryRecordModel).filter(folder_id=folder_id)
        if not include_archived:
            qb = qb.filter(status__ne="archived")
        if not include_deleted:
            qb = qb.filter(is_deleted=0)
        rows = await qb.order_by("-updated_at").limit(max(1, limit)).all()
        return [r.memory_id for r in rows]  # type: ignore[union-attr]


__all__ = ["BookuMemoryMetadataRepository"]
