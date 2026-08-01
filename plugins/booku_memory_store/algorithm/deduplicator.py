"""Booku Memory Store 检索结果去重器。

基于残差能量最大化的贪心选择，保证检索结果的多样性。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


class ResultDeduplicator:
    """基于残差能量最大化的结果去重器。

    算法策略：
    1. 先按 score 从高到低排序，取前缀候选作为贪婪池。
    2. 每轮选择"与已选子空间最不相似"的候选（残差能量最大）。
    3. 在满足多样性的同时，使用 score 作为并列破局因子。
    """

    def select(
        self,
        candidates: list[dict[str, Any]],
        *,
        limit: int,
        similarity_threshold: float,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or not candidates:
            return []

        prepared = [
            candidate
            for candidate in candidates
            if isinstance(candidate.get("embedding"), list)
        ]
        if not prepared:
            return []

        ranked = sorted(prepared, key=lambda item: float(item.get("score", 0.0)), reverse=True)
        selected: list[dict[str, Any]] = []

        for candidate in ranked:
            if len(selected) >= limit:
                break
            if self._is_redundant(candidate, selected, similarity_threshold):
                continue

            selected.append(candidate)

            if len(selected) >= limit:
                break

            pool = [item for item in ranked if item not in selected]
            while pool and len(selected) < limit:
                best = max(
                    pool,
                    key=lambda item: (
                        self._residual_energy(item, selected),
                        float(item.get("score", 0.0)),
                    ),
                )
                pool.remove(best)
                if self._is_redundant(best, selected, similarity_threshold):
                    continue
                selected.append(best)

            break

        return selected[:limit]

    def _is_redundant(
        self,
        candidate: dict[str, Any],
        selected: list[dict[str, Any]],
        threshold: float,
    ) -> bool:
        candidate_embedding = self._to_float_vector(candidate.get("embedding", []))
        if not candidate_embedding:
            return True
        for chosen in selected:
            chosen_embedding = self._to_float_vector(chosen.get("embedding", []))
            if not chosen_embedding:
                continue
            similarity = self._cosine_similarity(candidate_embedding, chosen_embedding)
            if similarity >= threshold:
                return True
        return False

    def _residual_energy(
        self,
        candidate: dict[str, Any],
        selected: list[dict[str, Any]],
    ) -> float:
        vector = self._to_float_vector(candidate.get("embedding", []))
        if not vector:
            return 0.0
        if not selected:
            return 1.0

        basis = [
            self._to_float_vector(item.get("embedding", []))
            for item in selected
            if self._to_float_vector(item.get("embedding", []))
        ]
        if not basis:
            return 1.0

        orthonormal_basis = self._orthonormalize(basis)
        if not orthonormal_basis:
            return 1.0

        vector_array = np.asarray(vector, dtype=np.float64)
        basis_matrix = np.asarray(orthonormal_basis, dtype=np.float64)
        projection = basis_matrix.T @ (basis_matrix @ vector_array)
        residual = vector_array - projection
        residual_energy = float(residual @ residual)
        total_energy = float(vector_array @ vector_array)
        if total_energy <= 1e-12:
            return 0.0
        return residual_energy / total_energy

    @staticmethod
    def _to_float_vector(values: Any) -> list[float]:
        if values is None:
            return []
        try:
            array = np.asarray(values, dtype=np.float64)
        except Exception:  # noqa: BLE001
            return []
        if array.size <= 0:
            return []
        if array.ndim == 0:
            return [float(array)]
        if array.ndim == 1:
            return array.tolist()
        return np.asarray(array[0], dtype=np.float64).reshape(-1).tolist()

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        left_array = np.asarray(left, dtype=np.float64)
        right_array = np.asarray(right, dtype=np.float64)
        dot_sum = float(left_array @ right_array)
        left_norm = float(np.linalg.norm(left_array))
        right_norm = float(np.linalg.norm(right_array))
        if left_norm <= 1e-12 or right_norm <= 1e-12:
            return 0.0
        return dot_sum / (left_norm * right_norm)

    @staticmethod
    def _orthonormalize(vectors: list[list[float]]) -> list[list[float]]:
        basis: list[np.ndarray] = []
        for vector in vectors:
            candidate = np.asarray(vector, dtype=np.float64)
            for base in basis:
                coefficient = float(candidate @ base)
                candidate = candidate - coefficient * base
            norm_sq = float(candidate @ candidate)
            if norm_sq <= 1e-12:
                continue
            basis.append(candidate / math.sqrt(norm_sq))
        return [row.tolist() for row in basis]


__all__ = ["ResultDeduplicator"]
