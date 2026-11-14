"""表情包向量索引。

直接复用 emoji_sender 的检索语义：每个 ``(emoji_id, tag)`` 一条向量，
同一表情的所有 tag 共用同一份 description embedding。metadata 不保存
文件路径，路径始终以 SQLite 为准。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TypedDict, cast

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    get_model_set_by_task,
)
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

from .types import VectorHit

logger = get_logger("neo_emoji_sender.vector")

_SCAN_PAGE_LIMIT = 512
_MAX_SCAN_PAGES = 1000


class EmojiVectorMetadata(TypedDict):
    """单条向量 metadata（每个 ``(emoji_id, tag)`` 一条）。"""

    emoji_id: str
    tag: str
    description: str
    revision: str
    updated_at: float


class IndexMeta(TypedDict):
    """``index_meta.json`` 中记录的 embedding 配置指纹。"""

    embedding_model: str
    collection: str


class EmojiVectorIndex:
    """Chroma 向量索引封装。"""

    def __init__(self, *, db_path: str, collection_name: str, meta_path: str) -> None:
        self._db_path = db_path
        self.collection = collection_name
        self._meta_path = Path(meta_path)

    def _vdb(self):
        return get_vector_db_service(self._db_path)

    async def initialize(self) -> None:
        """创建集合并校验 / 写入索引元信息。"""
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        await self._ensure_index_meta()

    async def embed(self, text: str, request_name: str = "neo_emoji_sender_embedding") -> list[float] | None:
        """生成文本 embedding，失败返回 None。"""
        value = str(text or "").strip()
        if not value:
            return None
        try:
            model_set = get_model_set_by_task("embedding")
        except Exception as e:
            logger.warning(f"未配置 embedding 任务模型: {e}")
            return None
        try:
            request = create_embedding_request(
                model_set=model_set,
                request_name=request_name,
                inputs=[value],
            )
            response = await request.send()
            return list(response.embeddings[0])
        except Exception as e:
            logger.warning(f"生成 embedding 失败: {e}")
            return None

    async def upsert(
        self,
        *,
        emoji_id: str,
        description: str,
        tags: list[str],
        revision: str,
        updated_at: float,
    ) -> bool:
        """重建某个表情包的全部 tag 向量。"""
        clean_tags = sorted({str(tag).strip() for tag in tags if str(tag).strip()})
        if not clean_tags:
            return False

        embedding = await self.embed(description)
        if embedding is None:
            return False

        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        try:
            await vdb.delete(collection_name=self.collection, where={"emoji_id": emoji_id})
        except Exception as e:
            logger.warning(f"删除旧向量失败: {emoji_id[:8]}... - {e}")
            return False

        ids = [f"{emoji_id}:{tag}" for tag in clean_tags]
        metadatas: list[EmojiVectorMetadata] = [
            EmojiVectorMetadata(
                emoji_id=emoji_id,
                tag=tag,
                description=description,
                revision=revision,
                updated_at=float(updated_at),
            )
            for tag in clean_tags
        ]
        try:
            await vdb.add(
                collection_name=self.collection,
                ids=ids,
                embeddings=[list(embedding) for _ in clean_tags],
                documents=[description for _ in clean_tags],
                metadatas=metadatas,
            )
        except Exception as e:
            logger.warning(f"写入向量失败: {emoji_id[:8]}... - {e}")
            return False

        # 包装层会吞掉 add 异常，必须回查确认。
        try:
            data = await vdb.get(
                collection_name=self.collection,
                where={"emoji_id": emoji_id},
                limit=len(ids) + 1,
                include=["metadatas"],
            )
        except Exception as e:
            logger.warning(f"确认向量写入失败: {emoji_id[:8]}... - {e}")
            return False
        stored = {str(item) for item in (data.get("ids") or [])}
        return set(ids).issubset(stored)

    async def delete(self, emoji_id: str) -> bool:
        """删除某个表情包的全部向量，并确认无残留。"""
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        try:
            await vdb.delete(collection_name=self.collection, where={"emoji_id": emoji_id})
        except Exception as e:
            logger.warning(f"删除向量失败: {emoji_id[:8]}... - {e}")
            return False
        try:
            data = await vdb.get(
                collection_name=self.collection,
                where={"emoji_id": emoji_id},
                limit=1,
                include=["metadatas"],
            )
        except Exception as e:
            logger.warning(f"确认向量删除失败: {emoji_id[:8]}... - {e}")
            return False
        return not bool(data.get("ids"))

    async def search(
        self,
        *,
        query: str,
        tags: list[str] | None,
        limit: int,
        exclude_ids: set[str] | None = None,
    ) -> list[VectorHit]:
        """向量检索并按 emoji_id 去重，保留最小距离。"""
        text = str(query or "").strip()
        if not text:
            return []
        embedding = await self.embed(text, "neo_emoji_sender_search")
        if embedding is None:
            return []

        exclude = set(exclude_ids or ())
        fetch_n = min(max((limit + len(exclude)) * 3, 20), 200)
        where = {"tag": list(tags)} if tags else None

        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        results = await vdb.query(
            collection_name=self.collection,
            query_embeddings=[embedding],
            n_results=fetch_n,
            where=where,
        )

        ids_list: list[list[str]] = list(results.get("ids") or [])
        distances_list: list[list[float]] = list(results.get("distances") or [])
        metadatas_list = cast(list[list[EmojiVectorMetadata]], results.get("metadatas") or [])
        if not ids_list or not ids_list[0]:
            return []

        best: dict[str, VectorHit] = {}
        for index, _ in enumerate(ids_list[0]):
            distance = 999.0
            if distances_list and distances_list[0] and index < len(distances_list[0]):
                distance = float(distances_list[0][index])
            meta = (
                metadatas_list[0][index]
                if metadatas_list and metadatas_list[0] and index < len(metadatas_list[0])
                else {}
            )
            emoji_id = str(meta.get("emoji_id") or "").strip()
            tag = str(meta.get("tag") or "").strip()
            if not emoji_id or not tag or emoji_id in exclude:
                continue
            existing = best.get(emoji_id)
            if existing is None or distance < existing.distance:
                best[emoji_id] = VectorHit(emoji_id=emoji_id, tag=tag, distance=distance)

        ordered = sorted(best.values(), key=lambda item: item.distance)
        return ordered[: max(0, int(limit))]

    async def scan(self) -> dict[str, list[EmojiVectorMetadata]]:
        """扫描全部向量 metadata，按 emoji_id 分组。"""
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        grouped: dict[str, list[EmojiVectorMetadata]] = {}
        offset = 0
        for _page in range(_MAX_SCAN_PAGES):
            data = await vdb.get(
                collection_name=self.collection,
                limit=_SCAN_PAGE_LIMIT,
                offset=offset,
                include=["metadatas"],
            )
            ids: list[str] = list(data.get("ids") or [])
            if not ids:
                break
            metadatas = cast(list[EmojiVectorMetadata], data.get("metadatas") or [])
            for index, record_id in enumerate(ids):
                meta = metadatas[index] if index < len(metadatas) else {}
                emoji_id = str(meta.get("emoji_id") or "").strip()
                if not emoji_id:
                    emoji_id = record_id.split(":", 1)[0].strip()
                if emoji_id:
                    grouped.setdefault(emoji_id, []).append(dict(meta))
            offset += len(ids)
        return grouped

    async def count(self) -> int:
        """统计向量条数。"""
        vdb = self._vdb()
        await vdb.get_or_create_collection(self.collection)
        try:
            return int(await vdb.count(self.collection))
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # index meta
    # ------------------------------------------------------------------

    async def _ensure_index_meta(self) -> None:
        """校验 embedding 模型指纹；不一致时清空集合并重建。"""
        current = await asyncio.to_thread(self._read_meta)
        expected = self._current_meta()
        if current is None:
            await asyncio.to_thread(self._write_meta, expected)
            return
        if current.get("embedding_model") == expected.get("embedding_model"):
            return
        logger.warning(
            "embedding 模型已变化，清空向量集合并重建: "
            f"{current.get('embedding_model')!r} -> {expected.get('embedding_model')!r}"
        )
        vdb = self._vdb()
        await vdb.delete_collection(self.collection)
        await vdb.get_or_create_collection(self.collection)
        await asyncio.to_thread(self._write_meta, expected)

    def _current_meta(self) -> IndexMeta:
        """读取当前 embedding 配置指纹。"""
        model_name = ""
        try:
            model_set = get_model_set_by_task("embedding")
            if model_set:
                model_name = str(model_set[0].get("model_identifier") or "")
        except Exception:
            pass
        return IndexMeta(embedding_model=model_name, collection=self.collection)

    def _read_meta(self) -> IndexMeta | None:
        """读取 index_meta.json。"""
        if not self._meta_path.exists():
            return None
        try:
            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return cast(IndexMeta, data) if isinstance(data, dict) else None

    def _write_meta(self, data: IndexMeta) -> None:
        """写入 index_meta.json。"""
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        self._meta_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


__all__ = ["EmojiVectorIndex", "EmojiVectorMetadata", "IndexMeta"]
