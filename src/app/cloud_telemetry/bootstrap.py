"""云端遥测 app 层装配逻辑。"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.config import CoreConfig, get_core_config
from src.kernel.telemetry.cloud.identity import CloudTelemetryIdentityStore


@dataclass(slots=True)
class CloudTelemetryFoundation:
    """云端遥测基础设施装配结果。"""

    core_config: CoreConfig
    identity_store: CloudTelemetryIdentityStore


def build_cloud_telemetry_identity_store(
    core_config: CoreConfig | None = None,
) -> CloudTelemetryIdentityStore:
    """从 CoreConfig 构建云端遥测本地身份持久化服务。"""

    config = core_config or get_core_config()
    return CloudTelemetryIdentityStore(storage_dir=config.cloud_telemetry.identity_storage_dir)


async def initialize_cloud_telemetry_foundation(
    core_config: CoreConfig | None = None,
) -> CloudTelemetryFoundation:
    """初始化仅供本地客户端使用的云端遥测基础设施。"""

    config = core_config or get_core_config()
    identity_store = build_cloud_telemetry_identity_store(config)

    return CloudTelemetryFoundation(
        core_config=config,
        identity_store=identity_store,
    )
