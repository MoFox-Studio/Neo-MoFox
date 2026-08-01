"""Booku Memory Store 对外服务 API。

以知识库数据库的形式暴露记忆存储与检索能力：
- search: 语义检索记忆
- read:   读取记忆完整内容
- create: 创建新记忆（自动判重合并）
- update: 更新已有记忆
- delete: 删除记忆（支持软删除/硬删除）

外部插件通过这些 API 自行决定何时存储、检索、更新、删除数据。
本服务只提供高级检索和存储能力，不包含工具注册、事件处理、闪回等机制。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from src.core.components import BaseService
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

from ..algorithm.deduplicator import ResultDeduplicator
from ..algorithm.rag_engine import RagEngine
from ..algorithm.vector_math import VectorMath
from ..config import BookuMemoryStoreConfig
from ..database import BookuMemoryMetadataRepository

from . import utils

logger = get_logger("booku_memory_store_service")


class BookuMemoryStoreService(BaseService):
    """Booku Memory Store 服务组件。

    对外提供记忆（知识库）的 search/read/create/update/delete 五个核心 API。
    内部实现细节委托给 interface.utils 模块。
    """

    name: str = "booku_memory_store"
    description: str = "Booku Memory Store 记忆数据库服务，提供高级检索与存储能力"
    version: str = "1.0.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._repo: BookuMemoryMetadataRepository | None = None
        self._repo_initialized = False
        self._rag_engine: RagEngine | None = None
        self._deduplicator = ResultDeduplicator()

    def _get_config(self) -> BookuMemoryStoreConfig:
        if isinstance(self.plugin.config, BookuMemoryStoreConfig):
            return self.plugin.config
        return BookuMemoryStoreConfig()

    async def _get_repo(self) -> BookuMemoryMetadataRepository:
        config = self._get_config()
        if self._repo is None:
            self._repo = BookuMemoryMetadataRepository(db_path=config.storage.metadata_db_path)
        if not self._repo_initialized:
            await self._repo.initialize()
            self._repo_initialized = True
        return self._repo

    def _get_rag_engine(self) -> RagEngine:
        if self._rag_engine is None:
            config = self._get_config()
            rag_params_file = Path(__file__).resolve().parent.parent / "rag_params.json"
            self._rag_engine = RagEngine(
                rag_params_file=rag_params_file,
                retrieval_config=config.retrieval,
                write_conflict_config=config.write_conflict,
            )
        return self._rag_engine

    # ==================================================================
    # 对外 API
    # ==================================================================

    async def search(
        self,
        query_text: str,
        *,
        top_k: int | None = None,
        include_archived: bool | None = None,
        include_knowledge: bool | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """语义检索记忆。

        Args:
            query_text: 检索关键词文本。
            top_k: 返回条数，None 使用配置默认值。
            include_archived: 是否检索归档记忆。
            include_knowledge: 是否检索知识库。
            core_tags: 核心标签，提升匹配得分。
            diffusion_tags: 扩散标签，扇形扩展检索。
            opposing_tags: 对立标签，对匹配项降分。
            folder_id: 限定检索文件夹，None 时全局检索。

        Returns:
            包含 query、total、results 字段的字典。
        """
        config = self._get_config()
        rag_engine = self._get_rag_engine()
        repo = await self._get_repo()

        search_folders: list[str] = []
        if folder_id:
            search_folders = [folder_id.strip().lower()]
        else:
            search_folders = await repo.list_distinct_folder_ids()
            default = config.storage.default_folder_id.strip().lower()
            if default and default not in search_folders:
                search_folders.insert(0, default)
        if not search_folders:
            search_folders = ["default"]

        n_results = top_k or config.retrieval.default_top_k
        use_archived = (
            config.retrieval.include_archived_default
            if include_archived is None
            else include_archived
        )
        use_knowledge = (
            config.retrieval.include_knowledge_default
            if include_knowledge is None
            else include_knowledge
        )

        query_vector = await rag_engine.embed_text(query_text)
        query_core_tags = {tag.strip().lower() for tag in (core_tags or []) if tag and tag.strip()}
        query_diffusion_tags = {tag.strip().lower() for tag in (diffusion_tags or []) if tag and tag.strip()}
        query_opposing_tags = {tag.strip().lower() for tag in (opposing_tags or []) if tag and tag.strip()}
        query_tokens = {token for token in query_text.lower().split() if token}

        collections: list[str] = []
        for sf in search_folders:
            for cand in RagEngine._memory_collection_candidates(sf):
                if cand not in collections:
                    collections.append(cand)
        if use_knowledge:
            kc = RagEngine.collection_name("knowledge", "default")
            if kc not in collections:
                collections.append(kc)

        return await utils.execute_epa_retrieval(
            self, query_text, query_vector, query_core_tags, query_diffusion_tags,
            query_opposing_tags, query_tokens, n_results, collections,
        )

    async def read(self, *, memory_ids: list[str], include_deleted: bool = False) -> dict[str, Any]:
        """按 memory_id 批量读取记忆完整内容。"""
        repo = await self._get_repo()
        records = await repo.get_records_map(memory_ids, include_deleted=include_deleted)
        items = [
            utils.build_record_item(record, include_full_content=True)
            for memory_id in memory_ids
            if (record := records.get(memory_id)) is not None
        ]
        return {"action": "read", "requested": len(memory_ids), "total": len(items), "items": items}

    async def create(
        self, *, title: str, content: str, folder_id: str | None = None,
        bucket: str = "memory", core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None, opposing_tags: list[str] | None = None,
        memory_type: str = "knowledge", status: str = "active",
        person_id: str | None = None, relation_memory_ids: list[str] | None = None,
        relation_aliases: list[str] | None = None, event_start_at: float = 0.0,
        event_end_at: float = 0.0, related_people: list[str] | None = None,
        knowledge_type: str = "", address_or_coord: str = "", place_type: str = "",
        asset_type: str = "", disposition_status: str = "", procedure_type: str = "",
        source: str = "external",
    ) -> dict[str, Any]:
        """创建记忆，自动进行写入判重与合并。

        写入前检索邻域向量并计算新颖度能量比：
        - >= 阈值：创建新记忆（mode="created"）
        - < 阈值：自动合并到最相似的现有记忆（mode="merged"）
        """
        config = self._get_config()
        rag_engine = self._get_rag_engine()
        rag_params = rag_engine.get_rag_params()
        repo = await self._get_repo()

        normalized_memory_type = (memory_type or "knowledge").strip().lower()
        normalized_status = (status or "active").strip().lower()
        normalized_bucket = RagEngine._normalize_bucket(bucket)
        effective_folder_id = utils.normalize_folder_id(folder_id, config.storage.default_folder_id)

        text = content.strip()
        if not text:
            raise ValueError("content 不能为空")

        resolved_title = (title or "").strip() or utils.extract_title(text)
        merged_content = utils.join_title_and_content(resolved_title, text)
        if not merged_content:
            raise ValueError("title 与 content 不能同时为空")

        normalized_core_tags = utils.normalize_tags(core_tags)
        normalized_diffusion_tags = utils.normalize_tags(diffusion_tags)
        normalized_opposing_tags = utils.normalize_tags(opposing_tags)

        vector = await rag_engine.embed_text(merged_content)
        collection_name = RagEngine.collection_name(bucket=normalized_bucket, folder_id=effective_folder_id)
        vector_db = get_vector_db_service(config.storage.vector_db_path)

        query_result: dict[str, Any] = {}
        collection_count = await vector_db.count(collection_name)
        if collection_count > 0:
            query_result = await vector_db.query(
                collection_name=collection_name,
                query_embeddings=[vector],
                n_results=config.write_conflict.top_n,
                include=["embeddings", "metadatas", "documents", "distances"],
            )

        existing_embeddings: list[list[float]] = []
        existing_ids = VectorMath.safe_first_row(query_result.get("ids", []))
        query_embeddings = VectorMath.safe_first_row(query_result.get("embeddings", []))
        if query_embeddings:
            for embedding in query_embeddings:
                parsed = VectorMath.to_float_vector(embedding, expected_dim=len(vector))
                if parsed:
                    existing_embeddings.append(parsed)

        if not existing_embeddings and existing_ids:
            loaded = await vector_db.get(
                collection_name=collection_name,
                ids=[str(mid) for mid in existing_ids],
                include=["embeddings"],
            )
            for embedding in VectorMath.safe_list(loaded.get("embeddings", [])):
                parsed = VectorMath.to_float_vector(embedding, expected_dim=len(vector))
                if parsed:
                    existing_embeddings.append(parsed)

        novelty_energy = VectorMath.novelty_energy_ratio(vector, existing_embeddings)
        if novelty_energy >= rag_params.energy_cutoff:
            distance_rows = VectorMath.safe_first_row(query_result.get("distances", []))
            if distance_rows and min(float(v) for v in distance_rows) <= 1e-8:
                novelty_energy = 0.0

        mode = "created"
        memory_id = f"mem-{uuid.uuid4().hex}"
        now = time.time()

        if novelty_energy < rag_params.energy_cutoff and existing_ids:
            memory_id = str(existing_ids[0])
            mode = "merged"
            await vector_db.delete(collection_name=collection_name, ids=[memory_id])

        if normalized_memory_type == "person" and person_id:
            exists = await repo.search_records(
                memory_type="person", person_id=person_id, include_deleted=False, limit=1
            )
            if exists:
                memory_id = exists[0].memory_id
                mode = "merged"
                await vector_db.delete(collection_name=collection_name, ids=[memory_id])

        metadata: dict[str, Any] = {
            "title": resolved_title, "bucket": normalized_bucket,
            "folder_id": effective_folder_id, "source": source,
            "memory_type": normalized_memory_type, "status": normalized_status,
            "person_id": person_id, "timestamp": now, "novelty_energy": novelty_energy,
        }

        await vector_db.add(
            collection_name=collection_name,
            embeddings=[vector], documents=[merged_content],
            metadatas=[RagEngine.sanitize_vector_metadata(metadata)], ids=[memory_id],
        )

        await repo.upsert_record(
            memory_id=memory_id, title=resolved_title, folder_id=effective_folder_id,
            bucket=normalized_bucket, content=merged_content, source=source,
            memory_type=normalized_memory_type, status=normalized_status,
            person_id=person_id, relation_memory_ids=relation_memory_ids,
            relation_aliases=relation_aliases, event_start_at=event_start_at,
            event_end_at=event_end_at, related_people=related_people,
            knowledge_type=knowledge_type, address_or_coord=address_or_coord,
            place_type=place_type, asset_type=asset_type,
            disposition_status=disposition_status, procedure_type=procedure_type,
            novelty_energy=novelty_energy, tags=None,
            core_tags=normalized_core_tags, diffusion_tags=normalized_diffusion_tags,
            opposing_tags=normalized_opposing_tags,
        )

        record = await repo.get_record(memory_id)
        item = (
            utils.build_record_item(record)
            if record is not None
            else {
                "id": memory_id, "title": resolved_title,
                "content_snippet": text[:280], "is_truncated": len(text) > 280,
                "metadata": metadata,
            }
        )

        return {"action": "create", "mode": mode, "id": memory_id, "novelty_energy": novelty_energy, "item": item}

    async def update(
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
        """按 memory_id 更新记忆内容、标题及标签。未传入的字段保留原始值。"""
        config = self._get_config()
        rag_engine = self._get_rag_engine()
        repo = await self._get_repo()

        record = await repo.get_record(memory_id)
        if record is None:
            return {"action": "update", "updated": 0, "items": []}

        normalized_core_tags = utils.normalize_tags(core_tags)
        normalized_diffusion_tags = utils.normalize_tags(diffusion_tags)
        normalized_opposing_tags = utils.normalize_tags(opposing_tags)

        old_collection = RagEngine.collection_name(record.bucket, record.folder_id)
        resolved_title = (title or "").strip() or record.title or utils.extract_title(record.content)
        new_body = (
            content.strip() if content is not None
            else utils.split_title_and_content(record.title, record.content)[1]
        )
        merged_content = utils.join_title_and_content(resolved_title, new_body)

        vector_db = get_vector_db_service(config.storage.vector_db_path)
        vector = await rag_engine.embed_text(merged_content)
        loaded = await vector_db.get(
            collection_name=old_collection, ids=[memory_id], include=["metadatas"]
        )
        metadata_row = VectorMath.safe_first_row(loaded.get("metadatas", [[]]))
        vector_metadata = metadata_row[0] if metadata_row else {}
        if not isinstance(vector_metadata, dict):
            vector_metadata = {}
        vector_metadata.update({
            "title": resolved_title, "bucket": record.bucket, "folder_id": record.folder_id,
        })

        await vector_db.delete(collection_name=old_collection, ids=[memory_id])
        await vector_db.add(
            collection_name=old_collection, ids=[memory_id],
            documents=[merged_content], embeddings=[vector],
            metadatas=[RagEngine.sanitize_vector_metadata(vector_metadata)],
        )

        updated = await repo.update_record(
            memory_id, title=resolved_title, content=merged_content,
            core_tags=normalized_core_tags, diffusion_tags=normalized_diffusion_tags,
            opposing_tags=normalized_opposing_tags, memory_type=memory_type, status=status,
            person_id=person_id, relation_memory_ids=relation_memory_ids,
            relation_aliases=relation_aliases, event_start_at=event_start_at,
            event_end_at=event_end_at, related_people=related_people,
            knowledge_type=knowledge_type, address_or_coord=address_or_coord,
            place_type=place_type, asset_type=asset_type,
            disposition_status=disposition_status, procedure_type=procedure_type,
        )
        if not updated:
            return {"action": "update", "updated": 0, "items": []}

        updated_record = await repo.get_record(memory_id)
        return {
            "action": "update", "updated": 1,
            "items": [utils.build_record_item(updated_record)] if updated_record is not None else [],
        }

    async def delete(self, *, memory_ids: list[str], hard: bool = False) -> dict[str, Any]:
        """删除指定记忆（默认软删除，hard=True 为硬删除）。

        软删除：仅标记 is_deleted=1，向量库保留。
        硬删除：同时从向量库和元数据库中永久移除。
        """
        repo = await self._get_repo()
        config = self._get_config()
        records = await repo.get_records_map(memory_ids, include_deleted=True)
        vector_db = get_vector_db_service(config.storage.vector_db_path)

        if hard:
            for record in records.values():
                collection = RagEngine.collection_name(record.bucket, record.folder_id)
                try:
                    await vector_db.delete(collection_name=collection, ids=[record.memory_id])
                except Exception:  # noqa: BLE001
                    continue
            deleted = await repo.hard_delete_records(memory_ids)
            return {"action": "delete", "mode": "hard", "deleted": deleted, "requested": len(memory_ids)}

        deleted = await repo.soft_delete_records(memory_ids)
        return {"action": "delete", "mode": "soft", "deleted": deleted, "requested": len(memory_ids)}
