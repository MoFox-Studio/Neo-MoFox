"""LLM 统计数据库管理。

管理独立的 SQLite 数据库连接，与公共数据库完全隔离。
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger

from .config import LLMStatsConfig

if TYPE_CHECKING:
    from .collector import LLMStatsCollector

logger = get_logger("kernel.llm.stats.db", display="LLM 统计数据库")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    model_name TEXT NOT NULL,
    model_identifier TEXT NOT NULL DEFAULT '',
    api_provider TEXT NOT NULL DEFAULT '',
    request_name TEXT NOT NULL DEFAULT '',
    stream_id TEXT DEFAULT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    latency REAL NOT NULL DEFAULT 0.0,
    success INTEGER NOT NULL DEFAULT 1,
    error_type TEXT DEFAULT NULL,
    stream INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_req_model ON llm_requests(model_name);
CREATE INDEX IF NOT EXISTS idx_llm_req_name ON llm_requests(request_name);
CREATE INDEX IF NOT EXISTS idx_llm_req_stream ON llm_requests(stream_id);
CREATE INDEX IF NOT EXISTS idx_llm_req_time ON llm_requests(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_req_provider ON llm_requests(api_provider);
"""

_PRAGMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA cache_size=-8000;
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;
"""


class LLMStatsDatabase:
    """LLM 统计的独立 SQLite 数据库管理器。

    线程安全：所有写操作通过专用锁串行化，读操作可并发。
    """

    def __init__(self, config: LLMStatsConfig) -> None:
        self._config = config
        self._conn: sqlite3.Connection | None = None
        self._write_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._initialized = False

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def initialize(self) -> None:
        """初始化数据库连接和表结构。"""
        if self._initialized:
            return
        if not self._config.enabled:
            logger.info("LLM 统计已禁用，跳过数据库初始化")
            self._initialized = True
            return

        db_path = Path(self._config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._loop = asyncio.get_running_loop()

        def _init() -> None:
            self._conn = sqlite3.connect(
                str(db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_PRAGMA_SQL)
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()

        await self._loop.run_in_executor(None, _init)
        self._initialized = True
        logger.info(f"LLM 统计数据库已初始化: {db_path}")

    async def close(self) -> None:
        """关闭数据库连接。"""
        if not self._conn:
            return

        loop = self._loop or asyncio.get_running_loop()

        def _close() -> None:
            with self._write_lock:
                if self._conn:
                    self._conn.close()
                    self._conn = None

        await loop.run_in_executor(None, _close)
        self._initialized = False

    async def execute_write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """执行写操作，返回 lastrowid。"""
        if not self._conn or not self._loop:
            return 0

        def _write() -> int:
            assert self._conn is not None
            with self._write_lock:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
                return cursor.lastrowid or 0

        return await self._loop.run_in_executor(None, _write)

    async def execute_read(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        """执行读操作，返回字典列表。"""
        if not self._conn or not self._loop:
            return []

        def _read() -> list[dict[str, Any]]:
            assert self._conn is not None
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

        return await self._loop.run_in_executor(None, _read)

    async def vacuum_if_needed(self, threshold: int = 100_000) -> None:
        """当记录数超过阈值时清理旧记录。"""
        if not self._config.max_records or self._config.max_records <= 0:
            return

        def _cleanup() -> None:
            assert self._conn is not None
            with self._write_lock:
                count_row = self._conn.execute(
                    "SELECT COUNT(*) as cnt FROM llm_requests"
                ).fetchone()
                if count_row and count_row["cnt"] > threshold:
                    excess = count_row["cnt"] - self._config.max_records
                    if excess > 0:
                        self._conn.execute(
                            "DELETE FROM llm_requests WHERE id IN ("
                            "SELECT id FROM llm_requests ORDER BY timestamp ASC LIMIT ?"
                            ")",
                            (excess,),
                        )
                        self._conn.commit()

        if self._loop:
            await self._loop.run_in_executor(None, _cleanup)


# ---------------------------------------------------------------------------
# 全局实例管理
# ---------------------------------------------------------------------------

_global_db: LLMStatsDatabase | None = None
_global_collector: "LLMStatsCollector | None" = None


def _get_global_db() -> LLMStatsDatabase:
    """获取全局数据库实例。"""
    if _global_db is None:
        raise RuntimeError("LLM 统计模块未初始化，请先调用 init_llm_stats()")
    return _global_db


async def init_llm_stats(
    config: LLMStatsConfig | None = None,
    *,
    db_path: str = "data/llm_stats/llm_stats.db",
    enabled: bool = True,
    max_records: int = 100_000,
    window_hours: float = 5.0,
) -> "LLMStatsCollector":
    """初始化 LLM 统计模块。

    Args:
        db_path: SQLite 数据库文件路径。
        enabled: 是否启用统计收集。
        max_records: 最大记录数。
        window_hours: 聚合查询时间窗口（小时）。

    Returns:
        LLMStatsCollector 实例。
    """
    global _global_db, _global_collector

    if _global_db is not None:
        await _global_db.close()

    effective_config = config or LLMStatsConfig(
        db_path=db_path,
        enabled=enabled,
        max_records=max_records,
        window_hours=window_hours,
    )
    _global_db = LLMStatsDatabase(effective_config)
    await _global_db.initialize()

    # 延迟导入避免循环依赖
    from .collector import LLMStatsCollector

    _global_collector = LLMStatsCollector(_global_db)
    return _global_collector


def get_llm_stats_collector() -> "LLMStatsCollector":
    """获取全局 LLMStatsCollector 实例。

    Returns:
        LLMStatsCollector 实例。若未初始化则返回一个空操作的占位实例。
    """
    global _global_collector
    if _global_collector is not None:
        return _global_collector

    # 返回占位实例，避免上层调用方需要处理 None
    from .collector import LLMStatsCollector

    _global_collector = LLMStatsCollector(None)
    return _global_collector


async def close_llm_stats_db() -> None:
    """关闭 LLM 统计数据库。"""
    global _global_db
    if _global_db is not None:
        await _global_db.close()
        _global_db = None
