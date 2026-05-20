"""WatchDog telemetry 测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.kernel.concurrency.watchdog import WatchDog
from src.kernel.telemetry import (
    TelemetryConfig,
    close_telemetry_db,
    get_telemetry_collector,
    init_telemetry,
)


@pytest.fixture(autouse=True)
async def _setup_telemetry(tmp_path) -> AsyncGenerator[None, None]:
    """初始化 telemetry。"""
    await close_telemetry_db()
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_watchdog_events=True,
        )
    )
    yield
    await close_telemetry_db()


def _decode_attributes(row: dict[str, object]) -> dict[str, object]:
    """解析 attributes_json。"""
    raw = row.get("attributes_json")
    if not isinstance(raw, str) or not raw:
        return {}
    return json.loads(raw)


@pytest.mark.asyncio
async def test_register_and_unregister_stream_record_events() -> None:
    """注册和注销流应记录 watchdog 事件。"""
    watchdog = WatchDog()

    watchdog.register_stream(
        stream_id="stream_1",
        tick_interval=0.5,
        warning_threshold=1.0,
        restart_threshold=2.0,
    )
    watchdog.unregister_stream("stream_1")

    rows = await get_telemetry_collector().get_recent(domain="watchdog", limit=10)
    event_names = [row["event_name"] for row in rows]
    assert "stream_registered" in event_names
    assert "stream_unregistered" in event_names
    registered_row = next(row for row in rows if row["event_name"] == "stream_registered")
    attributes = _decode_attributes(registered_row)
    assert attributes["tick_interval"] == 0.5
    assert attributes["warning_threshold"] == 1.0


@pytest.mark.asyncio
async def test_stream_warning_restart_and_recovery_record_events() -> None:
    """流卡顿、重启和恢复应写入 watchdog 事件。"""
    watchdog = WatchDog()
    restart_calls: list[bool] = []

    heartbeat = watchdog.register_stream(
        stream_id="stream_2",
        warning_threshold=1.0,
        restart_threshold=2.0,
        restart_callback=lambda: restart_calls.append(True),
        restart_cooldown=10.0,
    )
    heartbeat.last_tick = datetime.now() - timedelta(seconds=5.0)

    watchdog._check_streams()
    watchdog.feed_dog("stream_2")

    assert restart_calls == [True]
    rows = await get_telemetry_collector().get_recent(domain="watchdog", limit=20)
    event_names = [row["event_name"] for row in rows]
    assert "stream_warning" in event_names
    assert "stream_restart_requested" in event_names
    assert "stream_recovered" in event_names


@pytest.mark.asyncio
async def test_task_timeout_cancel_records_event() -> None:
    """任务超时取消应记录 watchdog 事件。"""
    watchdog = WatchDog()
    cancelled: list[bool] = []

    watchdog._task_manager = SimpleNamespace(
        cleanup_tasks=lambda: 0,
        get_active_tasks=lambda: [
            SimpleNamespace(
                daemon=False,
                timeout=1.0,
                created_at=datetime.now() - timedelta(seconds=5.0),
                name="slow_task",
                task_id="task_12345678",
                cancel=lambda: cancelled.append(True) or True,
            )
        ],
    )

    watchdog._check_tasks()

    assert cancelled == [True]
    rows = await get_telemetry_collector().get_recent(domain="watchdog", limit=10)
    timeout_rows = [row for row in rows if row["event_name"] == "task_timeout_cancelled"]
    assert timeout_rows
    attributes = _decode_attributes(timeout_rows[0])
    assert attributes["task_name"] == "slow_task"
    assert attributes["timeout"] == 1.0