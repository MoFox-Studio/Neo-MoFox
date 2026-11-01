"""Jev 客户端与插件 API 的兜底分支测试。

覆盖既有套件未触达的几处边界：事件总线完全不可用时的降级、真实传输层
构造，以及插件公共 API 的参数互斥校验。全部离线执行。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from src.app.plugin_system.api import jev_api
from src.kernel.jev import JevClient, JevClientConfig, noul


def _client(handler: Any, **config: Any) -> JevClient:
    """用内存传输层构造客户端。"""
    settings: dict[str, Any] = {
        "api_key": "test-key",
        "max_retries": 0,
        "retry_backoff_seconds": 0,
        "max_retry_backoff_seconds": 0,
    }
    settings.update(config)
    return JevClient(JevClientConfig(**settings), transport=httpx.MockTransport(handler))


def _ok_handler(request: httpx.Request) -> httpx.Response:
    """返回固定的合法 System One 响应。"""
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {"q": {"type": "noul", "noul": 0.5}},
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
    )


# ==============================================================================
# 客户端兜底分支
# ==============================================================================


async def test_unavailable_event_bus_does_not_break_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """事件总线完全不可用时，Jev 请求仍应正常完成。"""
    import src.kernel.event as event_module

    def broken_bus() -> Any:
        """模拟事件总线不可用。"""
        raise RuntimeError("event bus unavailable")

    monkeypatch.setattr(event_module, "get_event_bus", broken_bus)

    response = await _client(_ok_handler).evaluate(
        state="state",
        questions={"q": noul("ok?")},
    )
    assert response.answer("q").noul == 0.5


async def test_build_transport_uses_shared_network_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """客户端使用共享网络基础设施构造传输层，而不是自建连接池。"""
    import src.kernel.jev.client as client_module

    sentinel = httpx.AsyncHTTPTransport()
    calls: list[dict[str, Any]] = []

    def fake_build(**kwargs: Any) -> httpx.AsyncBaseTransport:
        """记录调用参数并返回哨兵传输层。"""
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(client_module, "build_httpx_transport", fake_build)
    client = _client(_ok_handler, force_ipv4=True)
    assert client._build_transport() is sentinel
    assert calls == [{"force_ipv4": True}]
    await sentinel.aclose()


async def test_client_without_explicit_transport_builds_shared_transport() -> None:
    """未注入传输层时按配置构造共享传输层。"""
    client = JevClient(JevClientConfig(api_key="test-key", force_ipv4=True))
    transport = client._build_transport()
    assert transport is not None
    if isinstance(transport, httpx.AsyncHTTPTransport):
        await transport.aclose()


# ==============================================================================
# 插件公共 API 的参数互斥校验
# ==============================================================================


def _mock_core_config(monkeypatch: pytest.MonkeyPatch, *, client_type: str = "jev") -> None:
    """注入一份最小的 Core 配置桩，避免依赖真实配置初始化。"""
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider.get_api_key.return_value = "provider-key"
    provider.base_url = "https://jev.example"
    provider.client_type = client_type
    provider.timeout = 7
    provider.max_retry = 2
    provider.retry_interval = 3
    model = MagicMock()
    model.api_provider = "typesafe"
    model.model_identifier = "jev-1.13.0"
    model.extra_params = {}
    config = MagicMock()
    config.get_provider.return_value = provider
    config.get_model.return_value = model
    monkeypatch.setattr("src.core.config.get_model_config", lambda: config)


def test_create_jev_client_rejects_provider_and_model_together() -> None:
    """provider_name 与 configured_model_name 不能同时传入。"""
    with pytest.raises(ValueError, match="不能与 configured_model_name 同时传入"):
        jev_api.create_jev_client(
            provider_name="TypeSafe",
            configured_model_name="system-one",
        )


def test_create_jev_client_rejects_base_url_override_with_core_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复用 Core 配置时不允许覆盖 base_url，避免把密钥发往非预期主机。"""
    _mock_core_config(monkeypatch)
    with pytest.raises(ValueError, match="不允许覆盖 base_url"):
        jev_api.create_jev_client(
            configured_model_name="system-one",
            base_url="https://evil.invalid",
        )


def test_create_jev_client_from_env_uses_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """既没有 config 也没有 Core 名称时，从环境变量读取 API key。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")
    client = jev_api.create_jev_client()
    assert client.config.api_key == "env-key"
    assert client.config.model == "jev-latest"


async def test_evaluate_jev_rejects_client_with_overrides() -> None:
    """传入 client 的同时再给 overrides 会造成配置来源歧义，必须拒绝。"""
    client = _client(_ok_handler)
    with pytest.raises(ValueError, match="不能与 Jev 客户端 overrides 同时传入"):
        await jev_api.evaluate_jev(
            client=client,
            state="state",
            questions={"q": noul("ok?")},
            base_url="https://evil.invalid",
        )


async def test_evaluate_jev_creates_client_from_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """未传 client 时按 overrides 创建一次性客户端。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")
    created: dict[str, Any] = {}

    real_create = jev_api.create_jev_client

    def spy(**kwargs: Any) -> JevClient:
        """记录创建的客户端，并注入内存传输层避免真实网络。"""
        client = real_create(**kwargs)
        created["client"] = client
        return JevClient(
            client.config,
            transport=httpx.MockTransport(_ok_handler),
        )

    monkeypatch.setattr(jev_api, "create_jev_client", spy)
    response = await jev_api.evaluate_jev(
        state="state",
        questions={"q": noul("ok?")},
        base_url="https://api.typesafe.ai",
        request_name="jev.test",
    )
    assert response.answer("q").noul == 0.5
    assert created["client"].config.base_url == "https://api.typesafe.ai"


async def test_evaluate_jev_requires_non_empty_questions() -> None:
    """questions 为空时必须在发出请求前失败。"""
    with pytest.raises(Exception):
        await jev_api.evaluate_jev(
            state="state",
            questions={},
            api_key="test-key",
            base_url="https://api.typesafe.ai",
        )


def test_create_jev_client_rejects_non_jev_client_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core provider 不是 Jev 类型时必须拒绝，避免误用聊天 provider。"""
    _mock_core_config(monkeypatch, client_type="openai")
    with pytest.raises(ValueError, match="client_type 必须是 jev/typesafe"):
        jev_api.create_jev_client(configured_model_name="system-one")


def test_environment_key_is_not_leaked_into_repr() -> None:
    """API key 不得出现在配置对象的 repr 中。"""
    os.environ["TYPESAFE_API_KEY"] = "env-key"
    client = jev_api.create_jev_client()
    assert "env-key" not in repr(client.config)
