"""neo_emoji_sender 领域对象。

ORM 模型位于 :mod:`manager.models`；本模块只放跨层传递的轻量 dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmojiRecord:
    """一条表情包元数据。"""

    emoji_id: str
    rel_path: str
    mime: str
    size_bytes: int
    description: str
    revision: str
    tags: tuple[str, ...]
    source_kind: str
    source_ref: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class EmojiHit:
    """检索命中项。"""

    record: EmojiRecord
    tag: str
    distance: float


@dataclass(frozen=True, slots=True)
class VectorHit:
    """向量库原始命中项。"""

    emoji_id: str
    tag: str
    distance: float


@dataclass(frozen=True, slots=True)
class EmojiCandidate:
    """来源发现阶段返回的候选（元数据，不携带 bytes）。"""

    source_kind: str
    source_ref: str
    mime: str
    hint: str = ""
    known_hash: str | None = None
    path: str = ""


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """删除操作结果。"""

    emoji_id: str
    db_deleted: bool
    file_deleted: bool
    vector_deleted: bool
    message: str
