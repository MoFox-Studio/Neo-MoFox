"""供应商技术边界的离线回归，使用真实 SDK 和自有 MockTransport。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
import inspect
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from google.genai.errors import ClientError

from src.kernel.llm import exceptions, request_execution
from src.kernel.llm.model_client import gemini_client, registry
from src.kernel.llm.model_client.gemini_codec import target_identity


def entry(**changes: Any) -> dict[str, Any]:
    """构造虚拟模型技术配置，覆盖字段由 changes 提供。"""
    return {"api_key": "test-key", "base_url": "https://probe.invalid/v1beta",
            "timeout": 2.0, "api_provider": "private-provider", "model_identifier": "m",
            "client_type": "gemini", **changes}


def test_transport_snapshot_repr_hides_authentication_values() -> None:
    """快照不在 repr 暴露凭据与头值，但认证差异仍影响完整分组键。"""
    from src.kernel.llm.model_client.gemini_codec import resolve_gemini_transport

    model = entry(extra_params={"headers": {"x-private": "private-header-value"}})
    snapshot = resolve_gemini_transport(model)
    assert "test-key" not in repr(snapshot)
    assert "private-header-value" not in repr(snapshot)
    assert snapshot != resolve_gemini_transport({**model, "api_key": "other-key"})
    assert snapshot != resolve_gemini_transport({**model, "extra_params": {"headers": {"x-private": "other"}}})
    assert snapshot == resolve_gemini_transport(dict(model))
    with pytest.raises(FrozenInstanceError):
        snapshot.key = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_runtime_contract_and_owned_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """统一关闭仅委托旧关闭入口，不构造默认客户端或扫描注入对象。"""
    from src.kernel.llm.model_client import runtime
    close = AsyncMock()
    monkeypatch.setattr(gemini_client, "close_gemini_clients", close)
    monkeypatch.setattr(gemini_client, "get_default_gemini_client", None)
    await runtime.close_default_model_clients()
    close.assert_awaited_once_with()
    result = runtime.ProviderProbeResult("available", "固定消息")
    with pytest.raises(FrozenInstanceError):
        result.detail = "更改"  # type: ignore[misc]
    assert runtime.get_provider_probe(" AIOHTTP-GEMINI ").requires_model_entries
    assert not runtime.get_provider_probe("typesafe-ai").requires_model_entries
    assert (await runtime.get_provider_probe("typesafe-ai").check([], 1))[0].status == "unsupported"
    assert runtime.get_provider_probe("gemini").requires_provider_entry
    assert runtime.get_provider_probe("openai").requires_provider_entry
    assert not runtime.get_provider_probe("typesafe-ai").requires_provider_entry
    assert runtime.get_provider_probe("openai").allows_anonymous_probe
    assert not runtime.get_provider_probe("gemini").allows_anonymous_probe
    assert not runtime.get_provider_probe("typesafe-ai").allows_anonymous_probe


@pytest.mark.asyncio
async def test_unsupported_probe_never_reads_key_or_creates_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """独立协议跳过不依赖 provider 条目，也不轮询 key 或构造 HTTP 客户端。"""
    from src.kernel.llm.model_client import runtime

    class ForbiddenEntry(dict[str, Any]):
        """任何字段访问都失败，防止隐式读取凭据。"""
        def get(self, key: str, default: Any = None) -> Any:
            """拒绝读取任何配置字段。"""
            raise AssertionError("unsupported probe must not read entries")

    def forbidden_client(**kwargs: Any) -> httpx.AsyncClient:
        """拒绝创建网络客户端。"""
        raise AssertionError("unsupported probe must not create HTTP clients")

    monkeypatch.setattr(runtime.httpx, "AsyncClient", forbidden_client)
    probe = runtime.get_provider_probe(" TYPESAFE-AI ")
    assert not probe.requires_model_entries and not probe.requires_provider_entry
    assert (await probe.check([], 1, provider_only=True))[0].status == "unsupported"
    assert (await probe.check([ForbiddenEntry()], 1))[0].status == "unsupported"


@pytest.mark.asyncio
async def test_sdk_probe_groups_closes_and_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 SDK 按传输快照分组、关闭响应和临时 transport，错误组互相隔离。"""
    from src.kernel.llm.model_client.runtime import get_provider_probe
    transports: list[ProbeTransport] = []
    requests: list[httpx.Request] = []
    bodies: list[ProbeBody] = []

    class ProbeBody(httpx.AsyncByteStream):
        """记录真实响应体的关闭行为。"""
        def __init__(self, value: bytes) -> None:
            """保存模拟响应内容。"""
            self.value, self.closed = value, False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            """交付固定 JSON 字节。"""
            yield self.value

        async def aclose(self) -> None:
            """记录响应已关闭。"""
            self.closed = True

    class ProbeTransport(httpx.AsyncBaseTransport):
        """记录每组独占传输的请求与关闭。"""
        def __init__(self) -> None:
            """初始化关闭状态。"""
            self.closed = False

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            """仅响应 models.list，首组失败且不重试。"""
            requests.append(request)
            assert request.method == "GET"
            assert request.url.path.endswith("/models")
            assert request.url.params["pageSize"] == "1"
            assert request.headers["x-goog-api-key"] in {"test-key", "other-key"}
            body = ProbeBody(b'{"error":{"code":404,"message":"secret-sdk-text"}}' if len(requests) == 1 else b'{"models":[]}')
            bodies.append(body)
            return httpx.Response(404 if len(requests) == 1 else 200, stream=body)

        async def aclose(self) -> None:
            """记录临时传输已关闭。"""
            self.closed = True

    def factory(*, force_ipv4: bool) -> ProbeTransport:
        """为每组创建新的自有传输。"""
        transport = ProbeTransport()
        transports.append(transport)
        return transport

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    results = await get_provider_probe("google").check([
        entry(), entry(model_identifier="n"), entry(api_key="other-key"),
        entry(base_url="http://unsafe.invalid"),
    ], 1.0)
    assert len(requests) == 2
    assert len(transports) == 2 and all(item.closed for item in transports)
    assert all(item.closed for item in bodies)
    assert sorted(item.covered_models for item in results) == [0, 1, 2]
    assert any("跳过不支持 models.list" in item.detail for item in results)
    assert any(item.status == "available" for item in results)
    detail = " ".join(item.detail for item in results)
    assert all(secret not in detail for secret in ("test-key", "private-provider", "probe.invalid", "secret-sdk-text"))
    assert "未验证" in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"api_key": "other-key"},
    {"base_url": "https://other.invalid/v1beta"},
    {"base_url": "https://probe.invalid/v1"},
    {"timeout": 3.0},
    {"extra_params": {"trust_env": False}},
    {"extra_params": {"force_ipv4": True}},
    {"extra_params": {"headers": {"x-test": "private-header"}}},
])
async def test_probe_preserves_all_group_dimensions(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any],
) -> None:
    """真实 SDK 分别探测 key/root/version/timeout/trust/IPv4/headers 的独立配置。"""
    from src.kernel.llm.model_client.runtime import get_provider_probe
    requests: list[httpx.Request] = []
    flags: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录每组非生成请求，不提供生成端点。"""
        requests.append(request)
        assert request.method == "GET" and request.url.path.endswith("/models")
        return httpx.Response(200, json={"models": []})

    def factory(*, force_ipv4: bool) -> httpx.MockTransport:
        """记录网络策略并创建每组自有传输。"""
        flags.append(force_ipv4)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    results = await get_provider_probe("gemini").check([entry(), entry(**changes)], 1)
    assert len(requests) == len(flags) == len(results) == 2
    assert all(item.status == "available" and item.groups == 1 for item in results)
    assert all("private-header" not in item.detail for item in results)


@pytest.mark.parametrize("alias", ["gemini", " GOOGLE ", "aiohttp-gemini", "gemini-v2"])
def test_registry_replay_alias_contract(alias: str) -> None:
    """注册表保持原始别名和精确来源锁，不放宽其他协议。"""
    clients = registry.ModelClientRegistry()
    model = entry(client_type=alias)
    target = target_identity(model)
    assert clients.matches_replay_target(model, target)
    assert not clients.matches_replay_target(entry(client_type="vertex_ai"), target)
    assert not clients.matches_replay_target(entry(client_type="jev"), target)
    assert not clients.matches_replay_target(entry(model_identifier="other"), target)
    assert target_identity({"model_identifier": "m"})[2] == "m"


def test_sdk_subclass_suppresses_trace_without_execution_sdk_logic() -> None:
    """异地定义的 SDK 子类也由归一化层设置安全标记，执行层不猜模块名。"""
    class ExternalError(ClientError):
        """模拟定义于 SDK 包之外的合法 SDK 异常子类。"""
    error = ExternalError(429, {"error": {"message": "secret-key"}})
    classified = exceptions.classify_exception(error)
    assert isinstance(classified, exceptions.LLMRateLimitError)
    assert getattr(classified, "_suppress_provider_trace", False)
    assert "secret-key" not in str(classified)
    source = inspect.getsource(request_execution)
    assert "google.genai" not in source
    assert "gemini_codec" not in source
    assert "is_gemini_client_type" not in source


@pytest.mark.asyncio
async def test_probe_bad_entries_and_provider_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """全坏不网络回退，无模型探测明确声明未覆盖模型。"""
    from src.kernel.llm.model_client.runtime import get_provider_probe
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录非生成 HTTP 请求并返回合法 SDK 枚举结果。"""
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    def factory(*, force_ipv4: bool) -> httpx.MockTransport:
        """为预检创建自有离线传输。"""
        return httpx.MockTransport(handler)

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    probe = get_provider_probe("gemini")
    results = await probe.check([entry(api_key=""), entry(timeout=0)], 1)
    assert len(results) == 2 and all(item.status == "unverified" for item in results)
    assert not requests
    assert await probe.check([], 1) == []
    result = (await probe.check([entry()], 1, provider_only=True))[0]
    assert result.status == "available" and result.covered_models == 0
    assert "仅 provider" in result.detail and "未覆盖模型" in result.detail


