"""neo_emoji_sender 事件契约与统一发布器。

本模块是 neo_emoji_sender 对外暴露事件能力的唯一入口，结构对齐
``neo_default_chatter/utils/event_publisher.py``：

- :class:`EmojiEvent`：本插件全部自定义事件的 ``StrEnum``。值为完整事件名
  字符串（带 ``neo_emoji_sender:`` 前缀）。第三方既可
  ``init_subscribe = [EmojiEvent.SEARCH]``，也可
  ``init_subscribe = ["neo_emoji_sender:emoji_search"]``，两种写法等价
  （``StrEnum`` 的 ``str()`` 返回值即事件名）。
- :class:`EmojiPublisher`：封装 ``publish_event + payload 预填 + 结果读回``
  样板。调用方不接触 dict 构造，也不读写 ``result["params"]``。

设计要点：

- **payload key 集合固定**：EventBus 要求处理器返回的 key 集合与初始 payload
  完全一致（``src/kernel/event/core.py``），因此预填集中在发布方法内。
- **异常直接上抛**：与 NDFC 一致，Publisher 不吞异常。需要容错的调用方
  （例如"向量化失败不能阻断注册"）自行捕获。
- **不注册为 Service**：插件内部直接调用类方法；第三方若要手动触发事件，
  也可直接 import 本模块使用。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict

from src.app.plugin_system.api.event_api import publish_event
from src.kernel.event.core import EventParams

EVENT_PREFIX = "neo_emoji_sender:"


class EmojiEvent(StrEnum):
    """neo_emoji_sender 全部自定义事件。

    使用 ``StrEnum`` 而非 ``str, Enum``：后者的 ``str(member)`` 在 Python
    3.11+ 返回 ``"EmojiEvent.X"`` 而非事件名，会破坏
    ``EventManager._coerce_event_name`` 对非 ``EventType`` 走 ``str(event)``
    的分支，导致第三方用字符串字面量订阅时无法匹配发布方。
    """

    REGISTERED = f"{EVENT_PREFIX}emoji_registered"
    UPDATED = f"{EVENT_PREFIX}emoji_updated"
    DELETED = f"{EVENT_PREFIX}emoji_deleted"
    SEARCH = f"{EVENT_PREFIX}emoji_search"


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


class EmojiPublisher:
    """neo_emoji_sender 统一事件发布器。

    四个静态方法，每个 = 一次 ``publish_event`` + payload 预填全部字段
    + 结果读回。返回值直接是调用方关心的结果，不是 ``result["params"]``。

    异常直接上抛：需要容错的调用方自行捕获。
    """

    @staticmethod
    async def registered(
        *,
        emoji_id: str,
        description: str,
        tags: list[str],
        revision: str,
        source_kind: str,
        updated_at: float = 0.0,
    ) -> tuple[bool, str]:
        """发布 ``:emoji_registered``，要求订阅方建立向量索引。

        Args:
            emoji_id: 表情包完整 ID。
            description: 表情包描述。
            tags: 情感标签列表。
            revision: 内容指纹，用于判断向量是否过期。
            source_kind: 来源类型。
            updated_at: 更新时间戳。

        Returns:
            (向量是否写入成功, 错误信息)。
        """
        params = await EmojiPublisher._publish(
            EmojiEvent.REGISTERED,
            RegisteredPayload(
                emoji_id=emoji_id,
                description=description,
                tags=list(tags),
                revision=revision,
                source_kind=source_kind,
                updated_at=float(updated_at),
                ok=False,
                error="",
            ),
        )
        return bool(params.get("ok")), str(params.get("error") or "")

    @staticmethod
    async def updated(
        *,
        emoji_id: str,
        description: str,
        tags: list[str],
        revision: str,
        source_kind: str,
        updated_at: float = 0.0,
    ) -> tuple[bool, str]:
        """发布 ``:emoji_updated``，要求订阅方重建向量索引。

        Args:
            emoji_id: 表情包完整 ID。
            description: 表情包描述。
            tags: 情感标签列表。
            revision: 内容指纹，用于判断向量是否过期。
            source_kind: 来源类型。
            updated_at: 更新时间戳。

        Returns:
            (向量是否重建成功, 错误信息)。
        """
        params = await EmojiPublisher._publish(
            EmojiEvent.UPDATED,
            RegisteredPayload(
                emoji_id=emoji_id,
                description=description,
                tags=list(tags),
                revision=revision,
                source_kind=source_kind,
                updated_at=float(updated_at),
                ok=False,
                error="",
            ),
        )
        return bool(params.get("ok")), str(params.get("error") or "")

    @staticmethod
    async def deleted(*, emoji_id: str) -> tuple[bool, str]:
        """发布 ``:emoji_deleted``，要求订阅方清理向量。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            (向量是否清理成功, 错误信息)。
        """
        params = await EmojiPublisher._publish(
            EmojiEvent.DELETED,
            DeletedPayload(emoji_id=emoji_id, ok=False, error=""),
        )
        return bool(params.get("ok")), str(params.get("error") or "")

    @staticmethod
    async def search(
        *,
        query: str,
        tags: list[str],
        limit: int,
        exclude_ids: list[str],
    ) -> list[SearchResultItem]:
        """发布 ``:emoji_search``，返回向量检索命中项。

        Args:
            query: 查询文本。
            tags: 标签过滤。
            limit: 结果数量上限。
            exclude_ids: 需要排除的表情包 ID。

        Returns:
            命中项列表；订阅方报告失败时返回空列表。
        """
        params = await EmojiPublisher._publish(
            EmojiEvent.SEARCH,
            SearchPayload(
                query=query,
                tags=list(tags),
                limit=int(limit),
                exclude_ids=list(exclude_ids),
                results=[],
                ok=False,
                error="",
            ),
        )
        if not params.get("ok"):
            return []
        return list(params.get("results") or [])

    @staticmethod
    async def _publish(
        event: EmojiEvent,
        payload: RegisteredPayload | DeletedPayload | SearchPayload,
    ) -> EventParams:
        """发布事件并返回处理器回填后的参数。

        TypedDict 描述的是普通 dict 的结构，但类型检查器不认为它可以赋值给
        ``publish_event`` 的普通 dict 参数，因此在此集中做一次显式转换。

        Args:
            event: 事件枚举。
            payload: 已预填全部 key 的 payload。

        Returns:
            处理器处理后的事件参数。
        """
        result = await publish_event(event, EventParams(payload))
        return result.get("params") or {}


__all__ = [
    "EVENT_PREFIX",
    "EmojiEvent",
    "EmojiPublisher",
    "RegisteredPayload",
    "DeletedPayload",
    "SearchPayload",
    "SearchResultItem",
]
