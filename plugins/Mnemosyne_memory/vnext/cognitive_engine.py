"""Cue-Driven Recall 的字面认知后端编排。"""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from .backends.hopfield import ModernHopfieldMemory
from .backends.kda import KDAAttention
from .backends.layersplit import LayerSplitRouter, LayerSplitStage


class CognitiveRecallEngine:
    """组合 LayerSplit、Modern Hopfield 和 KDA 的有界候选评分器。"""

    def __init__(self, *, dimension: int = 16, reconstruction_budget: int = 8) -> None:
        """创建无外部依赖的确定性 Recall Engine。"""
        if dimension < 4 or reconstruction_budget <= 0:
            raise ValueError("dimension 至少为 4，reconstruction_budget 必须大于 0")
        self.dimension = dimension
        self.router = LayerSplitRouter(
            association_budget=max(reconstruction_budget * 2, 8),
            reconstruction_budget=reconstruction_budget,
        )
        self.hopfield = ModernHopfieldMemory(beta=8.0)
        self.kda = KDAAttention(temperature=0.7)

    def rank(
        self,
        cue_text: str,
        candidates: Sequence[tuple[str, str, float]],
    ) -> dict[str, float]:
        """返回候选的认知融合分数，候选元组为 ID、文本、可靠性。"""
        if not cue_text.strip():
            return {}
        routed = self.router.route(
            LayerSplitStage.ASSOCIATION,
            tuple(candidate_id for candidate_id, _, _ in candidates),
        )
        allowed = set(routed.candidate_ids)
        query_vector = self._vector(cue_text)
        self.hopfield = ModernHopfieldMemory(beta=8.0)
        vector_candidates: list[tuple[str, tuple[float, ...], float]] = []
        for candidate_id, text, reliability in candidates:
            if candidate_id not in allowed:
                continue
            pattern = self._vector(text)
            self.hopfield.store(candidate_id, pattern)
            vector_candidates.append((candidate_id, pattern, reliability))
        if not vector_candidates:
            return {}
        hopfield_scores = dict(self.hopfield.retrieve(query_vector, limit=len(vector_candidates)))
        kda_scores = dict(self.kda.rank(query_vector, vector_candidates, limit=len(vector_candidates)))
        reconstruction = self.router.route(
            LayerSplitStage.RECONSTRUCTION,
            tuple(item[0] for item in vector_candidates),
        )
        reconstruction_ids = set(reconstruction.candidate_ids)
        return {
            candidate_id: 0.45 * hopfield_scores.get(candidate_id, 0.0)
            + 0.45 * kda_scores.get(candidate_id, 0.0)
            + 0.10 * (1.0 if candidate_id in reconstruction_ids else 0.0)
            for candidate_id, _, _ in vector_candidates
        }

    def _vector(self, text: str) -> tuple[float, ...]:
        """把文本稳定映射为有界向量，避免在在线 Recall 中调用额外模型。"""
        digest = sha256(text.strip().casefold().encode("utf-8")).digest()
        values = []
        for index in range(self.dimension):
            byte = digest[index % len(digest)]
            values.append((byte / 127.5) - 1.0)
        return tuple(values)
