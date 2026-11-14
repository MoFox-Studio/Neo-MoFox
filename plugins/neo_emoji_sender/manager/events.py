"""neo_emoji_sender 事件契约。

四个事件的 payload key 集合固定，供 EventBus 校验。
"""

from __future__ import annotations

from typing import TypedDict, cast

from src.kernel.event.core import EventParams

EVENT_PREFIX = "neo_emoji_sender:"
EVENT_REGISTERED = f"{EVENT_PREFIX}emoji_registered"
EVENT_UPDATED = f"{EVENT_PREFIX}emoji_updated"
EVENT_DELETED = f"{EVENT_PREFIX}emoji_deleted"
EVENT_SEARCH = f"{EVENT_PREFIX}emoji_search"


class RegisteredPayload(TypedDict):
    """注册 / 更新事件 payload。"""

    emoji_id: str
    description: str
    tags: list[str]
    revision: str
    source_kind: str
    updated_at: float
    ok: bool
    error: str


class DeletedPayload(TypedDict):
    """删除事件 payload。"""

    emoji_id: str
    ok: bool
    error: str


class SearchResultItem(TypedDict):
    """检索事件回填的单个命中项。"""

    emoji_id: str
    tag: str
    distance: float


class SearchPayload(TypedDict):
    """检索事件 payload。"""

    query: str
    tags: list[str]
    limit: int
    exclude_ids: list[str]
    results: list[SearchResultItem]
    ok: bool
    error: str


def registered_payload(
    *,
    emoji_id: str,
    description: str,
    tags: list[str],
    revision: str,
    source_kind: str,
    updated_at: float = 0.0,
) -> RegisteredPayload:
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


def deleted_payload(*, emoji_id: str) -> DeletedPayload:
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
) -> SearchPayload:
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


def as_event_params(payload: RegisteredPayload | DeletedPayload | SearchPayload) -> EventParams:
    """把 payload 转成 EventBus 期望的参数字典。

    ``publish_event`` 的参数类型是普通字典，而 TypedDict 虽然描述的
    也是普通 dict 的结构，类型检查器并不会认为它可以赋值给该参数，
    因此在此集中做一次显式转换。

    Args:
        payload: 事件 payload。

    Returns:
        可直接传给 ``publish_event`` 的参数字典。
    """
    return cast(EventParams, payload)


__all__ = [
    "EVENT_PREFIX",
    "EVENT_REGISTERED",
    "EVENT_UPDATED",
    "EVENT_DELETED",
    "EVENT_SEARCH",
    "RegisteredPayload",
    "DeletedPayload",
    "SearchPayload",
    "SearchResultItem",
    "as_event_params",
    "registered_payload",
    "deleted_payload",
    "search_payload",
]
