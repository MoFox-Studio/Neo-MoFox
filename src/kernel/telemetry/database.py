"""通用遥测运行时缓冲管理。"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger

from .config import TelemetryConfig

if TYPE_CHECKING:
    from .collector import TelemetryCollector

logger = get_logger("kernel.telemetry.buffer", display="遥测缓冲")


class TelemetryBufferStore:
    """通用遥测内存窗口缓冲。

    遥测事件只在当前进程内保留，并在心跳采样时被消费。
    """

    def __init__(self, config: TelemetryConfig) -> None:
        self._config = config
        self._rows: list[dict[str, Any]] = []
        self._next_row_id = 1
        self._lock = threading.Lock()
        self._initialized = False

    @property
    def enabled(self) -> bool:
        """返回是否启用遥测。"""
        return self._config.enabled

    @property
    def detail_enabled(self) -> bool:
        """返回是否允许记录调试明细。"""
        return self._config.detail_enabled

    @property
    def hash_salt(self) -> str:
        """返回脱敏标识计算使用的盐值。"""
        return self._config.hash_salt

    @property
    def collect_error_events(self) -> bool:
        """返回是否收集错误摘要事件。"""
        return self._config.collect_error_events

    @property
    def collect_watchdog_events(self) -> bool:
        """返回是否收集 WatchDog 事件。"""
        return self._config.collect_watchdog_events

    @property
    def collect_db_metrics(self) -> bool:
        """返回是否收集数据库指标。"""
        return self._config.collect_db_metrics

    @property
    def collect_plugin_events(self) -> bool:
        """返回是否收集插件事件。"""
        return self._config.collect_plugin_events

    @property
    def collect_tool_events(self) -> bool:
        """返回是否收集工具事件。"""
        return self._config.collect_tool_events

    @property
    def collect_runtime_snapshots(self) -> bool:
        """返回是否记录运行时快照。"""
        return self._config.collect_runtime_snapshots

    @property
    def slow_query_threshold_ms(self) -> float:
        """返回慢查询阈值。"""
        return self._config.slow_query_threshold_ms

    async def initialize(self) -> None:
        """初始化运行时缓冲。"""
        if self._initialized:
            return
        if not self._config.enabled:
            logger.info("通用遥测已禁用，跳过缓冲初始化")
            self._initialized = True
            return

        with self._lock:
            self._rows.clear()
            self._next_row_id = 1
        self._initialized = True
        logger.info("通用遥测已初始化为内存窗口缓冲")

    async def close(self) -> None:
        """关闭缓冲并丢弃内存中的事件。"""
        with self._lock:
            self._rows.clear()
            self._next_row_id = 1
        self._initialized = False

    async def append_row(self, row: dict[str, Any]) -> int:
        """异步写入一条遥测事件。"""
        return self.append_row_sync(row)

    def append_row_sync(self, row: dict[str, Any]) -> int:
        """同步写入一条遥测事件。"""
        if not self.enabled:
            return 0

        with self._lock:
            stored = dict(row)
            stored["id"] = self._next_row_id
            self._next_row_id += 1
            self._rows.append(stored)
            self._trim_locked(now=float(stored.get("timestamp") or time.time()))
            return int(stored["id"])

    async def get_rows(
        self,
        *,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """返回当前窗口中的事件快照。"""
        with self._lock:
            rows = [dict(row) for row in self._rows]

        if domain is not None:
            rows = [row for row in rows if row.get("domain") == domain]

        rows.sort(
            key=lambda row: (
                float(row.get("timestamp") or 0.0),
                int(row.get("id") or 0),
            ),
            reverse=True,
        )
        if limit is not None:
            return rows[:limit]
        return rows

    async def drain_rows(self) -> list[dict[str, Any]]:
        """取出并清空当前窗口中的事件。"""
        with self._lock:
            rows = [dict(row) for row in self._rows]
            self._rows.clear()
        return rows

    async def clear(self) -> None:
        """清空当前窗口中的事件。"""
        with self._lock:
            self._rows.clear()

    def _trim_locked(self, *, now: float) -> None:
        """按配置约束裁剪当前窗口。"""
        max_age_days = self._config.max_age_days
        if max_age_days > 0:
            cutoff_ts = now - max_age_days * 86400
            self._rows = [
                row for row in self._rows if float(row.get("timestamp") or 0.0) >= cutoff_ts
            ]

        max_records = self._config.max_records
        if max_records > 0 and len(self._rows) > max_records:
            self._rows = self._rows[-max_records:]


_global_db: TelemetryBufferStore | None = None
_global_collector: "TelemetryCollector | None" = None


async def init_telemetry(
    config: TelemetryConfig | None = None,
    *,
    enabled: bool = False,
    max_records: int = 100_000,
    max_age_days: int = 30,
    detail_enabled: bool = False,
    hash_salt: str = "",
    slow_query_threshold_ms: float = 500.0,
    collect_error_events: bool = True,
    collect_watchdog_events: bool = True,
    collect_db_metrics: bool = True,
    collect_plugin_events: bool = True,
    collect_tool_events: bool = True,
    collect_runtime_snapshots: bool = True,
) -> "TelemetryCollector":
    """初始化通用遥测模块。"""
    global _global_db, _global_collector

    if _global_db is not None:
        await _global_db.close()

    effective_config = config or TelemetryConfig(
        enabled=enabled,
        max_records=max_records,
        max_age_days=max_age_days,
        detail_enabled=detail_enabled,
        hash_salt=hash_salt,
        slow_query_threshold_ms=slow_query_threshold_ms,
        collect_error_events=collect_error_events,
        collect_watchdog_events=collect_watchdog_events,
        collect_db_metrics=collect_db_metrics,
        collect_plugin_events=collect_plugin_events,
        collect_tool_events=collect_tool_events,
        collect_runtime_snapshots=collect_runtime_snapshots,
    )
    _global_db = TelemetryBufferStore(effective_config)
    await _global_db.initialize()

    from .collector import TelemetryCollector

    _global_collector = TelemetryCollector(_global_db)
    return _global_collector


def get_telemetry_collector() -> "TelemetryCollector":
    """获取全局 TelemetryCollector 实例。"""
    global _global_collector
    if _global_collector is not None:
        return _global_collector

    from .collector import TelemetryCollector

    _global_collector = TelemetryCollector(None)
    return _global_collector


async def close_telemetry_db() -> None:
    """关闭通用遥测运行时缓冲。"""
    global _global_db, _global_collector
    if _global_db is not None:
        await _global_db.close()
        _global_db = None
    _global_collector = None