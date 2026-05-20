"""通用遥测配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TelemetryConfig:
    """通用遥测模块的配置。

    Attributes:
        enabled: 是否启用通用遥测。
        max_records: 当前心跳窗口内缓存的最大事件数，0 表示不限制。
        max_age_days: 当前心跳窗口内保留事件的最大天数，0 表示不按时间清理。
        detail_enabled: 是否允许记录高敏感调试明细。
        hash_salt: 脱敏标识计算时使用的可选盐值。
        slow_query_threshold_ms: 慢查询阈值（毫秒）。
        collect_error_events: 是否收集错误摘要事件。
        collect_watchdog_events: 是否收集 WatchDog 事件。
        collect_db_metrics: 是否收集数据库聚合指标和慢查询事件。
        collect_plugin_events: 是否收集插件生命周期事件。
        collect_tool_events: 是否收集工具调用摘要事件。
        collect_runtime_snapshots: 是否记录运行时快照。
    """

    enabled: bool = False
    max_records: int = 100_000
    max_age_days: int = 30
    detail_enabled: bool = False
    hash_salt: str = ""
    slow_query_threshold_ms: float = 500.0
    collect_error_events: bool = True
    collect_watchdog_events: bool = True
    collect_db_metrics: bool = True
    collect_plugin_events: bool = True
    collect_tool_events: bool = True
    collect_runtime_snapshots: bool = True