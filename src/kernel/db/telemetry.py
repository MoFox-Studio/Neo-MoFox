"""数据库遥测辅助。"""

from __future__ import annotations

from typing import Any

from src.kernel.telemetry import TelemetryEventRecord, get_telemetry_collector


async def record_db_lifecycle_event(
    *,
    event_name: str,
    summary: str,
    severity: str = "info",
    attributes: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """记录数据库生命周期事件。"""
    collector = get_telemetry_collector()
    if not collector.is_domain_enabled("db"):
        return

    await collector.record(
        TelemetryEventRecord(
            domain="db",
            event_name=event_name,
            severity=severity,
            summary=summary,
            attributes=attributes or {},
            detail=detail,
        )
    )


async def record_db_operation_event(
    *,
    operation: str,
    model_name: str,
    duration_ms: float,
    custom_session_factory: bool,
    success: bool,
    result_count: int | None = None,
    error: Exception | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """记录数据库操作事件。

    默认只记录失败和慢查询，不记录快速成功操作。
    """
    collector = get_telemetry_collector()
    if not collector.is_domain_enabled("db"):
        return

    payload = {
        "operation": operation,
        "model_name": model_name,
        "duration_ms": round(duration_ms, 3),
        "custom_session_factory": custom_session_factory,
    }
    if result_count is not None:
        payload["result_count"] = result_count
    if attributes:
        payload.update(attributes)

    if success:
        if duration_ms < collector.slow_query_threshold_ms:
            return
        await collector.record(
            TelemetryEventRecord(
                domain="db",
                event_name="slow_query",
                severity="warning",
                summary=f"slow {operation} on {model_name}",
                attributes=payload,
            )
        )
        return

    assert error is not None
    await collector.record(
        TelemetryEventRecord(
            domain="db",
            event_name="db_operation_error",
            severity="error",
            summary=f"{operation} failed on {model_name}",
            attributes={
                **payload,
                "error_type": type(error).__name__,
            },
            detail={"message": str(error)},
        )
    )