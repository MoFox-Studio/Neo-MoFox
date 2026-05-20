"""测试云端遥测客户端队列与发送。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.app.cloud_telemetry import CloudTelemetryClient, CloudTelemetryClientConfig, CloudTelemetryPendingQueue
from src.kernel.llm.stats import LLMRequestRecord, close_llm_stats_db, init_llm_stats
from src.kernel.telemetry import TelemetryConfig, TelemetryEventRecord, close_telemetry_db, get_telemetry_collector, init_telemetry
from src.kernel.telemetry.cloud import (
    CONSENT_GRANTED,
    CloudTelemetryIdentityStore,
)


def _build_mock_transport(accepted_sequences: list[int]) -> httpx.MockTransport:
    """构建模拟云端服务响应的传输层。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        if request.url.path.endswith("/register/challenge"):
            return httpx.Response(
                200,
                json={
                    "challenge_id": "challenge-1",
                    "challenge_token": "token-1",
                    "issued_at": 1.0,
                    "expires_at": 301.0,
                    "server_time": 1.0,
                    "mode": "skeleton",
                },
            )
        if request.url.path.endswith("/register"):
            return httpx.Response(
                200,
                json={
                    "client_instance_id": payload["client_instance_id"],
                    "registration_status": "registered",
                    "install_credential": "install-1",
                    "credential_issued_at": 2.0,
                    "credential_expires_at": 86402.0,
                    "next_heartbeat_interval_seconds": 1,
                    "server_time": 2.0,
                    "mode": "skeleton",
                },
            )
        if request.url.path.endswith("/heartbeats/batch"):
            windows = payload.get("windows", [])
            accepted_sequences.extend(window["window_sequence"] for window in windows)
            return httpx.Response(
                200,
                json={
                    "request_id": payload["request_id"],
                    "accepted_window_count": len(windows),
                    "duplicate_window_count": 0,
                    "rejected_window_count": 0,
                    "window_results": [
                        {
                            "window_sequence": window["window_sequence"],
                            "status": "accepted",
                            "reason": None,
                        }
                        for window in windows
                    ],
                    "next_heartbeat_interval_seconds": 1,
                    "instance_status": "active",
                    "server_time": 4.0,
                    "mode": "skeleton",
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_pending_queue_trims_oldest_windows_by_capacity(tmp_path: Path) -> None:
    """待发送窗口队列应按容量限制丢弃最旧窗口。"""

    queue = CloudTelemetryPendingQueue(
        max_bytes=120,
        max_windows=2,
    )

    await queue.enqueue_window(
        window_started_at=0.0,
        window_ended_at=1.0,
        summary={"a": "x" * 80},
        diagnostic_events=[],
    )
    await queue.enqueue_window(
        window_started_at=1.0,
        window_ended_at=2.0,
        summary={"b": "y" * 80},
        diagnostic_events=[],
    )
    await queue.enqueue_window(
        window_started_at=2.0,
        window_ended_at=3.0,
        summary={"c": "z" * 10},
        diagnostic_events=[],
    )

    windows = await queue.get_windows()
    assert len(windows) <= 2
    assert windows[-1].window_sequence == 3


@pytest.mark.asyncio
async def test_pending_queue_aligns_window_sequence_floor() -> None:
    """Unsent windows should be renumbered after server-side sequence sync."""

    queue = CloudTelemetryPendingQueue(
        max_bytes=4096,
        max_windows=16,
    )
    await queue.enqueue_window(
        window_started_at=0.0,
        window_ended_at=1.0,
        summary={"a": 1},
        diagnostic_events=[],
    )
    await queue.enqueue_window(
        window_started_at=1.0,
        window_ended_at=2.0,
        summary={"b": 2},
        diagnostic_events=[],
    )

    await queue.align_next_window_sequence(31)

    windows = await queue.get_windows()
    assert [window.window_sequence for window in windows] == [31, 32]
    status = await queue.get_status_summary()
    assert status["next_window_sequence"] == 33


@pytest.mark.asyncio
async def test_cloud_telemetry_client_registers_and_flushes_pending_windows(tmp_path: Path) -> None:
    """客户端应完成注册、上线心跳并清空已确认窗口。"""

    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
        )
    )
    await get_telemetry_collector().record(
        TelemetryEventRecord(
            domain="runtime",
            event_name="ready",
            severity="warning",
            summary="runtime warning",
        )
    )
    llm_collector = await init_llm_stats(
        db_path=str(tmp_path / "llm.db"),
        enabled=True,
        window_hours=5.0,
    )
    await llm_collector.record(
        LLMRequestRecord(
            model_name="test-model",
            request_name="reply",
            total_tokens=128,
            prompt_tokens=64,
            completion_tokens=64,
        )
    )

    accepted_sequences: list[int] = []
    try:
        identity_store = CloudTelemetryIdentityStore(storage_dir=str(tmp_path / "state"))
        await identity_store.set_consent(CONSENT_GRANTED, allow_ip_retention=False)
        pending_queue = CloudTelemetryPendingQueue(
            max_bytes=4096,
            max_windows=16,
        )
        client = CloudTelemetryClient(
            CloudTelemetryClientConfig(
                enabled=True,
                ingest_base_url="http://testserver/_cloud_telemetry",
                pending_queue_max_bytes=4096,
                pending_queue_max_windows=16,
                default_heartbeat_interval_seconds=0.05,
                send_timeout_seconds=5.0,
                trust_env=False,
            ),
            identity_store,
            pending_queue,
            transport=_build_mock_transport(accepted_sequences),
        )

        window = await client.capture_snapshot_window()
        assert window is not None

        result = await client.run_once(collect_window=False)
        assert result["ok"] is True
        assert result["reason"] == "sent"

        identity_state = await identity_store.load()
        assert identity_state is not None
        assert identity_state.install_credential is not None

        queue_status = await pending_queue.get_status_summary()
        assert queue_status["pending_window_count"] == 0
        assert queue_status["last_send_status"] == "success"
        assert accepted_sequences == [window.window_sequence]
    finally:
        await close_llm_stats_db()
        await close_telemetry_db()


