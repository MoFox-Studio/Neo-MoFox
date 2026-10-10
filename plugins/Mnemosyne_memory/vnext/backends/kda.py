"""KDA-style kernel delta attention for bounded candidate scoring。"""

from __future__ import annotations

import math
from collections.abc import Sequence


class KDAAttention:
    """用 key/query 相似度和 value 可靠性聚合候选，不改变来源内容。"""

    def __init__(self, *, temperature: float = 1.0) -> None:
        """设置 softmax 温度。"""
        if temperature <= 0 or not math.isfinite(temperature):
            raise ValueError("temperature 必须是正有限数")
        self.temperature = temperature

    def rank(
        self,
        query: Sequence[float],
        candidates: Sequence[tuple[str, Sequence[float], float]],
        *,
        limit: int = 5,
    ) -> tuple[tuple[str, float], ...]:
        """返回候选注意力权重；第三项是 0-1 来源可靠性。"""
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        query_values = tuple(float(value) for value in query)
        query_norm = math.sqrt(sum(value * value for value in query_values))
        if not query_values or query_norm == 0:
            raise ValueError("query 必须是非零向量")
        logits: list[tuple[str, float]] = []
        for key, vector, reliability in candidates:
            values = tuple(float(value) for value in vector)
            norm = math.sqrt(sum(value * value for value in values))
            if len(values) != len(query_values) or norm == 0:
                continue
            if not 0 <= reliability <= 1:
                raise ValueError("reliability 必须在 0-1 之间")
            cosine = sum(left * right for left, right in zip(query_values, values)) / (query_norm * norm)
            logits.append((key, cosine * max(reliability, 1e-6) / self.temperature))
        if not logits:
            return ()
        pivot = max(score for _, score in logits)
        weights = [(key, math.exp(score - pivot)) for key, score in logits]
        total = sum(weight for _, weight in weights)
        return tuple(sorted(
            ((key, weight / total) for key, weight in weights),
            key=lambda item: (-item[1], item[0]),
        )[:limit])
