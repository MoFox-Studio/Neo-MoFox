"""云端遥测本地身份持久化。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any
from uuid import uuid4

from src.kernel.storage import JSONStore

CONSENT_UNKNOWN = "unknown"
CONSENT_GRANTED = "granted"
CONSENT_REVOKED = "revoked"

_VALID_CONSENT_STATES = {
    CONSENT_UNKNOWN,
    CONSENT_GRANTED,
    CONSENT_REVOKED,
}


@dataclass(slots=True)
class CloudTelemetryIdentityState:
    """云端遥测本地身份状态。"""

    client_instance_id: str
    consent_state: str = CONSENT_UNKNOWN
    allow_ip_retention: bool = True
    install_credential: str | None = None
    credential_issued_at: float | None = None
    credential_expires_at: float | None = None
    last_registered_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """将身份状态转换为可序列化字典。"""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CloudTelemetryIdentityState":
        """从字典恢复身份状态。"""

        state = cls(
            client_instance_id=str(data["client_instance_id"]),
            consent_state=str(data.get("consent_state", CONSENT_UNKNOWN)),
            allow_ip_retention=bool(data.get("allow_ip_retention", True)),
            install_credential=data.get("install_credential"),
            credential_issued_at=_coerce_optional_float(data.get("credential_issued_at")),
            credential_expires_at=_coerce_optional_float(data.get("credential_expires_at")),
            last_registered_at=_coerce_optional_float(data.get("last_registered_at")),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )
        _validate_consent_state(state.consent_state)
        return state


def _coerce_optional_float(value: Any) -> float | None:
    """将可选值转换为浮点时间戳。"""

    if value is None:
        return None
    return float(value)


def _validate_consent_state(consent_state: str) -> None:
    """校验同意状态是否合法。"""

    if consent_state not in _VALID_CONSENT_STATES:
        raise ValueError(f"Invalid consent state: {consent_state}")


class CloudTelemetryIdentityStore:
    """云端遥测本地身份持久化服务。"""

    def __init__(
        self,
        storage_dir: str = "data/cloud_telemetry/state",
        file_name: str = "identity",
    ) -> None:
        """初始化本地身份持久化服务。"""

        self._store = JSONStore(storage_dir=storage_dir)
        self._file_name = file_name

    async def load(self) -> CloudTelemetryIdentityState | None:
        """加载本地身份状态。"""

        data = await self._store.load(self._file_name)
        if data is None:
            return None
        return CloudTelemetryIdentityState.from_dict(data)

    async def save(self, state: CloudTelemetryIdentityState) -> CloudTelemetryIdentityState:
        """保存本地身份状态。"""

        _validate_consent_state(state.consent_state)
        if state.created_at <= 0:
            state.created_at = time.time()
        state.updated_at = time.time()
        await self._store.save(self._file_name, state.to_dict())
        return state

    async def ensure(self) -> CloudTelemetryIdentityState:
        """确保本地身份状态存在，不存在时自动生成。"""

        existing = await self.load()
        if existing is not None:
            return existing

        now = time.time()
        state = CloudTelemetryIdentityState(
            client_instance_id=uuid4().hex,
            created_at=now,
            updated_at=now,
        )
        await self.save(state)
        return state

    async def set_consent(
        self,
        consent_state: str,
        *,
        allow_ip_retention: bool | None = None,
    ) -> CloudTelemetryIdentityState:
        """更新同意状态和来源 IP 保留偏好。"""

        _validate_consent_state(consent_state)
        state = await self.ensure()
        state.consent_state = consent_state
        if allow_ip_retention is not None:
            state.allow_ip_retention = allow_ip_retention
        return await self.save(state)

    async def set_install_credential(
        self,
        credential: str,
        *,
        issued_at: float | None = None,
        expires_at: float | None = None,
        registered_at: float | None = None,
    ) -> CloudTelemetryIdentityState:
        """设置安装实例凭证信息。"""

        state = await self.ensure()
        state.install_credential = credential
        state.credential_issued_at = issued_at if issued_at is not None else time.time()
        state.credential_expires_at = expires_at
        state.last_registered_at = registered_at if registered_at is not None else time.time()
        return await self.save(state)

    async def clear_install_credential(self) -> CloudTelemetryIdentityState:
        """清除安装实例凭证，但保留稳定的客户端实例 ID。"""

        state = await self.ensure()
        state.install_credential = None
        state.credential_issued_at = None
        state.credential_expires_at = None
        return await self.save(state)
