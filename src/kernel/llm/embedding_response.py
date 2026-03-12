"""Embedding 响应模块。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EmbeddingResponse:
    """Embedding 请求响应。"""

    embeddings: list[list[float]]
    model_name: str
    request_name: str = ""
