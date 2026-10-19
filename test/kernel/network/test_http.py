"""共享 httpx 网络辅助函数测试。"""

from __future__ import annotations

import httpx

from src.kernel.network import build_httpx_timeout, build_httpx_transport


def test_build_httpx_timeout_uses_provider_shared_policy() -> None:
    """共享 timeout 同时设置 total/connect/read/write/pool。"""
    timeout = build_httpx_timeout(20.0)

    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.read == 20.0
    assert timeout.write == 20.0
    assert timeout.pool == 5.0


def test_build_httpx_timeout_rejects_non_positive_values() -> None:
    """未启用 timeout 时返回 None。"""
    assert build_httpx_timeout(None) is None
    assert build_httpx_timeout(0) is None
    assert build_httpx_timeout(-1) is None


def test_build_httpx_transport_only_overrides_ipv4_mode() -> None:
    """默认不接管 httpx transport，从而保留 trust_env 代理行为。"""
    assert build_httpx_transport(force_ipv4=False) is None
    transport = build_httpx_transport(force_ipv4=True)
    assert isinstance(transport, httpx.AsyncHTTPTransport)
