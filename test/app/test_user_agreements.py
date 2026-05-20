"""启动协议确认流程测试。"""

from __future__ import annotations

import hashlib
import tomllib
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
    local_telemetry_enabled: bool = False,
) -> CoreConfig:
    return CoreConfig(
        bot=CoreConfig.BotSection(
            data_dir=str(tmp_path / "data"),
        ),
        telemetry=CoreConfig.TelemetrySection(
            enabled=local_telemetry_enabled,
        ),
        cloud_telemetry=CoreConfig.CloudTelemetrySection(
            client_enabled=cloud_client_enabled,
            identity_storage_dir=str(tmp_path / "cloud-state"),
        ),
    )


@pytest.mark.asyncio
async def test_ensure_eula_accepted_persists_document_hash(tmp_path: Path) -> None:
    """同意 EULA 后应持久化，同版本后续不再重复询问。"""

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
async def test_ensure_cloud_telemetry_consent_accepts_and_enables_configs(
    tmp_path: Path,
) -> None:
    """明确同意后应写入 granted，并直接打开本地/云端遥测配置。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "PRIVACY.md").write_text("# PRIVACY\n\ntelemetry", encoding="utf-8")
    config_dir = project_root / "config"
    config_dir.mkdir()
    config_path = config_dir / "core.toml"
    config_path.write_text(
        f"""
[bot]
data_dir = "{(tmp_path / "data").as_posix()}"

[telemetry]
enabled = false

[cloud_telemetry]
client_enabled = false
identity_storage_dir = "{(tmp_path / "cloud-state").as_posix()}"
""".strip(),
        encoding="utf-8",
    )
    config = _build_config(
        tmp_path,
        cloud_client_enabled=False,
        local_telemetry_enabled=False,
    )

    await ensure_cloud_telemetry_consent(
        config,
        project_root,
        ui=_SilentUI(),
        config_path=config_path,
        input_func=lambda _: "agree",
    )

    identity_store = CloudTelemetryIdentityStore(
        storage_dir=str(tmp_path / "cloud-state")
    )
    state = await identity_store.load()
    assert state is not None
    assert state.consent_state == CONSENT_GRANTED
    assert state.allow_ip_retention is False
    assert config.cloud_telemetry.client_enabled is True
    assert config.telemetry.enabled is True

    with config_path.open("rb") as file:
        raw_config = tomllib.load(file)
    assert raw_config["cloud_telemetry"]["client_enabled"] is True
    assert raw_config["telemetry"]["enabled"] is True


@pytest.mark.asyncio
async def test_ensure_cloud_telemetry_consent_records_decline_and_keeps_cloud_disabled(
    tmp_path: Path,
) -> None:
    """明确拒绝后应持久化 revoked，并保持云端遥测关闭。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "PRIVACY.md").write_text("# PRIVACY\n\ntelemetry", encoding="utf-8")
    config_dir = project_root / "config"
    config_dir.mkdir()
    config_path = config_dir / "core.toml"
    config_path.write_text(
        f"""
[bot]
data_dir = "{(tmp_path / "data").as_posix()}"

[telemetry]
enabled = true

[cloud_telemetry]
client_enabled = true
identity_storage_dir = "{(tmp_path / "cloud-state").as_posix()}"
""".strip(),
        encoding="utf-8",
    )
    config = _build_config(
        tmp_path,
        cloud_client_enabled=True,
        local_telemetry_enabled=True,
    )

    await ensure_cloud_telemetry_consent(
        config,
        project_root,
        ui=_SilentUI(),
        config_path=config_path,
        input_func=lambda _: "decline",
    )

    identity_store = CloudTelemetryIdentityStore(
        storage_dir=str(tmp_path / "cloud-state")
    )
    state = await identity_store.load()
    assert state is not None
    assert state.consent_state == CONSENT_REVOKED
    assert state.allow_ip_retention is False
    assert config.cloud_telemetry.client_enabled is False
    assert config.telemetry.enabled is True

    with config_path.open("rb") as file:
        raw_config = tomllib.load(file)
    assert raw_config["cloud_telemetry"]["client_enabled"] is False
    assert raw_config["telemetry"]["enabled"] is True


@pytest.mark.asyncio
async def test_ensure_cloud_telemetry_consent_skips_prompt_when_same_privacy_version_already_accepted(
    tmp_path: Path,
) -> None:
    """相同版本协议已明确同意时，不应再次询问，并会修正配置为启用。"""

    project_root = tmp_path / "project"
    project_root.mkdir()
    privacy_content = "# PRIVACY\n\ntelemetry"
    (project_root / "PRIVACY.md").write_text(privacy_content, encoding="utf-8")
    config_dir = project_root / "config"
    config_dir.mkdir()
    config_path = config_dir / "core.toml"
    config_path.write_text(
        f"""
[bot]
data_dir = "{(tmp_path / "data").as_posix()}"

[telemetry]
enabled = false

[cloud_telemetry]
client_enabled = false
identity_storage_dir = "{(tmp_path / "cloud-state").as_posix()}"
""".strip(),
        encoding="utf-8",
    )

    agreements_dir = tmp_path / "data" / "system" / "agreements"
    agreements_dir.mkdir(parents=True)
    document_hash = hashlib.sha256(privacy_content.encode("utf-8")).hexdigest()
    (agreements_dir / "agreement_acceptance.json").write_text(
        (
            "{\n"
            '  "cloud_telemetry_privacy": {\n'
            f'    "document_path": "{(project_root / "PRIVACY.md").as_posix()}",\n'
            f'    "document_sha256": "{document_hash}",\n'
            '    "decision": "agree",\n'
            '    "confirmed_at": 1.0\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    config = _build_config(
        tmp_path,
        cloud_client_enabled=False,
        local_telemetry_enabled=False,
    )

    def should_not_be_called(_: str) -> str:
        raise AssertionError("prompt should not be called")

    await ensure_cloud_telemetry_consent(
        config,
        project_root,
        ui=_SilentUI(),
        config_path=config_path,
        input_func=should_not_be_called,
    )

    with config_path.open("rb") as file:
        raw_config = tomllib.load(file)
    assert raw_config["cloud_telemetry"]["client_enabled"] is True
    assert raw_config["telemetry"]["enabled"] is True


@pytest.mark.asyncio
async def test_ensure_startup_agreements_prompts_telemetry_even_when_initially_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次启动即使遥测初始为关闭，也应明确确认；同意后直接打开。"""

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

[telemetry]
enabled = false

[cloud_telemetry]
client_enabled = false
identity_storage_dir = "{(tmp_path / "cloud-state").as_posix()}"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(project_root)
    answers = iter(["agree", "agree"])
    result = await ensure_startup_agreements(
        "config/core.toml",
        UILevel.MINIMAL,
        input_func=lambda _: next(answers),
    )
    assert result is True

    with (config_dir / "core.toml").open("rb") as file:
        raw_config = tomllib.load(file)
    assert raw_config["cloud_telemetry"]["client_enabled"] is True
    assert raw_config["telemetry"]["enabled"] is True


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
