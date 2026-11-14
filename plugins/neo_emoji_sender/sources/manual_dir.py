"""手动目录来源。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

from ..manager.store import ALLOWED_SUFFIXES, guess_mime
from ..manager.types import EmojiCandidate, EmojiRecord
from .base import EmojiSource

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

    from ..config import NeoEmojiSenderConfig

logger = get_logger("neo_emoji_sender.source.manual")


class ManualDirectorySource(EmojiSource):
    """扫描配置目录中的表情包文件。"""

    source_kind = "manual_dir"

    def __init__(self, plugin: BasePlugin) -> None:
        super().__init__(plugin)
        self._cursor = 0

    @property
    def _source_config(self) -> NeoEmojiSenderConfig.SourcesSection.ManualSection:
        return self.config.sources.manual

    @property
    def _root(self) -> Path:
        return Path(self._source_config.dir)

    async def collect_candidates(self, *, limit: int) -> list[EmojiCandidate]:
        """按文件名顺序返回接下来的候选。

        Args:
            limit: 候选数量上限。

        Returns:
            候选列表。
        """
        files = await asyncio.to_thread(self._list_files)
        if not files:
            return []
        if self._cursor >= len(files):
            self._cursor = 0
        selected = files[self._cursor : self._cursor + max(1, limit)]
        self._cursor = (self._cursor + len(selected)) % len(files)
        return [
            EmojiCandidate(
                source_kind=self.source_kind,
                source_ref=str(path),
                mime=guess_mime(path.suffix),
                path=str(path),
            )
            for path in selected
        ]

    def _list_files(self) -> list[Path]:
        """线程内列出允许的后缀文件。"""
        if not self._root.exists():
            self._root.mkdir(parents=True, exist_ok=True)
        return sorted(
            path
            for path in self._root.iterdir()
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
        )

    async def load(self, candidate: EmojiCandidate) -> bytes:
        """读取候选文件。

        Args:
            candidate: 候选。

        Returns:
            文件字节。
        """
        return await asyncio.to_thread(Path(candidate.path).read_bytes)

    async def after_register(self, candidate: EmojiCandidate, record: EmojiRecord) -> None:
        """按配置移动或删除已注册的源文件。

        Args:
            candidate: 候选。
            record: 已注册记录。
        """
        action = str(self._source_config.after_register or "keep").strip().lower()
        if action == "keep":
            return
        source = Path(candidate.path)
        if action == "delete":
            await asyncio.to_thread(source.unlink, True)
            return
        if action == "move":
            target_dir = self.runtime.data_dir / "processed"
            await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
            target = target_dir / source.name
            await asyncio.to_thread(shutil.move, str(source), str(target))
            logger.debug(f"已移动源文件: {source} -> {target}")


__all__ = ["ManualDirectorySource"]
