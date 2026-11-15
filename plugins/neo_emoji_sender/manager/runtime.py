"""neo_emoji_sender 运行时（插件级模块单例）。

Service、来源、事件处理器都是框架按需实例化的组件，构造时只能拿到
``BasePlugin``，看不到插件子类上的字段。若让它们反过来 ``getattr`` 插件实例，
既拿不到类型信息，也把依赖方向扭反了。

因此跨组件共享状态集中在这里，并以模块级单例暴露：

- 组件侧一律 ``EmojiRuntime.get()``，拿到的是具体类型
- 插件 ``on_plugin_loaded`` 负责 ``create``，``on_plugin_unloaded`` 负责 ``close``

单例的生命周期必须跟着插件走：``close()`` 会把内部引用置空，避免插件重载后
拿到指向已关闭数据库的旧对象。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

from .repository import EmojiRepository
from .state import StateStore
from .store import EmojiStore
from .vector_index import EmojiVectorIndex

logger = get_logger("neo_emoji_sender.runtime")

if TYPE_CHECKING:
    from ..config import NeoEmojiSenderConfig
    from ..sources.base import EmojiSource
    from .reconciler import EmojiReconciler


class EmojiRuntime:
    """插件级共享运行时。

    不直接实例化，统一走 :meth:`create` 与 :meth:`get`。
    """

    _instance: EmojiRuntime | None = None

    def __init__(self, config: NeoEmojiSenderConfig) -> None:
        """装配仓储、文件存储与向量索引。

        Args:
            config: 已加载的插件配置。
        """
        self.config = config
        data_dir = Path(config.storage.data_dir)
        self.data_dir = data_dir
        self.repo = EmojiRepository(str(data_dir / "emoji.db"))
        self.store = EmojiStore(data_dir)
        # 插件处理状态（embedding 指纹、迁移标记）统一放在 state.json
        self.state = StateStore(data_dir / "state.json")
        self.vector = EmojiVectorIndex(
            db_path=str(data_dir / "vector"),
            collection_name=str(config.vector.collection_name),
            state=self.state,
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

    @classmethod
    def create(cls, config: NeoEmojiSenderConfig) -> EmojiRuntime:
        """创建并登记模块单例，覆盖可能残留的旧实例。

        Args:
            config: 已加载的插件配置。

        Returns:
            新建的运行时实例。
        """
        cls._instance = cls(config)
        return cls._instance

    @classmethod
    def get(cls) -> EmojiRuntime:
        """获取当前运行时。

        Returns:
            已登记的运行时实例。

        Raises:
            RuntimeError: 插件未启用或运行时尚未创建时抛出。
        """
        if cls._instance is None:
            raise RuntimeError("neo_emoji_sender 运行时未初始化，请确认插件已启用并加载成功")
        return cls._instance

    @classmethod
    def peek(cls) -> EmojiRuntime | None:
        """获取当前运行时但不抛异常。

        供加载 / 卸载流程在自身可能重复调用时做判断。

        Returns:
            已登记的运行时实例；未创建时返回 None。
        """
        return cls._instance

    async def initialize(self) -> None:
        """初始化存储与向量索引。"""
        await self.store.initialize()
        await self.repo.initialize()
        await self.vector.initialize()

    async def migrate_legacy_if_needed(self) -> None:
        """在满足条件时把旧 emoji_sender 数据并入本插件。

        两个条件缺一不可：

        - 旧插件的定时任务已不在调度器中（说明它已停用或从未加载）
        - 本库尚未写过迁移标记

        旧任务仍在时跳过而不写标记，下次加载（旧插件停用后）会重新尝试。
        """
        from .legacy_migration import (
            MIGRATION_MARKER,
            LegacyPaths,
            legacy_schedule_active,
            migrate,
        )

        cfg = self.config
        if not cfg.migration.enabled:
            return
        if await self.state.get_migration() is not None:
            return
        if legacy_schedule_active():
            # 旧插件仍在运行并持续写入旧库，此时迁移只能拿到不一致的快照
            logger.info("检测到 emoji_sender 定时任务仍在注册，跳过数据迁移")
            return

        paths = LegacyPaths(
            root=Path(cfg.migration.legacy_root),
            vector_db=str(cfg.migration.legacy_vector_db),
            collection=str(cfg.migration.legacy_collection),
        )
        if not paths.root.exists() and not Path(paths.vector_db).exists():
            # 没有旧数据可迁，写标记避免每次加载都做无谓探测
            await self.state.set_migration(
                {"marker": MIGRATION_MARKER, "migrated": 0, "skipped": 0}
            )
            return

        result = await migrate(
            paths=paths,
            target_root=self.data_dir,
            repo=self.repo,
            store=self.store,
        )
        await self.state.set_migration(
            {
                "marker": MIGRATION_MARKER,
                "migrated": result.migrated,
                "skipped": result.skipped,
                "finished_at": time.time(),
            }
        )

    async def close(self) -> None:
        """释放资源：移除调度任务、关闭数据库并注销单例。

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
        # 必须注销，否则插件重载后 get() 会拿到指向已关闭数据库的旧对象
        if EmojiRuntime._instance is self:
            EmojiRuntime._instance = None

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


__all__ = ["EmojiRuntime"]
