"""云端遥测 app 层装配入口。"""

from .bootstrap import (
    CloudTelemetryFoundation,
    build_cloud_telemetry_identity_store,
    initialize_cloud_telemetry_foundation,
)
from .client import (
    CloudTelemetryClient,
    CloudTelemetryClientConfig,
    CloudTelemetryPendingQueue,
    CloudTelemetryQueueState,
)
from .runtime import (
    close_cloud_telemetry_runtime,
    get_cloud_telemetry_client,
    get_cloud_telemetry_runtime,
    get_cloud_telemetry_status_summary,
    initialize_cloud_telemetry_runtime,
)

__all__ = [
    "CloudTelemetryClient",
    "CloudTelemetryClientConfig",
    "CloudTelemetryFoundation",
    "CloudTelemetryPendingQueue",
    "CloudTelemetryQueueState",
    "build_cloud_telemetry_identity_store",
    "close_cloud_telemetry_runtime",
    "get_cloud_telemetry_client",
    "get_cloud_telemetry_runtime",
    "get_cloud_telemetry_status_summary",
    "initialize_cloud_telemetry_foundation",
    "initialize_cloud_telemetry_runtime",
]