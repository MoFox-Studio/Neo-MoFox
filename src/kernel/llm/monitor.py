"""监控和日志系统。

提供请求指标收集、统计分析功能。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RequestMetrics:
    """单次请求的指标数据。"""

    model_name: str
    request_name: str
    latency: float  # 延迟（秒）
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: float | None = None
    success: bool = True
    error: str | None = None
    error_type: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    stream: bool = False
    retry_count: int = 0
    model_index: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelStats:
    """模型的统计数据。"""

    model_name: str
    total_requests: int = 0
    success_count: int = 0
    error_count: int = 0
    total_latency: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0
    error_types: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.success_count / self.total_requests

    @property
    def avg_latency(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_latency / self.total_requests

    @property
    def avg_cost(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_cost / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": self.success_rate,
            "total_latency": self.total_latency,
            "avg_latency": self.avg_latency,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost": self.total_cost,
            "avg_cost": self.avg_cost,
            "error_types": dict(self.error_types),
        }


class MetricsCollector:
    """指标收集器。

    线程安全，用于收集和存储 LLM 请求的指标。
    """

    def __init__(self, *, max_history: int = 10000) -> None:
        self._lock = threading.Lock()
        self._history: list[RequestMetrics] = []
        self._max_history = max_history
        self._stats: dict[str, ModelStats] = {}

    def record_request(self, metrics: RequestMetrics) -> None:
        """记录一次请求。"""
        with self._lock:
            self._history.append(metrics)
            if len(self._history) > self._max_history:
                self._history.pop(0)

            # 更新统计
            model_name = metrics.model_name
            if model_name not in self._stats:
                self._stats[model_name] = ModelStats(model_name=model_name)

            stats = self._stats[model_name]
            stats.total_requests += 1
            if metrics.success:
                stats.success_count += 1
            else:
                stats.error_count += 1
                if metrics.error_type:
                    stats.error_types[metrics.error_type] += 1

            stats.total_latency += metrics.latency
            if metrics.tokens_in:
                stats.total_tokens_in += metrics.tokens_in
            if metrics.tokens_out:
                stats.total_tokens_out += metrics.tokens_out
            if metrics.cost:
                stats.total_cost += metrics.cost

    def get_stats(self, model_name: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        """获取统计数据。

        Args:
            model_name: 指定模型名称，若为 None 则返回所有模型的统计。

        Returns:
            单个模型的统计字典或所有模型的统计列表。
        """
        with self._lock:
            if model_name is not None:
                stats = self._stats.get(model_name)
                if stats is None:
                    return {
                        "model_name": model_name,
                        "total_requests": 0,
                        "success_count": 0,
                        "error_count": 0,
                        "success_rate": 0.0,
                    }
                return stats.to_dict()

            return [stats.to_dict() for stats in self._stats.values()]

    def get_recent_history(self, limit: int = 100) -> list[RequestMetrics]:
        """获取最近的请求历史。"""
        with self._lock:
            return self._history[-limit:]

    def clear(self) -> None:
        """清空所有统计数据。"""
        with self._lock:
            self._history.clear()
            self._stats.clear()


class RequestTimer:
    """请求计时器（上下文管理器）。"""

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self.end_time: float = 0.0

    def __enter__(self) -> RequestTimer:
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.end_time = time.perf_counter()

    @property
    def elapsed(self) -> float:
        """已用时间（秒）。"""
        if self.end_time > 0:
            return self.end_time - self.start_time
        return time.perf_counter() - self.start_time


# 全局单例
_global_collector: MetricsCollector | None = None


def get_global_collector() -> MetricsCollector:
    """获取全局指标收集器。"""
    global _global_collector
    if _global_collector is None:
        _global_collector = MetricsCollector()
    return _global_collector
