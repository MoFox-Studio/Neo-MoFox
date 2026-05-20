"""云端遥测客户端队列与发送器。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from copy import deepcopy
import json
import platform as platform_module
import time
from typing import Any
from uuid import uuid4

import httpx

from src.core.config import CORE_VERSION
from src.kernel.concurrency import get_task_manager
from src.kernel.llm.stats import get_llm_stats_collector
from src.kernel.telemetry import get_telemetry_collector
from src.kernel.telemetry.cloud import CONSENT_GRANTED, CloudTelemetryIdentityStore

# 诊断事件 summary 长度上限（字符），避免日志正文外泄
_DIAGNOSTIC_SUMMARY_MAX_CHARS = 200
# 允许进入诊断事件 attributes 的白名单字段
_DIAGNOSTIC_ATTRIBUTE_ALLOWLIST: frozenset[str] = frozenset({"domain", "entity_id"})
# 后台返回的实例状态枚举
_INSTANCE_STATUS_ACTIVE = "active"
_INSTANCE_STATUS_SUSPENDED = "suspended"
# 可被认为已结案的窗口确认状态（无需保留在待发送队列）
_REMOVABLE_ACK_STATUSES: frozenset[str] = frozenset(
    {"accepted", "duplicate", "rejected_permanent"}
)


@dataclass(slots=True)
class CloudTelemetryClientConfig:
    """云端遥测客户端配置。"""

    enabled: bool = False
    ingest_base_url: str = ""
    pending_queue_max_bytes: int = 524288
    pending_queue_max_windows: int = 128
    default_heartbeat_interval_seconds: float = 300.0
    send_timeout_seconds: float = 10.0
    trust_env: bool = True


@dataclass(slots=True)
class CloudTelemetryPendingWindow:
    """待发送心跳窗口。"""

    window_sequence: int
    window_started_at: float
    window_ended_at: float
    payload_bytes: int
    summary: dict[str, Any]
    diagnostic_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CloudTelemetryPendingWindow":
        """从字典恢复窗口。"""

        return cls(
            window_sequence=int(data["window_sequence"]),
            window_started_at=float(data["window_started_at"]),
            window_ended_at=float(data["window_ended_at"]),
            payload_bytes=int(data.get("payload_bytes", 0)),
            summary=dict(data.get("summary", {})),
            diagnostic_events=list(data.get("diagnostic_events", [])),
        )


@dataclass(slots=True)
class CloudTelemetryQueueState:
    """待发送窗口队列状态。"""

    next_window_sequence: int = 1
    pending_windows: list[CloudTelemetryPendingWindow] = field(default_factory=list)
    next_heartbeat_interval_seconds: float = 300.0
    last_collected_at: float | None = None
    last_send_at: float | None = None
    last_send_status: str | None = None
    last_send_error: str | None = None
    instance_status: str = _INSTANCE_STATUS_ACTIVE
    last_rejection_reason: str | None = None
    last_window_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""

        return {
            "next_window_sequence": self.next_window_sequence,
            "pending_windows": [window.to_dict() for window in self.pending_windows],
            "next_heartbeat_interval_seconds": self.next_heartbeat_interval_seconds,
            "last_collected_at": self.last_collected_at,
            "last_send_at": self.last_send_at,
            "last_send_status": self.last_send_status,
            "last_send_error": self.last_send_error,
            "instance_status": self.instance_status,
            "last_rejection_reason": self.last_rejection_reason,
            "last_window_results": list(self.last_window_results),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CloudTelemetryQueueState":
        """从字典恢复队列状态。"""

        return cls(
            next_window_sequence=int(data.get("next_window_sequence", 1)),
            pending_windows=[
                CloudTelemetryPendingWindow.from_dict(item)
                for item in data.get("pending_windows", [])
            ],
            next_heartbeat_interval_seconds=float(
                data.get("next_heartbeat_interval_seconds", 300.0)
            ),
            last_collected_at=_coerce_optional_float(data.get("last_collected_at")),
            last_send_at=_coerce_optional_float(data.get("last_send_at")),
            last_send_status=data.get("last_send_status"),
            last_send_error=data.get("last_send_error"),
            instance_status=str(data.get("instance_status", _INSTANCE_STATUS_ACTIVE)),
            last_rejection_reason=data.get("last_rejection_reason"),
            last_window_results=list(data.get("last_window_results", [])),
        )


def _coerce_optional_float(value: Any) -> float | None:
    """将可选值转换为浮点数。"""

    if value is None:
        return None
    return float(value)


def _truncate_summary(summary: Any) -> str:
    """按白名单上限截断诊断事件 summary。"""

    if summary is None:
        return ""
    text = str(summary)
    if len(text) <= _DIAGNOSTIC_SUMMARY_MAX_CHARS:
        return text
    return text[:_DIAGNOSTIC_SUMMARY_MAX_CHARS] + "…"


def _filter_attributes(event: dict[str, Any]) -> dict[str, Any]:
    """仅保留白名单内的属性字段。"""

    allowed: dict[str, Any] = {}
    for field_name in _DIAGNOSTIC_ATTRIBUTE_ALLOWLIST:
        value = event.get(field_name)
        if value is None:
            continue
        # 字段值统一转为字符串，避免泄漏复杂结构
        allowed[field_name] = str(value)
    return allowed


def _build_diagnostic_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """从原始事件构建受控诊断事件载荷。"""

    return {
        "event_name": str(event.get("event_name", "")),
        "severity": str(event.get("severity", "info")),
        "event_at": float(event.get("timestamp") or time.time()),
        "summary": _truncate_summary(event.get("summary") or ""),
        "attributes": _filter_attributes(event),
    }


def _get_app_version() -> str:
    """返回当前项目版本号。"""

    return CORE_VERSION


def _get_platform_info() -> str:
    """返回当前系统平台信息，供服务端按真实运行环境聚合。"""

    return (
        f"{platform_module.system()} {platform_module.release()} "
        f"({platform_module.machine()}); Python {platform_module.python_version()}"
    )


def _build_client_metadata() -> dict[str, str]:
    """构造注册和心跳请求共享的客户端元信息。"""

    return {
        "app_version": _get_app_version(),
        "platform": _get_platform_info(),
    }


def _build_runtime_health_payload() -> dict[str, Any]:
    """收集运行时健康统计，失败时降级为空对象。"""

    payload: dict[str, Any] = {}
    try:
        from src.kernel.concurrency import get_watchdog

        payload["watchdog"] = get_watchdog().get_stats()
    except Exception:
        payload["watchdog"] = {}
    try:
        payload["task_manager"] = get_task_manager().get_stats()
    except Exception:
        payload["task_manager"] = {}
    try:
        from src.core.transport.distribution.stream_loop_manager import (
            get_stream_loop_manager,
        )

        payload["stream_loop_manager"] = get_stream_loop_manager().get_stats()
    except Exception:
        payload["stream_loop_manager"] = {}
    return payload


class CloudTelemetryPendingQueue:
    """待发送窗口内存队列。

    遥测窗口仅保留在当前进程内，避免本地持久化遥测数据。
    """

    def __init__(
        self,
        *,
        max_bytes: int,
        max_windows: int,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_windows = max_windows
        self._state = CloudTelemetryQueueState()
        self._state_lock = asyncio.Lock()

    async def load_state(self) -> CloudTelemetryQueueState:
        """加载队列状态。"""
        async with self._state_lock:
            return self._clone_state(self._state)

    async def save_state(self, state: CloudTelemetryQueueState) -> CloudTelemetryQueueState:
        """保存队列状态。"""
        async with self._state_lock:
            self._state = self._clone_state(state)
            return self._clone_state(self._state)

    async def align_next_window_sequence(
        self,
        next_window_sequence: int | None,
    ) -> CloudTelemetryQueueState:
        """Align unsent windows to the server-side monotonic sequence floor."""

        if next_window_sequence is None:
            return await self.load_state()

        sequence_floor = max(1, int(next_window_sequence))
        state = await self.load_state()
        if not state.pending_windows:
            state.next_window_sequence = max(state.next_window_sequence, sequence_floor)
            return await self.save_state(state)

        min_pending_sequence = min(
            window.window_sequence for window in state.pending_windows
        )
        if min_pending_sequence < sequence_floor:
            for offset, window in enumerate(state.pending_windows):
                window.window_sequence = sequence_floor + offset
            state.next_window_sequence = sequence_floor + len(state.pending_windows)
        else:
            max_pending_sequence = max(
                window.window_sequence for window in state.pending_windows
            )
            state.next_window_sequence = max(
                state.next_window_sequence,
                sequence_floor,
                max_pending_sequence + 1,
            )
        return await self.save_state(state)

    async def enqueue_window(
        self,
        *,
        window_started_at: float,
        window_ended_at: float,
        summary: dict[str, Any],
        diagnostic_events: list[dict[str, Any]],
    ) -> CloudTelemetryPendingWindow:
        """入队新的待发送窗口。"""

        state = await self.load_state()
        payload_bytes = len(
            json.dumps(
                {"summary": summary, "diagnostic_events": diagnostic_events},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
        window = CloudTelemetryPendingWindow(
            window_sequence=state.next_window_sequence,
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            payload_bytes=payload_bytes,
            summary=summary,
            diagnostic_events=diagnostic_events,
        )
        state.next_window_sequence += 1
        state.pending_windows.append(window)
        state.last_collected_at = window_ended_at
        self._trim_state(state)
        await self.save_state(state)
        return window

    async def get_windows(self, limit: int | None = None) -> list[CloudTelemetryPendingWindow]:
        """返回待发送窗口列表。"""

        state = await self.load_state()
        if limit is None:
            return list(state.pending_windows)
        return list(state.pending_windows[:limit])

    async def acknowledge(
        self,
        results: list[dict[str, Any]],
        *,
        sent_at: float,
        next_interval_seconds: float | None = None,
        instance_status: str | None = None,
    ) -> CloudTelemetryQueueState:
        """按逐窗口确认结果更新队列。"""

        state = await self.load_state()
        removable_sequences = {
            int(result["window_sequence"])
            for result in results
            if result.get("status") in _REMOVABLE_ACK_STATUSES
        }
        rejection_reasons = [
            str(result.get("reason"))
            for result in results
            if result.get("status")
            in {"rejected_retryable", "rejected_permanent"}
            and result.get("reason")
        ]
        state.pending_windows = [
            window
            for window in state.pending_windows
            if window.window_sequence not in removable_sequences
        ]
        state.last_send_at = sent_at
        state.last_send_status = "success"
        state.last_send_error = None
        state.last_window_results = list(results)
        state.last_rejection_reason = rejection_reasons[-1] if rejection_reasons else None
        if next_interval_seconds is not None:
            state.next_heartbeat_interval_seconds = float(next_interval_seconds)
        if instance_status is not None:
            state.instance_status = str(instance_status)
        await self.save_state(state)
        return state

    async def mark_send_failure(
        self,
        error: str,
        *,
        sent_at: float,
    ) -> CloudTelemetryQueueState:
        """记录发送失败状态。"""

        state = await self.load_state()
        state.last_send_at = sent_at
        state.last_send_status = "failed"
        state.last_send_error = error
        await self.save_state(state)
        return state

    async def mark_instance_status(self, instance_status: str) -> CloudTelemetryQueueState:
        """更新实例状态。"""

        state = await self.load_state()
        state.instance_status = str(instance_status)
        await self.save_state(state)
        return state

    async def get_status_summary(self) -> dict[str, Any]:
        """返回队列状态摘要。"""

        state = await self.load_state()
        return {
            "pending_window_count": len(state.pending_windows),
            "pending_bytes": self._current_bytes(state),
            "next_window_sequence": state.next_window_sequence,
            "next_heartbeat_interval_seconds": state.next_heartbeat_interval_seconds,
            "last_collected_at": state.last_collected_at,
            "last_send_at": state.last_send_at,
            "last_send_status": state.last_send_status,
            "last_send_error": state.last_send_error,
            "instance_status": state.instance_status,
            "last_rejection_reason": state.last_rejection_reason,
            "last_window_results": list(state.last_window_results),
        }

    def _trim_state(self, state: CloudTelemetryQueueState) -> None:
        """按窗口数量与字节上限修剪队列。"""

        while len(state.pending_windows) > self._max_windows:
            state.pending_windows.pop(0)

        while self._current_bytes(state) > self._max_bytes and state.pending_windows:
            state.pending_windows.pop(0)

    @staticmethod
    def _current_bytes(state: CloudTelemetryQueueState) -> int:
        """计算当前队列总字节数。"""

        return sum(window.payload_bytes for window in state.pending_windows)

    @staticmethod
    def _clone_state(state: CloudTelemetryQueueState) -> CloudTelemetryQueueState:
        """克隆队列状态，避免外部原地修改内部状态。"""

        return CloudTelemetryQueueState.from_dict(deepcopy(state.to_dict()))


class CloudTelemetryClient:
    """云端遥测客户端发送器。"""

    def __init__(
        self,
        config: CloudTelemetryClientConfig,
        identity_store: CloudTelemetryIdentityStore,
        pending_queue: CloudTelemetryPendingQueue,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._identity_store = identity_store
        self._pending_queue = pending_queue
        self._transport = transport
        self._running = False
        self._task_id: str | None = None

    @property
    def enabled(self) -> bool:
        """返回客户端是否启用。"""

        return bool(self._config.enabled and self._config.ingest_base_url.strip())

    async def capture_snapshot_window(self) -> CloudTelemetryPendingWindow | None:
        """从本地统计中采集一个待发送窗口。"""

        identity = await self._identity_store.ensure()
        if identity.consent_state != CONSENT_GRANTED:
            return None

        # 实例已被服务端停传时不再采集窗口
        queue_state = await self._pending_queue.load_state()
        if queue_state.instance_status == _INSTANCE_STATUS_SUSPENDED:
            return None

        telemetry_collector = get_telemetry_collector()
        llm_collector = get_llm_stats_collector()
        now = time.time()
        window_started_at = queue_state.last_collected_at or (
            now - float(queue_state.next_heartbeat_interval_seconds)
        )

        telemetry_window = await telemetry_collector.consume_window(limit=20)
        llm_summary = await llm_collector.get_summary()
        llm_request_name_top = await llm_collector.get_request_name_window_summary(
            start_ts=window_started_at,
            end_ts=now,
            limit=10,
        )
        diagnostic_events = [
            _build_diagnostic_event_payload(event)
            for event in telemetry_window["recent"]
            if event.get("severity") in {"warning", "error", "critical"}
        ]
        summary = {
            "telemetry_summary": telemetry_window["summary"],
            "telemetry_domains": telemetry_window["domains"],
            "llm_summary": llm_summary,
            "llm_request_name_top": llm_request_name_top,
            "runtime_health": _build_runtime_health_payload(),
        }
        return await self._pending_queue.enqueue_window(
            window_started_at=window_started_at,
            window_ended_at=now,
            summary=summary,
            diagnostic_events=diagnostic_events,
        )

    async def run_once(self, *, collect_window: bool = True) -> dict[str, Any]:
        """执行一次注册/上线心跳/批量心跳发送。"""

        identity = await self._identity_store.ensure()
        if identity.consent_state != CONSENT_GRANTED:
            return {"ok": False, "reason": "consent_not_granted"}
        if not self.enabled:
            return {"ok": False, "reason": "client_disabled"}

        queue_state = await self._pending_queue.load_state()
        if queue_state.instance_status == _INSTANCE_STATUS_SUSPENDED:
            return {"ok": False, "reason": "instance_suspended"}

        identity = await self._ensure_registered(identity)
        if identity.install_credential is None:
            return {"ok": False, "reason": "registration_failed"}

        if collect_window:
            await self.capture_snapshot_window()

        windows = await self._pending_queue.get_windows()
        if not windows:
            return {"ok": True, "reason": "idle"}

        response = await self._post_json(
            "/api/heartbeats/batch",
            {
                "request_id": uuid4().hex,
                "client_instance_id": identity.client_instance_id,
                "install_credential": identity.install_credential,
                "windows": [window.to_dict() for window in windows],
            },
        )

        if response.status_code == 403:
            await self._identity_store.clear_install_credential()
            await self._pending_queue.mark_send_failure(
                "invalid install credential",
                sent_at=time.time(),
            )
            return {"ok": False, "reason": "invalid_install_credential"}

        response.raise_for_status()
        payload = response.json()
        instance_status = payload.get("instance_status")
        await self._pending_queue.acknowledge(
            payload["window_results"],
            sent_at=time.time(),
            next_interval_seconds=payload.get("next_heartbeat_interval_seconds"),
            instance_status=instance_status,
        )
        await self._pending_queue.align_next_window_sequence(
            payload.get("next_window_sequence")
        )
        result: dict[str, Any] = {
            "ok": True,
            "reason": "sent",
            "accepted": payload["accepted_window_count"],
            "duplicate": payload.get("duplicate_window_count", 0),
            "rejected": payload.get("rejected_window_count", 0),
        }
        if instance_status == _INSTANCE_STATUS_SUSPENDED:
            result["instance_status"] = _INSTANCE_STATUS_SUSPENDED
        return result

    async def start(self) -> None:
        """启动后台发送循环。"""

        if self._running:
            return
        self._running = True
        task_info = get_task_manager().create_task(
            self._run_loop(),
            name="cloud_telemetry_sender",
            daemon=True,
        )
        self._task_id = task_info.task_id

    async def stop(self) -> None:
        """停止后台发送循环。"""

        self._running = False
        if self._task_id is None:
            return

        task_manager = get_task_manager()
        task_manager.cancel_task(self._task_id)
        try:
            task_info = task_manager.get_task(self._task_id)
            if task_info.task is not None:
                await task_info.task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._task_id = None

    async def get_status_summary(self) -> dict[str, Any]:
        """返回客户端状态摘要。"""

        status = await self._pending_queue.get_status_summary()
        status.update({
            "client_enabled": self._config.enabled,
            "ingest_base_url": self._config.ingest_base_url,
            "loop_running": self._running,
        })
        return status

    async def _ensure_registered(self, identity_state):
        """确保安装实例已注册。"""

        if identity_state.install_credential:
            return identity_state

        challenge_response = await self._post_json(
            "/api/register/challenge",
            {
                "client_instance_id": identity_state.client_instance_id,
                **_build_client_metadata(),
            },
        )
        challenge_response.raise_for_status()
        challenge_payload = challenge_response.json()

        register_response = await self._post_json(
            "/api/register",
            {
                "client_instance_id": identity_state.client_instance_id,
                "challenge_id": challenge_payload["challenge_id"],
                "challenge_token": challenge_payload["challenge_token"],
                "allow_ip_retention": identity_state.allow_ip_retention,
                **_build_client_metadata(),
            },
        )
        register_response.raise_for_status()
        register_payload = register_response.json()
        await self._pending_queue.align_next_window_sequence(
            register_payload.get("next_window_sequence")
        )
        return await self._identity_store.set_install_credential(
            register_payload["install_credential"],
            issued_at=register_payload["credential_issued_at"],
            expires_at=register_payload["credential_expires_at"],
            registered_at=register_payload["server_time"],
        )
    async def _run_loop(self) -> None:
        """后台发送循环。"""

        while self._running:
            try:
                result = await self.run_once(collect_window=True)
                if result.get("reason") == "instance_suspended":
                    # 服务端已停传，不再继续轮询，等待外部干预
                    self._running = False
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._pending_queue.mark_send_failure(str(exc), sent_at=time.time())

            state = await self._pending_queue.load_state()
            await asyncio.sleep(max(float(state.next_heartbeat_interval_seconds), 0.05))

    async def _post_json(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        """发送 JSON POST 请求。"""

        base_url = self._config.ingest_base_url.rstrip("/")
        url = f"{base_url}{path}"
        async with httpx.AsyncClient(
            timeout=self._config.send_timeout_seconds,
            trust_env=self._config.trust_env,
            transport=self._transport,
        ) as client:
            return await client.post(url, json=payload)
