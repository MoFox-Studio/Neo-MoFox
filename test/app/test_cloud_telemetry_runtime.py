"""测试云端遥测运行时状态访问。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.cloud_telemetry import (
    close_cloud_telemetry_runtime,
    get_cloud_telemetry_client,
    get_cloud_telemetry_runtime,
    get_cloud_telemetry_status_summary,
    initialize_cloud_telemetry_runtime,
)
from src.app.cloud_telemetry.bootstrap import initialize_cloud_telemetry_foundation
from src.app.cloud_telemetry.runtime import CLOUD_TELEMETRY_INGEST_BASE_URL
from src.core.config import CoreConfig


@pytest.mark.asyncio
async def test_cloud_telemetry_runtime_status_defaults_when_uninitialized() -> None:
    """未初始化时应返回可预测的默认状态。"""

    await close_cloud_telemetry_runtime()
    summary = await get_cloud_telemetry_status_summary()

    assert summary["initialized"] is False
    assert summary["client_instance_id"] is None
    assert summary["credential_present"] is False


@pytest.mark.asyncio
async def test_cloud_telemetry_runtime_status_reads_identity_state(tmp_path: Path) -> None:
    """初始化后应读取本地身份状态摘要。"""

    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            client_enabled=True,
            identity_storage_dir=str(tmp_path / "state"),
        )
    )
    foundation = await initialize_cloud_telemetry_foundation(config)
    state = await foundation.identity_store.ensure()
    await foundation.identity_store.set_consent("granted", allow_ip_retention=False)
    await foundation.identity_store.set_install_credential(
        "credential-1",
        issued_at=1.0,
        expires_at=2.0,
        registered_at=1.5,
    )

    initialized = await initialize_cloud_telemetry_runtime(foundation)
    assert get_cloud_telemetry_runtime() is initialized
    assert get_cloud_telemetry_client() is not None

    summary = await get_cloud_telemetry_status_summary()
    assert summary["initialized"] is True
    assert summary["client_enabled"] is True
    assert summary["client_instance_id"] == state.client_instance_id
    assert summary["consent_state"] == "granted"
    assert summary["allow_ip_retention"] is False
    assert summary["credential_present"] is True
    assert summary["last_registered_at"] == 1.5
    assert summary["credential_expires_at"] == 2.0
    assert summary["pending_window_count"] == 0
    assert summary["loop_running"] is False

    await close_cloud_telemetry_runtime()


@pytest.mark.asyncio
async def test_cloud_telemetry_runtime_keeps_client_disabled_without_consent(
    tmp_path: Path,
) -> None:
    """client_enabled 打开后应自动进入可发送状态。"""

    await close_cloud_telemetry_runtime()
    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            client_enabled=True,
            identity_storage_dir=str(tmp_path / "state"),
        )
    )

    foundation = await initialize_cloud_telemetry_runtime(config)
    identity_state = await foundation.identity_store.load()
    summary = await get_cloud_telemetry_status_summary()
    client = get_cloud_telemetry_client()

    assert identity_state is not None
    assert identity_state.consent_state == "unknown"
    assert summary["consent_state"] == "unknown"
    assert summary["client_enabled"] is False
    assert summary["allow_ip_retention"] is True
    assert client is not None
    assert client.enabled is False

    await close_cloud_telemetry_runtime()


@pytest.mark.asyncio
async def test_cloud_telemetry_runtime_with_core_config_stays_client_only(
    tmp_path: Path,
) -> None:
    """本地 runtime 初始化仅使用客户端常量地址。"""

    await close_cloud_telemetry_runtime()
    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            client_enabled=True,
            identity_storage_dir=str(tmp_path / "state"),
        )
    )

    foundation = await initialize_cloud_telemetry_runtime(config)
    client = get_cloud_telemetry_client()

    assert client is not None
    assert client._config.ingest_base_url == CLOUD_TELEMETRY_INGEST_BASE_URL

    await close_cloud_telemetry_runtime()
