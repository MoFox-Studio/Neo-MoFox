"""telemetry 模块测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from src.kernel.telemetry import (
    TelemetryConfig,
    TelemetryEventRecord,
    anonymize_identifier,
    close_telemetry_db,
    get_telemetry_collector,
    init_telemetry,
)


@pytest.mark.asyncio
async def test_init_telemetry_disabled_is_noop(tmp_path) -> None:
    """禁用时 collector 应退化为空操作。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=False,
        ),
    )
    collector = get_telemetry_collector()

    assert collector.enabled is False
    assert await collector.record(TelemetryEventRecord(domain="runtime", event_name="boot")) == 0
    assert await collector.get_summary() == {
        "enabled": False,
        "total_events": 0,
        "error_events": 0,
        "warning_events": 0,
        "detail_events": 0,
        "domains": 0,
    }


@pytest.mark.asyncio
async def test_telemetry_record_and_query(tmp_path) -> None:
    """启用时应能写入并查询聚合结果。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            max_records=10,
            max_age_days=7,
            detail_enabled=False,
        ),
    )
    collector = get_telemetry_collector()

    await collector.record(
        TelemetryEventRecord(
            domain="runtime",
            event_name="initialized",
            severity="info",
            summary="runtime ready",
            attributes={"phase": "boot"},
        )
    )
    await collector.record(
        TelemetryEventRecord(
            domain="runtime",
            event_name="warning",
            severity="warning",
            summary="runtime warning",
        )
    )
    await collector.record(
        TelemetryEventRecord(
            domain="error",
            event_name="exception",
            severity="error",
            summary="boom",
            detail={"traceback": "should be dropped"},
        )
    )

    summary = await collector.get_summary()
    domains = await collector.get_domain_summary()
    recent = await collector.get_recent(limit=5)

    assert summary["enabled"] is True
    assert summary["total_events"] == 3
    assert summary["warning_events"] == 1
    assert summary["error_events"] == 1
    assert summary["detail_events"] == 0
    assert {item["domain"] for item in domains} == {"runtime", "error"}
    assert len(recent) == 3
    assert recent[0]["detail_json"] is None


@pytest.mark.asyncio
async def test_telemetry_cleanup_max_records(tmp_path) -> None:
    """应按最大记录数清理旧事件。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            max_records=2,
            max_age_days=0,
        ),
    )
    collector = get_telemetry_collector()

    await collector.record(TelemetryEventRecord(domain="runtime", event_name="one"))
    await collector.record(TelemetryEventRecord(domain="runtime", event_name="two"))
    await collector.record(TelemetryEventRecord(domain="runtime", event_name="three"))

    recent = await collector.get_recent(limit=10)
    assert len(recent) == 2
    assert {row["event_name"] for row in recent} == {"two", "three"}


@pytest.mark.asyncio
async def test_telemetry_consume_window_clears_previous_events(tmp_path) -> None:
    """消费窗口后，新窗口不应重复携带上一轮事件。"""

    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
        ),
    )
    collector = get_telemetry_collector()

    await collector.record(
        TelemetryEventRecord(domain="runtime", event_name="first", summary="first")
    )
    first_window = await collector.consume_window(limit=10)

    await collector.record(
        TelemetryEventRecord(domain="runtime", event_name="second", summary="second")
    )
    second_window = await collector.consume_window(limit=10)

    assert first_window["summary"]["total_events"] == 1
    assert [row["event_name"] for row in first_window["recent"]] == ["first"]
    assert second_window["summary"]["total_events"] == 1
    assert [row["event_name"] for row in second_window["recent"]] == ["second"]
    assert await collector.get_recent(limit=10) == []


def test_anonymize_identifier_is_stable() -> None:
    """相同输入和盐值应生成稳定哈希。"""
    first = anonymize_identifier("qq:123", salt="alpha")
    second = anonymize_identifier("qq:123", salt="alpha")
    third = anonymize_identifier("qq:123", salt="beta")

    assert first == second
    assert third != first
    assert first is not None and len(first) == 24


@pytest.mark.asyncio
async def test_telemetry_domain_switches(tmp_path) -> None:
    """collector 应尊重域级开关。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_error_events=False,
            collect_runtime_snapshots=True,
        ),
    )
    collector = get_telemetry_collector()

    assert collector.is_domain_enabled("runtime") is True
    assert collector.is_domain_enabled("error") is False


@pytest.fixture(autouse=True)
async def _cleanup_global_state() -> AsyncGenerator[None, None]:
    """确保全局 telemetry 状态在测试后被清理。"""
    yield
    await close_telemetry_db()