@pytest.mark.asyncio
async def test_probe_cancellation_closes_without_visiting_next_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 SDK 读取取消仍关闭响应和自有传输，不转成普通结果或访问后组。"""
    from src.kernel.concurrency import get_task_manager
    from src.kernel.llm.model_client.runtime import get_provider_probe
    entered = asyncio.Event()
    release = asyncio.Event()
    body_closed = asyncio.Event()
    transport_closed = asyncio.Event()
    requests: list[httpx.Request] = []

    class BlockingBody(httpx.AsyncByteStream):
        """利用事件固定 SDK 正在读取真实响应的时序。"""
        async def __aiter__(self) -> AsyncIterator[bytes]:
            """等候测试取消，不靠 sleep 争抢时序。"""
            entered.set()
            await release.wait()
            yield b'{"models":[]}'

        async def aclose(self) -> None:
            """记录真实响应体关闭。"""
            body_closed.set()

    class CancelTransport(httpx.AsyncBaseTransport):
        """提供可取消的响应并记录临时传输关闭。"""
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            """返回待读取的单个响应。"""
            requests.append(request)
            return httpx.Response(200, stream=BlockingBody())

        async def aclose(self) -> None:
            """记录自有传输关闭。"""
            transport_closed.set()

    def factory(*, force_ipv4: bool) -> CancelTransport:
        """返回临时自有传输。"""
        return CancelTransport()

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    info = get_task_manager().create_task(
        get_provider_probe("gemini").check([entry(), entry(api_key="other")], 10),
        name="supplier-boundary-cancel",
    )
    assert info.task is not None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        info.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await info.task
        assert body_closed.is_set() and transport_closed.is_set()
        assert len(requests) == 1
    finally:
        release.set()
        if not info.task.done():
            info.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await info.task


@pytest.mark.asyncio
async def test_legacy_models_http_status_is_not_request_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧协议保留 Bearer GET 和 4xx 状态报告，不调用 raise_for_status。"""
    from src.kernel.llm.model_client import runtime
    original = httpx.AsyncClient
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """检查历史认证与 URL 兼容并返回非成功 HTTP 状态。"""
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-key"
        assert str(request.url) == "https://probe.invalid/v1beta/models"
        return httpx.Response(403, text="private-upstream-body")

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        """注入离线传输，不改变真实 HTTPX 客户端行为。"""
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(runtime.httpx, "AsyncClient", factory)
    result = (await runtime.get_provider_probe("anthropic").check([entry()], 1))[0]
    assert result.status == "available" and "HTTP 403" in result.detail
    assert "private-upstream-body" not in result.detail and len(requests) == 1


