"""neo_emoji_sender 对账器。

以 SQLite 为事实源，修复 DB ↔ 文件 ↔ 向量三方漂移。没有状态字段，
所有修复都由实际内容推导。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

from .types import EmojiRecord
from .vector_index import EmojiVectorMetadata

if TYPE_CHECKING:
    from .runtime import EmojiRuntime

logger = get_logger("neo_emoji_sender.reconcile")


class EmojiReconciler:
    """三方对账器。"""

    def __init__(self, runtime: EmojiRuntime) -> None:
        self.runtime = runtime

    async def run_once(self) -> None:
        """执行一次完整对账。"""
        runtime = self.runtime
        cfg = runtime.config
        records = await runtime.repo.list_all()
        db_by_id = {record.emoji_id: record for record in records}
        files = await runtime.store.list_rel_paths()
        grace = max(0, int(cfg.reconcile.grace_seconds))
        now = time.time()

        # 1) 文件缺失的 DB 记录
        db_missing = 0
        for record in records:
            if record.rel_path in files:
                continue
            if grace > 0 and now - float(record.created_at or 0.0) < grace:
                continue
            db_missing += 1
            async with runtime.mutation_lock:
                await runtime.repo.delete(record.emoji_id)
            db_by_id.pop(record.emoji_id, None)
            await self._publish_delete(record.emoji_id)
            logger.warning(f"对账：文件缺失，已删除记录 {record.emoji_id[:8]}...")

        # 2) 无 DB 记录的孤儿文件
        known_paths = {record.rel_path for record in db_by_id.values()}
        orphan_files = 0
        for rel_path in sorted(files - known_paths):
            if rel_path in runtime.pending_paths:
                continue
            try:
                mtime = await runtime.store.mtime(rel_path)
            except Exception:
                continue
            if grace > 0 and now - mtime < grace:
                continue
            try:
                await runtime.store.delete(rel_path)
                orphan_files += 1
            except Exception as e:
                logger.warning(f"对账：删除孤儿文件失败 {rel_path}: {e}")

        # 3) 向量检查
        vector_map = await runtime.vector.scan()
        reindexed = 0
        for record in db_by_id.values():
            if await self._vector_ok(record, vector_map.get(record.emoji_id, [])):
                continue
            async with runtime.index_lock(record.emoji_id):
                ok = await runtime.vector.upsert(
                    emoji_id=record.emoji_id,
                    description=record.description,
                    tags=list(record.tags),
                    revision=record.revision,
                    updated_at=record.updated_at,
                )
            if ok:
                reindexed += 1
            else:
                logger.warning(f"对账：重建向量失败 {record.emoji_id[:8]}...")

        # 4) 孤儿向量
        orphan_vectors = 0
        for emoji_id in list(vector_map):
            if emoji_id in db_by_id:
                continue
            async with runtime.index_lock(emoji_id):
                if await runtime.vector.delete(emoji_id):
                    orphan_vectors += 1

        logger.info(
            "对账完成: "
            f"records={len(db_by_id)} files={len(files)} "
            f"db_missing={db_missing} orphan_files={orphan_files} "
            f"reindexed={reindexed} orphan_vectors={orphan_vectors}"
        )

    @staticmethod
    async def _vector_ok(record: EmojiRecord, vectors: list[EmojiVectorMetadata]) -> bool:
        """判断某个表情包的向量是否完整且是最新 revision。"""
        if not vectors:
            return False
        tags = {str(item.get("tag") or "").strip() for item in vectors}
        expected = set(record.tags)
        if tags != expected:
            return False
        return all(
            str(item.get("revision") or "") == record.revision for item in vectors
        )

    async def _publish_delete(self, emoji_id: str) -> None:
        """发布删除事件，清理可能残留的向量。"""
        from .events import EmojiPublisher

        try:
            await EmojiPublisher.deleted(emoji_id=emoji_id)
        except Exception as e:
            logger.debug(f"对账发布删除事件失败: {e}")


__all__ = ["EmojiReconciler"]
