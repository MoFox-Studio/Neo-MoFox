"""启动前用户协议确认流程。"""

from __future__ import annotations

import time
from hashlib import sha256
from pathlib import Path
from typing import Callable

from src.core.config import CoreConfig, init_core_config
from src.kernel.storage import JSONStore
from src.kernel.telemetry.cloud import (
    CONSENT_GRANTED,
    CONSENT_REVOKED,
    CloudTelemetryIdentityStore,
)

from .console_ui import ConsoleUIManager, UILevel

InputFunc = Callable[[str], str]

_STATE_FILE_NAME = "agreement_acceptance"


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


def _load_document(path: Path) -> tuple[str, str]:
    content = path.read_text(encoding="utf-8")
    return content, sha256(content.encode("utf-8")).hexdigest()


def _print_document(ui: ConsoleUIManager, title: str, path: Path, content: str) -> None:
    ui.section(title)
    ui.display_info(f"协议文件：{path}", title=title)
    ui.print(content)


def _prompt_for_choice(
    ui: ConsoleUIManager,
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
        f"请先阅读协议文件：{document_path}\n"
        "输入 view 查看全文，输入 agree 表示同意，输入 decline 表示拒绝。",
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


async def ensure_eula_accepted(
    config: CoreConfig,
    project_root: Path,
    ui: ConsoleUIManager,
    *,
    input_func: InputFunc = input,
) -> bool:
    """确保用户已同意当前版本 EULA。"""

    eula_path = project_root / "eula.md"
    eula_content, eula_hash = _load_document(eula_path)
    store = JSONStore(_resolve_agreement_state_dir(config, project_root))
    state = await store.load(_STATE_FILE_NAME) or {}
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

    state["eula"] = {
        "document_path": str(eula_path),
        "document_sha256": eula_hash,
        "accepted_at": time.time(),
    }
    await store.save(_STATE_FILE_NAME, state)
    ui.display_success("EULA 已确认。")
    return True


async def ensure_cloud_telemetry_consent(
    config: CoreConfig,
    project_root: Path,
    ui: ConsoleUIManager,
    *,
    input_func: InputFunc = input,
) -> None:
    """在需要时确认云端遥测隐私协议。"""

    if not config.cloud_telemetry.client_enabled:
        return

    identity_store = CloudTelemetryIdentityStore(
        storage_dir=str(_resolve_identity_storage_dir(config, project_root))
    )
    identity_state = await identity_store.ensure()
    if identity_state.consent_state == CONSENT_GRANTED:
        return
    if identity_state.consent_state == CONSENT_REVOKED:
        ui.display_info(
            "你此前未同意遥测隐私协议，本次启动将保持云端遥测关闭。",
            title="云端遥测",
        )
        return

    privacy_path = project_root / "PRIVACY.md"
    privacy_content, _ = _load_document(privacy_path)
    agreed = _prompt_for_choice(
        ui,
        title="云端遥测",
        required_message=(
            "当前配置尝试启用云端遥测，但只有在你明确同意遥测隐私协议后才会真正启用。"
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
        ui.display_success("你已同意遥测隐私协议，云端遥测可按配置启用。")
        return

    await identity_store.set_consent(
        CONSENT_REVOKED,
        allow_ip_retention=False,
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
        input_func=input_func,
    )
    return True


__all__ = [
    "ensure_cloud_telemetry_consent",
    "ensure_eula_accepted",
    "ensure_startup_agreements",
]
