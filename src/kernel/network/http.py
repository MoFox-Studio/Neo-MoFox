"""可供多个 kernel provider 复用的 httpx 传输配置。"""

from __future__ import annotations

from typing import Any


def build_httpx_timeout(timeout: float | None) -> Any:
    """按统一策略构造 ``httpx.Timeout``，非法或非正值返回 None。"""
    import httpx

    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        return None
    total = float(timeout)
    if total <= 0:
        return None
    return httpx.Timeout(
        timeout=total,
        connect=min(total, 10.0),
        read=total,
        write=total,
        pool=min(total, 5.0),
    )


def build_httpx_transport(*, force_ipv4: bool) -> Any:
    """构造统一的 AsyncHTTPTransport；默认保留 httpx 的环境代理行为。"""
    import httpx

    if force_ipv4:
        return httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return None
