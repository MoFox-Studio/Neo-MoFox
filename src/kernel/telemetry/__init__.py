"""通用遥测模块。"""

from .collector import TelemetryCollector, TelemetryEventRecord, anonymize_identifier
from .config import TelemetryConfig
from .database import close_telemetry_db, get_telemetry_collector, init_telemetry

__all__ = [
    "TelemetryCollector",
    "TelemetryConfig",
    "TelemetryEventRecord",
    "anonymize_identifier",
    "close_telemetry_db",
    "get_telemetry_collector",
    "init_telemetry",
]