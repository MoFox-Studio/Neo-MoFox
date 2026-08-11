"""Booku Memory Store 向量数学工具类。

提供余弦相似度、SVD 子空间基、投影熵、向量重塑等底层向量运算。
所有方法均为静态/类方法，无副作用，可独立测试。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


class VectorMath:
    """向量数学工具箱，提供 RAG 算法所需的所有底层向量运算。"""

    @staticmethod
    def clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    @staticmethod
    def vector_norm_sq(vector: list[float]) -> float:
        if not vector:
            return 0.0
        vector_array = np.asarray(vector, dtype=np.float64)
        return float(vector_array @ vector_array)

    @staticmethod
    def vector_dot(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        shared_size = min(len(left), len(right))
        if shared_size <= 0:
            return 0.0
        left_array = np.asarray(left[:shared_size], dtype=np.float64)
        right_array = np.asarray(right[:shared_size], dtype=np.float64)
        return float(left_array @ right_array)

    @classmethod
    def normalize_vector(cls, vector: list[float]) -> list[float]:
        if not vector:
            return []
        vector_array = np.asarray(vector, dtype=np.float64)
        norm_sq = float(vector_array @ vector_array)
        if norm_sq <= 1e-12:
            return [0.0 for _ in vector]
        normalized = vector_array / math.sqrt(norm_sq)
        return normalized.tolist()

    @classmethod
    def cosine_similarity(cls, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot_sum = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm <= 1e-12 or right_norm <= 1e-12:
            return 0.0
        return dot_sum / (left_norm * right_norm)

    @classmethod
    def project_to_basis(
        cls, vector: list[float], basis_vectors: list[list[float]]
    ) -> list[float]:
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
    def power_iteration(
        cls,
        matrix: list[list[float]],
        *,
        iterations: int = 24,
    ) -> tuple[float, list[float]]:
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
    def build_local_svd_basis(cls, vectors: list[list[float]]) -> list[list[float]]:
        if not vectors:
            return []
        first_dim = len(vectors[0]) if vectors[0] else 0
        valid_vectors = [
            vector
            for vector in vectors
            if vector
            and len(vector) == first_dim
            and cls.vector_norm_sq(vector) > 1e-12
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
            normalized = cls.normalize_vector(direction.tolist())
            if cls.vector_norm_sq(normalized) <= 1e-12:
                break
            basis.append(normalized)

            explained += float(energy)
            if explained / total_trace >= 0.9:
                break

        return basis

    @classmethod
    def novelty_energy_ratio(
        cls,
        new_vector: list[float],
        basis_vectors: list[list[float]],
    ) -> float:
        if not basis_vectors:
            return 1.0
        svd_basis = cls.build_local_svd_basis(basis_vectors)
        if not svd_basis:
            return 1.0

        projection = cls.project_to_basis(new_vector, svd_basis)
        residual = [a - b for a, b in zip(new_vector, projection, strict=False)]
        residual_energy = cls.vector_norm_sq(residual)
        total_energy = cls.vector_norm_sq(new_vector)
        if total_energy <= 1e-12:
            return 0.0
        return residual_energy / total_energy

    @classmethod
    def projection_entropy_logic_depth(
        cls,
        query_vector: list[float],
        evidence_vectors: list[list[float]],
    ) -> float:
        basis = cls.build_local_svd_basis(evidence_vectors)
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
        return cls.clamp(1.0 - entropy / max_entropy, 0.0, 1.0)

    @classmethod
    def weighted_centroid(
        cls,
        query_vector: list[float],
        vectors_with_weight: list[tuple[list[float], float]],
    ) -> list[float]:
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
    def reshape_query_vector(
        cls,
        query_vector: list[float],
        *,
        beta: float,
        core_vectors: list[tuple[list[float], float]],
        diffusion_vectors: list[tuple[list[float], float]],
        opposing_vectors: list[tuple[list[float], float]],
        energy_cutoff: float,
    ) -> list[float]:
        if not query_vector:
            return []
        query_array = np.asarray(query_vector, dtype=np.float64)
        core_term = cls.weighted_centroid(query_vector, core_vectors)
        opposing_term = cls.weighted_centroid(query_vector, opposing_vectors)
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

        reshaped = (1.0 - beta) * query_array + beta * (
            core_array + diffusion_array - opposing_array
        )
        return cls.normalize_vector(reshaped.tolist())

    @staticmethod
    def to_float_vector(
        values: Any,
        *,
        expected_dim: int | None = None,
    ) -> list[float]:
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
                return []

        if expected_dim is not None and len(vector) != expected_dim:
            return []
        return vector

    @staticmethod
    def safe_list(value: Any) -> list[Any]:
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
    def safe_first_row(value: Any) -> list[Any]:
        rows = VectorMath.safe_list(value)
        if len(rows) == 0:
            return []
        first = rows[0]
        if isinstance(first, list | tuple | np.ndarray):
            return VectorMath.safe_list(first)
        return rows


__all__ = ["VectorMath"]
