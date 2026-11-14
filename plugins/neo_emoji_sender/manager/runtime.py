"""neo_emoji_sender 运行时。

Service 不是单例，所有跨实例共享状态都放在这里。
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from .repository import EmojiRepository
from .store import EmojiStore
from .vector_index import EmojiVectorIndex

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

    from ..config import NeoEmojiSenderConfig
    from ..sources.base import EmojiSource
    from .reconciler import EmojiReconciler


class EmojiRuntime:
    """插件级共享运行时。"""

    def __init__(self, plugin: BasePlugin) -> None:
        """装配仓储、文件存储与向量索引。

        Args:
            plugin: 所属插件实例，用于读取配置。
        """
        self.plugin = plugin
        cfg = self.config
        data_dir = Path(cfg.storage.data_dir)
        self.data_dir = data_dir
        self.repo = EmojiRepository(str(data_dir / "emoji.db"))
        self.store = EmojiStore(data_dir)
        self.vector = EmojiVectorIndex(
            db_path=str(data_dir / "vector"),
            collection_name=str(cfg.vector.collection_name),
            meta_path=str(data_dir / "index_meta.json"),
        )
        self.usage_history: dict[str, deque[str]] = {}
        # 全局写锁：注册 / 更新 / 删除互斥，避免并发写入时 DB 与文件状态错位
        self.mutation_lock = asyncio.Lock()
        # 正在落盘的文件路径，对账器据此跳过，避免误删写了一半的文件
        self.pending_paths: set[str] = set()
        # 按 emoji_id 细分的索引锁：同一表情包的向量重建串行，不同表情包可并行
        self.index_locks: dict[str, asyncio.Lock] = {}
        self.sources: list[EmojiSource] = []
        self.schedule_ids: list[str] = []
        self.reconciler: EmojiReconciler | None = None

    @property
    def config(self) -> "NeoEmojiSenderConfig":
        """获取插件配置。

        Returns:
            强类型化的插件配置对象。

        Raises:
            RuntimeError: 插件配置未加载或类型不符时抛出。
        """
        from ..config import NeoEmojiSenderConfig

        cfg = self.plugin.config
        if not isinstance(cfg, NeoEmojiSenderConfig):
            raise RuntimeError("neo_emoji_sender plugin config 未正确加载")
        return cfg

    async def initialize(self) -> None:
        """初始化存储与向量索引。"""
        await self.store.initialize()
        await self.repo.initialize()
        await self.vector.initialize()

    async def close(self) -> None:
        """释放资源：移除调度任务并关闭数据库。

        调度任务统一登记在 ``schedule_ids`` 中，按 ID 移除即可，
        各来源不再自行维护撤销逻辑。
        """
        if self.schedule_ids:
            try:
                from src.kernel.scheduler import get_unified_scheduler

                scheduler = get_unified_scheduler()
                for schedule_id in list(self.schedule_ids):
                    try:
                        await scheduler.remove_schedule(schedule_id)
                    except Exception:
                        pass
            except Exception:
                pass
            self.schedule_ids.clear()
        self.sources.clear()
        await self.repo.close()

    def index_lock(self, emoji_id: str) -> asyncio.Lock:
        """获取某个表情包的索引锁。

        锁按需创建并常驻，保证同一表情包的向量重建始终串行。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            该表情包对应的锁对象。
        """
        lock = self.index_locks.get(emoji_id)
        if lock is None:
            lock = asyncio.Lock()
            self.index_locks[emoji_id] = lock
        return lock

    # ------------------------------------------------------------------
    # 使用历史
    # ------------------------------------------------------------------

    def recently_used(self, stream_id: str) -> set[str]:
        """获取指定聊天流最近已发送的表情包。

        Args:
            stream_id: 聊天流 ID。

        Returns:
            最近发送过的表情包 ID 集合；无历史时为空集合。
        """
        history = self.usage_history.get(stream_id)
        return set(history) if history else set()

    def record_usage(self, stream_id: str, emoji_id: str) -> None:
        """记录一次发送，供去重使用。

        Args:
            stream_id: 聊天流 ID。
            emoji_id: 表情包完整 ID。
        """
        if not stream_id or not emoji_id:
            return
        cfg = self.config
        if not cfg.dedup.enabled:
            return
        window = max(1, int(cfg.dedup.window))
        history = self.usage_history.get(stream_id)
        if history is None:
            history = deque(maxlen=window)
            self.usage_history[stream_id] = history
        history.append(emoji_id)
