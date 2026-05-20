"""测试云端遥测本地身份持久化。"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from src.kernel.telemetry.cloud import (
    CONSENT_GRANTED,
    CONSENT_REVOKED,
    CloudTelemetryIdentityStore,
)


@pytest.mark.asyncio
async def test_identity_store_ensure_creates_and_reuses_client_instance_id(tmp_path: Path) -> None:
    """测试 ensure 会生成稳定 client id 且重复加载不变。"""

    store = CloudTelemetryIdentityStore(storage_dir=str(tmp_path))

    state = await store.ensure()
    UUID(hex=state.client_instance_id)
    assert state.consent_state == "unknown"
    assert state.allow_ip_retention is True

    reloaded = await store.ensure()
    assert reloaded.client_instance_id == state.client_instance_id
    assert reloaded.created_at == state.created_at


@pytest.mark.asyncio
async def test_identity_store_updates_consent_and_credential(tmp_path: Path) -> None:
    """测试同意状态与安装实例凭证可持久化更新。"""

    store = CloudTelemetryIdentityStore(storage_dir=str(tmp_path))

    consented = await store.set_consent(CONSENT_GRANTED, allow_ip_retention=False)
    assert consented.consent_state == CONSENT_GRANTED
    assert consented.allow_ip_retention is False

    updated = await store.set_install_credential(
        "credential-1",
        issued_at=100.0,
        expires_at=200.0,
        registered_at=150.0,
    )
    assert updated.install_credential == "credential-1"
    assert updated.credential_issued_at == 100.0
    assert updated.credential_expires_at == 200.0
    assert updated.last_registered_at == 150.0

    cleared = await store.clear_install_credential()
    assert cleared.install_credential is None
    assert cleared.credential_issued_at is None
    assert cleared.credential_expires_at is None
    assert cleared.consent_state == CONSENT_GRANTED

    reloaded = await store.load()
    assert reloaded is not None
    assert reloaded.client_instance_id == consented.client_instance_id
    assert reloaded.consent_state == CONSENT_GRANTED


@pytest.mark.asyncio
async def test_identity_store_rejects_invalid_consent_state(tmp_path: Path) -> None:
    """测试非法同意状态会被拒绝。"""

    store = CloudTelemetryIdentityStore(storage_dir=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid consent state"):
        await store.set_consent("invalid")

    revoked = await store.set_consent(CONSENT_REVOKED)
    assert revoked.consent_state == CONSENT_REVOKED