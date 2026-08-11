"""Booku Memory Store RAG 检索引擎。

实现 EPA 向量动力学重塑检索和写入判重合并算法。
将核心算法从 service 层解耦到独立的算法层。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    get_model_set_by_task,
)

from .deduplicator import ResultDeduplicator
from .vector_math import VectorMath

_MEMORY_BUCKET = "memory"
_KNOWLEDGE_BUCKET = "knowledge"


@dataclass(slots=True)
class _RagParams:
    """RAG 热参数。"""
    deduplication_threshold: float
    core_boost_min: float
    core_boost_max: float
    energy_cutoff: float


class RagEngine:
    """RAG 检索引擎，封装向量检索、EPA 重塑和写入判重。

    作为无状态的算法组件，通过依赖注入获取配置、embedding、向量库等外部资源。
    """

    def __init__(
        self,
        *,
        embedding_task: str = "embedding",
        rag_params_file: Path | None = None,
        retrieval_config: Any = None,
        write_conflict_config: Any = None,
    ) -> None:
        self._embedding_task = embedding_task
        self._rag_params_file = rag_params_file
        self._retrieval_config = retrieval_config
        self._write_conflict_config = write_conflict_config
        self._deduplicator = ResultDeduplicator()
        self._rag_params_cache: _RagParams | None = None
        self._rag_params_mtime: float = -1.0

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        model_set = get_model_set_by_task(self._embedding_task)
        request = create_embedding_request(
            model_set=model_set,
            request_name="booku_memory_store_embedding",
            inputs=[text],
        )
        response = await request.send()
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            raise RuntimeError("Embedding 请求返回为空")
        return [float(value) for value in embeddings[0]]

    # ------------------------------------------------------------------
    # RAG 参数管理
    # ------------------------------------------------------------------

    def _default_rag_params(self) -> _RagParams:
        retrieval = self._retrieval_config
        write_conflict = self._write_conflict_config
        return _RagParams(
            deduplication_threshold=float(getattr(retrieval, "deduplication_threshold", 0.88)),
            core_boost_min=float(getattr(retrieval, "core_boost_min", 1.2)),
            core_boost_max=float(getattr(retrieval, "core_boost_max", 1.4)),
            energy_cutoff=float(getattr(write_conflict, "energy_cutoff", 0.1)),
        )

    def _load_rag_params_from_file(self, default_params: _RagParams) -> _RagParams:
        if self._rag_params_file is None:
            return default_params
        path = self._rag_params_file
        if not path.exists():
            return default_params

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default_params

        if not isinstance(payload, dict):
            return default_params

        dedup = float(
            payload.get("deduplicationThreshold", default_params.deduplication_threshold)
        )
        core_boost_range = payload.get(
            "coreBoostRange",
            [default_params.core_boost_min, default_params.core_boost_max],
        )
        if isinstance(core_boost_range, list | tuple) and len(core_boost_range) >= 2:
            core_min = float(core_boost_range[0])
            core_max = float(core_boost_range[1])
        else:
            core_min = default_params.core_boost_min
            core_max = default_params.core_boost_max
        if core_min > core_max:
            core_min, core_max = core_max, core_min

        energy_cutoff = float(payload.get("energyCutoff", default_params.energy_cutoff))

        return _RagParams(
            deduplication_threshold=VectorMath.clamp(dedup, 0.0, 1.0),
            core_boost_min=VectorMath.clamp(core_min, 1.0, 2.0),
            core_boost_max=VectorMath.clamp(core_max, 1.0, 2.0),
            energy_cutoff=VectorMath.clamp(energy_cutoff, 0.0, 1.0),
        )

    def get_rag_params(self) -> _RagParams:
        default_params = self._default_rag_params()
        if self._rag_params_file is None:
            return default_params

        path = self._rag_params_file
        current_mtime = path.stat().st_mtime if path.exists() else -1.0
        if self._rag_params_cache is not None and abs(current_mtime - self._rag_params_mtime) < 0.001:
            return self._rag_params_cache

        loaded = self._load_rag_params_from_file(default_params)
        self._rag_params_cache = loaded
        self._rag_params_mtime = current_mtime
        return loaded

    # ------------------------------------------------------------------
    # 集合名称
    # ------------------------------------------------------------------

    @staticmethod
    def collection_name(bucket: str, folder_id: str) -> str:
        safe_bucket = RagEngine._normalize_bucket(bucket)
        if safe_bucket == _KNOWLEDGE_BUCKET:
            return "booku_ms__knowledge"
        safe_folder = folder_id.strip().lower() or "default"
        return f"booku_ms__memory__{safe_folder}"

    @staticmethod
    def _normalize_bucket(bucket: str | None) -> str:
        normalized = str(bucket or "").strip().lower()
        if normalized == _KNOWLEDGE_BUCKET:
            return _KNOWLEDGE_BUCKET
        return _MEMORY_BUCKET

    @classmethod
    def _memory_collection_candidates(cls, folder_id: str) -> list[str]:
        safe_folder = folder_id.strip().lower() or "default"
        candidates = [
            cls.collection_name(_MEMORY_BUCKET, safe_folder),
        ]
        deduped: list[str] = []
        for item in candidates:
            if item not in deduped:
                deduped.append(item)
        return deduped

    @classmethod
    def collection_candidates(cls, bucket: str, folder_id: str) -> list[str]:
        normalized_bucket = cls._normalize_bucket(bucket)
        if normalized_bucket == _KNOWLEDGE_BUCKET:
            return [cls.collection_name(_KNOWLEDGE_BUCKET, "default")]
        return cls._memory_collection_candidates(folder_id)

    # ------------------------------------------------------------------
    # TAG 标签匹配与共振估算
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_resonance(
        query_text: str,
        query_core_tags: set[str],
        query_diffusion_tags: set[str],
        query_opposing_tags: set[str],
    ) -> bool:
        explicit_domain_count = sum(
            1
            for tag_set in (query_core_tags, query_diffusion_tags, query_opposing_tags)
            if len(tag_set) > 0
        )
        if explicit_domain_count >= 2:
            return True

        markers = ("并且", "同时", "以及", "cross", "across", "对比")
        lower_text = query_text.lower()
        return any(marker in lower_text for marker in markers)

    def match_score_with_tags(
        self,
        query_text: str,
        similarity: float,
        metadata: dict[str, Any],
        beta: float,
        *,
        query_core_tags: set[str] | None = None,
        query_diffusion_tags: set[str] | None = None,
        query_opposing_tags: set[str] | None = None,
    ) -> float:
        rag_params = self.get_rag_params()
        retrieval = self._retrieval_config
        query_tokens = {token for token in query_text.lower().split() if token}

        core_tags = set(metadata.get("core_tags", []) or [])
        diffusion_tags = set(metadata.get("diffusion_tags", []) or [])
        opposing_tags = set(metadata.get("opposing_tags", []) or [])

        core_overlap = len((query_core_tags or query_tokens) & core_tags)
        diffusion_overlap = len((query_diffusion_tags or query_tokens) & diffusion_tags)
        opposing_overlap = len((query_opposing_tags or query_tokens) & opposing_tags)

        core_boost = (rag_params.core_boost_min + rag_params.core_boost_max) / 2
        score_delta = (
            core_boost * core_overlap
            + getattr(retrieval, "diffusion_boost", 0.3) * diffusion_overlap
            - getattr(retrieval, "opposing_penalty", 0.5) * opposing_overlap
        )
        return similarity + beta * score_delta

    # ------------------------------------------------------------------
    # 元数据工具
    # ------------------------------------------------------------------

    @staticmethod
    def sanitize_vector_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, str | int | float | bool):
                cleaned[key] = value
        return cleaned

    @staticmethod
    def _resolve_collection_name_for_id(
        memory_id: str, bucket: str, folder_id: str, collection_name_func,
    ) -> str:
        return collection_name_func(bucket, folder_id)

    __all__ = ["RagEngine"]
