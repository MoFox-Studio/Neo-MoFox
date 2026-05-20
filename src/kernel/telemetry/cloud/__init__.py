"""云端遥测客户端本地身份能力。"""

from .identity import (
    CONSENT_GRANTED,
    CONSENT_REVOKED,
    CONSENT_UNKNOWN,
    CloudTelemetryIdentityState,
    CloudTelemetryIdentityStore,
)

__all__ = [
    "CONSENT_GRANTED",
    "CONSENT_REVOKED",
    "CONSENT_UNKNOWN",
    "CloudTelemetryIdentityState",
    "CloudTelemetryIdentityStore",
]