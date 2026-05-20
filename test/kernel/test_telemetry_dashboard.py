"""telemetry dashboard 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.config.core_config as core_config_module
from src.core.config.core_config import CoreConfig
from src.app.cloud_telemetry import (
    close_cloud_telemetry_runtime,
    initialize_cloud_telemetry_runtime,
)
from src.kernel.telemetry import (
    TelemetryConfig,
    TelemetryEventRecord,
    close_telemetry_db,
    get_telemetry_collector,
    get_telemetry_dashboard,
    init_telemetry,
)


@pytest.mark.asyncio
async def test_dashboard_summary_requires_api_key(tmp_path: Path) -> None:
    """summary API 应复用现有 API key 认证。"""
    original = core_config_module._global_config
    core_config_module._global_config = CoreConfig(
        http_router=CoreConfig.HttpRouterSection(api_keys=["secret-key"])
    )

    try:
        await init_telemetry(
            config=TelemetryConfig(
                enabled=True,
            ),
        )
        await get_telemetry_collector().record(
            TelemetryEventRecord(domain="runtime", event_name="ready", summary="ready")
        )

        app = FastAPI()
        get_telemetry_dashboard().mount(app)
        client = TestClient(app)

        unauthorized = client.get("/_telemetry/api/summary")
        authorized = client.get(
            "/_telemetry/api/summary",
            headers={"X-API-Key": "secret-key"},
        )

        assert unauthorized.status_code in {401, 403}
        assert authorized.status_code == 200
        payload = authorized.json()
        assert payload["telemetry_enabled"] is True
        assert payload["telemetry_summary"]["total_events"] == 1
        assert payload["telemetry_summary"]["enabled"] is True
        assert payload["recent_events"][0]["event_name"] == "ready"
    finally:
        core_config_module._global_config = original
        await close_telemetry_db()


@pytest.mark.asyncio
async def test_dashboard_summary_includes_cloud_telemetry_status(tmp_path: Path) -> None:
    """summary API 应包含云端遥测本地状态摘要。"""

    original = core_config_module._global_config
    core_config_module._global_config = CoreConfig(
        http_router=CoreConfig.HttpRouterSection(api_keys=["secret-key"]),
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            identity_storage_dir=str(tmp_path / "state"),
        ),
    )

    try:
        foundation = await initialize_cloud_telemetry_runtime()
        state = await foundation.identity_store.ensure()
        await foundation.identity_store.set_consent("granted", allow_ip_retention=False)
        await foundation.identity_store.set_install_credential(
            "credential-1",
            issued_at=100.0,
            expires_at=200.0,
            registered_at=150.0,
        )

        await init_telemetry(
            config=TelemetryConfig(
                enabled=True,
            ),
        )

        app = FastAPI()
        get_telemetry_dashboard().mount(app)
        client = TestClient(app)

        response = client.get(
            "/_telemetry/api/summary",
            headers={"X-API-Key": "secret-key"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["cloud_telemetry_status"]["initialized"] is True
        assert payload["cloud_telemetry_status"]["client_instance_id"] == state.client_instance_id
        assert payload["cloud_telemetry_status"]["consent_state"] == "granted"
        assert payload["cloud_telemetry_status"]["allow_ip_retention"] is False
        assert payload["cloud_telemetry_status"]["credential_present"] is True
    finally:
        core_config_module._global_config = original
        await close_cloud_telemetry_runtime()
        await close_telemetry_db()