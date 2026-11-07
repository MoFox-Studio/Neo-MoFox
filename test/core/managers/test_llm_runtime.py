"""验证领域预检只准备配置，不承担供应商 SDK 与传输机制。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.config.model_config import APIProviderSection, ModelConfig, ModelInfoSection


class RecordingProbe:
    """记录技术输入，用可替换能力验证领域编排而非某个 SDK。"""

    requires_model_entries = True
    requires_provider_entry = True
    allows_anonymous_probe = False

    def __init__(self) -> None:
        """初始化每次探测收到的配置快照。"""
        self.calls: list[tuple[list[dict[str, Any]], float, bool]] = []

    async def check(
        self, entries: list[dict[str, Any]], timeout: float, *, provider_only: bool = False,
    ) -> list[Any]:
        """记录 entries、预算与覆盖范围，返回供应商无关结果。"""
        from src.kernel.llm.model_client.runtime import ProviderProbeResult

        self.calls.append((entries, timeout, provider_only))
        return [ProviderProbeResult("available", "本地探测可用", len(entries), 1)]


async def test_domain_preflight_uses_effective_entries_and_replaceable_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """领域层复用配置合并，并可换成不暴露 SDK/池的探测器。"""
    from src.core.managers import llm_runtime

    provider = APIProviderSection(name="local", client_type="gemini", api_key="offline-key")
    model = ModelInfoSection(name="sample", model_identifier="sample", api_provider="local",
                             extra_params={"headers": {"x-tenant": "offline-tenant"}})
    config = ModelConfig(api_providers=[provider], models=[model])
    probe = RecordingProbe()

    def select_probe(client_type: object) -> RecordingProbe:
        """对任意协议选择替身，不暴露实际传输结构。"""
        assert client_type == "gemini"
        return probe

    monkeypatch.setattr(llm_runtime, "get_provider_probe", select_probe)
    results = await llm_runtime.preflight_llm_providers(config, timeout=2, trust_env=False)
    assert results[0].status == "available"
    assert len(probe.calls) == 1
    entries, timeout, provider_only = probe.calls[0]
    assert timeout == 2 and not provider_only
    assert entries == config.get_model_set_by_name("sample")
    assert entries[0]["extra_params"]["headers"] == {"x-tenant": "offline-tenant"}


async def test_domain_merge_failure_isolated_without_provider_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部模型合并失败也不伪造 provider 默认成功，不泄露配置原文。"""
    from src.core.managers import llm_runtime

    provider = APIProviderSection(name="local", client_type="gemini", api_key="offline-key")
    model = ModelInfoSection(name="sample", model_identifier="sample", api_provider="local")
    config = ModelConfig(api_providers=[provider], models=[model])
    probe = RecordingProbe()

    def select_probe(client_type: object) -> RecordingProbe:
        """返回需要有效模型配置的技术能力。"""
        return probe

    def broken_merge(self: ModelConfig, model_name: str, **kwargs: Any) -> Any:
        """模拟领域配置损坏，异常原文不得进入结果。"""
        raise ValueError("sentinel-secret")

    monkeypatch.setattr(llm_runtime, "get_provider_probe", select_probe)
    monkeypatch.setattr(ModelConfig, "get_model_set_by_name", broken_merge)
    results = await llm_runtime.preflight_llm_providers(config, timeout=2, trust_env=False)
    assert results[0].status == "unverified"
    assert "sentinel-secret" not in repr(results)
    assert probe.calls[0][0] == []
    assert probe.calls[0][2] is False


async def test_domain_provider_only_and_provider_failure_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有模型明确 provider-only，前一个能力失败不阻止后一个。"""
    from src.core.managers import llm_runtime

    providers = [APIProviderSection(name=name, client_type="gemini", api_key="offline-key")
                 for name in ["bad", "good"]]
    probe = RecordingProbe()
    calls = 0

    def select_probe(client_type: object) -> RecordingProbe:
        """只让第一项能力选择失败以验证 provider 隔离。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("sentinel-secret")
        return probe

    monkeypatch.setattr(llm_runtime, "get_provider_probe", select_probe)
    results = await llm_runtime.preflight_llm_providers(
        ModelConfig(api_providers=providers), timeout=2, trust_env=False,
    )
    assert [result.status for result in results] == ["unverified", "available"]
    assert probe.calls[0][2] is True
    assert probe.calls[0][0][0]["extra_params"]["trust_env"] is False
    assert "sentinel-secret" not in repr(results)


