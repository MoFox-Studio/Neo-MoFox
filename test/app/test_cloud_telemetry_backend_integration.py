"""Neo-MoFox 客户端与独立后端的端到端集成测试。

要求工作区中存在 ../cloud_telemetry_backend 包以及 asgi-lifespan、httpx 依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# 尝试解析 cloud_telemetry_backend 包路径
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
BACKEND_PARENT = WORKSPACE_ROOT
if str(BACKEND_PARENT) not in sys.path:
    sys.path.insert(0, str(BACKEND_PARENT))


cloud_backend = pytest.importorskip("cloud_telemetry_backend")
asgi_lifespan = pytest.importorskip("asgi_lifespan")
LifespanManager = asgi_lifespan.LifespanManager


@pytest.mark.asyncio
async def test_neo_mofox_client_register_and_send_to_backend(tmp_path: Path) -> None:
    """Neo-MoFox 客户端应能完成注册并把窗口送达独立后端。"""

    from cloud_telemetry_backend.app import create_cloud_telemetry_app
    from cloud_telemetry_backend.settings import CloudTelemetryBackendSettings

    from src.app.cloud_telemetry.client import (
        CloudTelemetryClient,
        CloudTelemetryClientConfig,
        CloudTelemetryPendingQueue,
    )
    from src.kernel.llm.stats import (
        LLMRequestRecord,
        close_llm_stats_db,
        init_llm_stats,
    )
    from src.kernel.telemetry import (
        TelemetryConfig,
        TelemetryEventRecord,
        close_telemetry_db,
        get_telemetry_collector,
        init_telemetry,
    )
    from src.kernel.telemetry.cloud import (
        CONSENT_GRANTED,
        CloudTelemetryIdentityStore,
    )

    settings = CloudTelemetryBackendSettings(
        ingest_prefix="/_cloud_telemetry",
        admin_api_keys=("test-admin-key",),
        default_heartbeat_interval_seconds=10,
        challenge_ttl_seconds=60,
        offline_grace_factor=2.0,
        offline_scan_interval_seconds=60.0,
        gap_recovery_window=5,
        instance_detail_max_windows=20,
        instance_detail_max_diagnostic_events=20,
        database_type="sqlite",
        sqlite_path=str(tmp_path / "cloud_telemetry.db"),
    )
    app = create_cloud_telemetry_app(settings)

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
            summary="runtime warning for integration",
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

    try:
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)

            identity_store = CloudTelemetryIdentityStore(
                storage_dir=str(tmp_path / "state")
            )
            await identity_store.set_consent(CONSENT_GRANTED, allow_ip_retention=True)

            pending_queue = CloudTelemetryPendingQueue(
                max_bytes=8192,
                max_windows=16,
            )
            client = CloudTelemetryClient(
                CloudTelemetryClientConfig(
                    enabled=True,
                    ingest_base_url="http://testserver/_cloud_telemetry",
                    pending_queue_max_bytes=8192,
                    pending_queue_max_windows=16,
                    default_heartbeat_interval_seconds=0.1,
                    send_timeout_seconds=5.0,
                    trust_env=False,
                ),
                identity_store,
                pending_queue,
                transport=transport,
            )

            window = await client.capture_snapshot_window()
            assert window is not None

            result = await client.run_once(collect_window=False)
            assert result["ok"] is True, result
            assert result["reason"] == "sent"
            assert result["accepted"] == 1

            identity_state = await identity_store.load()
            assert identity_state is not None
            assert identity_state.install_credential is not None

            queue_status = await pending_queue.get_status_summary()
            assert queue_status["pending_window_count"] == 0
            assert queue_status["last_send_status"] == "success"
            assert queue_status["instance_status"] == "active"
    finally:
        await close_llm_stats_db()
        await close_telemetry_db()


@pytest.mark.asyncio
async def test_neo_mofox_client_handles_suspended_instance(tmp_path: Path) -> None:
    """服务端把实例标记为 suspended 后，客户端应停止后续上报并写状态。"""

    from sqlalchemy import select

    from cloud_telemetry_backend.app import create_cloud_telemetry_app
    from cloud_telemetry_backend.database import get_cloud_telemetry_database
    from cloud_telemetry_backend.models import CloudTelemetryInstance
    from cloud_telemetry_backend.settings import CloudTelemetryBackendSettings

    from src.app.cloud_telemetry.client import (
        CloudTelemetryClient,
        CloudTelemetryClientConfig,
        CloudTelemetryPendingQueue,
    )
    from src.kernel.llm.stats import close_llm_stats_db, init_llm_stats
    from src.kernel.telemetry import (
        TelemetryConfig,
        close_telemetry_db,
        init_telemetry,
    )
    from src.kernel.telemetry.cloud import (
        CONSENT_GRANTED,
        CloudTelemetryIdentityStore,
    )

    settings = CloudTelemetryBackendSettings(
        ingest_prefix="/_cloud_telemetry",
        admin_api_keys=("test-admin-key",),
        default_heartbeat_interval_seconds=10,
        challenge_ttl_seconds=60,
        offline_grace_factor=2.0,
        offline_scan_interval_seconds=60.0,
        gap_recovery_window=5,
        instance_detail_max_windows=20,
        instance_detail_max_diagnostic_events=20,
        database_type="sqlite",
        sqlite_path=str(tmp_path / "cloud_telemetry.db"),
    )
    app = create_cloud_telemetry_app(settings)

    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
        )
    )
    await init_llm_stats(
        db_path=str(tmp_path / "llm.db"),
        enabled=True,
        window_hours=5.0,
    )

    try:
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)

            identity_store = CloudTelemetryIdentityStore(
                storage_dir=str(tmp_path / "state")
            )
            await identity_store.set_consent(CONSENT_GRANTED, allow_ip_retention=True)
            pending_queue = CloudTelemetryPendingQueue(
                max_bytes=8192,
                max_windows=16,
            )
            client = CloudTelemetryClient(
                CloudTelemetryClientConfig(
                    enabled=True,
                    ingest_base_url="http://testserver/_cloud_telemetry",
                    pending_queue_max_bytes=8192,
                    pending_queue_max_windows=16,
                    default_heartbeat_interval_seconds=0.1,
                    send_timeout_seconds=5.0,
                    trust_env=False,
                ),
                identity_store,
                pending_queue,
                transport=transport,
            )

            await client.capture_snapshot_window()
            first = await client.run_once(collect_window=False)
            assert first["ok"] is True, first

            identity_state = await identity_store.load()
            assert identity_state is not None
            cid = identity_state.client_instance_id
            database = get_cloud_telemetry_database()
            async with database.session() as session:
                instance = (
                    await session.execute(
                        select(CloudTelemetryInstance).where(
                            CloudTelemetryInstance.client_instance_id == cid
                        )
                    )
                ).scalars().first()
                assert instance is not None
                instance.is_suspended = True
                instance.suspension_reason = "integration_suspend"
                await session.commit()

            await client.capture_snapshot_window()
            second = await client.run_once(collect_window=False)
            assert second["ok"] is True
            assert second.get("instance_status") == "suspended"

            queue_status = await pending_queue.get_status_summary()
            assert queue_status["instance_status"] == "suspended"
            assert queue_status["last_rejection_reason"] == "integration_suspend"
            assert queue_status["pending_window_count"] == 0

            third = await client.run_once(collect_window=True)
            assert third["ok"] is False
            assert third["reason"] == "instance_suspended"
    finally:
        await close_llm_stats_db()
        await close_telemetry_db()
