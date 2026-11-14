"""neo_emoji_sender 事件契约。

四个事件的 payload key 集合固定，供 EventBus 校验。
"""

from __future__ import annotations

from typing import Any

EVENT_PREFIX = "neo_emoji_sender:"
EVENT_REGISTERED = f"{EVENT_PREFIX}emoji_registered"
EVENT_UPDATED = f"{EVENT_PREFIX}emoji_updated"
EVENT_DELETED = f"{EVENT_PREFIX}emoji_deleted"
EVENT_SEARCH = f"{EVENT_PREFIX}emoji_search"


def registered_payload(
    *,
    emoji_id: str,
    description: str,
    tags: list[str],
    revision: str,
    source_kind: str,
    updated_at: float = 0.0,
) -> dict[str, Any]:
    """构建注册 / 更新事件 payload。

    Args:
        emoji_id: 表情包完整 ID。
        description: 表情包描述。
        tags: 情感标签列表。
        revision: 内容指纹。
        source_kind: 来源类型。
        updated_at: 更新时间戳。

    Returns:
        固定 key 集合的 payload。
    """
    return {
        "emoji_id": emoji_id,
        "description": description,
        "tags": list(tags),
        "revision": revision,
        "source_kind": source_kind,
        "updated_at": float(updated_at),
        "ok": False,
        "error": "",
    }


def deleted_payload(*, emoji_id: str) -> dict[str, Any]:
    """构建删除事件 payload。

    Args:
        emoji_id: 表情包完整 ID。

    Returns:
        固定 key 集合的 payload。
    """
    return {
        "emoji_id": emoji_id,
        "ok": False,
        "error": "",
    }


def search_payload(
    *,
    query: str,
    tags: list[str],
    limit: int,
    exclude_ids: list[str],
) -> dict[str, Any]:
    """构建检索事件 payload。

    Args:
        query: 查询文本。
        tags: 标签过滤。
        limit: 结果数量上限。
        exclude_ids: 需要排除的表情包 ID。

    Returns:
        固定 key 集合的 payload。
    """
    return {
        "query": query,
        "tags": list(tags),
        "limit": int(limit),
        "exclude_ids": list(exclude_ids),
        "results": [],
        "ok": False,
        "error": "",
    }


__all__ = [
    "EVENT_PREFIX",
    "EVENT_REGISTERED",
    "EVENT_UPDATED",
    "EVENT_DELETED",
    "EVENT_SEARCH",
    "registered_payload",
    "deleted_payload",
    "search_payload",
]