@pytest.mark.asyncio
async def test_default_close_leaves_external_client_and_borrowed_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关闭不创建实例，也不回收外部 Gemini 实例或其借用 transport。"""
    from src.kernel.llm.model_client import runtime
    closed: list[bool] = []

    class BorrowedTransport(httpx.MockTransport):
        """记录调用方持有的 transport 是否被越权关闭。"""
        async def aclose(self) -> None:
            """记录 transport 关闭。"""
            closed.append(True)
            await super().aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        """提供离线响应，本测试只建立 lease 不请求。"""
        return httpx.Response(200, json={})

    transport = BorrowedTransport(handler)
    external = gemini_client.GeminiChatClient(transport=transport)
    lease = await external._get_client(entry())
    lease.active -= 1
    monkeypatch.setattr(gemini_client, "_default_client", None)
    try:
        await runtime.close_default_model_clients()
        assert gemini_client._default_client is None
        assert not lease.http.is_closed and not closed
    finally:
        await external.aclose()
    assert lease.http.is_closed and not closed
    await transport.aclose()
    assert closed == [True]


@pytest.mark.asyncio
async def test_probe_results_have_safe_entry_and_group_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    """坏条目与去重后的探测组具有独立安全序号，不依赖配置原文定位。"""
    from src.kernel.llm.model_client import runtime
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录各组的真实 SDK GET 请求。"""
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    def factory(*, force_ipv4: bool) -> httpx.MockTransport:
        """每组创建离线传输，禁止访问供应商网络。"""
        return httpx.MockTransport(handler)

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    results = await runtime.get_provider_probe("gemini").check([
        entry(api_key=""), entry(), entry(model_identifier="another"), entry(api_key="other-key"),
    ], 1)
    assert len(requests) == 2 and len(results) == 3
    invalid = [item for item in results if item.entry_index is not None]
    groups = [item for item in results if item.group_index is not None]
    assert len(invalid) == 1 and invalid[0].entry_index == 1 and invalid[0].group_index is None
    assert [(item.group_index, item.covered_models) for item in groups] == [(1, 2), (2, 1)]
    assert all(item.entry_index is None for item in groups)
    legacy = runtime.ProviderProbeResult("available", "固定消息", 2, 1)
    assert legacy.entry_index is None and legacy.group_index is None
    assert all(secret not in " ".join(item.detail for item in results)
               for secret in ("private-provider", "probe.invalid", "other-key"))


