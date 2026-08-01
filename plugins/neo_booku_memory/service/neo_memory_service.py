"""Neo Booku Memory 服务。

封装 booku_memory_store 的存储与检索能力，
提供与原 BookuMemoryService 兼容的 API 供 tools 和 event_handler 使用。
"""

from __future__ import annotations

import math
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from src.app.plugin_system.api.storage_api import PluginDatabase
from src.core.components import BaseService
from src.core.managers.stream_manager import get_stream_manager
from src.core.prompt import (
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
)
from src.kernel.logger import get_logger

from ..config import NeoBookuMemoryConfig
from ..manual import BOOKU_MEMORY_COMMAND_MANUAL, BOOKU_TEMPORARY_MEMO_MANUAL

logger = get_logger("neo_booku_memory_service")

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "booku_memory"
_TARGET_ACTIVE_REMINDER_NAME = "活跃记忆速览"
_TARGET_TEMPORARY_MEMO_REMINDER_NAME = "临时备忘录"
_TARGET_KNOWLEDGE_REMINDER_NAME = "专业知识引导语"
_ACTIVE_REMINDER_LIMIT = 10


@dataclass(slots=True)
class _TemporaryMemo:
    memo_id: str
    stream_id: str
    content: str
    expires_at: float
    created_at: float
    updated_at: float


