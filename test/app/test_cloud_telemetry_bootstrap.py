"""测试云端遥测本地基础设施装配。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.cloud_telemetry import (
    build_cloud_telemetry_identity_store,
    initialize_cloud_telemetry_foundation,
)
from src.core.config import CoreConfig


def test_build_cloud_telemetry_identity_store_from_core_config(tmp_path: Path) -> None:
    """测试从 CoreConfig 映射本地身份持久化目录。"""

    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            identity_storage_dir=str(tmp_path),
        )
    )

    store = build_cloud_telemetry_identity_store(config)
    assert store is not None


@pytest.mark.asyncio
async def test_initialize_cloud_telemetry_foundation_skips_database_when_disabled(
    tmp_path: Path,
) -> None:
    """仅装配本地身份服务。"""

    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            identity_storage_dir=str(tmp_path / "state"),
        )
    )

    foundation = await initialize_cloud_telemetry_foundation(config)

    state = await foundation.identity_store.ensure()
    assert state.client_instance_id


@pytest.mark.asyncio
async def test_initialize_cloud_telemetry_foundation_ignores_server_flag_locally(
    tmp_path: Path,
) -> None:
    """本地基础设施只依赖本地 identity 配置。"""

    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            identity_storage_dir=str(tmp_path / "state"),
        )
    )

    foundation = await initialize_cloud_telemetry_foundation(config)
    state = await foundation.identity_store.ensure()
    assert state.client_instance_id


@pytest.mark.asyncio
async def test_initialize_cloud_telemetry_foundation_uses_identity_storage_dir(
    tmp_path: Path,
) -> None:
    """本地基础设施应使用配置中的 identity 存储目录。"""

    config = CoreConfig(
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            identity_storage_dir=str(tmp_path / "state"),
        )
    )

    foundation = await initialize_cloud_telemetry_foundation(config)
    state = await foundation.identity_store.ensure()
    assert (tmp_path / "state" / "identity.json").exists()
    assert state.client_instance_id