@pytest.mark.asyncio
async def test_probe_ignores_generation_shape_but_generation_rejects_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非生成预检只验证传输；错误生成形状仍由真实生成入口明确拒绝。"""
    from src.kernel.llm.exceptions import LLMConfigurationError
    from src.kernel.llm.model_client.runtime import get_provider_probe
    from src.kernel.llm.payload import LLMPayload, Text
    from src.kernel.llm.roles import ROLE
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """仅允许真实 SDK models.list，不允许错误生成参数触发 POST。"""
        requests.append(request)
        assert request.method == "GET" and request.url.path.endswith("/models")
        return httpx.Response(200, json={"models": []})

    def factory(*, force_ipv4: bool) -> httpx.MockTransport:
        """提供每组独占离线传输。"""
        return httpx.MockTransport(handler)

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    model = entry(extra_params={"gemini": {"generation_config": []}})
    result = (await get_provider_probe("gemini").check([model], 1))[0]
    assert result.status == "available" and len(requests) == 1
    assert target_identity({"model_identifier": "m", "extra_params": model["extra_params"]})[2] == "m"
    client = gemini_client.GeminiChatClient()
    try:
        with pytest.raises(LLMConfigurationError, match="objects"):
            await client.create(
                model_name="m", payloads=[LLMPayload(ROLE.USER, Text("hi"))], tools=[],
                request_name="invalid-generation-shape", model_set=model, stream=False,
            )
        assert len(requests) == 1 and not client._pools
    finally:
        await client.aclose()


def test_probe_check_annotations_resolve_without_import_order_dependency() -> None:
    """探测入口的真实类型注解可解析，不依赖仅类型检查期的名称绑定。"""
    from typing import get_type_hints
    from src.kernel.llm.model_client.runtime import ProviderProbeResult
    hints = get_type_hints(gemini_client.GeminiProviderProbe.check)
    assert hints["return"] == list[ProviderProbeResult]


@pytest.mark.parametrize("first", ["gemini_client", "runtime", "probe_contract"])
def test_probe_contract_cold_import_order(first: str) -> None:
    """冷进程按不同顺序导入契约和适配器，类型解析不触发循环依赖。"""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    code = (
        "import importlib; from typing import get_type_hints; "
        f"importlib.import_module('src.kernel.llm.model_client.{first}'); "
        "from src.kernel.llm.model_client import gemini_client, runtime, probe_contract; "
        "assert runtime.ProviderProbeResult is probe_contract.ProviderProbeResult; "
        "assert get_type_hints(gemini_client.GeminiProviderProbe.check)['return'] "
        "== list[runtime.ProviderProbeResult]"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
