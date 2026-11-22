"""主库 Images 表情来源。"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, cast

from src.app.plugin_system.api.database_api import query
from src.core.models.sql_alchemy import Images
from src.kernel.logger import get_logger

from ..manager.store import guess_mime
from ..manager.types import EmojiCandidate
from .base import EmojiSource

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

    from ..config import NeoEmojiSenderConfig

logger = get_logger("neo_emoji_sender.source.media")


class MediaCacheSource(EmojiSource):
    """从主库 ``Images`` 表中读取 ``type='emoji'`` 的记录。"""

    source_kind = "media_cache"

    def __init__(self, plugin: BasePlugin) -> None:
        super().__init__(plugin)
        self._cursor_id = 0
        # 已从主库取出、但还没交给入库流程的候选。基类一轮只消费 batch_size
        # 条，如果取候选时就把游标推到页末，剩下的候选会被永久跳过
        self._pending: deque[tuple[int, EmojiCandidate]] = deque()
        self.vlm_enabled = bool(self.config.sources.media.vlm_enabled)

    @property
    def _source_config(self) -> NeoEmojiSenderConfig.SourcesSection.MediaSection:
        """获取主库媒体来源的配置节。

        Returns:
            主库媒体来源配置。
        """
        return self.config.sources.media

    async def collect_candidates(self, *, limit: int) -> list[EmojiCandidate]:
        """按主键游标分页读取候选。

        游标只在候选真正交给入库流程时（即被本方法返回时）推进。因此
        "一次取多行、一轮只入库一部分"不会丢掉剩下的候选：本轮没消费完的
        候选由 :meth:`requeue` 退回缓冲区，下一轮按原顺序继续返回。
        主库读到页末时游标回绕到 0，保证已入库 / 被抑制的行不会让扫描
        永久停在尾部。

        返回列表可能多于 ``limit``：基类按自己的 ``batch_size`` 截断时，
        未消费的候选仍在本次名单里，由 :meth:`requeue` 收回。

        Args:
            limit: 本轮期望的候选数量。

        Returns:
            候选列表。
        """
        limit = max(1, limit)
        candidates: list[EmojiCandidate] = []
        if self._pending:
            # 上一轮已取出但未交给基类的候选优先返回，游标此时才推进
            self._drain_pending(candidates)
            return candidates

        # 多取一些，让基类按 batch_size 截断后仍有富余；未消费的部分由
        # requeue 退回缓冲区，不会因为游标推进而丢失
        scan_limit = max(limit, int(self.config.sources.scan_batch_size))
        builder = self._build_query(scan_limit)
        # ``all()`` 的返回类型是 ``list[T] | list[dict]``，这里显式收窄为模型实例。
        rows = cast(list[Images], await builder.all())
        if not rows and self._cursor_id:
            # 已经读到末尾：回到表头，避免游标永久停在尾部后不再发现新表情
            self._cursor_id = 0
        for row in rows:
            path = str(row.path or "").strip()
            if not path:
                continue
            known_hash = None
            image_id = str(row.image_id or "").strip()
            if len(image_id) == 64 and all(char in "0123456789abcdef" for char in image_id.lower()):
                known_hash = image_id.lower()
            self._pending.append(
                (
                    int(row.id),
                    EmojiCandidate(
                        source_kind=self.source_kind,
                        source_ref=str(row.id),
                        mime=guess_mime(Path(path).suffix),
                        hint=str(row.description or ""),
                        known_hash=known_hash,
                        path=path,
                    ),
                )
            )
        self._drain_pending(candidates)
        if not candidates and rows:
            # 整页都因缺少路径被跳过：游标仍停在页前，下一轮会重复取到同一页
            # 并空转。直接推进到页末，让扫描继续往后走
            self._cursor_id = max(int(row.id) for row in rows)
        return candidates

    def _build_query(self, limit: int):
        """构造按主键游标读取主库 ``Images`` 的查询。

        Args:
            limit: 单次读取的行数上限。

        Returns:
            可继续链式调用或 ``await .all()`` 的查询构建器。
        """
        return (
            query(Images)
            .filter(type="emoji", id__gt=self._cursor_id)
            .order_by("id")
            .limit(limit)
        )

    def _drain_pending(self, sink: list[EmojiCandidate]) -> None:
        """把缓冲区里的候选全部移入结果列表，并同步推进游标。

        只有在这里（真正交给调用方时）才推进 ``_cursor_id``，未消费的候选
        就不会因为游标先跳到页末而丢失。

        Args:
            sink: 结果列表，就地追加。
        """
        while self._pending:
            row_id, candidate = self._pending.popleft()
            self._cursor_id = row_id
            sink.append(candidate)

    async def load(self, candidate: EmojiCandidate) -> bytes:
        """读取候选文件。

        Args:
            candidate: 候选。

        Returns:
            文件字节。
        """
        return await asyncio.to_thread(Path(candidate.path).read_bytes)

    async def requeue(self, candidates: list[EmojiCandidate]) -> None:
        """把本轮未消费的候选放回缓冲区。

        ``source_ref`` 就是主库 ``Images.id``，可直接还原游标位置；逆序
        ``appendleft`` 保证下一轮按原顺序返回。

        Args:
            candidates: 本轮未交给入库流程的候选。
        """
        for candidate in reversed(candidates):
            try:
                row_id = int(candidate.source_ref)
            except (TypeError, ValueError):
                continue
            self._pending.appendleft((row_id, candidate))


__all__ = ["MediaCacheSource"]
