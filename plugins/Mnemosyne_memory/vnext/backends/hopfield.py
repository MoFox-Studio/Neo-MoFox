"""纯 Python Modern Hopfield associative memory。"""

from __future__ import annotations

import math
from collections.abc import Sequence


class ModernHopfieldMemory:
    """使用 log-sum-exp 能量更新的现代 Hopfield 关联存储。"""

    def __init__(self, *, beta: float = 8.0) -> None:
        """创建记忆；beta 越大，检索越接近硬最大相似度。"""
        if beta <= 0 or not math.isfinite(beta):
            raise ValueError("beta 必须是正有限数")
        self.beta = beta
        self._patterns: dict[str, tuple[float, ...]] = {}

    def store(self, key: str, pattern: Sequence[float]) -> None:
        """保存或替换一个归一化前的模式。"""
        values = tuple(float(value) for value in pattern)
        if not key.strip() or not values or any(not math.isfinite(value) for value in values):
            raise ValueError("key 和 pattern 必须有效")
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            raise ValueError("pattern 不能是零向量")
        self._patterns[key] = tuple(value / norm for value in values)

    def retrieve(self, query: Sequence[float], *, limit: int = 5) -> tuple[tuple[str, float], ...]:
        """以现代 Hopfield softmax 权重返回最相似模式。"""
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        values = tuple(float(value) for value in query)
        norm = math.sqrt(sum(value * value for value in values))
        if not values or norm == 0 or any(not math.isfinite(value) for value in values):
            raise ValueError("query 必须是非零有限向量")
        normalized = tuple(value / norm for value in values)
        scored = []
        for key, pattern in self._patterns.items():
            if len(pattern) != len(normalized):
                continue
            similarity = sum(left * right for left, right in zip(normalized, pattern))
            scored.append((key, similarity))
        if not scored:
            return ()
        logits = [self.beta * score for _, score in scored]
        pivot = max(logits)
        denominator = sum(math.exp(logit - pivot) for logit in logits)
        return tuple(sorted(
            ((key, math.exp(logit - pivot) / denominator) for (key, _), logit in zip(scored, logits)),
            key=lambda item: (-item[1], item[0]),
        )[:limit])
