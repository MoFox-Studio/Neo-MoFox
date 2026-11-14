"""主库 Images 表情来源。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.database_api import query
from src.core.models.sql_alchemy import Images
from src.kernel.logger import get_logger

from ..manager.store import guess_mime
from ..manager.types import EmojiCandidate
from .base import EmojiSource

logger = get_logger("neo_emoji_sender.source.media")


class MediaCacheSource(EmojiSource):
    """从主库 ``Images`` 表中读取 ``type='emoji'`` 的记录。"""

    source_kind = "media_cache"

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._cursor_id = 0
        self.vlm_enabled = bool(self.config.sources.media.vlm_enabled)

    @property
    def _source_config(self) -> Any:
        return self.config.sources.media

    async def collect_candidates(self, *, limit: int) -> list[EmojiCandidate]:
        """按主键游标分页读取候选。

        Args:
            limit: 候选数量上限。

        Returns:
            候选列表。
        """
        rows = await query(Images).filter(type="emoji", id__gt=self._cursor_id).order_by("id").limit(max(1, limit)).all()
        candidates: list[EmojiCandidate] = []
        for row in rows:
            self._cursor_id = int(row.id)
            path = str(row.path or "").strip()
            if not path:
                continue
            known_hash = None
            image_id = str(row.image_id or "").strip()
            if len(image_id) == 64 and all(char in "0123456789abcdef" for char in image_id.lower()):
                known_hash = image_id.lower()
            candidates.append(
                EmojiCandidate(
                    source_kind=self.source_kind,
                    source_ref=str(row.id),
                    mime=guess_mime(Path(path).suffix),
                    hint=str(row.description or ""),
                    known_hash=known_hash,
                    path=path,
                )
            )
        return candidates

    async def load(self, candidate: EmojiCandidate) -> bytes:
        """读取候选文件。

        Args:
            candidate: 候选。

        Returns:
            文件字节。
        """
        return await asyncio.to_thread(Path(candidate.path).read_bytes)


__all__ = ["MediaCacheSource"]
