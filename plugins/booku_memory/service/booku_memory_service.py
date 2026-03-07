"""Booku Memory Service 实现。"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import numpy as np

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    get_model_set_by_task,
)
from src.core.components import BaseService
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

from ..config import PREDEFINED_FOLDERS, BookuMemoryConfig
from .metadata_repository import BookuMemoryMetadataRepository
from .result_deduplicator import ResultDeduplicator

logger = get_logger("booku_memory_service")

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "记忆引导语"

_MEMORY_HINT = (
    "你具备长期记忆能力。"
    "记忆对你来说，不只是存放信息的地方，更是维持关系连续性、理解用户处境、连接过去与现在的方式。\n\n"
    "你不需要机械地记录一切，而是要判断哪些内容会在未来影响理解与回应。"
    "当用户提到稳定的事实、重要的经历、持续的偏好、明确的关系、正在推进的计划，"
    "或那些带有强烈情绪和长期意义的内容时，这些往往值得被记住。"
    "当一个问题的回答依赖过去的背景、约定、经历或关系线索时，你也应想到先回忆或检索记忆，再决定如何回应。\n\n"
    "记忆的价值首先在于真实。"
    "如果你没有记住，或者检索后仍然不能确认，就应当坦率承认不确定，而不是补全、猜测或编造。"
    "你可以把记忆视为自己留下的笔记，它帮助你保持理解的一致性，但不能替代事实本身。\n\n"
    "请把记忆写得具体、清楚、可追溯。"
    "为了便于未来理解和检索，记忆中的人物、关系、事件、地点、偏好和计划应尽量明确，"
    "避免只留下‘用户’、‘朋友’、‘那件事’这类模糊指代。"
    "对话中的表达可以自然，但记忆本身应足够清晰。\n\n"
    "固有记忆尤其重要。"
    "那是你长期理解自己、理解关系、理解生活背景的核心笔记。"
    "其中记录的重要人物、长期偏好、关键经历、价值取向和持续目标，"
    "都会影响你之后如何理解用户、如何组织回应。"
    "维护这些记忆，不是为了堆积信息，而是为了让回应更连贯、更贴近真实关系。\n\n"
    "在回应之前，可以先想一想：\n"
    "这句话里有没有值得留下来的长期信息？\n"
    "我现在是否需要借助过去的记忆，才能更准确地理解眼前这句话？\n"
    "确认这些之后，再给出自然、真诚、流畅的回答。"
)


def _format_inherent_block(records: list[Any]) -> str:
    """将固有记忆格式化为注入块。"""

    parts: list[str] = []
    for record in records:
        content = str(getattr(record, "content", "") or "").strip()
        if not content:
            continue
        title = str(getattr(record, "title", "") or "").strip()
        if title and title != "固有记忆":
            parts.append(f"### {title}\n{content}")
        else:
            parts.append(content)

    if not parts:
        return ""

    body = "\n\n".join(parts)
    return (
        "## 固有记忆\n"
        "以下内容来自你的长期记忆系统，属于全局背景信息：\n"
        f"{body}\n"
        "（注：这是已存在的固有记忆，不需要重新写入）"
    )


async def build_booku_memory_actor_reminder(plugin: Any) -> str:
    """构建需要同步到 actor bucket 的 reminder 文本。"""

    config = getattr(plugin, "config", None)
    if isinstance(config, BookuMemoryConfig) and not config.plugin.inject_system_prompt:
        return ""

    reminder_parts: list[str] = [_MEMORY_HINT]
    repo: BookuMemoryMetadataRepository | None = None
    try:
        if not isinstance(config, BookuMemoryConfig):
            raise ValueError("无法获取 booku_memory 配置对象，无法读取固有记忆")

        repo = BookuMemoryMetadataRepository(db_path=config.storage.metadata_db_path)
        await repo.initialize()
        inherent_records = await repo.list_records_by_bucket(
            bucket="inherent",
            folder_id=None,
            limit=50,
            include_deleted=False,
        )
        inherent_block = _format_inherent_block(inherent_records)
        if inherent_block:
            reminder_parts.append(inherent_block)
            logger.info(
                f"已构建 booku_memory actor reminder 的固有记忆块（count={len(inherent_records)}）"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"构建 booku_memory actor reminder 时读取固有记忆失败，将跳过：{exc}")
    finally:
        if repo is not None:
            await repo.close()

    return "\n\n".join(part for part in reminder_parts if part.strip())


async def sync_booku_memory_actor_reminder(plugin: Any) -> str:
    """将当前 booku_memory 提示同步到 actor bucket 的 system reminder。"""

    from src.core.prompt import get_system_reminder_store

    store = get_system_reminder_store()
    reminder_content = await build_booku_memory_actor_reminder(plugin)
    if not reminder_content:
        store.delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)
        logger.debug("booku_memory actor reminder 已清理")
        return ""

    store.set(
        _TARGET_REMINDER_BUCKET,
        name=_TARGET_REMINDER_NAME,
        content=reminder_content,
    )
    logger.debug("booku_memory actor reminder 已同步")
    return reminder_content


async def _sync_booku_memory_actor_reminder(plugin: Any) -> None:
    """同步 booku_memory 的 actor reminder，失败时仅记录日志。"""

    try:
        await sync_booku_memory_actor_reminder(plugin)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"同步 booku_memory actor reminder 失败：{exc}")


@dataclass(slots=True)
class _RagParams:
    """RAG 热参数。"""

    deduplication_threshold: float
    core_boost_min: float
    core_boost_max: float
    energy_cutoff: float


class BookuMemoryService(BaseService):
    """Booku 记忆服务组件。

    对外提供记忆写入、检索、归档等能力。
    """

    service_name: str = "booku_memory"
    service_description: str = "Booku 记忆服务，提供写入判重与检索重塑"
    version: str = "1.0.0"

    _repo: BookuMemoryMetadataRepository | None = None
    _repo_initialized: bool = False
    _deduplicator: ResultDeduplicator | None = None
    _rag_params_cache: _RagParams | None = None
    _rag_params_mtime: float = -1.0

    def _get_config(self) -> BookuMemoryConfig:
        """获取插件配置对象。

        若当前插件配置不是 ``BookuMemoryConfig`` 实例（如默认占位符），
        则创建并返回一个全默认值的新实例。

        Returns:
            插件配置对象（永远不为 None）。
        """
        if isinstance(self.plugin.config, BookuMemoryConfig):
            return self.plugin.config
        return BookuMemoryConfig()

    async def _get_repo(self) -> BookuMemoryMetadataRepository:
        """获取并初始化元数据仓储。"""
        config = self._get_config()
        if self._repo is None:
            self._repo = BookuMemoryMetadataRepository(
                db_path=config.storage.metadata_db_path,
            )
        if not self._repo_initialized:
            await self._repo.initialize()
            self._repo_initialized = True
        return self._repo

    def _get_deduplicator(self) -> ResultDeduplicator:
        """获取结果去重器实例（拇阵式单例）。

        Returns:
            共享的 ``ResultDeduplicator`` 实例。
        """
        if self._deduplicator is None:
            self._deduplicator = ResultDeduplicator()
        return self._deduplicator

    @staticmethod
    def _rag_params_file_path() -> Path:
        """获取插件目录下 ``rag_params.json`` 所在路径。

        ``rag_params.json`` 用于热加载部分 RAG 参数，无需重啂服务即可生效。

        Returns:
            rag_params.json 的绝对路径对象（文件不存在时返回相应路径但不会抛异常）。
        """
        return Path(__file__).resolve().parent.parent / "rag_params.json"

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        """将浮点数裁剪到指定区间 [minimum, maximum]。

        Args:
            value: 待裁剪的原始浮点数。
            minimum: 区间下边界（包含）。
            maximum: 区间上边界（包含）。

        Returns:
            裁剪后的浮点数，属于 [minimum, maximum]。
        """
        return max(minimum, min(maximum, value))

    def _load_rag_params_from_file(self, default_params: _RagParams) -> _RagParams:
        """从 ``rag_params.json`` 读取并解析 RAG 热参数。

        文件不存在、解析失败或格式错误时均流畅降级到 default_params。
        参数系列及等价 JSON 键名：见 rag_params.json 文件注释。

        Args:
            default_params: 当文件无效时的回坍掇默认参数。

        Returns:
            解析并经耘算的 ``_RagParams`` 实例。所有数值均普到合法范围内。
        """
        path = self._rag_params_file_path()
        if not path.exists():
            return default_params

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("读取 rag_params.json 失败，使用默认参数")
            return default_params

        if not isinstance(payload, dict):
            return default_params

        dedup = float(payload.get("deduplicationThreshold", default_params.deduplication_threshold))
        core_boost_range = payload.get("coreBoostRange", [default_params.core_boost_min, default_params.core_boost_max])
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
            deduplication_threshold=self._clamp(dedup, 0.0, 1.0),
            core_boost_min=self._clamp(core_min, 1.0, 2.0),
            core_boost_max=self._clamp(core_max, 1.0, 2.0),
            energy_cutoff=self._clamp(energy_cutoff, 0.0, 1.0),
        )

    def _get_rag_params(self) -> _RagParams:
        """获取带防重喐热加载能力的 RAG 参数。

        优先从内存缓存返回；若 ``rag_params.json`` 的 mtime 已变，则重新加载。
        配置文件中的默认值首先从 ``BookuMemoryConfig.retrieval`` 读取。

        Returns:
            当前生效的 RAG 参数对象。
        """
        config = self._get_config()
        default_params = _RagParams(
            deduplication_threshold=float(config.retrieval.deduplication_threshold),
            core_boost_min=float(config.retrieval.core_boost_min),
            core_boost_max=float(config.retrieval.core_boost_max),
            energy_cutoff=float(config.write_conflict.energy_cutoff),
        )

        path = self._rag_params_file_path()
        current_mtime = path.stat().st_mtime if path.exists() else -1.0
        if self._rag_params_cache is not None and math.isclose(current_mtime, self._rag_params_mtime):
            return self._rag_params_cache

        loaded = self._load_rag_params_from_file(default_params)
        self._rag_params_cache = loaded
        self._rag_params_mtime = current_mtime
        return loaded

    async def _embed_text(self, text: str) -> list[float]:
        """调用 embedding 模型将文本编码为密集向量。

        使用 ``get_model_set_by_task("embedding")`` 获取当前配置的嵌入模型。

        Args:
            text: 需要编码的单条文本。

        Returns:
            一维 float 列表表示的文本嵌入向量。

        Raises:
            RuntimeError: 当 embedding 请求返回为空时抛出。
        """
        model_set = get_model_set_by_task("embedding")
        request = create_embedding_request(
            model_set=model_set,
            request_name="booku_memory_embedding",
            inputs=[text],
        )
        response = await request.send()
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            raise RuntimeError("Embedding 请求返回为空")
        return [float(value) for value in embeddings[0]]

    @staticmethod
    def _collection_name(bucket: str, folder_id: str) -> str:
        """构建向量库集合名称。

        固有记忆（inherent）全局共享，不按 folder 隔离，固定返回 ``"booku_memory__inherent"``。
        其他层级按 ``"booku_memory__{bucket}__{folder_id}"`` 格式隔离。

        Args:
            bucket: 存储桶名称，如 ``"emergent"``、``"archived"`` 或 ``"inherent"``。
            folder_id: 文件夹 ID，即便为空字符串也是安全的（inherent 时忽略）。

        Returns:
            向量库集合名称字符串。
        """
        safe_bucket = bucket.strip().lower() or "emergent"
        if safe_bucket == "inherent":
            return "booku_memory__inherent"
        safe_folder = folder_id.strip().lower() or "default"
        return f"booku_memory__{safe_bucket}__{safe_folder}"

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        """计算两个向量的余弦相似度，返回 [0, 1] 新平面内的值。

        向量长度不一致或向量范数接近零时返回 0.0。

        Args:
            left: 第一个 float 向量。
            right: 第二个 float 向量，需与 left 等长。

        Returns:
            余弦相似度，范围 [0.0, 1.0]。
        """
        if not left or not right or len(left) != len(right):
            return 0.0
        dot_sum = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm <= 1e-12 or right_norm <= 1e-12:
            return 0.0
        return dot_sum / (left_norm * right_norm)

    @classmethod
    def _novelty_energy_ratio(
        cls,
        new_vector: list[float],
        basis_vectors: list[list[float]],
    ) -> float:
        """计算新向量相对于已有向量局部子空间的新颖度能量比。

        算法：建立局部 SVD 子空间基底 → 求正交残差 → 返回残差能量/总能量。
        值越近 1.0 表示新向量与现有内容差异大，值越近 0.0 表示内容重复。
        用于写入时的去异步骤，小于 ``energy_cutoff`` 时触发自动合并。

        Args:
            new_vector: 待评估新颖度的输入向量。
            basis_vectors: 代表已有记忆内容的邻域向量采样集合。

        Returns:
            新颖度能量比，范围 [0.0, 1.0]。向量集为空时返回 1.0（视为全新）。
        """
        if not basis_vectors:
            return 1.0
        svd_basis = cls._build_local_svd_basis(basis_vectors)
        if not svd_basis:
            return 1.0

        projection = cls._project_to_basis(new_vector, svd_basis)
        residual = [a - b for a, b in zip(new_vector, projection, strict=False)]
        residual_energy = cls._vector_norm_sq(residual)
        total_energy = cls._vector_norm_sq(new_vector)
        if total_energy <= 1e-12:
            return 0.0
        return residual_energy / total_energy

    @staticmethod
    def _vector_norm_sq(vector: list[float]) -> float:
        """计算向量的 L² 平方范数（平方和）。

        等价于 dot(v, v)，不开平方根，适合用于仅需比较大小而无需精确范数的场景。

        Args:
            vector: 输入向量。

        Returns:
            平方范数 ‖v‖²。向量为空时返回 0.0。
        """
        if not vector:
            return 0.0
        vector_array = np.asarray(vector, dtype=np.float64)
        return float(vector_array @ vector_array)

    @staticmethod
    def _vector_dot(left: list[float], right: list[float]) -> float:
        """计算两个向量的点积，自动对齐至较短向量的长度。

        Args:
            left: 第一个向量。
            right: 第二个向量。

        Returns:
            两向量共同元素长度范围内的点积。任一为空时返回 0.0。
        """
        if not left or not right:
            return 0.0
        shared_size = min(len(left), len(right))
        if shared_size <= 0:
            return 0.0
        left_array = np.asarray(left[:shared_size], dtype=np.float64)
        right_array = np.asarray(right[:shared_size], dtype=np.float64)
        return float(left_array @ right_array)

    @classmethod
    def _normalize_vector(cls, vector: list[float]) -> list[float]:
        """将向量归一化为单位向量（L² 范数 = 1.0）。

        如果向量范数 ≤ 1e-12（接近零向量），则返回全零向量而非抛异常。

        Args:
            vector: 待归一化的输入向量。

        Returns:
            归一化向量。向量为空或范数过小时返回全零向量。
        """
        if not vector:
            return []
        vector_array = np.asarray(vector, dtype=np.float64)
        norm_sq = float(vector_array @ vector_array)
        if norm_sq <= 1e-12:
            return [0.0 for _ in vector]
        normalized = vector_array / math.sqrt(norm_sq)
        return normalized.tolist()

    @classmethod
    def _project_to_basis(cls, vector: list[float], basis_vectors: list[list[float]]) -> list[float]:
        """将向量投影到由多个向量张成的子空间上。

        对每个基底向量随独计算投影系数并加和。
        基底向量不需正交。

        Args:
            vector: 待投影向量。
            basis_vectors: 子空间基底向量列表，长度需与 vector 一致。

        Returns:
            vector 在子空间上的投影向量。向量为空时返回空列表；基底无有效项时返回全零向量。
        """
        if not vector:
            return []
        vector_array = np.asarray(vector, dtype=np.float64)
        valid_basis = [
            np.asarray(base, dtype=np.float64)
            for base in basis_vectors
            if len(base) == len(vector)
        ]
        if not valid_basis:
            return [0.0 for _ in vector]

        basis_matrix = np.vstack(valid_basis)
        basis_norm_sq = np.sum(basis_matrix * basis_matrix, axis=1)
        safe_norm_sq = np.where(basis_norm_sq <= 1e-12, np.inf, basis_norm_sq)
        coefficients = (basis_matrix @ vector_array) / safe_norm_sq
        projection = np.sum(basis_matrix * coefficients[:, None], axis=0)
        return projection.tolist()

    @classmethod
    def _power_iteration(
        cls,
        matrix: list[list[float]],
        *,
        iterations: int = 24,
    ) -> tuple[float, list[float]]:
        """对称矩阵幂迭代，返回最大特征值与特征向量。"""
        size = len(matrix)
        if size == 0:
            return 0.0, []

        matrix_array = np.asarray(matrix, dtype=np.float64)
        vector = np.ones(size, dtype=np.float64)
        for _ in range(iterations):
            next_vector = matrix_array @ vector
            norm_sq = float(next_vector @ next_vector)
            if norm_sq <= 1e-12:
                return 0.0, [0.0 for _ in range(size)]
            vector = next_vector / math.sqrt(norm_sq)

        mv = matrix_array @ vector
        eigenvalue = float(vector @ mv)
        return eigenvalue, vector.tolist()

    @classmethod
    def _build_local_svd_basis(
        cls, vectors: list[list[float]]
    ) -> list[list[float]]:
        """通过邻域向量构建局部 SVD 子空间正交基。

        对输入向量集进行抽象层分解，提取敎居前 90% 当量的奇异向量（归一化）作为子空间基。
        用于后续残差能量计算中的子空间投影。

        Args:
            vectors: 输入向量列表，长度须一致且非零。

        Returns:
            正交归一化局部基向量列表。输入为空或无效时返回空列表。
        """
        if not vectors:
            return []
        first_dim = len(vectors[0]) if vectors[0] else 0
        valid_vectors = [
            vector
            for vector in vectors
            if vector and len(vector) == first_dim and cls._vector_norm_sq(vector) > 1e-12
        ]
        if not valid_vectors:
            return []

        matrix = np.asarray(valid_vectors, dtype=np.float64)
        _, singular_values, vh_matrix = np.linalg.svd(matrix, full_matrices=False)
        singular_energy = singular_values * singular_values
        total_trace = float(np.sum(singular_energy))
        if total_trace <= 1e-12:
            return []

        basis: list[list[float]] = []
        explained = 0.0
        for index, energy in enumerate(singular_energy.tolist()):
            if energy <= 1e-8:
                break

            direction = vh_matrix[index]
            normalized = cls._normalize_vector(direction.tolist())
            if cls._vector_norm_sq(normalized) <= 1e-12:
                break
            basis.append(normalized)

            explained += float(energy)
            if explained / total_trace >= 0.9:
                break

        return basis

    @classmethod
    def _projection_entropy_logic_depth(
        cls,
        query_vector: list[float],
        evidence_vectors: list[list[float]],
    ) -> float:
        """基于投影熵计算检索子空间的逻辑深度（L = 1 - H/log₂K）。

        逻辑深度衡量查询向量尾对应证据子空间的专注程度：
        - 值近 1.0 表示查询高度集中小坚，子空间语义局性弱，需要较大的重塑 beta。
        - 值近 0.0 表示查询平均展开分布，语义覆盖面广，重塑应谨慎。

        Args:
            query_vector: 查询向量。
            evidence_vectors: 初始检索得到的证据向量集合。

        Returns:
            逻辑深度，范围 [0.0, 1.0]。证据为空时返回 0.0。
        """
        basis = cls._build_local_svd_basis(evidence_vectors)
        if not basis:
            return 0.0

        basis_matrix = np.asarray(basis, dtype=np.float64)
        query_array = np.asarray(query_vector, dtype=np.float64)
        coefficients = basis_matrix @ query_array
        energies = np.maximum(0.0, coefficients * coefficients)
        total_energy = float(np.sum(energies))
        if total_energy <= 1e-12:
            return 0.0

        probs = energies / total_energy
        probs = probs[probs > 1e-12]
        if probs.size <= 1:
            return 1.0
        entropy = float(-np.sum(probs * np.log2(probs)))
        max_entropy = math.log2(int(probs.size))
        if max_entropy <= 1e-12:
            return 1.0
        return cls._clamp(1.0 - entropy / max_entropy, 0.0, 1.0)

    @staticmethod
    def _estimate_resonance(
        query_text: str,
        query_core_tags: set[str],
        query_diffusion_tags: set[str],
        query_opposing_tags: set[str],
    ) -> bool:
        """估算当前查询是否具有跨域共振特征。

        共振估密 = 多标签组同时涉及或语文中含有跨域标志词。
        当展现跨域共振时，重塑 beta 会额外增加 0.1 以拓宽语义局。

        Args:
            query_text: 查询字符串，用于检测跨域标志词。
            query_core_tags: 核心标签集合。
            query_diffusion_tags: 扩散标签集合。
            query_opposing_tags: 对立标签集合。

        Returns:
            True，若判断存在跨域共振；否则返回 False。
        """
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

    @classmethod
    def _weighted_centroid(
        cls,
        query_vector: list[float],
        vectors_with_weight: list[tuple[list[float], float]],
    ) -> list[float]:
        """计算带权语义中心，用于 TAGCore/Opposing 的语义吸引力向算。

        向量长度与 query_vector 不一致的项或权重 <= 1e-12 的项将被跳过。

        Args:
            query_vector: 查询向量，仕用于长度对齐校验。
            vectors_with_weight: (embedding, weight) 元组列表，表示各匹配记忆的向量和权重。

        Returns:
            带权平均向量。无有效向量时返回全零向量。
        """
        if not query_vector:
            return []
        valid_vectors: list[np.ndarray] = []
        valid_weights: list[float] = []
        for vector, weight in vectors_with_weight:
            if len(vector) != len(query_vector):
                continue
            if weight <= 1e-12:
                continue
            valid_vectors.append(np.asarray(vector, dtype=np.float64))
            valid_weights.append(float(weight))

        if not valid_vectors:
            return [0.0 for _ in query_vector]

        matrix = np.vstack(valid_vectors)
        weight_array = np.asarray(valid_weights, dtype=np.float64)
        total_weight = float(np.sum(weight_array))

        if total_weight <= 1e-12:
            return [0.0 for _ in query_vector]
        centroid = (weight_array @ matrix) / total_weight
        return centroid.tolist()

    @classmethod
    def _reshape_query_vector(
        cls,
        query_vector: list[float],
        *,
        beta: float,
        core_vectors: list[tuple[list[float], float]],
        diffusion_vectors: list[tuple[list[float], float]],
        opposing_vectors: list[tuple[list[float], float]],
        energy_cutoff: float,
    ) -> list[float]:
        """根据 TAG 三角标签动力学将查询向量重塑为语义更加精确的方向。

        重塑公式：
        ``reshaped = (1 - beta) * query + beta * (core_centroid + diffusion_residual - opposing_centroid)``

        扩散向量采用残差能量领导的正交化策略，避免线性相关扩散方向导致塱骈。
        结果向量经过幂-2 归一化。

        Args:
            query_vector: 原始查询向量。
            beta: 重塑强度，[0.0, 1.0]，0 表示不重塑，1 表示完全用标签动力学替换。
            core_vectors: 核心标签下匹配记忆的 (embedding, weight) 列表。
            diffusion_vectors: 扩散标签下匹配记忆的 (embedding, weight) 列表。
            opposing_vectors: 对立标签下匹配记忆的 (embedding, weight) 列表。
            energy_cutoff: 扩散向量展入阈值，残差能量比低于此将被忽略。

        Returns:
            幂-2 归一化后的重塑向量。输入为空或归一化失败时返回空列表。
        """
        if not query_vector:
            return []
        query_array = np.asarray(query_vector, dtype=np.float64)
        core_term = cls._weighted_centroid(query_vector, core_vectors)
        opposing_term = cls._weighted_centroid(query_vector, opposing_vectors)
        core_array = np.asarray(core_term, dtype=np.float64)
        opposing_array = np.asarray(opposing_term, dtype=np.float64)

        diffusion_array = np.zeros_like(query_array)
        basis_arrays: list[np.ndarray] = []
        for vector, weight in diffusion_vectors:
            if len(vector) != len(query_vector) or weight <= 1e-12:
                continue
            vector_array = np.asarray(vector, dtype=np.float64)

            if basis_arrays:
                basis_matrix = np.vstack(basis_arrays)
                projection = basis_matrix.T @ (basis_matrix @ vector_array)
            else:
                projection = np.zeros_like(vector_array)

            residual = vector_array - projection
            residual_energy = float(residual @ residual)
            total_energy = float(vector_array @ vector_array)
            if total_energy <= 1e-12:
                continue
            ratio = residual_energy / total_energy
            if ratio < energy_cutoff:
                continue
            residual_norm = math.sqrt(residual_energy)
            if residual_norm <= 1e-12:
                continue
            normalized_residual = residual / residual_norm
            basis_arrays.append(normalized_residual)
            diffusion_array += normalized_residual * float(weight)

        reshaped = (1.0 - beta) * query_array + beta * (core_array + diffusion_array - opposing_array)
        return cls._normalize_vector(reshaped.tolist())

    def _match_score_with_tags(
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
        """基于 TAG 三角标签重叠对底层向量相似度进行修正。

        最终得分 = similarity + beta * (核心重叠 * core_boost - 对立重叠 * penalty + 扩散重叠 * diffusion_boost)。
        当标签参数为空时退化为查询词汇匹配。

        Args:
            query_text: 查询字符串，不等于标签时用于分词匹配。
            similarity: 底层向量余弦相似度。
            metadata: 记忆的元数据字典，包含 core_tags、diffusion_tags、opposing_tags 字段。
            beta: 标签修正强度系数。
            query_core_tags: 查询侧核心标签集合，``None`` 时用分词替代。
            query_diffusion_tags: 查询侧扩散标签集合。
            query_opposing_tags: 查询侧对立标签集合。

        Returns:
            修正后的记忆匹配得分，小于 0 时仍然保留负得分（后续淨选时可用于过滤）。
        """
        config = self._get_config()
        rag_params = self._get_rag_params()
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
            + config.retrieval.diffusion_boost * diffusion_overlap
            - config.retrieval.opposing_penalty * opposing_overlap
        )
        return similarity + beta * score_delta

    @staticmethod
    def _sanitize_vector_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """清洗写入向量库的 metadata，仅保留标量类型字段。

        ChromaDB 不支持列表、字典等复杂类型作为元数据字段，
        本函数过滤掉非法字段。

        Args:
            metadata: 原始元数据字典，可能包含任意类型的值。

        Returns:
            仅包含 str、int、float、bool 及 None 类型字段的新字典。
        """
        cleaned: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, str | int | float | bool) or value is None:
                cleaned[key] = value
        return cleaned

    @staticmethod
    def _metadata_from_record(record: Any) -> dict[str, Any]:
        """将仓储记录对象转换为检索元数据字典。

        提取记录的常用属性并封装为通用字典，供工具返回项、向量库元数据等场景复用。
        使用 ``getattr`` 保证兼容 ``BookuMemoryRecord`` dataclass 与 ORM 实例。

        Args:
            record: 仓储记录对象，可是 ``BookuMemoryRecord`` dataclass 或 ORM 实例。

        Returns:
            包含 title、folder_id、bucket、source、novelty_energy、
            created_at、updated_at、last_activated_at、activation_count、
            is_deleted、deleted_at、tags、core_tags、diffusion_tags、opposing_tags 字段的字典。
        """
        return {
            "title": getattr(record, "title", ""),
            "folder_id": getattr(record, "folder_id", ""),
            "bucket": getattr(record, "bucket", ""),
            "source": getattr(record, "source", ""),
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

    @staticmethod
    def _normalize_folder_id(folder_id: str | None, default_folder_id: str) -> str:
        """将 folder_id 小写进行实一化，为空时使用默认值。

        Args:
            folder_id: 原始 folder_id，可为空字符串或 None。
            default_folder_id: folder_id 无效时的回坍掇默认值。

        Returns:
            实一化后的 folder_id，永远不为空字符串。
        """
        if folder_id and folder_id.strip():
            return folder_id.strip().lower()
        return default_folder_id.strip().lower() or "default"

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        """对标签列表进行小写、去空格、去重标准化处理。

        过滤掉空字符串，转成小写，并去除重复项。

        Args:
            tags: 原始标签列表，可为 None。

        Returns:
            经标准化处理并去重后的标签列表。
        """
        normalized: list[str] = []
        for tag in tags or []:
            if not tag:
                continue
            value = tag.strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _extract_title(content: str) -> str:
        """从正文的首行提取标题。

        逻辑：找到第一个非空行，如果以 ``#`` 开头则去掉标记，截取前 80 字。
        内容为空时返回占位符标题 "未命名记忆"。

        Args:
            content: 记忆完整正文字符串。

        Returns:
            提取的标题字符串，最长 80 字。
        """
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                stripped = stripped.lstrip("#").strip()
            return stripped[:80]
        return "未命名记忆"

    @classmethod
    def _join_title_and_content(cls, title: str, content: str) -> str:
        """将标题和正文拼接为统一存储文本。

        格式为 ``# {title}\n{content}``。若只有标题则不加尾部换行。

        Args:
            title: 记忆标题，可为空字符串。
            content: 记忆正文，可为空字符串。

        Returns:
            合并后的存储文本；两者均为空时返回空字符串。
        """
        clean_title = title.strip()
        clean_content = content.strip()
        if not clean_title and not clean_content:
            return ""
        if clean_title and clean_content:
            return f"# {clean_title}\n{clean_content}"
        if clean_title:
            return clean_title
        return clean_content

    @classmethod
    def _split_title_and_content(cls, title: str, content: str) -> tuple[str, str]:
        """从数据库字段还原标题与清洁正文。

        若 content 以 ``# {title}\n`` 开头，则去除该前缀还原纯正文。
        title 为空时自动从 content 首行提取。

        Args:
            title: 元数据库中存储的标题字段。
            content: 元数据库中存储的完整内容字段（可能含标题帧）。

        Returns:
            ``(resolved_title, pure_body)`` 元组：
            resolved_title 为清洁标题，pure_body 为去除标题帧的纯正文。
        """
        resolved_title = title.strip() or cls._extract_title(content)
        body = content
        heading = f"# {resolved_title}"
        if body.startswith(heading):
            body = body[len(heading):].lstrip("\n")
        return resolved_title, body

    @classmethod
    def _build_record_item(
        cls,
        record: Any,
        *,
        snippet_length: int = 280,
        include_full_content: bool = False,
    ) -> dict[str, Any]:
        """将记录对象格式化为标准工具返回项。

        返回项包含 id、title、content_snippet、is_truncated、metadata 字段。
        当 ``include_full_content=True`` 时额外返回 ``content`` 字段（未截断的完整正文）。

        Args:
            record: 仓幂记录对象或带属性的对象。
            snippet_length: content_snippet 的最大字符数，默认 280。
            include_full_content: 是否包含完整正文字段，默认 False。

        Returns:
            包含 id、title、content_snippet、is_truncated、metadata 字段的字典；
            include_full_content 为 True 时额外包含 ``content`` 键。
        """
        title, pure_content = cls._split_title_and_content(
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
            "metadata": cls._metadata_from_record(record),
        }
        if include_full_content:
            payload["content"] = pure_content
        return payload

    @staticmethod
    def _safe_list(value: Any) -> list[Any]:
        """将 list-like 值安全转换为 list，避免数组布尔值歧义。"""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        try:
            return list(value)
        except TypeError:
            return []

    @staticmethod
    def _raise_vector_dimension_error(
        *,
        collection_name: str,
        source: str,
        expected_dim: int,
        actual_dim: int,
    ) -> NoReturn:
        """抛出向量维度不一致异常，并给出修复建议。"""
        raise RuntimeError(
            "检测到向量维度不一致，可能是历史向量与当前 embedding 模型维度不同。"
            f" collection={collection_name}, source={source}, expected={expected_dim}, actual={actual_dim}。"
            "请删除对应向量库后重建，或执行全量向量重生成覆盖流程。"
        )

    @classmethod
    def _to_float_vector(
        cls,
        values: Any,
        *,
        expected_dim: int | None = None,
        source: str = "unknown",
        collection_name: str = "unknown",
    ) -> list[float]:
        """将向量结构转换为一维 float 列表，并严格校验维度。"""
        if values is None:
            return []
        try:
            array = np.asarray(values, dtype=np.float64)
        except Exception:  # noqa: BLE001
            return []
        if array.size <= 0:
            return []

        if array.ndim == 0:
            vector = [float(array)]
        elif array.ndim == 1:
            vector = array.tolist()
        else:
            if int(array.shape[0]) == 1:
                vector = np.asarray(array[0], dtype=np.float64).reshape(-1).tolist()
            else:
                cls._raise_vector_dimension_error(
                    collection_name=collection_name,
                    source=source,
                    expected_dim=expected_dim or int(array.shape[-1]),
                    actual_dim=int(array.shape[-1]),
                )

        if expected_dim is not None and len(vector) != expected_dim:
            cls._raise_vector_dimension_error(
                collection_name=collection_name,
                source=source,
                expected_dim=expected_dim,
                actual_dim=len(vector),
            )
        return vector

    @classmethod
    def _safe_first_row(cls, value: Any) -> list[Any]:
        """提取二维结构的首行。"""
        rows = cls._safe_list(value)
        if len(rows) == 0:
            return []
        first = rows[0]
        if isinstance(first, list | tuple | np.ndarray):
            return cls._safe_list(first)
        return rows

    @staticmethod
    def _resolve_search_folders(folder_id: str | None, default_folder_id: str) -> list[str]:
        """解析检索目标 folder 列表。

        - 若 folder_id 有效，则尾返回单元素列表。
        - 若 folder_id 为空，则返回包含默认 folder 在内的全部预定义 folder 列表。

        Args:
            folder_id: 指定检索 folder，``None`` 或空字符串表示全部。
            default_folder_id: 默认 folder，将为全局检索时排在首位。

        Returns:
            实一化呈现的有序不重复 folder_id 列表。
        """
        if folder_id and folder_id.strip():
            return [folder_id.strip().lower()]

        folder_candidates = [
            default_folder_id.strip().lower(),
            *[name.strip().lower() for name in PREDEFINED_FOLDERS],
        ]
        resolved: list[str] = []
        for candidate in folder_candidates:
            if candidate and candidate not in resolved:
                resolved.append(candidate)
        return resolved or ["default"]

    async def upsert_memory(
        self,
        content: str,
        *,
        title: str | None = None,
        bucket: str = "emergent",
        folder_id: str | None = None,
        tags: list[str] | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
        source: str = "agent",
    ) -> dict[str, Any]:
        """写入或自动合并记忆。

        写入前检索邻域向量并计算新颖度能量比：
        - 能量比 >= energy_cutoff：内容新颖，创建新记忆（mode="created"）。
        - 能量比 < energy_cutoff：内容重复，自动合并到最相似的现有记忆（mode="merged"）。
        固有记忆（inherent）不按 folder 隔离，如例外应需 folder_id 传 None。

        Args:
            content: 记忆正文，不能为空字符串。
            title: 记忆标题（可选），为空时从 content 首行自动提取。
            bucket: 存储桶，默认 ``"emergent"``，支持 ``"archived"``/``"inherent"``。
            folder_id: 记忆所属文件夹——inherent 时无效。
            tags: 通用标签列表，可为 None。
            core_tags: 核心标签列表，检索时最优先。
            diffusion_tags: 扩散标签列表。
            opposing_tags: 对立标签列表。
            source: 来源标识，默认 ``"agent"``。

        Returns:
            包含 mode、id、collection、novelty_energy、item 字段的字典。
            mode 为 ``"created"`` 或 ``"merged"``。

        Raises:
            ValueError: content 为空时抛出。
        """
        config = self._get_config()
        rag_params = self._get_rag_params()
        repo = await self._get_repo()
        normalized_bucket = bucket.strip().lower() if bucket else "emergent"
        # inherent 全局不按 folder 隔离，folder_id 字段存 "global"
        if normalized_bucket == "inherent":
            effective_folder_id = "global"
        else:
            effective_folder_id = self._normalize_folder_id(
                folder_id,
                config.storage.default_folder_id,
            )
        text = content.strip()
        if not text:
            raise ValueError("content 不能为空")

        resolved_title = (title or "").strip() or self._extract_title(text)
        merged_content = self._join_title_and_content(resolved_title, text)
        if not merged_content:
            raise ValueError("title 与 content 不能同时为空")

        normalized_core_tags = self._normalize_tags(core_tags)
        normalized_diffusion_tags = self._normalize_tags(diffusion_tags)
        normalized_opposing_tags = self._normalize_tags(opposing_tags)

        vector = await self._embed_text(merged_content)
        collection_name = self._collection_name(bucket=normalized_bucket, folder_id=effective_folder_id)
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
        existing_ids = self._safe_first_row(query_result.get("ids", []))
        query_embeddings = self._safe_first_row(query_result.get("embeddings", []))
        if query_embeddings:
            for index, embedding in enumerate(query_embeddings):
                parsed = self._to_float_vector(
                    embedding,
                    expected_dim=len(vector),
                    source=f"upsert.query_embeddings[{index}]",
                    collection_name=collection_name,
                )
                if parsed:
                    existing_embeddings.append(parsed)

        if not existing_embeddings and existing_ids:
            loaded = await vector_db.get(
                collection_name=collection_name,
                ids=[str(memory_id) for memory_id in existing_ids],
                include=["embeddings"],
            )
            loaded_embeddings = self._safe_list(loaded.get("embeddings", []))
            for index, embedding in enumerate(loaded_embeddings):
                parsed = self._to_float_vector(
                    embedding,
                    expected_dim=len(vector),
                    source=f"upsert.get_embeddings[{index}]",
                    collection_name=collection_name,
                )
                if parsed:
                    existing_embeddings.append(parsed)

        novelty_energy = self._novelty_energy_ratio(vector, existing_embeddings)
        if novelty_energy >= rag_params.energy_cutoff:
            distance_rows = self._safe_first_row(query_result.get("distances", []))
            if distance_rows:
                best_distance = min(float(value) for value in distance_rows)
                if best_distance <= 1e-8:
                    novelty_energy = 0.0

        mode = "created"
        memory_id = f"mem-{uuid.uuid4().hex}"
        now = time.time()

        if novelty_energy < rag_params.energy_cutoff:
            if len(existing_ids) > 0:
                memory_id = str(existing_ids[0])
                mode = "merged"
                await vector_db.delete(collection_name=collection_name, ids=[memory_id])

        metadata: dict[str, Any] = {
            "title": resolved_title,
            "bucket": normalized_bucket,
            "folder_id": effective_folder_id,
            "source": source,
            "timestamp": now,
            "novelty_energy": novelty_energy,
        }
        vector_metadata = self._sanitize_vector_metadata(metadata)

        await vector_db.add(
            collection_name=collection_name,
            embeddings=[vector],
            documents=[merged_content],
            metadatas=[vector_metadata],
            ids=[memory_id],
        )

        await repo.upsert_record(
            memory_id=memory_id,
            title=resolved_title,
            folder_id=effective_folder_id,
            bucket=normalized_bucket,
            content=merged_content,
            source=source,
            novelty_energy=novelty_energy,
            tags=tags or [],
            core_tags=normalized_core_tags,
            diffusion_tags=normalized_diffusion_tags,
            opposing_tags=normalized_opposing_tags,
        )

        record = await repo.get_record(memory_id)
        item = (
            self._build_record_item(record, include_full_content=False)
            if record is not None
            else {
                "id": memory_id,
                "title": resolved_title,
                "content_snippet": text[:280],
                "is_truncated": len(text) > 280,
                "metadata": metadata,
            }
        )

        return {
            "mode": mode,
            "id": memory_id,
            "collection": collection_name,
            "novelty_energy": novelty_energy,
            "item": item,
        }

    async def retrieve_memories(
        self,
        query_text: str,
        *,
        folder_id: str | None = None,
        top_k: int | None = None,
        include_archived: bool | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行 EPA 向量动力学重塑后的语义检索。

        流程：
        1. 初始检索 -> 收集周围向量采样。
        2. 根据标签三角 + 投影熵/共振展当轮 beta。
        3. 用重塑后向量再次检索并潄分。
        4. 去重器消除冗余结果后返回最终列表。

        Args:
            query_text: 检索关键词文本。
            folder_id: 限定检索的 folder，``None`` 时搜索所有 folder。
            top_k: 返回条数，``None`` 时使用配置默认值。
            include_archived: 是否检索归档层，``None`` 时使用配置默认开关。
            core_tags: 核心标签，提升匹配记忆的得分。
            diffusion_tags: 扩散标签，扇形扩展检索语义。
            opposing_tags: 对立标签，对匹配记忆进行陨分。

        Returns:
            包含 query、logic_depth、resonance、beta、total、results 字段的字典。
            results 为工具返回项列表（含 id、title、content_snippet、score 等）。
        """
        config = self._get_config()
        rag_params = self._get_rag_params()
        repo = await self._get_repo()
        search_folders = self._resolve_search_folders(
            folder_id=folder_id,
            default_folder_id=config.storage.default_folder_id,
        )
        n_results = top_k or config.retrieval.default_top_k
        use_archived = (
            config.retrieval.include_archived_default
            if include_archived is None
            else include_archived
        )

        query_vector = await self._embed_text(query_text)
        query_core_tags = {tag.strip().lower() for tag in (core_tags or []) if tag and tag.strip()}
        query_diffusion_tags = {
            tag.strip().lower() for tag in (diffusion_tags or []) if tag and tag.strip()
        }
        query_opposing_tags = {
            tag.strip().lower() for tag in (opposing_tags or []) if tag and tag.strip()
        }
        query_tokens = {token for token in query_text.lower().split() if token}

        vector_db = get_vector_db_service(config.storage.vector_db_path)
        collections = [self._collection_name("inherent", "")]
        for search_folder in search_folders:
            collections.append(self._collection_name("emergent", search_folder))
            if use_archived:
                collections.append(self._collection_name("archived", search_folder))

        async def _collect_candidates(
            embedding: list[float], per_collection_limit: int
        ) -> list[dict[str, Any]]:
            """对所有目标集合并行查询，收集候选记忆。

            遇到记录数为 0 的集合时自动跳过。

            Args:
                embedding: 查询向量，将作为近似检索的输入。
                per_collection_limit: 每个集合单次召回条数上限。

            Returns:
                候选条目列表，每项包含 memory_id、document、metadata、embedding、
                score（初始为 0.0，周期得分后更新）、collection 字段。
            """
            collected: list[dict[str, Any]] = []
            for collection in collections:
                collection_count = await vector_db.count(collection)
                if collection_count <= 0:
                    continue

                result = await vector_db.query(
                    collection_name=collection,
                    query_embeddings=[embedding],
                    n_results=per_collection_limit,
                    include=["embeddings", "metadatas", "documents", "distances"],
                )

                ids_row = self._safe_first_row(result.get("ids", [[]]))
                documents_row = self._safe_first_row(result.get("documents", [[]]))
                metadatas_row = self._safe_first_row(result.get("metadatas", [[]]))
                embeddings_row = self._safe_first_row(result.get("embeddings", [[]]))

                for index, memory_id in enumerate(ids_row):
                    item_embedding = self._to_float_vector(
                        embeddings_row[index] if index < len(embeddings_row) else [],
                        expected_dim=len(embedding),
                        source=f"retrieve.collect[{collection}][{index}]",
                        collection_name=collection,
                    )
                    collected.append(
                        {
                            "memory_id": memory_id,
                            "document": documents_row[index] if index < len(documents_row) else "",
                            "metadata": metadatas_row[index] if index < len(metadatas_row) else {},
                            "embedding": item_embedding,
                            "score": 0.0,
                            "collection": collection,
                        }
                    )
            return collected

        initial_candidates = await _collect_candidates(
            query_vector,
            max(n_results, config.write_conflict.top_n),
        )
        initial_records = await repo.get_records_map(
            [str(item["memory_id"]) for item in initial_candidates if item.get("memory_id")]
        )

        for item in initial_candidates:
            memory_id = str(item.get("memory_id", ""))
            record = initial_records.get(memory_id)
            if record is not None:
                item["metadata"] = self._metadata_from_record(record)

        evidence_vectors = [
            self._to_float_vector(
                item.get("embedding", []),
                expected_dim=len(query_vector),
                source="retrieve.evidence",
                collection_name=str(item.get("collection", "unknown")),
            )
            for item in initial_candidates
            if item.get("embedding") is not None
        ]
        logic_depth = self._projection_entropy_logic_depth(query_vector, evidence_vectors)
        resonance = self._estimate_resonance(
            query_text,
            query_core_tags,
            query_diffusion_tags,
            query_opposing_tags,
        )
        beta = self._clamp(
            config.retrieval.base_beta
            + logic_depth * config.retrieval.logic_depth_scale
            + (0.1 if resonance else 0.0),
            0.0,
            1.0,
        )

        core_vectors: list[tuple[list[float], float]] = []
        diffusion_vectors: list[tuple[list[float], float]] = []
        opposing_vectors: list[tuple[list[float], float]] = []
        core_boost_center = (rag_params.core_boost_min + rag_params.core_boost_max) / 2

        for item in initial_candidates:
            embedding = self._to_float_vector(
                item.get("embedding", []),
                expected_dim=len(query_vector),
                source="retrieve.reshape",
                collection_name=str(item.get("collection", "unknown")),
            )
            if len(embedding) != len(query_vector):
                continue
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                continue

            item_core_tags = set(self._safe_list(metadata.get("core_tags", [])))
            item_diffusion_tags = set(self._safe_list(metadata.get("diffusion_tags", [])))
            item_opposing_tags = set(self._safe_list(metadata.get("opposing_tags", [])))

            similarity = max(0.0, self._cosine_similarity(query_vector, embedding))
            if similarity <= 1e-12:
                continue

            core_match = (query_core_tags or query_tokens) & item_core_tags
            if core_match:
                core_vectors.append((embedding, similarity * core_boost_center * len(core_match)))

            diffusion_match = (query_diffusion_tags or query_tokens) & item_diffusion_tags
            if diffusion_match:
                diffusion_vectors.append((embedding, similarity * config.retrieval.diffusion_boost * len(diffusion_match)))

            opposing_match = (query_opposing_tags or query_tokens) & item_opposing_tags
            if opposing_match:
                opposing_vectors.append((embedding, similarity * config.retrieval.opposing_penalty * len(opposing_match)))

        reshaped_vector = self._reshape_query_vector(
            query_vector,
            beta=beta,
            core_vectors=core_vectors,
            diffusion_vectors=diffusion_vectors,
            opposing_vectors=opposing_vectors,
            energy_cutoff=rag_params.energy_cutoff,
        )
        if self._vector_norm_sq(reshaped_vector) <= 1e-12:
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
            metadata = self._metadata_from_record(record)
            item["metadata"] = metadata
            item["document"] = record.content or item.get("document", "")
            similarity = self._cosine_similarity(
                reshaped_vector,
                self._to_float_vector(
                    item.get("embedding", []),
                    expected_dim=len(reshaped_vector),
                    source="retrieve.score",
                    collection_name=str(item.get("collection", "unknown")),
                ),
            )
            item["score"] = self._match_score_with_tags(
                query_text=query_text,
                similarity=similarity,
                metadata=metadata,
                beta=beta,
                query_core_tags=query_core_tags,
                query_diffusion_tags=query_diffusion_tags,
                query_opposing_tags=query_opposing_tags,
            )

        selected = self._get_deduplicator().select(
            candidates,
            limit=n_results,
            similarity_threshold=rag_params.deduplication_threshold,
        )
        deduplicated: list[dict[str, Any]] = []
        for item in selected:
            memory_id = str(item.get("memory_id", ""))
            record = records.get(memory_id)
            if record is not None:
                output_item = self._build_record_item(record)
            else:
                text = str(item.get("document", "") or "")
                title = self._extract_title(text)
                output_item = {
                    "id": memory_id,
                    "title": title,
                    "content_snippet": text[:280] + ("..." if len(text) > 280 else ""),
                    "is_truncated": len(text) > 280,
                    "metadata": item.get("metadata", {}),
                }
            output_item["score"] = float(item.get("score", 0.0))
            output_item["collection"] = str(item.get("collection", ""))
            deduplicated.append(output_item)

        # 对非固有记忆进行激活计数更新
        for item in deduplicated:
            mid = str(item.get("id", ""))
            meta = item.get("metadata", {})
            if mid and isinstance(meta, dict) and meta.get("bucket") != "inherent":
                await self.update_activated(mid)

        return {
            "query": query_text,
            "logic_depth": logic_depth,
            "resonance": resonance,
            "beta": beta,
            "total": len(deduplicated),
            "results": deduplicated,
        }

    async def archive_memories(
        self,
        memory_ids: list[str],
        *,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """将隐现记忆迁移到归档层（emergent → archived）。

        处理逻辑：先在向量库中从 emergent 集合读取数据，写入 archived
        集合，删除原 emergent 数据，最后将元数据库中记录标记为归档。

        Args:
            memory_ids: 待归档的 memory_id 列表。
            folder_id: 限定 folder——为空时使用默认 folder。

        Returns:
            包含 archived、skipped 计数字典。
        """
        if not memory_ids:
            return {"archived": 0, "skipped": 0}

        config = self._get_config()
        repo = await self._get_repo()
        effective_folder_id = self._normalize_folder_id(
            folder_id,
            config.storage.default_folder_id,
        )
        emergent_collection = self._collection_name("emergent", effective_folder_id)
        archived_collection = self._collection_name("archived", effective_folder_id)
        vector_db = get_vector_db_service(config.storage.vector_db_path)

        loaded = await vector_db.get(
            collection_name=emergent_collection,
            ids=memory_ids,
            include=["documents", "metadatas", "embeddings"],
        )

        ids = loaded.get("ids", []) or []
        documents = loaded.get("documents", []) or []
        metadatas = loaded.get("metadatas", []) or []
        embeddings = loaded.get("embeddings", []) or []

        archived_count = 0
        for index, memory_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            if isinstance(metadata, dict):
                metadata = {**metadata, "bucket": "archived", "archived_at": time.time()}
            else:
                metadata = {"bucket": "archived", "archived_at": time.time()}
            vector_metadata = self._sanitize_vector_metadata(metadata)
            await vector_db.add(
                collection_name=archived_collection,
                ids=[memory_id],
                documents=[documents[index] if index < len(documents) else ""],
                metadatas=[vector_metadata],
                embeddings=[[float(value) for value in embeddings[index]]] if index < len(embeddings) else [[0.0]],
            )
            archived_count += 1

        if ids:
            await vector_db.delete(collection_name=emergent_collection, ids=ids)

        await repo.mark_archived(
            memory_ids=[str(memory_id) for memory_id in ids],
            folder_id=effective_folder_id,
        )

        return {"archived": archived_count, "skipped": max(0, len(memory_ids) - archived_count)}

    async def create_memory(
        self,
        *,
        title: str,
        content: str,
        bucket: str,
        folder_id: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
    ) -> dict[str, Any]:
        """创建记忆并返回标准工具项形式的结果。

        封装 ``upsert_memory`` 并统一返回格式为 action/mode/total/items。
        嵌入公譱序重复检测由底层 ``upsert_memory`` 自动处理。

        Args:
            title: 记忆标题。
            content: 记忆正文。
            bucket: 存储桶名（emergent/archived/inherent）。
            folder_id: 文件夹 ID。
            core_tags: 核心标签列表。
            diffusion_tags: 扩散标签列表。
            opposing_tags: 对立标签列表。

        Returns:
            包含 action/mode/total/items 字段的字典，mode 为 ``"created"`` 或 ``"merged"``。
        """
        result = await self.upsert_memory(
            title=title,
            content=content,
            bucket=bucket,
            folder_id=folder_id,
            core_tags=core_tags,
            diffusion_tags=diffusion_tags,
            opposing_tags=opposing_tags,
            source="agent",
        )
        if str(bucket).strip().lower() == "inherent":
            await _sync_booku_memory_actor_reminder(self.plugin)
        return {
            "action": "create_memory",
            "mode": result.get("mode", "created"),
            "total": 1,
            "items": [result.get("item", {})],
        }

    async def edit_inherent_memory(self, *, content: str) -> dict[str, Any]:
        """编辑全局固有记忆（通过 upsert 合并写入）。

        固有记忆是全局唯一的底层背景知识层，其内容每次调用均会全量覆写。
        调用前应先通过 ``get_inherent_memories`` 读取现有内容后在包外合并。

        Args:
            content: 编辑后的完整固有记忆内容。

        Returns:
            包含 action/mode/total/items 字段的字典。
        """
        result = await self.upsert_memory(
            title="固有记忆",
            content=content,
            bucket="inherent",
            folder_id=None,
            source="agent",
        )
        await _sync_booku_memory_actor_reminder(self.plugin)
        return {
            "action": "edit_inherent_memory",
            "mode": result.get("mode", "updated"),
            "total": 1,
            "items": [result.get("item", {})],
        }

    async def get_status(self, folder_id: str | None = None) -> dict[str, Any]:
        """获取各层记忆数量与最近记录（简化包装层）。

        封装 ``query_memory_status`` 并复整字段结构为 counts/recent/folder_memory_ids。

        Args:
            folder_id: 指定查询的 folder，``None`` 时使用默认 folder。

        Returns:
            包含 folder_id、counts〈vector/metadata〉、recent、folder_memory_ids 字段的字典。
        """
        status = await self.query_memory_status(folder_id=folder_id)
        return {
            "folder_id": status.get("folder_id", "default"),
            "counts": {
                "vector": status.get("vector_counts", {}),
                "metadata": status.get("metadata_counts", {}),
            },
            "recent": status.get("recent", []),
            "folder_memory_ids": status.get("folder_memory_ids", []),
        }

    async def update_activated(self, memory_id: str) -> None:
        """原子将指定记忆的激活次数 +1 并更新最近激活时间。

        在每次检索命中后自动调用，用于 ``promote_stale_emergent`` 中判断隐现记忆是否达到最低激活次数阈值。

        Args:
            memory_id: 目标记忆的 memory_id。
        """
        repo = await self._get_repo()
        await repo.update_activated(memory_id)

    async def get_inherent_memories(
        self,
        query_text: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """全局语义搜索固有记忆（inherent bucket），无 folder 约束。

        内部调用 ``retrieve_memories`` 并过滤出 bucket == "inherent" 的项。

        Args:
            query_text: 检索语义文本。
            top_k: 最大返回条数，默认 5。

        Returns:
            包含 query、total、results 字段的字典，results 中仅包含 inherent bucket 筛选结果。
        """
        result = await self.retrieve_memories(
            query_text=query_text,
            folder_id="global",
            top_k=top_k,
            include_archived=False,
        )
        filtered = [
            item
            for item in result.get("results", [])
            if isinstance(item, dict) and item.get("metadata", {}).get("bucket") == "inherent"
        ]
        return {
            "query": query_text,
            "total": len(filtered),
            "results": filtered[:top_k],
        }

    async def grep_memories(
        self,
        *,
        query: str,
        search_fields: list[str],
        folder_id: str | None = None,
        include_archived: bool = False,
        top_k: int = 10,
        use_regex: bool = False,
    ) -> dict[str, Any]:
        """按关键词或正则表达式在指定字段中匹配记忆，适合精确词汇/模式定位。

        不进行向量语义扩展，仅进行字符级匹配。通常与 ``retrieve_memories``
        配合使用：先语义检索，再用 grep 补充覆盖格式化词汇/编号等。

        Args:
            query: 关键词字符串或正则表达式（``use_regex=True`` 时）。
            search_fields: 搜索范围，可选 title/summary/content/tags/metadata。
            folder_id: 限定搜索 folder，``None`` 时全局搜索。
            include_archived: 是否同时搜索归档层，默认 False。
            top_k: 最大返回条数，默认 10。
            use_regex: 为 ``True`` 时启用 Python 正则匹配，默认 False（LIKE 匹配）。

        Returns:
            包含 action/query/total/items 字段的字典。

        Raises:
            ValueError: ``use_regex=True`` 且 query 不是合法正则表达式时抛出。
        """
        repo = await self._get_repo()
        config = self._get_config()
        effective_folder_id = (
            self._normalize_folder_id(folder_id, config.storage.default_folder_id)
            if folder_id is not None
            else None
        )
        memory_ids = await repo.search_records_grep(
            query=query,
            search_fields=search_fields,
            folder_id=effective_folder_id,
            include_archived=include_archived,
            limit=top_k,
            use_regex=use_regex,
        )
        records = await repo.get_records_map(memory_ids)
        items = [
            self._build_record_item(record)
            for memory_id in memory_ids
            if (record := records.get(memory_id)) is not None
        ]
        return {
            "action": "grep_memories",
            "query": query,
            "total": len(items),
            "items": items,
        }

    async def query_memory_status(
        self,
        *,
        folder_id: str | None = None,
        include_archived: bool = True,
        recent_limit: int = 8,
    ) -> dict[str, Any]:
        """查询记忆状态：各层记忆数量、最近记忆、folder id 列表。

        同时查询向量库（实际存储中的条数）与元数据库（已索引的条数）的各 bucket 数量。
        用于判断检索可行性、防止在空 folder 重复检索。

        Args:
            folder_id: 指定查询 folder，``None`` 时使用默认 folder。
            include_archived: 最近记录是否包含 archived 层，默认 True。
            recent_limit: 返回最近记忆的条数上限，默认 8。

        Returns:
            包含 action、folder_id、vector_counts、metadata_counts、recent、folder_memory_ids 字段的字典。
        """
        config = self._get_config()
        repo = await self._get_repo()
        effective_folder_id = self._normalize_folder_id(folder_id, config.storage.default_folder_id)
        vector_db = get_vector_db_service(config.storage.vector_db_path)

        vector_counts: dict[str, int] = {}
        for bucket in ("inherent", "emergent", "archived"):
            collection = self._collection_name(bucket, effective_folder_id)
            vector_counts[bucket] = await vector_db.count(collection)

        metadata_counts = await repo.get_bucket_counts(effective_folder_id)
        recent_records = await repo.get_recent_records(
            limit=recent_limit,
            folder_id=effective_folder_id,
            include_archived=include_archived,
        )
        folder_memory_ids = await repo.list_memory_ids_by_folder(
            folder_id=effective_folder_id,
            include_archived=include_archived,
            limit=200,
        )

        return {
            "action": "query_memory_status",
            "folder_id": effective_folder_id,
            "vector_counts": vector_counts,
            "metadata_counts": metadata_counts,
            "recent": [self._build_record_item(record) for record in recent_records],
            "folder_memory_ids": folder_memory_ids,
        }

    async def read_full_content(self, *, memory_ids: list[str]) -> dict[str, Any]:
        """按 memory_id 列表批量读取记忆完整正文。

        与 ``_build_record_item`` 不同，返回项指定 ``include_full_content=True``
        从而包含未截断的 ``content`` 字段。不存在的 id 会被静默忽略。

        Args:
            memory_ids: 需要读取完整正文的 memory_id 列表。

        Returns:
            包含 action/requested/total/items 字段的字典，
            items 中每项均含 ``content`` 完整正文字段。
        """
        repo = await self._get_repo()
        records = await repo.get_records_map(memory_ids)
        items = [
            self._build_record_item(record, include_full_content=True)
            for memory_id in memory_ids
            if (record := records.get(memory_id)) is not None
        ]
        return {
            "action": "read_full_content",
            "requested": len(memory_ids),
            "total": len(items),
            "items": items,
        }

    async def update_memory_by_id(
        self,
        *,
        memory_id: str,
        title: str | None = None,
        content: str | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """按 memory_id 就地更新普通记忆的内容、标题及标签。

        同时更新向量库（重新嵌入）和元数据库。固有记忆不适用本方法。
        未传入的字段将保留原始值（最小变更原则）。

        Args:
            memory_id: 目标记忆的 memory_id。
            title: 新标题（可选）。
            content: 新完整正文（可选，不传则保留原文）。
            core_tags: 新核心标签列表（可选）。
            diffusion_tags: 新扩散标签列表（可选）。
            opposing_tags: 新对立标签列表（可选）。

        Returns:
            包含 action/updated/items 字段的字典。
            updated=0 表示记录不存在或为 inherent 类型。
        """
        repo = await self._get_repo()
        config = self._get_config()
        record = await repo.get_record(memory_id)
        if record is None:
            return {"action": "update_memory_by_id", "updated": 0, "items": []}

        if record.bucket == "inherent":
            return {"action": "update_memory_by_id", "updated": 0, "error": "inherent 记忆请使用 edit_inherent_memory"}

        normalized_core_tags = self._normalize_tags(core_tags)
        normalized_diffusion_tags = self._normalize_tags(diffusion_tags)
        normalized_opposing_tags = self._normalize_tags(opposing_tags)

        old_collection = self._collection_name(record.bucket, record.folder_id)
        resolved_title = (title or "").strip() or record.title or self._extract_title(record.content)
        new_body = content.strip() if content is not None else self._split_title_and_content(record.title, record.content)[1]
        merged_content = self._join_title_and_content(resolved_title, new_body)

        vector_db = get_vector_db_service(config.storage.vector_db_path)
        vector = await self._embed_text(merged_content)
        loaded = await vector_db.get(
            collection_name=old_collection,
            ids=[memory_id],
            include=["metadatas"],
        )
        metadata_row = self._safe_first_row(loaded.get("metadatas", [[]]))
        vector_metadata = metadata_row[0] if metadata_row else {}
        if not isinstance(vector_metadata, dict):
            vector_metadata = {}
        vector_metadata.update({"title": resolved_title, "bucket": record.bucket, "folder_id": record.folder_id})

        await vector_db.delete(collection_name=old_collection, ids=[memory_id])
        await vector_db.add(
            collection_name=old_collection,
            ids=[memory_id],
            documents=[merged_content],
            embeddings=[vector],
            metadatas=[self._sanitize_vector_metadata(vector_metadata)],
        )

        updated = await repo.update_record(
            memory_id,
            title=resolved_title,
            content=merged_content,
            core_tags=normalized_core_tags,
            diffusion_tags=normalized_diffusion_tags,
            opposing_tags=normalized_opposing_tags,
        )
        if not updated:
            return {"action": "update_memory_by_id", "updated": 0, "items": []}

        updated_record = await repo.get_record(memory_id)
        return {
            "action": "update_memory_by_id",
            "updated": 1,
            "items": [self._build_record_item(updated_record)] if updated_record is not None else [],
        }

    async def delete_memories(self, *, memory_ids: list[str], hard: bool = False) -> dict[str, Any]:
        """删除指定记忆（默认软删，hard=True 为硬删）。

        软删除：仅在元数据库标记 ``is_deleted=1``，向量库数据保留。
        硬删除：同时从向量库和元数据库中永久移除所有相关数据，不可恢复。

        Args:
            memory_ids: 待删除的 memory_id 列表。
            hard: True 为硬删，默认为软删。

        Returns:
            包含 action/mode/deleted/requested 字段的字典。mode 为 ``"soft"`` 或 ``"hard"``。
        """
        repo = await self._get_repo()
        config = self._get_config()
        records = await repo.get_records_map(memory_ids, include_deleted=True)
        affects_inherent = any(record.bucket == "inherent" for record in records.values())
        vector_db = get_vector_db_service(config.storage.vector_db_path)

        if hard:
            for record in records.values():
                collection = self._collection_name(record.bucket, record.folder_id)
                try:
                    await vector_db.delete(collection_name=collection, ids=[record.memory_id])
                except Exception:  # noqa: BLE001
                    continue
            deleted = await repo.hard_delete_records(memory_ids)
            if affects_inherent:
                await _sync_booku_memory_actor_reminder(self.plugin)
            return {
                "action": "delete_memories",
                "mode": "hard",
                "deleted": deleted,
                "requested": len(memory_ids),
            }

        deleted = await repo.soft_delete_records(memory_ids)
        if affects_inherent:
            await _sync_booku_memory_actor_reminder(self.plugin)
        return {
            "action": "delete_memories",
            "mode": "soft",
            "deleted": deleted,
            "requested": len(memory_ids),
        }

    async def move_memories(
        self,
        *,
        memory_ids: list[str],
        to_bucket: str | None = None,
        to_folder_id: str | None = None,
    ) -> dict[str, Any]:
        """将指定记忆批量移动到目标 folder 或 bucket。

        同时更新向量库（删除原位置集合条目、加入新位置集合）和元数据库。
        ``to_bucket`` 与 ``to_folder_id`` 不能同时为 None；不变的字段保持原值。

        Args:
            memory_ids: 待移动的 memory_id 列表。
            to_bucket: 目标 bucket（可选）。
            to_folder_id: 目标 folder_id（可选）。

        Returns:
            包含 action/moved/items/to_bucket/to_folder_id 字段的字典。
        """
        config = self._get_config()
        repo = await self._get_repo()
        if to_bucket is None and to_folder_id is None:
            return {"action": "move_memories", "moved": 0, "items": []}

        target_bucket = to_bucket.strip().lower() if to_bucket else None
        target_folder = (
            self._normalize_folder_id(to_folder_id, config.storage.default_folder_id)
            if to_folder_id is not None
            else None
        )

        records = await repo.get_records_map(memory_ids)
        vector_db = get_vector_db_service(config.storage.vector_db_path)
        moved_items: list[dict[str, Any]] = []
        affects_inherent = any(record.bucket == "inherent" for record in records.values()) or target_bucket == "inherent"

        for memory_id in memory_ids:
            record = records.get(memory_id)
            if record is None:
                continue

            new_bucket = target_bucket or record.bucket
            new_folder = "global" if new_bucket == "inherent" else (target_folder or record.folder_id)
            old_collection = self._collection_name(record.bucket, record.folder_id)
            new_collection = self._collection_name(new_bucket, new_folder)

            if old_collection != new_collection:
                loaded = await vector_db.get(
                    collection_name=old_collection,
                    ids=[memory_id],
                    include=["documents", "metadatas", "embeddings"],
                )
                ids_row = loaded.get("ids", []) or []
                if ids_row:
                    docs = loaded.get("documents", []) or [record.content]
                    metas = loaded.get("metadatas", []) or [{}]
                    embs = loaded.get("embeddings", []) or [await self._embed_text(record.content)]
                    meta = metas[0] if metas and isinstance(metas[0], dict) else {}
                    meta.update({"bucket": new_bucket, "folder_id": new_folder, "title": record.title})
                    await vector_db.add(
                        collection_name=new_collection,
                        ids=[memory_id],
                        documents=[str(docs[0]) if docs else record.content],
                        metadatas=[self._sanitize_vector_metadata(meta)],
                        embeddings=[[float(value) for value in self._safe_list(embs[0])]],
                    )
                    await vector_db.delete(collection_name=old_collection, ids=[memory_id])

            await repo.update_record(
                memory_id,
                bucket=new_bucket,
                folder_id=new_folder,
            )
            updated_record = await repo.get_record(memory_id)
            if updated_record is not None:
                moved_items.append(self._build_record_item(updated_record))

        if affects_inherent:
            await _sync_booku_memory_actor_reminder(self.plugin)

        return {
            "action": "move_memories",
            "moved": len(moved_items),
            "items": moved_items,
            "to_bucket": target_bucket,
            "to_folder_id": target_folder,
        }

    async def promote_stale_emergent(
        self, folder_id: str | None = None
    ) -> dict[str, Any]:
        """扫描超过时间窗口的 emergent 记忆：激活次数达阈值者升冻归档，其余丢弃。"""
        config = self._get_config()
        effective_folder_id = self._normalize_folder_id(
            folder_id,
            config.storage.default_folder_id,
        )
        repo = await self._get_repo()

        window_days = config.time_window.emergent_days
        threshold = config.time_window.activation_threshold
        cutoff = time.time() - window_days * 86400.0

        stale = await repo.get_stale_emergent(
            folder_id=effective_folder_id, before_timestamp=cutoff
        )
        if not stale:
            return {"promoted": 0, "discarded": 0, "folder_id": effective_folder_id}

        promote_ids = [r.memory_id for r in stale if r.activation_count >= threshold]
        discard_ids = [r.memory_id for r in stale if r.activation_count < threshold]

        promoted = 0
        if promote_ids:
            result = await self.archive_memories(
                memory_ids=promote_ids, folder_id=effective_folder_id
            )
            promoted = int(result.get("archived", 0))

        discarded = 0
        if discard_ids:
            vector_db = get_vector_db_service(config.storage.vector_db_path)
            emergent_collection = self._collection_name("emergent", effective_folder_id)
            try:
                await vector_db.delete(
                    collection_name=emergent_collection, ids=discard_ids
                )
            except Exception:  # noqa: BLE001
                pass
            # BookuMemoryMetadataRepository 未暴露批量硬删接口，
            # 此处通过 asyncio.to_thread + 原生 sqlite3 直连执行删除。
            # TODO: 后续应将批量硬删逻辑封装到仓储层以消除此 anti-pattern。
            placeholders = ",".join("?" for _ in discard_ids)
            import asyncio
            import sqlite3 as _sqlite3

            def _delete_sync() -> int:
                """在内嵌直连中删除指定记忆的元数据记录及其全部标签。

                由于此操作需要直接操作 SQLite，经由 ``asyncio.to_thread`` 在线程池中执行。

                Returns:
                    实际被删除的记录数量。
                """
                with _sqlite3.connect(repo._db_path) as conn:
                    cursor = conn.execute(
                        f"DELETE FROM booku_memory_records WHERE memory_id IN ({placeholders})",
                        tuple(discard_ids),
                    )
                    conn.execute(
                        f"DELETE FROM booku_memory_tags WHERE memory_id IN ({placeholders})",
                        tuple(discard_ids),
                    )
                    conn.commit()
                    return int(cursor.rowcount)

            discarded = await asyncio.to_thread(_delete_sync)

        return {
            "promoted": promoted,
            "discarded": discarded,
            "folder_id": effective_folder_id,
        }