@pytest.mark.asyncio
async def test_cloud_telemetry_client_background_loop_dispatches_windows(tmp_path: Path) -> None:
    """后台发送循环应能周期性发送并清空窗口。"""

    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
        )
    )
    await get_telemetry_collector().record(
        TelemetryEventRecord(domain="runtime", event_name="ready", summary="ready")
    )
    await init_llm_stats(
        db_path=str(tmp_path / "llm-loop.db"),
        enabled=True,
        window_hours=5.0,
    )
    accepted_sequences: list[int] = []
    try:
        identity_store = CloudTelemetryIdentityStore(storage_dir=str(tmp_path / "state-loop"))
        await identity_store.set_consent(CONSENT_GRANTED)
        pending_queue = CloudTelemetryPendingQueue(
            max_bytes=4096,
            max_windows=16,
        )
        client = CloudTelemetryClient(
            CloudTelemetryClientConfig(
                enabled=True,
                ingest_base_url="http://testserver/_cloud_telemetry",
                pending_queue_max_bytes=4096,
                pending_queue_max_windows=16,
                default_heartbeat_interval_seconds=0.05,
                send_timeout_seconds=5.0,
                trust_env=False,
            ),
            identity_store,
            pending_queue,
            transport=_build_mock_transport(accepted_sequences),
        )

        await client.capture_snapshot_window()
        await client.start()
        try:
            for _ in range(20):
                status = await pending_queue.get_status_summary()
                if status["pending_window_count"] == 0 and status["last_send_status"] == "success":
                    break
                await asyncio.sleep(0.05)
        finally:
            await client.stop()

        status = await pending_queue.get_status_summary()
        assert status["pending_window_count"] == 0
        assert status["last_send_status"] == "success"
        assert accepted_sequences
    finally:
        await close_llm_stats_db()
        await close_telemetry_db()


@pytest.mark.asyncio
async def test_capture_snapshot_window_drains_current_telemetry_window(tmp_path: Path) -> None:
    """每次心跳采样后，后续窗口只应包含新的 telemetry 事件。"""

    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
        )
    )
    await init_llm_stats(
        db_path=str(tmp_path / "llm-drain.db"),
        enabled=True,
        window_hours=5.0,
    )

    try:
        identity_store = CloudTelemetryIdentityStore(storage_dir=str(tmp_path / "state-drain"))
        await identity_store.set_consent(CONSENT_GRANTED)
        pending_queue = CloudTelemetryPendingQueue(
            max_bytes=4096,
            max_windows=16,
        )
        client = CloudTelemetryClient(
            CloudTelemetryClientConfig(
                enabled=True,
                ingest_base_url="http://testserver/_cloud_telemetry",
                pending_queue_max_bytes=4096,
                pending_queue_max_windows=16,
                default_heartbeat_interval_seconds=0.05,
                send_timeout_seconds=5.0,
                trust_env=False,
            ),
            identity_store,
            pending_queue,
            transport=_build_mock_transport([]),
        )

        collector = get_telemetry_collector()
        await collector.record(
            TelemetryEventRecord(domain="runtime", event_name="first", summary="first")
        )
        first_window = await client.capture_snapshot_window()

        await collector.record(
            TelemetryEventRecord(domain="runtime", event_name="second", summary="second")
        )
        second_window = await client.capture_snapshot_window()

        assert first_window is not None
        assert second_window is not None
        assert first_window.summary["telemetry_summary"]["total_events"] == 1
        assert second_window.summary["telemetry_summary"]["total_events"] == 1

        windows = await pending_queue.get_windows()
        assert [window.summary["telemetry_summary"]["total_events"] for window in windows] == [1, 1]
    finally:
        await close_llm_stats_db()
        await close_telemetry_db()
