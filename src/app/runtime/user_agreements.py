"""启动前用户协议确认流程。"""

from __future__ import annotations

import os
import time
import tomllib
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Protocol

from src.core.config import CoreConfig, init_core_config
from src.kernel.config.core import _merge_with_model_defaults, _render_toml_with_signature
from src.kernel.storage import JSONStore
from src.kernel.telemetry.cloud import (
    CONSENT_GRANTED,
    CONSENT_REVOKED,
    CloudTelemetryIdentityStore,
)

from .console_ui import ConsoleUIManager, UILevel

InputFunc = Callable[[str], str]


class AgreementUI(Protocol):
    """启动协议确认使用的最小 UI 协议。"""

    def section(self, title: str) -> None:
        """展示分节标题。"""

    def display_warning(self, message: str) -> None:
        """展示警告信息。"""

    def display_info(self, message: str, title: str = "Info") -> None:
        """展示提示信息。"""

    def display_success(self, message: str) -> None:
        """展示成功信息。"""

    def print(self, *args: Any, **kwargs: Any) -> None:
        """输出原始文本内容。"""

_STATE_FILE_NAME = "agreement_acceptance"
_CLOUD_TELEMETRY_PRIVACY_KEY = "cloud_telemetry_privacy"
_STARTUP_AGREEMENT_ENV_VAR = "MOFOX_ACCEPT_STARTUP_AGREEMENTS"


def _project_root_from_config_path(config_path: str) -> Path:
    return Path(config_path).resolve().parent.parent


def _resolve_data_dir(config: CoreConfig, project_root: Path) -> Path:
    data_dir = Path(config.bot.data_dir)
    if not data_dir.is_absolute():
        data_dir = project_root / data_dir
    return data_dir


def _resolve_agreement_state_dir(config: CoreConfig, project_root: Path) -> Path:
    return _resolve_data_dir(config, project_root) / "system" / "agreements"


def _resolve_identity_storage_dir(config: CoreConfig, project_root: Path) -> Path:
    storage_dir = Path(config.cloud_telemetry.identity_storage_dir)
    if not storage_dir.is_absolute():
        storage_dir = project_root / storage_dir
    return storage_dir


