"""LLM 统计收集器。

提供请求记录的写入和查询接口。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.kernel.logger import get_logger

logger = get_logger("kernel.llm.stats.collector", display="LLM 统计收集器")


@dataclass(slots=True)
class LLMRequestRecord:
    """单次 LLM 请求的完整统计记录。"""

    model_name: str = ""
    model_identifier: str = ""
    api_provider: str = ""
    request_name: str = ""
    stream_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    latency: float = 0.0
    success: bool = True
    error_type: str | None = None
    stream: bool = False
    retry_count: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "model_name": self.model_name,
            "model_identifier": self.model_identifier,
            "api_provider": self.api_provider,
            "request_name": self.request_name,
            "stream_id": self.stream_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost": self.cost,
            "latency": self.latency,
            "success": self.success,
            "error_type": self.error_type,
            "stream": self.stream,
            "retry_count": self.retry_count,
        }


class LLMStatsCollector:
    """LLM 请求统计收集器。

    提供原子化的记录写入与多维度的查询 API。
    当 database 为 None 时，所有操作均为空操作（用于未初始化场景）。
    """

    def __init__(self, database: Any) -> None:
        self._db = database

    @property
    def enabled(self) -> bool:
        return self._db is not None and self._db.enabled

    @property
    def window_hours(self) -> float:
        """返回统计查询时间窗口（小时）。"""
        if self._db is None:
            return 5.0
        return max(float(getattr(self._db._config, "window_hours", 5.0)), 0.0)

    def _window_cutoff(self) -> float | None:
        """返回窗口起始时间戳；0 或负数表示不限制。"""
        if self.window_hours <= 0:
            return None
        return time.time() - self.window_hours * 3600

    async def get_request_name_window_summary(
        self,
        *,
        start_ts: float,
        end_ts: float,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return Top N request_name aggregates within an explicit time window."""

        if not self.enabled:
            return []

        rows = await self._db.execute_read(
            """
            SELECT
                timestamp,
                model_name,
                model_identifier,
                api_provider,
                request_name,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cache_hit_tokens,
                cache_miss_tokens,
                cost,
                latency,
                success
            FROM llm_requests
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (float(start_ts), float(end_ts)),
        )
        if not rows:
            return []

        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            request_name = str(row.get("request_name") or "(unnamed)")
            bucket = buckets.setdefault(
                request_name,
                {
                    "request_name": request_name,
                    "request_count": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                    "total_tokens": 0,
                    "total_cache_hit_tokens": 0,
                    "total_cache_miss_tokens": 0,
                    "total_cost": 0.0,
                    "total_latency": 0.0,
                    "first_request_at": None,
                    "last_request_at": None,
                    "model_identifiers": set(),
                    "model_names": set(),
                    "base_urls": set(),
                },
            )
            timestamp = float(row.get("timestamp") or 0.0)
            success = bool(row.get("success"))
            bucket["request_count"] += 1
            bucket["success_count"] += 1 if success else 0
            bucket["error_count"] += 0 if success else 1
            bucket["total_prompt_tokens"] += int(row.get("prompt_tokens") or 0)
            bucket["total_completion_tokens"] += int(row.get("completion_tokens") or 0)
            bucket["total_tokens"] += int(row.get("total_tokens") or 0)
            bucket["total_cache_hit_tokens"] += int(row.get("cache_hit_tokens") or 0)
            bucket["total_cache_miss_tokens"] += int(row.get("cache_miss_tokens") or 0)
            bucket["total_cost"] += float(row.get("cost") or 0.0)
            bucket["total_latency"] += float(row.get("latency") or 0.0)
            bucket["first_request_at"] = (
                timestamp
                if bucket["first_request_at"] is None
                else min(float(bucket["first_request_at"]), timestamp)
            )
            bucket["last_request_at"] = (
                timestamp
                if bucket["last_request_at"] is None
                else max(float(bucket["last_request_at"]), timestamp)
            )
            for field_name, set_name in (
                ("model_identifier", "model_identifiers"),
                ("model_name", "model_names"),
                ("api_provider", "base_urls"),
            ):
                value = row.get(field_name)
                if value:
                    bucket[set_name].add(str(value))

        summaries: list[dict[str, Any]] = []
        for bucket in buckets.values():
            request_count = int(bucket["request_count"])
            cache_total = (
                int(bucket["total_cache_hit_tokens"])
                + int(bucket["total_cache_miss_tokens"])
            )
            first_request_at = float(bucket["first_request_at"] or 0.0)
            last_request_at = float(bucket["last_request_at"] or 0.0)
            avg_interval = (
                (last_request_at - first_request_at) / (request_count - 1)
                if request_count > 1
                else 0.0
            )
            model_identifiers = sorted(bucket["model_identifiers"])
            summaries.append(
                {
                    "request_name": bucket["request_name"],
                    "request_count": request_count,
                    "average_request_interval_seconds": round(avg_interval, 4),
                    "total_prompt_tokens": int(bucket["total_prompt_tokens"]),
                    "total_completion_tokens": int(bucket["total_completion_tokens"]),
                    "total_tokens": int(bucket["total_tokens"]),
                    "average_prompt_tokens_per_request": round(
                        int(bucket["total_prompt_tokens"]) / request_count,
                        4,
                    ),
                    "average_completion_tokens_per_request": round(
                        int(bucket["total_completion_tokens"]) / request_count,
                        4,
                    ),
                    "average_tokens_per_request": round(
                        int(bucket["total_tokens"]) / request_count,
                        4,
                    ),
                    "average_latency": round(
                        float(bucket["total_latency"]) / request_count,
                        4,
                    ),
                    "cache_hit_rate": (
                        int(bucket["total_cache_hit_tokens"]) / cache_total
                        if cache_total > 0
                        else 0.0
                    ),
                    "total_cache_hit_tokens": int(bucket["total_cache_hit_tokens"]),
                    "total_cache_miss_tokens": int(bucket["total_cache_miss_tokens"]),
                    "model_identifier": model_identifiers[0]
                    if len(model_identifiers) == 1
                    else "",
                    "model_identifiers": model_identifiers,
                    "model_names": sorted(bucket["model_names"]),
                    "base_urls": sorted(bucket["base_urls"]),
                    "success_rate": int(bucket["success_count"]) / request_count,
                    "success_count": int(bucket["success_count"]),
                    "error_count": int(bucket["error_count"]),
                    "total_cost": round(float(bucket["total_cost"]), 6),
                    "first_request_at": first_request_at,
                    "last_request_at": last_request_at,
                }
            )

        return sorted(
            summaries,
            key=lambda item: (-int(item["total_tokens"]), str(item["request_name"])),
        )[: max(1, int(limit))]

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def record(self, record: LLMRequestRecord) -> int:
        """记录一次 LLM 请求的统计数据。

        Args:
            record: 请求统计记录。

        Returns:
            插入行的 id，若禁用则返回 0。
        """
        if not self.enabled:
            return 0

        data = record.to_dict()
        sql = """
            INSERT INTO llm_requests (
                timestamp, model_name, model_identifier, api_provider,
                request_name, stream_id,
                prompt_tokens, completion_tokens, total_tokens,
                cache_hit_tokens, cache_miss_tokens, cache_write_tokens,
                cost, latency, success, error_type, stream, retry_count
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
        """
        row_id = await self._db.execute_write(sql, self._dict_to_tuple(data))

        # 定期清理旧数据
        await self._db.vacuum_if_needed()

        return row_id

    # ------------------------------------------------------------------
    # 查询 — 综合摘要
    # ------------------------------------------------------------------

    async def get_summary(self) -> dict[str, Any]:
        """获取整体统计摘要。

        Returns:
            包含总请求数、成功率、总 token、总成本等字段的字典。
        """
        if not self.enabled:
            return self._empty_summary()

        cutoff_ts = self._window_cutoff()
        sql = """
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success THEN 0 ELSE 1 END) as error_count,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit_tokens,
                COALESCE(SUM(cache_miss_tokens), 0) as total_cache_miss_tokens,
                COALESCE(SUM(cost), 0.0) as total_cost,
                COALESCE(AVG(latency), 0.0) as avg_latency
            FROM llm_requests
        """
        if cutoff_ts is not None:
            sql += " WHERE timestamp >= ?"
            rows = await self._db.execute_read(sql, (cutoff_ts,))
        else:
            rows = await self._db.execute_read(sql)
        if not rows:
            return self._empty_summary()
        row = rows[0]
        total = row["total_requests"] or 0
        cache_total = (row["total_cache_hit_tokens"] or 0) + (row["total_cache_miss_tokens"] or 0)
        return {
            "window_hours": self.window_hours,
            "total_requests": total,
            "success_count": row["success_count"] or 0,
            "error_count": row["error_count"] or 0,
            "success_rate": (row["success_count"] / total) if total > 0 else 0.0,
            "total_prompt_tokens": row["total_prompt_tokens"] or 0,
            "total_completion_tokens": row["total_completion_tokens"] or 0,
            "total_tokens": row["total_tokens"] or 0,
            "total_cache_hit_tokens": row["total_cache_hit_tokens"] or 0,
            "total_cache_miss_tokens": row["total_cache_miss_tokens"] or 0,
            "cache_hit_rate": (row["total_cache_hit_tokens"] / cache_total) if cache_total > 0 else 0.0,
            "total_cost": round(row["total_cost"] or 0.0, 6),
            "avg_latency": round(row["avg_latency"] or 0.0, 4),
        }

    # ------------------------------------------------------------------
    # 查询 — 按模型
    # ------------------------------------------------------------------

    async def get_by_model(self) -> list[dict[str, Any]]:
        """获取按模型分组的统计数据。

        Returns:
            每个模型的统计字典列表。
        """
        if not self.enabled:
            return []

        cutoff_ts = self._window_cutoff()
        sql = """
            SELECT
                model_name,
                model_identifier,
                api_provider,
                COUNT(*) as total_requests,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success THEN 0 ELSE 1 END) as error_count,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit_tokens,
                COALESCE(SUM(cache_miss_tokens), 0) as total_cache_miss_tokens,
                COALESCE(SUM(cost), 0.0) as total_cost,
                COALESCE(AVG(latency), 0.0) as avg_latency
            FROM llm_requests
        """
        params: tuple[Any, ...] = ()
        if cutoff_ts is not None:
            sql += " WHERE timestamp >= ?"
            params = (cutoff_ts,)
        sql += """
            GROUP BY model_name, model_identifier, api_provider
            ORDER BY total_requests DESC
        """
        rows = await self._db.execute_read(sql, params)
        return [
            {
                "model_name": r["model_name"],
                "model_identifier": r["model_identifier"],
                "api_provider": r["api_provider"],
                "total_requests": r["total_requests"] or 0,
                "success_count": r["success_count"] or 0,
                "error_count": r["error_count"] or 0,
                "total_prompt_tokens": r["total_prompt_tokens"] or 0,
                "total_completion_tokens": r["total_completion_tokens"] or 0,
                "total_tokens": r["total_tokens"] or 0,
                "total_cache_hit_tokens": r["total_cache_hit_tokens"] or 0,
                "total_cost": round(r["total_cost"] or 0.0, 6),
                "avg_latency": round(r["avg_latency"] or 0.0, 4),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 查询 — 按 request_name
    # ------------------------------------------------------------------

    async def get_by_request_name(self) -> list[dict[str, Any]]:
        """获取按请求名称分组的统计数据。

        Returns:
            每个 request_name 的统计字典列表。
        """
        if not self.enabled:
            return []

        cutoff_ts = self._window_cutoff()
        sql = """
            SELECT
                request_name,
                COUNT(*) as total_requests,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN success THEN 0 ELSE 1 END) as error_count,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit_tokens,
                COALESCE(SUM(cache_miss_tokens), 0) as total_cache_miss_tokens,
                COALESCE(SUM(cost), 0.0) as total_cost,
                COALESCE(AVG(latency), 0.0) as avg_latency
            FROM llm_requests
        """
        params: tuple[Any, ...] = ()
        if cutoff_ts is not None:
            sql += " WHERE timestamp >= ?"
            params = (cutoff_ts,)
        sql += """
            GROUP BY request_name
            ORDER BY total_requests DESC
        """
        rows = await self._db.execute_read(sql, params)
        return [
            {
                "request_name": r["request_name"] or "(未命名)",
                "total_requests": r["total_requests"] or 0,
                "success_count": r["success_count"] or 0,
                "error_count": r["error_count"] or 0,
                "total_prompt_tokens": r["total_prompt_tokens"] or 0,
                "total_completion_tokens": r["total_completion_tokens"] or 0,
                "total_tokens": r["total_tokens"] or 0,
                "total_cache_hit_tokens": r["total_cache_hit_tokens"] or 0,
                "total_cost": round(r["total_cost"] or 0.0, 6),
                "avg_latency": round(r["avg_latency"] or 0.0, 4),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 查询 — 缓存命中率（按 stream / 全局）
    # ------------------------------------------------------------------

    async def get_cache_hit_rate(
        self, stream_id: str | None = None
    ) -> dict[str, Any]:
        """获取缓存命中率统计。

        Args:
            stream_id: 可选，按指定聊天流查询。

        Returns:
            缓存命中相关统计字典。
        """
        if not self.enabled:
            return {"cache_hit_rate": 0.0, "total_cache_hit": 0, "total_cache_miss": 0}

        cutoff_ts = self._window_cutoff()
        if stream_id:
            sql = """
                SELECT
                    COALESCE(SUM(cache_hit_tokens), 0) as hit,
                    COALESCE(SUM(cache_miss_tokens), 0) as miss
                FROM llm_requests
                WHERE stream_id = ?
            """
            params: tuple[Any, ...] = (stream_id,)
            if cutoff_ts is not None:
                sql += " AND timestamp >= ?"
                params = (stream_id, cutoff_ts)
            rows = await self._db.execute_read(sql, params)
        else:
            sql = """
                SELECT
                    COALESCE(SUM(cache_hit_tokens), 0) as hit,
                    COALESCE(SUM(cache_miss_tokens), 0) as miss
                FROM llm_requests
            """
            if cutoff_ts is not None:
                sql += " WHERE timestamp >= ?"
                rows = await self._db.execute_read(sql, (cutoff_ts,))
            else:
                rows = await self._db.execute_read(sql)

        if not rows:
            return {"cache_hit_rate": 0.0, "total_cache_hit": 0, "total_cache_miss": 0}

        hit = rows[0]["hit"] or 0
        miss = rows[0]["miss"] or 0
        total = hit + miss
        return {
            "cache_hit_rate": (hit / total) if total > 0 else 0.0,
            "total_cache_hit": hit,
            "total_cache_miss": miss,
        }

    # ------------------------------------------------------------------
    # 查询 — 按 stream_id 分组
    # ------------------------------------------------------------------

    async def get_by_stream(self) -> list[dict[str, Any]]:
        """获取按聊天流分组的统计数据。

        Returns:
            每个 stream_id 的统计字典列表。
        """
        if not self.enabled:
            return []

        cutoff_ts = self._window_cutoff()
        sql = """
            SELECT
                stream_id,
                COUNT(*) as total_requests,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit,
                COALESCE(SUM(cache_miss_tokens), 0) as total_cache_miss,
                COALESCE(SUM(cost), 0.0) as total_cost
            FROM llm_requests
            WHERE stream_id IS NOT NULL
        """
        params: tuple[Any, ...] = ()
        if cutoff_ts is not None:
            sql += " AND timestamp >= ?"
            params = (cutoff_ts,)
        sql += """
            GROUP BY stream_id
            ORDER BY total_requests DESC
        """
        rows = await self._db.execute_read(sql, params)
        result: list[dict[str, Any]] = []
        for r in rows:
            hit = r["total_cache_hit"] or 0
            miss = r["total_cache_miss"] or 0
            total = hit + miss
            result.append({
                "stream_id": r["stream_id"],
                "total_requests": r["total_requests"] or 0,
                "total_prompt_tokens": r["total_prompt_tokens"] or 0,
                "total_completion_tokens": r["total_completion_tokens"] or 0,
                "total_cache_hit": hit,
                "total_cache_miss": miss,
                "cache_hit_rate": (hit / total) if total > 0 else 0.0,
                "total_cost": round(r["total_cost"] or 0.0, 6),
            })
        return result

    # ------------------------------------------------------------------
    # 查询 — 近期请求明细
    # ------------------------------------------------------------------

    async def get_recent(
        self, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """获取最近的请求记录明细。

        Args:
            limit: 返回数量上限。
            offset: 偏移量。

        Returns:
            请求记录字典列表。
        """
        if not self.enabled:
            return []

        cutoff_ts = self._window_cutoff()
        sql = """
            SELECT * FROM llm_requests
        """
        if cutoff_ts is not None:
            sql += " WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            return await self._db.execute_read(sql, (cutoff_ts, limit, offset))
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        return await self._db.execute_read(sql, (limit, offset))

    async def list_requests_by_time_range(
        self,
        start_ts: float,
        end_ts: float,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """获取指定 Unix 时间戳范围内的请求记录明细。

        Args:
            start_ts: 起始 Unix 时间戳，单位为秒。
            end_ts: 结束 Unix 时间戳，单位为秒。
            limit: 返回数量上限。
            offset: 偏移量。

        Returns:
            指定时间范围内按时间倒序排列的请求记录字典列表。
        """
        if not self.enabled:
            return []

        sql = """
            SELECT * FROM llm_requests
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        return await self._db.execute_read(sql, (start_ts, end_ts, limit, offset))

    # ------------------------------------------------------------------
    # 查询 — 时间范围统计
    # ------------------------------------------------------------------

    async def get_by_time_range(
        self,
        start_ts: float,
        end_ts: float,
    ) -> dict[str, Any]:
        """获取指定时间范围内的统计摘要。

        Args:
            start_ts: 起始时间戳。
            end_ts: 结束时间戳。

        Returns:
            统计摘要字典。
        """
        if not self.enabled:
            return self._empty_summary()

        sql = """
            SELECT
                COUNT(*) as total_requests,
                COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit_tokens,
                COALESCE(SUM(cache_miss_tokens), 0) as total_cache_miss_tokens,
                COALESCE(SUM(cost), 0.0) as total_cost
            FROM llm_requests
            WHERE timestamp >= ? AND timestamp <= ?
        """
        rows = await self._db.execute_read(sql, (start_ts, end_ts))
        if not rows:
            return self._empty_summary()
        row = rows[0]
        total = row["total_requests"] or 0
        cache_total = (row["total_cache_hit_tokens"] or 0) + (row["total_cache_miss_tokens"] or 0)
        return {
            "total_requests": total,
            "total_prompt_tokens": row["total_prompt_tokens"] or 0,
            "total_completion_tokens": row["total_completion_tokens"] or 0,
            "total_tokens": row["total_tokens"] or 0,
            "total_cache_hit_tokens": row["total_cache_hit_tokens"] or 0,
            "total_cache_miss_tokens": row["total_cache_miss_tokens"] or 0,
            "cache_hit_rate": (row["total_cache_hit_tokens"] / cache_total) if cache_total > 0 else 0.0,
            "total_cost": round(row["total_cost"] or 0.0, 6),
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _dict_to_tuple(data: dict[str, Any]) -> tuple[Any, ...]:
        """将记录字典转换为 INSERT 参数元组。"""
        return (
            data["timestamp"],
            data["model_name"],
            data["model_identifier"],
            data["api_provider"],
            data["request_name"],
            data["stream_id"],
            data["prompt_tokens"],
            data["completion_tokens"],
            data["total_tokens"],
            data["cache_hit_tokens"],
            data["cache_miss_tokens"],
            data["cache_write_tokens"],
            data["cost"],
            data["latency"],
            int(data["success"]),
            data["error_type"],
            int(data["stream"]),
            data["retry_count"],
        )

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "window_hours": 5.0,
            "total_requests": 0,
            "success_count": 0,
            "error_count": 0,
            "success_rate": 0.0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "total_cache_hit_tokens": 0,
            "total_cache_miss_tokens": 0,
            "cache_hit_rate": 0.0,
            "total_cost": 0.0,
            "avg_latency": 0.0,
        }
