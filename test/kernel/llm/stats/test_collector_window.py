"""LLM stats 窗口过滤测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.kernel.llm.stats import (
    LLMRequestRecord,
    LLMStatsConfig,
    close_llm_stats_db,
    get_llm_stats_collector,
    init_llm_stats,
)


@pytest.fixture(autouse=True)
async def _setup_stats(tmp_path: Path) -> None:
    """初始化独立 LLM stats 数据库。"""
    await close_llm_stats_db()
    await init_llm_stats(
        config=LLMStatsConfig(
            db_path=str(tmp_path / "llm_stats.db"),
            enabled=True,
            window_hours=5.0,
        )
    )
    yield
    await close_llm_stats_db()


@pytest.mark.asyncio
async def test_summary_uses_five_hour_window() -> None:
    """聚合摘要应只统计最近 5 小时数据。"""
    collector = get_llm_stats_collector()
    now = time.time()
    await collector.record(
        LLMRequestRecord(
            model_name="recent-model",
            model_identifier="recent-model",
            request_name="recent",
            total_tokens=100,
            prompt_tokens=40,
            completion_tokens=60,
            timestamp=now - 60,
        )
    )
    await collector.record(
        LLMRequestRecord(
            model_name="old-model",
            model_identifier="old-model",
            request_name="old",
            total_tokens=999,
            prompt_tokens=400,
            completion_tokens=599,
            timestamp=now - 6 * 3600,
        )
    )

    summary = await collector.get_summary()

    assert summary["window_hours"] == 5.0
    assert summary["total_requests"] == 1
    assert summary["total_tokens"] == 100


@pytest.mark.asyncio
async def test_grouped_queries_and_recent_respect_window() -> None:
    """分组查询和 recent 明细也应遵循时间窗口。"""
    collector = get_llm_stats_collector()
    now = time.time()
    await collector.record(
        LLMRequestRecord(
            model_name="kept-model",
            model_identifier="kept-model",
            request_name="kept-request",
            stream_id="stream-kept",
            total_tokens=20,
            cache_hit_tokens=10,
            cache_miss_tokens=10,
            timestamp=now - 120,
        )
    )
    await collector.record(
        LLMRequestRecord(
            model_name="dropped-model",
            model_identifier="dropped-model",
            request_name="dropped-request",
            stream_id="stream-dropped",
            total_tokens=50,
            cache_hit_tokens=50,
            cache_miss_tokens=0,
            timestamp=now - 6 * 3600,
        )
    )

    by_model = await collector.get_by_model()
    by_request_name = await collector.get_by_request_name()
    by_stream = await collector.get_by_stream()
    cache_hit_rate = await collector.get_cache_hit_rate()
    recent = await collector.get_recent(limit=10)

    assert [item["model_name"] for item in by_model] == ["kept-model"]
    assert [item["request_name"] for item in by_request_name] == ["kept-request"]
    assert [item["stream_id"] for item in by_stream] == ["stream-kept"]
    assert cache_hit_rate["total_cache_hit"] == 10
    assert cache_hit_rate["total_cache_miss"] == 10
    assert len(recent) == 1
    assert recent[0]["model_name"] == "kept-model"


@pytest.mark.asyncio
async def test_request_name_window_summary_orders_by_token_usage() -> None:
    """心跳窗口 request_name 聚合应按 token 消耗返回 Top N。"""
    collector = get_llm_stats_collector()
    now = time.time()
    records = [
        LLMRequestRecord(
            model_name="model-a",
            model_identifier="provider/model-a",
            api_provider="https://api.example.com/v1",
            request_name="small",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cache_hit_tokens=5,
            cache_miss_tokens=5,
            latency=0.2,
            timestamp=now - 4,
        ),
        LLMRequestRecord(
            model_name="model-b",
            model_identifier="provider/model-b",
            api_provider="https://api.example.com/v1",
            request_name="large",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cache_hit_tokens=30,
            cache_miss_tokens=70,
            latency=0.5,
            timestamp=now - 3,
        ),
        LLMRequestRecord(
            model_name="model-b",
            model_identifier="provider/model-b",
            api_provider="https://api.example.com/v1",
            request_name="large",
            prompt_tokens=40,
            completion_tokens=60,
            total_tokens=100,
            cache_hit_tokens=20,
            cache_miss_tokens=80,
            latency=1.5,
            success=False,
            timestamp=now - 1,
        ),
        LLMRequestRecord(
            model_name="old",
            request_name="old",
            total_tokens=999,
            timestamp=now - 100,
        ),
    ]
    for record in records:
        await collector.record(record)

    summary = await collector.get_request_name_window_summary(
        start_ts=now - 10,
        end_ts=now,
        limit=1,
    )

    assert len(summary) == 1
    item = summary[0]
    assert item["request_name"] == "large"
    assert item["request_count"] == 2
    assert item["total_tokens"] == 250
    assert item["average_prompt_tokens_per_request"] == 70
    assert item["average_completion_tokens_per_request"] == 55
    assert item["average_latency"] == 1.0
    assert item["cache_hit_rate"] == 50 / 200
    assert item["model_identifier"] == "provider/model-b"
    assert item["base_urls"] == ["https://api.example.com/v1"]
    assert item["success_rate"] == 0.5
