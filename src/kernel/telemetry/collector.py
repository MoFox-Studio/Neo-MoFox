"""通用遥测收集器。"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TelemetryEventRecord:
    """单条遥测事件记录。"""

    domain: str
    event_name: str
    severity: str = "info"
    summary: str = ""
    entity_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] | None = None
    timestamp: float = field(default_factory=time.time)

    def to_row(self, *, detail_enabled: bool) -> dict[str, Any]:
        """将事件记录转换为数据库行。"""
        return {
            "timestamp": self.timestamp,
            "domain": self.domain,
            "event_name": self.event_name,
            "severity": self.severity,
            "summary": self.summary,
            "entity_id": self.entity_id,
            "attributes_json": json.dumps(self.attributes, ensure_ascii=False),
            "detail_json": (
                json.dumps(self.detail, ensure_ascii=False)
                if detail_enabled and self.detail is not None
                else None
            ),
        }


def anonymize_identifier(raw_value: str | None, *, salt: str = "") -> str | None:
    """将稳定标识转换为不可逆哈希。"""
    if not raw_value:
        return None

    digest = hashlib.sha256(f"{salt}:{raw_value}".encode("utf-8")).hexdigest()
    return digest[:24]


class TelemetryCollector:
    """通用遥测收集器。

    当缓冲未初始化时，所有操作均退化为空操作。
    """

    def __init__(self, database: Any) -> None:
        self._db = database

    @property
    def enabled(self) -> bool:
        """返回是否已启用遥测。"""
        return self._db is not None and self._db.enabled

    @property
    def hash_salt(self) -> str:
        """返回脱敏标识计算使用的盐值。"""
        if not self.enabled:
            return ""
        return self._db.hash_salt

    @property
    def slow_query_threshold_ms(self) -> float:
        """返回慢查询阈值。"""
        if not self.enabled:
            return 0.0
        return self._db.slow_query_threshold_ms

    def is_domain_enabled(self, domain: str) -> bool:
        """判断指定域是否允许记录。"""
        if not self.enabled:
            return False

        normalized = domain.strip().lower()
        if normalized == "error":
            return self._db.collect_error_events
        if normalized == "watchdog":
            return self._db.collect_watchdog_events
        if normalized == "runtime":
            return self._db.collect_runtime_snapshots
        if normalized == "db":
            return self._db.collect_db_metrics
        if normalized == "plugin":
            return self._db.collect_plugin_events
        if normalized == "tool":
            return self._db.collect_tool_events
        return True

    async def record(self, record: TelemetryEventRecord) -> int:
        """写入一条遥测事件。"""
        if not self.enabled:
            return 0

        return await self._db.append_row(self._build_row(record))

    def record_sync(self, record: TelemetryEventRecord) -> int:
        """在当前线程同步写入一条遥测事件。"""
        if not self.enabled:
            return 0

        return self._db.append_row_sync(self._build_row(record))

    async def get_summary(self) -> dict[str, Any]:
        """获取遥测整体摘要。"""
        if not self.enabled:
            return self._empty_summary()

        rows = await self._db.get_rows()
        return self._summarize_rows(rows, enabled=True)

    async def get_domain_summary(self) -> list[dict[str, Any]]:
        """获取按域聚合的摘要。"""
        if not self.enabled:
            return []

        rows = await self._db.get_rows()
        return self._summarize_domains(rows)

    async def get_recent(
        self,
        *,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """获取近期遥测事件。"""
        if not self.enabled:
            return []

        return await self._db.get_rows(domain=domain, limit=limit)

    async def consume_window(self, *, limit: int = 100) -> dict[str, Any]:
        """消费并清空当前心跳窗口内的遥测事件。"""
        if not self.enabled:
            return {
                "summary": self._empty_summary(),
                "domains": [],
                "recent": [],
            }

        rows = await self._db.drain_rows()
        recent = self._slice_recent(rows, limit=limit)
        return {
            "summary": self._summarize_rows(rows, enabled=True),
            "domains": self._summarize_domains(rows),
            "recent": recent,
        }

    async def clear(self) -> None:
        """清除所有遥测事件。"""
        if not self.enabled:
            return

        await self._db.clear()

    def _build_row(
        self,
        record: TelemetryEventRecord,
    ) -> dict[str, Any]:
        """构造缓冲行。"""
        return record.to_row(detail_enabled=self._db.detail_enabled)

    def _summarize_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        """对给定事件列表进行整体聚合。"""
        return {
            "enabled": enabled,
            "total_events": len(rows),
            "error_events": sum(1 for row in rows if row.get("severity") == "error"),
            "warning_events": sum(1 for row in rows if row.get("severity") == "warning"),
            "detail_events": sum(1 for row in rows if row.get("detail_json") is not None),
            "domains": len({str(row.get("domain", "")) for row in rows if row.get("domain")}),
        }

    def _summarize_domains(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按域对给定事件列表聚合。"""
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            domain = str(row.get("domain", ""))
            if not domain:
                continue
            bucket = buckets.setdefault(
                domain,
                {
                    "domain": domain,
                    "total_events": 0,
                    "error_events": 0,
                    "warning_events": 0,
                    "last_event_at": 0.0,
                },
            )
            bucket["total_events"] += 1
            if row.get("severity") == "error":
                bucket["error_events"] += 1
            if row.get("severity") == "warning":
                bucket["warning_events"] += 1
            bucket["last_event_at"] = max(
                float(bucket["last_event_at"]),
                float(row.get("timestamp") or 0.0),
            )

        return sorted(
            buckets.values(),
            key=lambda item: (-int(item["total_events"]), str(item["domain"])),
        )

    def _slice_recent(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """按时间倒序截取近期事件。"""
        ordered = sorted(
            rows,
            key=lambda row: (
                float(row.get("timestamp") or 0.0),
                int(row.get("id") or 0),
            ),
            reverse=True,
        )
        return [dict(row) for row in ordered[:limit]]

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "enabled": False,
            "total_events": 0,
            "error_events": 0,
            "warning_events": 0,
            "detail_events": 0,
            "domains": 0,
        }