"""neo_emoji_sender 事件处理器：向量化与检索。

必须完整捕获异常并以 ``ok=False`` 回传，不能把异常抛给 EventBus；
EventBus 会把处理器异常转成 PASS，导致参数变更丢失。
"""

from __future__ import annotations

from typing import cast

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from ..manager.events import (
    DeletedPayload,
    EmojiEvent,
    RegisteredPayload,
    SearchPayload,
    SearchResultItem,
)
from ..manager.runtime import EmojiRuntime

logger = get_logger("neo_emoji_sender.vectorizer")


class EmojiVectorizer(BaseEventHandler):
    """订阅表情包领域事件，维护向量索引并响应检索。"""

    name = "emoji_vectorizer"
    description = "订阅表情包注册 / 更新 / 删除 / 检索事件"
    weight = 100
    timeout = 0
    init_subscribe = [
        EmojiEvent.REGISTERED,
        EmojiEvent.UPDATED,
        EmojiEvent.DELETED,
        EmojiEvent.SEARCH,
    ]

    async def execute(
        self, event_name: str, params: RegisteredPayload | DeletedPayload | SearchPayload
    ) -> tuple[EventDecision, RegisteredPayload | DeletedPayload | SearchPayload]:
        """处理表情包事件并维护向量索引。

        按事件名分派到重建 / 删除 / 检索三个处理器，所有失败都回填到
        ``params["ok"]`` 与 ``params["error"]`` 而不是抛出。

        Args:
            event_name: 事件名，取值见 :class:`EmojiEvent`。
            params: 事件参数。

        Returns:
            (事件决策, 回填后的参数)。
        """
        try:
            runtime = EmojiRuntime.get()
            if event_name in (EmojiEvent.REGISTERED, EmojiEvent.UPDATED):
                await self._handle_upsert(runtime, cast(RegisteredPayload, params))
            elif event_name == EmojiEvent.DELETED:
                await self._handle_delete(runtime, cast(DeletedPayload, params))
            elif event_name == EmojiEvent.SEARCH:
                await self._handle_search(runtime, cast(SearchPayload, params))
        except Exception as e:
            # 必须自己吞掉异常并回填 ok=False：EventBus 会把处理器抛出的异常
            # 转成 PASS，导致本次对 params 的修改整体丢弃，调用方读不到失败原因
            logger.warning(f"向量化处理器失败 {event_name}: {e}")
            params["ok"] = False
            params["error"] = str(e)
        return EventDecision.SUCCESS, params

    @staticmethod
    async def _handle_upsert(runtime: EmojiRuntime, params: RegisteredPayload) -> None:
        """注册 / 更新：重建该表情包的全部向量。

        Args:
            runtime: 插件运行时。
            params: 含 emoji_id / description / tags / revision 的参数。
        """
        emoji_id = str(params.get("emoji_id") or "").strip()
        description = str(params.get("description") or "").strip()
        tags = [str(tag).strip() for tag in (params.get("tags") or []) if str(tag).strip()]
        revision = str(params.get("revision") or "").strip()
        updated_at = float(params.get("updated_at") or 0.0)
        if not emoji_id or not description or not tags:
            params["ok"] = False
            params["error"] = "事件参数不完整"
            return
        # 按 emoji_id 加锁：同一表情包的注册与对账重建可能并发，串行化避免
        # "先删后加"写成一半时被另一方插进来
        async with runtime.index_lock(emoji_id):
            ok = await runtime.vector.upsert(
                emoji_id=emoji_id,
                description=description,
                tags=tags,
                revision=revision,
                updated_at=updated_at,
            )
        params["ok"] = ok
        if not ok:
            params["error"] = "向量写入失败"

    @staticmethod
    async def _handle_delete(runtime: EmojiRuntime, params: DeletedPayload) -> None:
        """删除：清理该表情包的全部向量。

        Args:
            runtime: 插件运行时。
            params: 含 emoji_id 的参数。
        """
        emoji_id = str(params.get("emoji_id") or "").strip()
        if not emoji_id:
            params["ok"] = False
            params["error"] = "emoji_id 为空"
            return
        async with runtime.index_lock(emoji_id):
            ok = await runtime.vector.delete(emoji_id)
        params["ok"] = ok
        if not ok:
            params["error"] = "向量删除失败或无法确认"

    @staticmethod
    async def _handle_search(runtime: EmojiRuntime, params: SearchPayload) -> None:
        """检索：按描述与标签检索并把命中写回 ``params["results"]``。

        Args:
            runtime: 插件运行时。
            params: 含 query / tags / limit / exclude_ids 的参数。
        """
        query = str(params.get("query") or "").strip()
        tags = [str(tag).strip() for tag in (params.get("tags") or []) if str(tag).strip()]
        limit = int(params.get("limit") or 8)
        exclude = {str(item) for item in (params.get("exclude_ids") or [])}
        hits = await runtime.vector.search(
            query=query,
            tags=tags or None,
            limit=limit,
            exclude_ids=exclude,
        )
        # results 的 key 集合必须与 search_payload 预填时一致，这里整体赋值即可
        params["results"] = [
            SearchResultItem(emoji_id=hit.emoji_id, tag=hit.tag, distance=hit.distance)
            for hit in hits
        ]
        params["ok"] = True
        params["error"] = ""


__all__ = ["EmojiVectorizer"]
