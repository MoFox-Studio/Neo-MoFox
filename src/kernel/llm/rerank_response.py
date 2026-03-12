"""Rerank 响应模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RerankItem:
    """Rerank 单条结果。"""

    index: int
    score: float
    document: Any


@dataclass(slots=True)
class RerankResponse:
    """Rerank 请求响应。"""

    results: list[RerankItem]
    model_name: str
    request_name: str = ""