class NeoMemoryService(BaseService):
    """Neo Booku Memory 服务 —— 桥接 booku_memory_store 与高层功能。"""

    name: str = "neo_booku_memory"
    description: str = "Neo Booku 记忆服务，提供工具命令处理、系统提醒同步、闪回支持"
    version: str = "1.0.0"

    _store_svc: Any = None

    def _get_config(self) -> NeoBookuMemoryConfig:
        if isinstance(self.plugin.config, NeoBookuMemoryConfig):
            return self.plugin.config
        return NeoBookuMemoryConfig()

    def _get_store(self):
        """懒加载 booku_memory_store 的 BookuMemoryStoreService。"""
        if self._store_svc is None:
            from plugins.booku_memory_store.interface import BookuMemoryStoreService
            self._store_svc = BookuMemoryStoreService(plugin=self.plugin)
        return self._store_svc

    # ==================================================================
    # 对外 API —— 对 booku_memory_store 的适配包装
    # ==================================================================

    async def search_memory_entries(
        self, *, top_n: int = 10, query_text: str | None = None,
        memory_type: str | None = None, status: str | None = None,
        person_id: str | None = None, relation_of: str | None = None,
        include_archived: bool = False, include_knowledge: bool = True,
        include_related: bool = False,
        core_tags: list[str] | None = None, diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """按条件检索记忆，返回 id/title/metadata 的结果列表。"""
        store = self._get_store()
        normalized_type = (memory_type or "").strip().lower() or None
        normalized_status = (status or "").strip().lower() or None
        normalized_person_id = (person_id or "").strip() or None
        normalized_relation_of = (relation_of or "").strip() or None

        entries: list[dict[str, Any]] = []
        existing_ids: set[str] = set()

        def _append(memory_id: str, title: str, metadata: dict[str, Any]) -> None:
            nid = str(memory_id).strip()
            if not nid or nid in existing_ids:
                return
            entries.append({"id": nid, "title": str(title), "metadata": metadata})
            existing_ids.add(nid)

        semantic_query = (
            (query_text or "").strip()
            or " ".join((core_tags or []) + (diffusion_tags or []) + (opposing_tags or [])).strip()
            or None
        )

        if semantic_query:
            retrieved = await store.search(
                query_text=semantic_query, top_k=top_n * 3,
                include_archived=include_archived, include_knowledge=include_knowledge,
                core_tags=core_tags, diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
            )
            for item in retrieved.get("results", []):
                if not isinstance(item, dict):
                    continue
                meta = item.get("metadata", {})
                if not isinstance(meta, dict):
                    continue
                if normalized_type and str(meta.get("memory_type", "")).lower() != normalized_type:
                    continue
                if normalized_status and str(meta.get("status", "")).lower() != normalized_status:
                    continue
                if normalized_person_id and str(meta.get("person_id", "") or "").strip() != normalized_person_id:
                    continue
                if normalized_relation_of and normalized_relation_of not in [
                    str(v) for v in meta.get("relation_memory_ids", [])
                ]:
                    continue
                _append(str(item.get("id", "")), str(item.get("title", "")), meta)
                if len(entries) >= top_n:
                    break

            if len(entries) < top_n and core_tags and diffusion_tags and opposing_tags:
                # 补充 Tag 三元组精确匹配
                repo = await store._get_repo()
                tag_records = await repo.search_records_by_tag_triplet(
                    core_tags=core_tags, diffusion_tags=diffusion_tags,
                    opposing_tags=opposing_tags, memory_type=normalized_type,
                    status=normalized_status, person_id=normalized_person_id,
                    folder_id=store._get_config().storage.default_folder_id,
                    include_deleted=False, limit=top_n * 3,
                )
                for record in tag_records:
                    if not include_archived and str(record.status).lower() == "archived":
                        continue
                    if not include_knowledge and str(record.memory_type).lower() == "knowledge":
                        continue
                    meta = store._metadata_from_record(record)
                    _append(record.memory_id, record.title, meta)
                    if len(entries) >= top_n:
                        break

            if (query_text or "").strip() and len(entries) < top_n:
                repo = await store._get_repo()
                fallback = await repo.search_records(
                    keyword=query_text.strip(), memory_type=normalized_type,
                    status=normalized_status, person_id=normalized_person_id,
                    include_deleted=False, limit=top_n * 3,
                )
                for record in fallback:
                    if not include_archived and str(record.status).lower() == "archived":
                        continue
                    if not include_knowledge and str(record.memory_type).lower() == "knowledge":
                        continue
                    meta = store._metadata_from_record(record)
                    _append(record.memory_id, record.title, meta)
                    if len(entries) >= top_n:
                        break
        else:
            repo = await store._get_repo()
            records = await repo.search_records(
                memory_type=normalized_type, status=normalized_status,
                person_id=normalized_person_id, include_deleted=False, limit=top_n,
            )
            for record in records:
                if not include_archived and str(record.status).lower() == "archived":
                    continue
                if not include_knowledge and str(record.memory_type).lower() == "knowledge":
                    continue
                meta = store._metadata_from_record(record)
                _append(record.memory_id, record.title, meta)

        if include_related:
            related_ids: list[str] = []
            for entry in entries:
                meta = entry.get("metadata", {})
                if isinstance(meta, dict):
                    related_ids.extend([str(v) for v in meta.get("relation_memory_ids", [])])
            dedup_related = list(dict.fromkeys([v for v in related_ids if v]))
            if dedup_related:
                repo = await store._get_repo()
                related_records = await repo.get_records_map(dedup_related)
                for rid in dedup_related:
                    if rid in existing_ids:
                        continue
                    record = related_records.get(rid)
                    if record is not None:
                        _append(record.memory_id, record.title, store._metadata_from_record(record))
                        if len(entries) >= top_n:
                            break

        return {"action": "search_memory_entries", "total": len(entries[:top_n]), "items": entries[:top_n]}

    async def read_full_content(self, *, memory_ids: list[str]) -> dict[str, Any]:
        store = self._get_store()
        return await store.read(memory_ids=memory_ids)

    async def create_memory(
        self, *, title: str, content: str, bucket: str = "emergent",
        folder_id: str | None = None,
        core_tags: list[str] | None = None, diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None, memory_type: str = "knowledge",
        status: str = "active", person_id: str | None = None,
        relation_memory_ids: list[str] | None = None, relation_aliases: list[str] | None = None,
        event_start_at: float = 0.0, event_end_at: float = 0.0,
        related_people: list[str] | None = None, knowledge_type: str = "",
        address_or_coord: str = "", place_type: str = "", asset_type: str = "",
        disposition_status: str = "", procedure_type: str = "",
    ) -> dict[str, Any]:
        store = self._get_store()
        store_config = store._get_config()
        fixed_folder = folder_id if folder_id else store_config.storage.default_folder_id
        result = await store.create(
            title=title, content=content, bucket=bucket, folder_id=fixed_folder,
            core_tags=core_tags, diffusion_tags=diffusion_tags, opposing_tags=opposing_tags,
            memory_type=memory_type, status=status, person_id=person_id,
            relation_memory_ids=relation_memory_ids, relation_aliases=relation_aliases,
            event_start_at=event_start_at, event_end_at=event_end_at,
            related_people=related_people, knowledge_type=knowledge_type,
            address_or_coord=address_or_coord, place_type=place_type,
            asset_type=asset_type, disposition_status=disposition_status,
            procedure_type=procedure_type, source="agent",
        )
        await self.sync_actor_reminder()
        return {
            "action": "create_memory", "mode": result.get("mode", "created"),
            "total": 1, "items": [result.get("item", {})],
        }

    async def update_memory_by_id(
        self, *, memory_id: str, title: str | None = None, content: str | None = None,
        core_tags: list[str] | None = None, diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None, memory_type: str | None = None,
        status: str | None = None, person_id: str | None = None,
        relation_memory_ids: list[str] | None = None, relation_aliases: list[str] | None = None,
        event_start_at: float | None = None, event_end_at: float | None = None,
        related_people: list[str] | None = None, knowledge_type: str | None = None,
        address_or_coord: str | None = None, place_type: str | None = None,
        asset_type: str | None = None, disposition_status: str | None = None,
        procedure_type: str | None = None,
    ) -> dict[str, Any]:
        store = self._get_store()
        return await store.update(
            memory_id=memory_id, title=title, content=content,
            core_tags=core_tags, diffusion_tags=diffusion_tags, opposing_tags=opposing_tags,
            memory_type=memory_type, status=status, person_id=person_id,
            relation_memory_ids=relation_memory_ids, relation_aliases=relation_aliases,
            event_start_at=event_start_at, event_end_at=event_end_at,
            related_people=related_people, knowledge_type=knowledge_type,
            address_or_coord=address_or_coord, place_type=place_type,
            asset_type=asset_type, disposition_status=disposition_status,
            procedure_type=procedure_type,
        )

    async def delete_memories(self, *, memory_ids: list[str], hard: bool = False) -> dict[str, Any]:
        store = self._get_store()
        result = await store.delete(memory_ids=memory_ids, hard=hard)
        await self.sync_actor_reminder()
        return result

    async def archive_memories(self, *, memory_ids: list[str], folder_id: str) -> dict[str, Any]:
        store = self._get_store()
        repo = await store._get_repo()
        archived = await repo.mark_archived(memory_ids)
        return {"archived": archived, "skipped": max(0, len(memory_ids) - archived)}

    async def promote_stale_emergent(self, folder_id: str | None = None) -> dict[str, Any]:
        config = self._get_config()
        store = self._get_store()
        store_config = store._get_config()
        effective_folder = folder_id or store_config.storage.default_folder_id
        repo = await store._get_repo()

        window_days = config.time_window.emergent_days
        threshold = config.time_window.activation_threshold
        cutoff = time.time() - window_days * 86400.0

        stale = await repo.get_stale_emergent(folder_id=effective_folder, before_timestamp=cutoff)
        if not stale:
            return {"promoted": 0, "discarded": 0, "folder_id": effective_folder}

        promote_ids = [r.memory_id for r in stale if r.activation_count >= threshold]
        discard_ids = [r.memory_id for r in stale if r.activation_count < threshold]

        promoted = 0
        if promote_ids:
            result = await self.archive_memories(memory_ids=promote_ids, folder_id=effective_folder)
            promoted = int(result.get("archived", 0))

        discarded = 0
        if discard_ids:
            from src.kernel.vector_db import get_vector_db_service
            vector_db = get_vector_db_service(store_config.storage.vector_db_path)
            emergent_collection = store._get_rag_engine()._mem_collection_name(effective_folder)
            try:
                await vector_db.delete(collection_name=emergent_collection, ids=discard_ids)
            except Exception:  # noqa: BLE001
                pass
            discarded = await repo.hard_delete_records(discard_ids)

        return {"promoted": promoted, "discarded": discarded, "folder_id": effective_folder}

    async def grep_memories(
        self, *, query: str, search_fields: list[str], folder_id: str | None = None,
        include_archived: bool = False, top_k: int = 10, use_regex: bool = False,
    ) -> dict[str, Any]:
        store = self._get_store()
        repo = await store._get_repo()
        memory_ids = await repo.search_records_grep(
            query=query, search_fields=search_fields, folder_id=folder_id,
            include_archived=include_archived, limit=top_k, use_regex=use_regex,
        )
        records = await repo.get_records_map(memory_ids)
        from plugins.booku_memory_store.interface.utils import build_record_item
        items = [build_record_item(record) for mid in memory_ids if (record := records.get(mid)) is not None]
        return {"action": "grep_memories", "query": query, "total": len(items), "items": items}

    async def get_status(self, folder_id: str | None = None) -> dict[str, Any]:
        store = self._get_store()
        repo = await store._get_repo()
        store_config = store._get_config()
        from src.kernel.vector_db import get_vector_db_service
        vector_db = get_vector_db_service(store_config.storage.vector_db_path)

        if folder_id is None:
            from plugins.booku_memory_store.interface.utils import build_record_item
            recent = await repo.get_recent_records(limit=8, folder_id=None, include_archived=True)
            return {
                "folder_id": "all",
                "counts": {
                    "vector": {"memory": 0, "knowledge": 0},
                    "metadata": await repo.get_bucket_counts(None),
                },
                "recent": [build_record_item(r) for r in recent],
                "folder_memory_ids": [],
            }

        from plugins.booku_memory_store.interface.utils import build_record_item
        from plugins.booku_memory_store.algorithm.rag_engine import RagEngine
        vector_counts = {"memory": 0, "knowledge": 0}
        for collection in RagEngine._memory_collection_candidates(folder_id):
            vector_counts["memory"] += await vector_db.count(collection)
        vector_counts["knowledge"] = await vector_db.count(RagEngine.collection_name("knowledge", "default"))
        metadata_counts = await repo.get_bucket_counts(folder_id)
        recent = await repo.get_recent_records(limit=8, folder_id=folder_id, include_archived=True)
        folder_memory_ids = await repo.list_memory_ids_by_folder(folder_id=folder_id, include_archived=True, limit=200)
        return {
            "folder_id": folder_id,
            "counts": {"vector": vector_counts, "metadata": metadata_counts},
            "recent": [build_record_item(r) for r in recent],
            "folder_memory_ids": folder_memory_ids,
        }

    async def list_folder_ids(self) -> dict[str, Any]:
        store = self._get_store()
        repo = await store._get_repo()
        folders = await repo.list_distinct_folder_ids()
        store_config = store._get_config()
        default = store_config.storage.default_folder_id.strip().lower() or "default"
        normalized = [default] + [f for f in folders if f.strip().lower() != default]
        return {"action": "list_folder_ids", "total": len(normalized), "items": normalized}

    # ==================================================================
    # 临时备忘录
    # ==================================================================

    async def create_temporary_memo(
        self, *, content: str, expire_hours: float = 2.0, stream_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        expires_at = now + float(expire_hours) * 3600.0
        store = self._get_store()
        repo = await store._get_repo()
        db = repo._db

        async with db.session() as s:
            from sqlalchemy import select
            from plugins.booku_memory_store.database.models import BookuTemporaryMemoModel
            M = BookuTemporaryMemoModel

            existing = (
                await s.execute(
                    select(M).where(
                        M.stream_id == (stream_id or "").strip(),
                        M.content == content.strip(),
                        M.expires_at > now,
                    ).order_by(M.updated_at.desc()).limit(1)
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.expires_at = expires_at
                existing.updated_at = now
                await s.flush()
                memo = _TemporaryMemo(
                    memo_id=str(existing.memo_id), stream_id=str(existing.stream_id or ""),
                    content=str(existing.content or ""), expires_at=float(existing.expires_at),
                    created_at=float(existing.created_at), updated_at=float(existing.updated_at),
                )
                return {
                    "action": "create_temporary_memo", "mode": "refreshed",
                    "memo_id": memo.memo_id, "expires_at": memo.expires_at,
                    "active_memo_count": 1,
                    "item": {
                        "memo_id": memo.memo_id, "stream_id": memo.stream_id,
                        "content": memo.content, "expires_at": memo.expires_at,
                        "created_at": memo.created_at, "updated_at": memo.updated_at,
                    },
                }

        return {"action": "create_temporary_memo", "mode": "created", "memo_id": "", "expires_at": expires_at}

    # ==================================================================
    # Actor Reminder 同步
    # ==================================================================

    async def build_actor_reminder(self) -> str:
        config = self._get_config()
        if not config.plugin.inject_system_prompt:
            return ""
        return BOOKU_MEMORY_COMMAND_MANUAL.strip()

    async def sync_actor_reminder(self) -> str:
        store = get_system_reminder_store()
        reminder_content = await self.build_actor_reminder()

        if not reminder_content:
            store.delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)
            store.delete(_TARGET_REMINDER_BUCKET, _TARGET_ACTIVE_REMINDER_NAME)
            store.delete(_TARGET_REMINDER_BUCKET, _TARGET_TEMPORARY_MEMO_REMINDER_NAME)
            store.delete(_TARGET_REMINDER_BUCKET, _TARGET_KNOWLEDGE_REMINDER_NAME)
            return ""

        store.set(_TARGET_REMINDER_BUCKET, name=_TARGET_REMINDER_NAME, content=reminder_content)

        # 活跃记忆提醒
        try:
            ms = self._get_store()
            repo = await ms._get_repo()
            from plugins.booku_memory_store.interface.utils import build_record_item

            active_records = await repo.list_recent_active_records(limit=_ACTIVE_REMINDER_LIMIT)
            if active_records:
                lines = [
                    f"{i}. {getattr(r, 'title', '未命名记忆') or '未命名记忆'} ({getattr(r, 'memory_id', '')})"
                    for i, r in enumerate(active_records[: _ACTIVE_REMINDER_LIMIT], 1)
                    if getattr(r, "memory_id", "")
                ]
                if lines:
                    content = "## 最新活跃记忆\n以下只展示一小部分最新的活跃记忆记录。你还有很多记忆没有在这里列出，不要把这个列表当作全部记忆。\n" + "\n".join(lines)
                    store.set(_TARGET_REMINDER_BUCKET, name=_TARGET_ACTIVE_REMINDER_NAME, content=content, insert_type=SystemReminderInsertType.DYNAMIC)
                else:
                    store.delete(_TARGET_REMINDER_BUCKET, _TARGET_ACTIVE_REMINDER_NAME)
            else:
                store.delete(_TARGET_REMINDER_BUCKET, _TARGET_ACTIVE_REMINDER_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"同步活跃记忆动态提示失败：{exc}")

        return reminder_content
