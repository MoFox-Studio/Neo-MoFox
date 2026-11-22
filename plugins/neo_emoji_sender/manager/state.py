"""插件状态文件（``state.json``）。

存放跨重启需要保留的轻量处理状态，例如 embedding 配置指纹与旧数据迁移标记。
业务数据不放这里——表情包记录、标签、抑制项一律走 SQLite。

文件缺失或损坏时一律视为“没有任何已记录状态”，由调用方按首次运行处理。
写入采用“临时文件 + 原子替换”，避免截断重写过程中断电留下半截 JSON。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import TypedDict, cast


class IndexMeta(TypedDict):
    """embedding 配置指纹。"""

    embedding_model: str
    collection: str


class MigrationMeta(TypedDict, total=False):
    """旧 emoji_sender 数据迁移标记。"""

    marker: str
    migrated: int
    skipped: int
    finished_at: float


class PluginState(TypedDict, total=False):
    """``state.json`` 的完整结构。"""

    index: IndexMeta
    migration: MigrationMeta


def _empty_state() -> PluginState:
    """构造空的初始状态。

    Returns:
        ``index`` 与 ``migration`` 均缺失的空状态。
    """
    return PluginState()


class StateStore:
    """读写 ``state.json``。

    结构是固定的顶层分组（``index`` / ``migration``），因此各写入方只改自己
    那一组，不会互相覆盖。文件体积很小，读写都放在线程池里执行。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        # 读-改-写需要串行：同一个实例的两个 set_* 并发时会各自读到旧内容，
        # 后写入的一方会把先写入的分组丢掉
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        """状态文件路径。

        Returns:
            ``state.json`` 的绝对或相对路径。
        """
        return self._path

    def _read(self) -> PluginState:
        """线程内读取状态文件。

        Returns:
            已落盘的状态；文件不存在或内容损坏时返回空状态。
        """
        if not self._path.exists():
            return _empty_state()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return _empty_state()
        return cast(PluginState, data) if isinstance(data, dict) else _empty_state()

    def _write(self, data: PluginState) -> None:
        """线程内整体写回状态文件。

        先写同目录临时文件再 ``os.replace``：直接截断重写时若进程中断，
        文件会变成半截 JSON，下次读取按"空状态"处理，索引指纹因此丢失。

        Args:
            data: 待落盘的完整状态。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    async def read(self) -> PluginState:
        """读取完整状态。

        Returns:
            已落盘的状态；文件不存在或损坏时返回空状态。
        """
        return await asyncio.to_thread(self._read)

    async def get_index_meta(self) -> IndexMeta | None:
        """读取 embedding 配置指纹。

        Returns:
            指纹；从未写入过时返回 None。
        """
        return (await self.read()).get("index")

    async def set_index_meta(self, meta: IndexMeta) -> None:
        """写入 embedding 配置指纹。

        只替换 ``index`` 分组，保留其余状态。

        Args:
            meta: 待落盘的指纹。
        """
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            data["index"] = meta
            await asyncio.to_thread(self._write, data)

    async def get_migration(self) -> MigrationMeta | None:
        """读取迁移标记。

        Returns:
            迁移标记；从未迁移过时返回 None。
        """
        return (await self.read()).get("migration")

    async def set_migration(self, meta: MigrationMeta) -> None:
        """写入迁移标记。

        只替换 ``migration`` 分组，保留其余状态。

        Args:
            meta: 待落盘的迁移标记。
        """
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            data["migration"] = meta
            await asyncio.to_thread(self._write, data)


__all__ = [
    "IndexMeta",
    "MigrationMeta",
    "PluginState",
    "StateStore",
]
