"""启动协议确认流程测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.runtime.console_ui import UILevel
from src.app.runtime.user_agreements import (
    ensure_cloud_telemetry_consent,
    ensure_eula_accepted,
    ensure_startup_agreements,
)
from src.core.config import CoreConfig
from src.kernel.telemetry.cloud import (
    CONSENT_GRANTED,
    CONSENT_REVOKED,
    CloudTelemetryIdentityStore,
)


def _build_config(
    tmp_path: Path,
    *,
    cloud_client_enabled: bool,
) -> CoreConfig:
    return CoreConfig(
        bot=CoreConfig.BotSection(
            data_dir=str(tmp_path / "data"),
        ),
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            client_enabled=cloud_client_enabled,
            identity_storage_dir=str(tmp_path / "cloud-state"),
        ),
    )


@pytest.mark.asyncio
async def test_ensure_eula_accepted_persists_document_hash(tmp_path: Path) -> None:
    """同意 EULA 后应持久化，后续同版本不再重复询问。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "eula.md").write_text("# EULA\n\nversion=1", encoding="utf-8")
    config = _build_config(tmp_path, cloud_client_enabled=False)

    answers = iter(["agree"])
    accepted = await ensure_eula_accepted(
        config,
        project_root,
        ui=_SilentUI(),
        input_func=lambda _: next(answers),
    )
    assert accepted is True

    called = False

    def should_not_be_called(_: str) -> str:
        nonlocal called
        called = True
        return "decline"

    accepted_again = await ensure_eula_accepted(
        config,
        project_root,
        ui=_SilentUI(),
        input_func=should_not_be_called,
    )
    assert accepted_again is True
    assert called is False


@pytest.mark.asyncio
async def test_ensure_eula_accepted_rejects_startup_when_declined(tmp_path: Path) -> None:
    """拒绝 EULA 时应阻止继续启动。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "eula.md").write_text("# EULA\n\nversion=1", encoding="utf-8")
    config = _build_config(tmp_path, cloud_client_enabled=False)

    accepted = await ensure_eula_accepted(
        config,
        project_root,
        ui=_SilentUI(),
        input_func=lambda _: "decline",
    )
    assert accepted is False


@pytest.mark.asyncio
async def test_ensure_cloud_telemetry_consent_accepts_explicit_opt_in(tmp_path: Path) -> None:
    """用户明确同意后才应写入 granted。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "PRIVACY.md").write_text("# PRIVACY\n\ntelemetry", encoding="utf-8")
    config = _build_config(tmp_path, cloud_client_enabled=True)

    await ensure_cloud_telemetry_consent(
        config,
        project_root,
        ui=_SilentUI(),
        input_func=lambda _: "agree",
    )

    identity_store = CloudTelemetryIdentityStore(
        storage_dir=str(tmp_path / "cloud-state")
    )
    state = await identity_store.load()
    assert state is not None
    assert state.consent_state == CONSENT_GRANTED
    assert state.allow_ip_retention is False


@pytest.mark.asyncio
async def test_ensure_cloud_telemetry_consent_records_decline(tmp_path: Path) -> None:
    """用户拒绝后应持久化 revoked。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "PRIVACY.md").write_text("# PRIVACY\n\ntelemetry", encoding="utf-8")
    config = _build_config(tmp_path, cloud_client_enabled=True)

    await ensure_cloud_telemetry_consent(
        config,
        project_root,
        ui=_SilentUI(),
        input_func=lambda _: "decline",
    )

    identity_store = CloudTelemetryIdentityStore(
        storage_dir=str(tmp_path / "cloud-state")
    )
    state = await identity_store.load()
    assert state is not None
    assert state.consent_state == CONSENT_REVOKED
    assert state.allow_ip_retention is False


@pytest.mark.asyncio
async def test_ensure_startup_agreements_only_prompts_telemetry_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """遥测配置关闭时，不应额外要求确认遥测协议。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    config_dir = project_root / "config"
    config_dir.mkdir()
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir()

    (project_root / "eula.md").write_text("# EULA\n\nversion=1", encoding="utf-8")
    (project_root / "PRIVACY.md").write_text("# PRIVACY\n\ntelemetry", encoding="utf-8")
    (config_dir / "core.toml").write_text(
        f"""
[bot]
data_dir = "{data_dir.as_posix()}"

[cloud_telemetry]
client_enabled = false
identity_storage_dir = "{(tmp_path / "cloud-state").as_posix()}"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(project_root)
    answers = iter(["agree"])
    result = await ensure_startup_agreements(
        "config/core.toml",
        UILevel.MINIMAL,
        input_func=lambda _: next(answers),
    )
    assert result is True


class _SilentUI:
    """测试用静默 UI。"""

    def section(self, title: str) -> None:
        return None

    def display_warning(self, message: str) -> None:
        return None

    def display_info(self, message: str, title: str = "Info") -> None:
        return None

    def display_success(self, message: str) -> None:
        return None

    def print(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None
