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


def test_explicit_transport_disables_env_proxy(monkeypatch) -> None:
    """显式 transport 会让 httpx 忽略环境代理，因此默认必须传 None。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")

    with_proxy = httpx.AsyncClient(
        transport=build_httpx_transport(force_ipv4=False),
        trust_env=True,
    )
    without_proxy = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(),
        trust_env=True,
    )

    # transport=None 时 httpx 才会装载环境代理；显式 transport 会静默禁用它。
    assert with_proxy._mounts
    assert not without_proxy._mounts
