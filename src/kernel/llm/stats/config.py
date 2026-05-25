"""LLM 统计配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LLMStatsConfig:
    """LLM 统计模块的配置。

    Attributes:
        db_path: SQLite 数据库文件路径，默认 "data/llm_stats/llm_stats.db"。
        enabled: 是否启用统计收集，默认 True。
        max_records: 数据库中保留的最大记录数，0 表示不限制。默认 100_000。
        window_hours: 聚合查询统计窗口（小时），默认 5.0。
    """

    db_path: str = "data/llm_stats/llm_stats.db"
    enabled: bool = True
    max_records: int = 100_000
    window_hours: float = 5.0