async def test_domain_cancellation_is_not_a_failed_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部取消原样传播，不能被启动的普通失败隔离吞掉。"""
    from src.core.managers import llm_runtime

    def cancel_selection(client_type: object) -> RecordingProbe:
        """以控制异常验证编排的异常边界。"""
        raise asyncio.CancelledError

    monkeypatch.setattr(llm_runtime, "get_provider_probe", cancel_selection)
    config = ModelConfig(api_providers=[APIProviderSection(api_key="offline-key")])
    with pytest.raises(asyncio.CancelledError):
        await llm_runtime.preflight_llm_providers(config, timeout=2, trust_env=False)


async def test_unsupported_probe_never_rotates_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """不提供启动探测的技术能力直接跳过，不读取或轮询凭据。"""
    from src.core.managers.llm_runtime import preflight_llm_providers

    provider = APIProviderSection(name="typed", api_key=["offline-first", "offline-second"],
                                  client_type="jev")
    config = ModelConfig(api_providers=[provider])
    result = await preflight_llm_providers(config, timeout=2, trust_env=False)
    assert result[0].status == "unsupported"
    assert provider.get_api_key() == "offline-first"


async def test_legacy_anonymous_probe_when_key_rotation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧兼容探测取密钥失败仍匿名 GET，不把此策略扩到其他能力。"""
    import httpx
    from src.core.managers.llm_runtime import preflight_llm_providers

    requests: list[httpx.Request] = []
    original = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """记录匿名请求并保持旧 HTTP 状态诊断。"""
        requests.append(request)
        return httpx.Response(403, json={})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """注入受控传输，禁止真实网络。"""
        kwargs["transport"] = httpx.MockTransport(handle)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    config = ModelConfig(api_providers=[APIProviderSection(
        name="local", client_type="openai", api_key=[], base_url="https://offline.invalid/v1",
    )])
    results = await preflight_llm_providers(config, timeout=2, trust_env=False)
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/models"
    assert "authorization" not in requests[0].headers
    assert results[0].status == "available"
    assert "403" in results[0].detail


async def test_domain_results_have_safe_provider_and_group_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    """多个提供商与分组结果保留序号，可定位而不输出敏感名称或地址。"""
    from src.core.managers import llm_runtime
    from src.kernel.llm.model_client.runtime import ProviderProbeResult

    class LocatedProbe(RecordingProbe):
        """返回两个分组，使安全上下文能在展平后保留。"""

        async def check(
            self, entries: list[dict[str, Any]], timeout: float, *, provider_only: bool = False,
        ) -> list[ProviderProbeResult]:
            """用固定分组序号替代可能敏感的配置文本。"""
            return [ProviderProbeResult("unverified", f"组 {index} 未验证", 0, 1)
                    for index in [1, 2]]

    def select_probe(client_type: object) -> LocatedProbe:
        """返回不持有 SDK 或池的能力替身。"""
        return LocatedProbe()

    monkeypatch.setattr(llm_runtime, "get_provider_probe", select_probe)
    config = ModelConfig(api_providers=[APIProviderSection(name=name, api_key="offline-key")
        for name in ["first-secret-name", "second-secret-name"]])
    results = await llm_runtime.preflight_llm_providers(config, timeout=2, trust_env=False)
    assert "提供商 1" in results[0].detail and "组 1" in results[0].detail
    assert "提供商 2" in results[2].detail and "组 1" in results[2].detail
    assert "secret-name" not in repr(results)


async def test_nonanonymous_probe_rejects_key_failure_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """需要认证的能力取 key 失败只能未验证，不能沿匿名兼容路径发送。"""
    import httpx
    from src.core.managers.llm_runtime import preflight_llm_providers
    from unittest.mock import MagicMock

    factory = MagicMock(side_effect=AssertionError("不应创建客户端"))
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    config = ModelConfig(api_providers=[APIProviderSection(client_type="gemini", api_key=[])], models=[])
    results = await preflight_llm_providers(config, timeout=2, trust_env=False)
    assert results[0].status == "unverified"
    assert "ValueError" in results[0].detail
    factory.assert_not_called()
