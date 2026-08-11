"""Booku Memory Store 接口层内部辅助函数。

包含：
- 记录格式化工具（title/content 处理、record item 构建、metadata 提取）
- EPA 向量动力学检索管道
- 标签/路径标准化
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from src.kernel.vector_db import get_vector_db_service

from ..algorithm.rag_engine import RagEngine
from ..algorithm.vector_math import VectorMath

if TYPE_CHECKING:
    from .memory_store import BookuMemoryStoreService


# ======================================================================
# 路径 / 标签标准化
# ======================================================================

def normalize_folder_id(folder_id: str | None, default_folder_id: str) -> str:
    if folder_id and folder_id.strip():
        return folder_id.strip().lower()
    return default_folder_id.strip().lower() or "default"


def normalize_tags(tags: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for tag in tags or []:
        if not tag:
            continue
        value = tag.strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


# ======================================================================
# Title / Content 工具
# ======================================================================

def extract_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        return stripped[:80]
    return "未命名记忆"


def join_title_and_content(title: str, content: str) -> str:
    clean_title = title.strip()
    clean_content = content.strip()
    if not clean_title and not clean_content:
        return ""
    if clean_title and clean_content:
        return f"# {clean_title}\n{clean_content}"
    if clean_title:
        return clean_title
    return clean_content


def split_title_and_content(title: str, content: str) -> tuple[str, str]:
    resolved_title = title.strip() or extract_title(content)
    body = content
    heading = f"# {resolved_title}"
    if body.startswith(heading):
        body = body[len(heading):].lstrip("\n")
    return resolved_title, body


# ======================================================================
# 记录格式化
# ======================================================================

def metadata_from_record(record: Any) -> dict[str, Any]:
    return {
        "title": getattr(record, "title", ""),
        "folder_id": getattr(record, "folder_id", ""),
        "bucket": RagEngine._normalize_bucket(getattr(record, "bucket", "")),
        "source": getattr(record, "source", ""),
        "memory_type": getattr(record, "memory_type", "knowledge"),
        "status": getattr(record, "status", "active"),
        "person_id": getattr(record, "person_id", None),
        "relation_memory_ids": list(getattr(record, "relation_memory_ids", [])),
        "relation_aliases": list(getattr(record, "relation_aliases", [])),
        "event_start_at": float(getattr(record, "event_start_at", 0.0) or 0.0),
        "event_end_at": float(getattr(record, "event_end_at", 0.0) or 0.0),
        "related_people": list(getattr(record, "related_people", [])),
        "knowledge_type": getattr(record, "knowledge_type", ""),
        "address_or_coord": getattr(record, "address_or_coord", ""),
        "place_type": getattr(record, "place_type", ""),
        "asset_type": getattr(record, "asset_type", ""),
        "disposition_status": getattr(record, "disposition_status", ""),
        "procedure_type": getattr(record, "procedure_type", ""),
        "novelty_energy": getattr(record, "novelty_energy", 0.0),
        "created_at": getattr(record, "created_at", 0.0),
        "updated_at": getattr(record, "updated_at", 0.0),
        "last_activated_at": getattr(record, "last_activated_at", 0.0),
        "activation_count": getattr(record, "activation_count", 0),
        "is_deleted": bool(getattr(record, "is_deleted", False)),
        "deleted_at": getattr(record, "deleted_at", 0.0),
        "tags": list(getattr(record, "tags", [])),
        "core_tags": list(getattr(record, "core_tags", [])),
        "diffusion_tags": list(getattr(record, "diffusion_tags", [])),
        "opposing_tags": list(getattr(record, "opposing_tags", [])),
    }


def build_record_item(
    record: Any,
    *,
    snippet_length: int = 280,
    include_full_content: bool = False,
) -> dict[str, Any]:
    title, pure_content = split_title_and_content(
        str(getattr(record, "title", "") or ""),
        str(getattr(record, "content", "") or ""),
    )
    truncated = len(pure_content) > snippet_length
    snippet = pure_content[:snippet_length] + ("..." if truncated else "")
    payload: dict[str, Any] = {
        "id": str(getattr(record, "memory_id", "")),
        "title": title,
        "content_snippet": snippet,
        "is_truncated": truncated,
        "metadata": metadata_from_record(record),
    }
    if include_full_content:
        payload["content"] = pure_content
    return payload


# ======================================================================
# EPA 检索管道
# ======================================================================

async def execute_epa_retrieval(
    svc: "BookuMemoryStoreService",
    query_text: str,
    query_vector: list[float],
    query_core_tags: set[str],
    query_diffusion_tags: set[str],
    query_opposing_tags: set[str],
    query_tokens: set[str],
    n_results: int,
    collections: list[str],
) -> dict[str, Any]:
    """执行 EPA 向量动力学重塑后的语义检索。

    步骤：
    1. 初始检索，收集周围向量采样
    2. 计算投影熵逻辑深度和共振特征，确定 beta
    3. 用 TAG 标签三角构建核心/扩散/对立向量组
    4. 重塑查询向量并二次检索
    5. 去重器消除冗余，返回最终结果
    """
    config = svc._get_config()
    rag_engine = svc._get_rag_engine()
    rag_params = rag_engine.get_rag_params()
    repo = await svc._get_repo()
    vector_db = get_vector_db_service(config.storage.vector_db_path)

    async def _collect_candidates(
        embedding: list[float], per_collection_limit: int
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for collection in collections:
            if await vector_db.count(collection) <= 0:
                continue
            result = await vector_db.query(
                collection_name=collection,
                query_embeddings=[embedding],
                n_results=per_collection_limit,
                include=["embeddings", "metadatas", "documents", "distances"],
            )
            ids_row = VectorMath.safe_first_row(result.get("ids", [[]]))
            documents_row = VectorMath.safe_first_row(result.get("documents", [[]]))
            metadatas_row = VectorMath.safe_first_row(result.get("metadatas", [[]]))
            embeddings_row = VectorMath.safe_first_row(result.get("embeddings", [[]]))
            for index, memory_id in enumerate(ids_row):
                item_embedding = VectorMath.to_float_vector(
                    embeddings_row[index] if index < len(embeddings_row) else [],
                    expected_dim=len(embedding),
                )
                collected.append({
                    "memory_id": memory_id,
                    "document": documents_row[index] if index < len(documents_row) else "",
                    "metadata": metadatas_row[index] if index < len(metadatas_row) else {},
                    "embedding": item_embedding,
                    "score": 0.0,
                    "collection": collection,
                })
        return collected

    initial_candidates = await _collect_candidates(
        query_vector, max(n_results, config.write_conflict.top_n)
    )
    initial_records = await repo.get_records_map(
        [str(item["memory_id"]) for item in initial_candidates if item.get("memory_id")]
    )

    for item in initial_candidates:
        memory_id = str(item.get("memory_id", ""))
        record = initial_records.get(memory_id)
        if record is not None:
            item["metadata"] = metadata_from_record(record)

    evidence_vectors = [
        VectorMath.to_float_vector(item.get("embedding", []), expected_dim=len(query_vector))
        for item in initial_candidates if item.get("embedding") is not None
    ]
    logic_depth = VectorMath.projection_entropy_logic_depth(query_vector, evidence_vectors)
    resonance = RagEngine.estimate_resonance(
        query_text, query_core_tags, query_diffusion_tags, query_opposing_tags
    )
    beta = VectorMath.clamp(
        config.retrieval.base_beta
        + logic_depth * config.retrieval.logic_depth_scale
        + (0.1 if resonance else 0.0),
        0.0, 1.0,
    )

    core_vectors: list[tuple[list[float], float]] = []
    diffusion_vectors: list[tuple[list[float], float]] = []
    opposing_vectors: list[tuple[list[float], float]] = []
    core_boost_center = (rag_params.core_boost_min + rag_params.core_boost_max) / 2

    for item in initial_candidates:
        embedding = VectorMath.to_float_vector(
            item.get("embedding", []), expected_dim=len(query_vector)
        )
        if len(embedding) != len(query_vector):
            continue
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            continue

        item_core_tags = set(VectorMath.safe_list(metadata.get("core_tags", [])))
        item_diffusion_tags = set(VectorMath.safe_list(metadata.get("diffusion_tags", [])))
        item_opposing_tags = set(VectorMath.safe_list(metadata.get("opposing_tags", [])))

        similarity = max(0.0, VectorMath.cosine_similarity(query_vector, embedding))
        if similarity <= 1e-12:
            continue

        core_match = (query_core_tags or query_tokens) & item_core_tags
        if core_match:
            core_vectors.append((embedding, similarity * core_boost_center * len(core_match)))

        diffusion_match = (query_diffusion_tags or query_tokens) & item_diffusion_tags
        if diffusion_match:
            diffusion_vectors.append((
                embedding,
                similarity * config.retrieval.diffusion_boost * len(diffusion_match),
            ))

        opposing_match = (query_opposing_tags or query_tokens) & item_opposing_tags
        if opposing_match:
            opposing_vectors.append((
                embedding,
                similarity * config.retrieval.opposing_penalty * len(opposing_match),
            ))

    reshaped_vector = VectorMath.reshape_query_vector(
        query_vector, beta=beta, core_vectors=core_vectors,
        diffusion_vectors=diffusion_vectors, opposing_vectors=opposing_vectors,
        energy_cutoff=rag_params.energy_cutoff,
    )
    if VectorMath.vector_norm_sq(reshaped_vector) <= 1e-12:
        reshaped_vector = query_vector

    candidates = await _collect_candidates(reshaped_vector, n_results)
    records = await repo.get_records_map(
        [str(item["memory_id"]) for item in candidates if item.get("memory_id")]
    )

    for item in candidates:
        memory_id = str(item.get("memory_id", ""))
        record = records.get(memory_id)
        if record is None:
            continue
        metadata = metadata_from_record(record)
        item["metadata"] = metadata
        item["document"] = record.content or item.get("document", "")
        similarity = VectorMath.cosine_similarity(
            reshaped_vector,
            VectorMath.to_float_vector(
                item.get("embedding", []), expected_dim=len(reshaped_vector)
            ),
        )
        item["score"] = rag_engine.match_score_with_tags(
            query_text=query_text, similarity=similarity, metadata=metadata, beta=beta,
            query_core_tags=query_core_tags, query_diffusion_tags=query_diffusion_tags,
            query_opposing_tags=query_opposing_tags,
        )

    selected = svc._deduplicator.select(
        candidates, limit=n_results,
        similarity_threshold=rag_params.deduplication_threshold,
    )
    results: list[dict[str, Any]] = []
    for item in selected:
        memory_id = str(item.get("memory_id", ""))
        record = records.get(memory_id)
        if record is not None:
            output_item = build_record_item(record)
        else:
            continue
        output_item["score"] = float(item.get("score", 0.0))
        output_item["collection"] = str(item.get("collection", ""))
        results.append(output_item)

    for item in results:
        mid = str(item.get("id", ""))
        if mid:
            await repo.update_activated(mid)

    return {
        "query": query_text, "logic_depth": logic_depth, "resonance": resonance,
        "beta": beta, "total": len(results), "results": results,
    }
