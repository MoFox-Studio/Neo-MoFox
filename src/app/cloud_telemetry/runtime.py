"""云端遥测运行时装配与状态访问。"""

from __future__ import annotations

from typing import Any

from src.core.config import CoreConfig
from src.kernel.telemetry.cloud import CONSENT_GRANTED

from .bootstrap import (
    CloudTelemetryFoundation,
    build_cloud_telemetry_identity_store,
)
from .client import CloudTelemetryClient, CloudTelemetryClientConfig, CloudTelemetryPendingQueue

CLOUD_TELEMETRY_INGEST_BASE_URL = "http://127.0.0.1:8765/_cloud_telemetry"

_runtime_foundation: CloudTelemetryFoundation | None = None
_runtime_client: CloudTelemetryClient | None = None


def _build_local_cloud_telemetry_foundation(
    core_config: CoreConfig | None = None,
) -> CloudTelemetryFoundation:
    """构建仅包含本地客户端所需能力的云端遥测基础设施。"""

    from src.core.config import get_core_config

    config = core_config or get_core_config()
    return CloudTelemetryFoundation(
        core_config=config,
        identity_store=build_cloud_telemetry_identity_store(config),
    )


async def initialize_cloud_telemetry_runtime(
    foundation: CloudTelemetryFoundation | CoreConfig | None = None,
) -> CloudTelemetryFoundation:
    """初始化全局云端遥测运行时。"""

    global _runtime_foundation, _runtime_client

    core_config: CoreConfig | None = None
    effective_foundation: CloudTelemetryFoundation | None = None

    if isinstance(foundation, CloudTelemetryFoundation):
        effective_foundation = foundation
    elif isinstance(foundation, CoreConfig):
        core_config = foundation

    if effective_foundation is None:
        effective_foundation = _build_local_cloud_telemetry_foundation(core_config)

    _runtime_foundation = effective_foundation

    if core_config is None:
        core_config = effective_foundation.core_config

    cloud_cfg = core_config.cloud_telemetry
    identity_state = await effective_foundation.identity_store.ensure()
    effective_client_enabled = (
        cloud_cfg.client_enabled
        and identity_state.consent_state == CONSENT_GRANTED
    )

    pending_queue = CloudTelemetryPendingQueue(
        max_bytes=cloud_cfg.pending_queue_max_bytes,
        max_windows=cloud_cfg.pending_queue_max_windows,
    )
    _runtime_client = CloudTelemetryClient(
        CloudTelemetryClientConfig(
            enabled=effective_client_enabled,
            ingest_base_url=CLOUD_TELEMETRY_INGEST_BASE_URL,
            pending_queue_max_bytes=cloud_cfg.pending_queue_max_bytes,
            pending_queue_max_windows=cloud_cfg.pending_queue_max_windows,
            default_heartbeat_interval_seconds=cloud_cfg.default_heartbeat_interval_seconds,
            send_timeout_seconds=cloud_cfg.send_timeout_seconds,
            trust_env=core_config.advanced.trust_env,
        ),
        effective_foundation.identity_store,
        pending_queue,
    )
    return effective_foundation


def get_cloud_telemetry_runtime() -> CloudTelemetryFoundation | None:
    """获取全局云端遥测运行时。"""

    return _runtime_foundation


def get_cloud_telemetry_client() -> CloudTelemetryClient | None:
    """获取全局云端遥测客户端。"""

    return _runtime_client


async def get_cloud_telemetry_status_summary() -> dict[str, Any]:
    """返回云端遥测本地状态摘要。"""

    foundation = get_cloud_telemetry_runtime()
    client = get_cloud_telemetry_client()
    if foundation is None:
        return {
            "initialized": False,
            "client_enabled": False,
            "client_instance_id": None,
            "consent_state": "unknown",
            "allow_ip_retention": True,
            "credential_present": False,
            "last_registered_at": None,
            "credential_expires_at": None,
            "pending_window_count": 0,
            "pending_bytes": 0,
            "last_send_status": None,
            "last_send_error": None,
            "last_send_at": None,
            "next_heartbeat_interval_seconds": None,
            "loop_running": False,
            "instance_status": "active",
            "last_rejection_reason": None,
            "last_window_results": [],
        }

    identity_state = await foundation.identity_store.load()
    client_status = await client.get_status_summary() if client is not None else {
        "client_enabled": False,
        "pending_window_count": 0,
        "pending_bytes": 0,
        "last_send_status": None,
        "last_send_error": None,
        "last_send_at": None,
        "next_heartbeat_interval_seconds": None,
        "loop_running": False,
        "instance_status": "active",
        "last_rejection_reason": None,
        "last_window_results": [],
    }
    return {
        "initialized": True,
        "client_enabled": client_status["client_enabled"],
        "client_instance_id": (
            identity_state.client_instance_id if identity_state is not None else None
        ),
        "consent_state": (
            identity_state.consent_state if identity_state is not None else "unknown"
        ),
        "allow_ip_retention": (
            identity_state.allow_ip_retention if identity_state is not None else True
        ),
        "credential_present": bool(
            identity_state is not None and identity_state.install_credential
        ),
        "last_registered_at": (
            identity_state.last_registered_at if identity_state is not None else None
        ),
        "credential_expires_at": (
            identity_state.credential_expires_at if identity_state is not None else None
        ),
        "pending_window_count": client_status["pending_window_count"],
        "pending_bytes": client_status["pending_bytes"],
        "last_send_status": client_status["last_send_status"],
        "last_send_error": client_status["last_send_error"],
        "last_send_at": client_status["last_send_at"],
        "next_heartbeat_interval_seconds": client_status["next_heartbeat_interval_seconds"],
        "loop_running": client_status["loop_running"],
        "instance_status": client_status.get("instance_status", "active"),
        "last_rejection_reason": client_status.get("last_rejection_reason"),
        "last_window_results": client_status.get("last_window_results", []),
    }


async def close_cloud_telemetry_runtime() -> None:
    """关闭全局云端遥测运行时。"""

    global _runtime_foundation, _runtime_client
    if _runtime_client is not None:
        await _runtime_client.stop()
        _runtime_client = None
    _runtime_foundation = None