def _resolve_config_file_path(config_path: str | Path, project_root: Path) -> Path:
    path = Path(config_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _load_document(path: Path) -> tuple[str, str]:
    content = path.read_text(encoding="utf-8")
    return content, sha256(content.encode("utf-8")).hexdigest()


def _print_document(ui: AgreementUI, title: str, path: Path, content: str) -> None:
    ui.section(title)
    ui.display_info(f"协议文件：{path}", title=title)
    ui.print(content)


def _prompt_for_choice(
    ui: AgreementUI,
    *,
    title: str,
    required_message: str,
    document_path: Path,
    document_content: str,
    input_func: InputFunc,
) -> bool:
    ui.section(title)
    ui.display_warning(required_message)
    ui.display_info(
        (
            f"请先阅读协议文件：{document_path}\n"
            "输入 view 查看全文，输入 agree 表示同意，输入 decline 表示拒绝。"
        ),
        title=title,
    )

    while True:
        try:
            raw_choice = input_func(f"[{title}] 请输入 view / agree / decline: ")
        except EOFError:
            return False
        except KeyboardInterrupt:
            return False

        choice = raw_choice.strip().lower()
        if choice in {"view", "v", "show", "查看"}:
            _print_document(ui, title, document_path, document_content)
            continue
        if choice in {"agree", "a", "yes", "同意"}:
            return True
        if choice in {"decline", "d", "no", "拒绝"}:
            return False
        ui.display_warning("无效输入，请输入 view、agree 或 decline。")


def _has_startup_agreement_env_override() -> bool:
    """检查是否通过环境变量启用了非交互协议确认。"""

    return _STARTUP_AGREEMENT_ENV_VAR in os.environ


async def _save_eula_acceptance(
    store: JSONStore,
    state: dict[str, Any],
    eula_path: Path,
    eula_hash: str,
) -> None:
    """保存 EULA 同意状态。"""

    state["eula"] = {
        "document_path": str(eula_path),
        "document_sha256": eula_hash,
        "accepted_at": time.time(),
    }
    await store.save(_STATE_FILE_NAME, state)


async def _save_cloud_telemetry_privacy_decision(
    store: JSONStore,
    state: dict[str, Any],
    privacy_path: Path,
    privacy_hash: str,
    decision: str,
) -> None:
    """保存云端遥测隐私协议确认结果。"""

    state[_CLOUD_TELEMETRY_PRIVACY_KEY] = {
        "document_path": str(privacy_path),
        "document_sha256": privacy_hash,
        "decision": decision,
        "confirmed_at": time.time(),
    }
    await store.save(_STATE_FILE_NAME, state)


async def _load_agreement_state_async(store: JSONStore) -> dict[str, Any]:
    state = await store.load(_STATE_FILE_NAME)
    if isinstance(state, dict):
        return state
    return {}


def _persist_cloud_telemetry_config(
    config: CoreConfig,
    project_root: Path,
    *,
    config_path: str | Path | None,
    client_enabled: bool,
    local_telemetry_enabled: bool | None = None,
) -> None:
    config.cloud_telemetry.client_enabled = client_enabled
    if local_telemetry_enabled is not None:
        config.telemetry.enabled = local_telemetry_enabled

    if config_path is None:
        return

    resolved_path = _resolve_config_file_path(config_path, project_root)
    with resolved_path.open("rb") as file:
        raw_config = tomllib.load(file)

    cloud_section = raw_config.get("cloud_telemetry")
    if not isinstance(cloud_section, dict):
        cloud_section = {}
        raw_config["cloud_telemetry"] = cloud_section
    cloud_section["client_enabled"] = client_enabled

    if local_telemetry_enabled is not None:
        telemetry_section = raw_config.get("telemetry")
        if not isinstance(telemetry_section, dict):
            telemetry_section = {}
            raw_config["telemetry"] = telemetry_section
        telemetry_section["enabled"] = local_telemetry_enabled

    merged_config = _merge_with_model_defaults(CoreConfig, raw_config)
    rendered = _render_toml_with_signature(CoreConfig, merged_config)
    resolved_path.write_text(rendered, encoding="utf-8")


async def ensure_eula_accepted(
    config: CoreConfig,
    project_root: Path,
    ui: AgreementUI,
    *,
    input_func: InputFunc = input,
) -> bool:
    """确保用户已同意当前版本 EULA。"""

    eula_path = project_root / "eula.md"
    eula_content, eula_hash = _load_document(eula_path)
    store = JSONStore(_resolve_agreement_state_dir(config, project_root))
    state = await _load_agreement_state_async(store)

    if _has_startup_agreement_env_override():
        await _save_eula_acceptance(store, state, eula_path, eula_hash)
        ui.display_success(
            f"检测到环境变量 {_STARTUP_AGREEMENT_ENV_VAR}，已自动确认 EULA。"
        )
        return True

    accepted = state.get("eula", {})
    if accepted.get("document_sha256") == eula_hash:
        return True

    agreed = _prompt_for_choice(
        ui,
        title="EULA",
        required_message="你必须明确同意 EULA，才能进入 Neo-MoFox。",
        document_path=eula_path,
        document_content=eula_content,
        input_func=input_func,
    )
    if not agreed:
        ui.display_warning("你未同意 EULA，程序将退出。")
        return False

    await _save_eula_acceptance(store, state, eula_path, eula_hash)
    ui.display_success("EULA 已确认。")
    return True


async def ensure_cloud_telemetry_consent(
    config: CoreConfig,
    project_root: Path,
    ui: AgreementUI,
    *,
    config_path: str | Path | None = None,
    input_func: InputFunc = input,
) -> None:
    """确认当前版本的云端遥测隐私协议，并同步落盘遥测配置。"""

    identity_store = CloudTelemetryIdentityStore(
        storage_dir=str(_resolve_identity_storage_dir(config, project_root))
    )
    identity_state = await identity_store.ensure()

    privacy_path = project_root / "PRIVACY.md"
    privacy_content, privacy_hash = _load_document(privacy_path)

    agreement_store = JSONStore(_resolve_agreement_state_dir(config, project_root))
    agreement_state = await _load_agreement_state_async(agreement_store)
    privacy_state = agreement_state.get(_CLOUD_TELEMETRY_PRIVACY_KEY, {})

    if _has_startup_agreement_env_override():
        if identity_state.consent_state != CONSENT_GRANTED:
            await identity_store.set_consent(
                CONSENT_GRANTED,
                allow_ip_retention=False,
            )
        await _save_cloud_telemetry_privacy_decision(
            agreement_store,
            agreement_state,
            privacy_path,
            privacy_hash,
            "agree",
        )
        _persist_cloud_telemetry_config(
            config,
            project_root,
            config_path=config_path,
            client_enabled=True,
            local_telemetry_enabled=True,
        )
        ui.display_success(
            (
                f"检测到环境变量 {_STARTUP_AGREEMENT_ENV_VAR}，"
                "已自动确认遥测隐私协议并启用遥测配置。"
            )
        )
        return

    if privacy_state.get("document_sha256") == privacy_hash:
        decision = privacy_state.get("decision")
        if decision == "agree":
            if identity_state.consent_state != CONSENT_GRANTED:
                await identity_store.set_consent(
                    CONSENT_GRANTED,
                    allow_ip_retention=False,
                )
            _persist_cloud_telemetry_config(
                config,
                project_root,
                config_path=config_path,
                client_enabled=True,
                local_telemetry_enabled=True,
            )
            return
        if decision == "decline":
            if identity_state.consent_state != CONSENT_REVOKED:
                await identity_store.set_consent(
                    CONSENT_REVOKED,
                    allow_ip_retention=False,
                )
            _persist_cloud_telemetry_config(
                config,
                project_root,
                config_path=config_path,
                client_enabled=False,
            )
            return

    agreed = _prompt_for_choice(
        ui,
        title="云端遥测",
        required_message=(
            "请明确确认当前版本的遥测隐私协议。"
            "如果同意，我们会立即打开本地遥测与云端遥测发送配置；"
            "如果拒绝，则保持云端遥测关闭。"
        ),
        document_path=privacy_path,
        document_content=privacy_content,
        input_func=input_func,
    )

    if agreed:
        await identity_store.set_consent(
            CONSENT_GRANTED,
            allow_ip_retention=False,
        )
        await _save_cloud_telemetry_privacy_decision(
            agreement_store,
            agreement_state,
            privacy_path,
            privacy_hash,
            "agree",
        )
        _persist_cloud_telemetry_config(
            config,
            project_root,
            config_path=config_path,
            client_enabled=True,
            local_telemetry_enabled=True,
        )
        ui.display_success("你已同意遥测隐私协议，遥测配置已打开。")
        return

    await identity_store.set_consent(
        CONSENT_REVOKED,
        allow_ip_retention=False,
    )
    await _save_cloud_telemetry_privacy_decision(
        agreement_store,
        agreement_state,
        privacy_path,
        privacy_hash,
        "decline",
    )
    _persist_cloud_telemetry_config(
        config,
        project_root,
        config_path=config_path,
        client_enabled=False,
    )
    ui.display_info(
        "你未同意遥测隐私协议，云端遥测将保持关闭。",
        title="云端遥测",
    )


async def ensure_startup_agreements(
    config_path: str,
    ui_level: UILevel,
    *,
    input_func: InputFunc = input,
) -> bool:
    """在程序真正启动前完成必要协议确认。"""

    config = init_core_config(config_path)
    project_root = _project_root_from_config_path(config_path)
    ui = ConsoleUIManager(level=ui_level)

    eula_accepted = await ensure_eula_accepted(
        config,
        project_root,
        ui,
        input_func=input_func,
    )
    if not eula_accepted:
        return False

    await ensure_cloud_telemetry_consent(
        config,
        project_root,
        ui,
        config_path=config_path,
        input_func=input_func,
    )
    return True


__all__ = [
    "ensure_cloud_telemetry_consent",
    "ensure_eula_accepted",
    "ensure_startup_agreements",
]
