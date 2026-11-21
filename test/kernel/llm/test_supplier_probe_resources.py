"""补充供应商预检资源矩阵；真实固定 SDK 配合离线传输，不修改生产行为。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from google import genai

from src.kernel.concurrency import get_task_manager
from src.kernel.llm.model_client import gemini_client
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.roles import ROLE


def _entry(key: str = "offline-key") -> dict[str, Any]:
    """返回带占位凭据和不可路由端点的模型技术条目。"""
    return {
        "api_key": key, "base_url": "https://resources.invalid", "timeout": 10.0,
        "client_type": "gemini", "api_provider": "offline", "model_identifier": "m",
        "max_tokens": 32, "extra_params": {"trust_env": False},
    }


class _Body(httpx.AsyncByteStream):
    """记录读取与关闭，可按事件阻塞或注入原始异常对象。"""

    def __init__(
        self, value: bytes = b'{"models":[]}', *, read_error: BaseException | None = None,
        close_error: BaseException | None = None, blocked: bool = False,
    ) -> None:
        """保存响应内容、失败对象和事件门，不拥有其他网络资源。"""
        self.value = value
        self.read_error, self.close_error = read_error, close_error
        self.blocked = blocked
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.close_calls = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """按事件交付字节或原样抛出读取异常。"""
        self.entered.set()
        if self.blocked:
            await self.release.wait()
        if self.read_error is not None:
            raise self.read_error
        yield self.value

    async def aclose(self) -> None:
        """记录真实 body 关闭次数并原样抛出指定关闭异常。"""
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _Transport(httpx.MockTransport):
    """在真实 HTTPX 请求边界记录 owner、循环和自有传输关闭次数。"""

    def __init__(self, body: _Body) -> None:
        """为单个临时客户端提供固定 body，并记录请求所属 owner。"""
        self.body = body
        self.close_calls = 0
        self.requests: list[httpx.Request] = []
        self.owners: list[Any] = []
        self.loop = asyncio.get_running_loop()
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """返回真实响应，让 SDK 解析与响应 hook 参与测试。"""
        self.requests.append(request)
        self.owners.append(gemini_client._response_owner.get())
        return httpx.Response(200, stream=self.body, headers={"content-type": "application/json"})

    async def aclose(self) -> None:
        """记录自有传输实际关闭次数。"""
        self.close_calls += 1
        await super().aclose()


class _CloseAudit:
    """只包装真实 SDK/HTTP 的关闭，不替代 SDK 操作或响应解析。"""

    def __init__(self, patch: pytest.MonkeyPatch, stage: str = "none") -> None:
        """安装关闭计数与指定普通失败，始终先执行真实关闭。"""
        original_http, original_sdk = httpx.AsyncClient, genai.Client
        self.http_calls = 0
        self.http_effective = 0
        self.sdk_async_calls = 0
        self.sdk_sync_calls = 0
        self.async_error = ValueError("offline async SDK close")
        self.sync_error = ValueError("offline sync SDK close")
        self.http_error = ValueError("offline HTTP close")
        self.clients: list[httpx.AsyncClient] = []
        audit = self

        class AuditedHTTP(original_http):
            """记录 HTTP 关闭调用与首次有效关闭，保留真实资源回收。"""

            async def aclose(self) -> None:
                """真实关闭后注入错误，不用替身掩盖资源泄漏。"""
                audit.http_calls += 1
                if not self.is_closed:
                    audit.http_effective += 1
                await super().aclose()
                if stage in {"http", "all"}:
                    raise audit.http_error

        def http_factory(**kwargs: Any) -> httpx.AsyncClient:
            """创建真实 HTTPX 子类并记录实例。"""
            client = AuditedHTTP(**kwargs)
            audit.clients.append(client)
            return client

        def sdk_factory(**kwargs: Any) -> Any:
            """创建真实固定 SDK，仅包装其两个关闭入口。"""
            sdk = original_sdk(**kwargs)
            original_async, original_sync = sdk.aio.aclose, sdk.close

            async def close_async() -> None:
                """执行 SDK 真实异步关闭并注入普通错误。"""
                audit.sdk_async_calls += 1
                await original_async()
                if stage in {"sdk_async", "all"}:
                    raise audit.async_error

            def close_sync() -> None:
                """执行 SDK 真实同步关闭并注入普通错误。"""
                audit.sdk_sync_calls += 1
                original_sync()
                if stage in {"sdk_sync", "all"}:
                    raise audit.sync_error

            patch.setattr(sdk.aio, "aclose", close_async)
            patch.setattr(sdk, "close", close_sync)
            return sdk

        patch.setattr(gemini_client.httpx, "AsyncClient", http_factory)
        patch.setattr(gemini_client.genai, "Client", sdk_factory)

    def assert_closed(self) -> None:
        """检查 SDK 两种关闭各一次，HTTP 只发生一次有效资源回收。"""
        assert self.sdk_async_calls == self.sdk_sync_calls == 1
        # 固定 SDK 可自行触达注入 HTTP；区分重复入口调用与真实传输关闭。
        assert self.http_calls >= 1 and self.http_effective == 1
        assert len(self.clients) == 1 and self.clients[0].is_closed


def _leaves(error: BaseException) -> list[BaseException]:
    """展开普通异常组，便于断言原始关闭异常的对象身份。"""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in _leaves(child)]
    return [error]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["success", "ordinary", "cancel"])
async def test_constructor_failure_cleanup_priority(
    monkeypatch: pytest.MonkeyPatch, cleanup: str,
) -> None:
    """SDK 构造失败后关闭 HTTP；普通清理不覆盖主异常，新取消保持身份。"""
    from src.kernel.llm.model_client.gemini_codec import resolve_gemini_transport
    primary = ValueError("offline SDK constructor")
    ordinary = RuntimeError("offline HTTP cleanup")
    cancellation = asyncio.CancelledError("new cleanup cancellation")
    body = _Body()
    transport = _Transport(body)
    original_http = httpx.AsyncClient
    close_calls: list[bool] = []
    clients: list[httpx.AsyncClient] = []

    class CleanupHTTP(original_http):
        """真实关闭构造失败后遗留的 HTTP，并注入清理错误。"""

        async def aclose(self) -> None:
            """记录关闭调用；新取消对象不可被主构造异常吞掉。"""
            close_calls.append(True)
            await super().aclose()
            if cleanup == "ordinary":
                raise ordinary
            if cleanup == "cancel":
                raise cancellation

    def http_factory(**kwargs: Any) -> httpx.AsyncClient:
        """创建并登记真实 HTTP 实例。"""
        client = CleanupHTTP(**kwargs)
        clients.append(client)
        return client

    def sdk_factory(**kwargs: Any) -> Any:
        """在 SDK 构造边界抛出固定主异常，不发起请求。"""
        raise primary

    def transport_factory(*, force_ipv4: bool) -> _Transport:
        """提供单个自有离线传输。"""
        return transport

    monkeypatch.setattr(gemini_client.httpx, "AsyncClient", http_factory)
    monkeypatch.setattr(gemini_client.genai, "Client", sdk_factory)
    monkeypatch.setattr(gemini_client, "build_httpx_transport", transport_factory)
    expected = cancellation if cleanup == "cancel" else primary
    with pytest.raises(type(expected)) as raised:
        await gemini_client.GeminiProviderProbe()._check_group(resolve_gemini_transport(_entry()), 2)
    assert raised.value is expected
    assert close_calls == [True] and clients[0].is_closed
    assert transport.close_calls == 1 and not transport.requests and body.close_calls == 0
    assert gemini_client._response_owner.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("read_kind", ["transport", "cancel"])
@pytest.mark.parametrize("body_failure", [False, True])
@pytest.mark.parametrize("close_stage", ["none", "sdk_async", "sdk_sync", "http", "all"])
async def test_real_sdk_read_primary_survives_all_ordinary_cleanup(
    monkeypatch: pytest.MonkeyPatch, read_kind: str, body_failure: bool, close_stage: str,
) -> None:
    """真实 SDK 读取失败与 body/SDK/HTTP 清理失败组合保持主异常或取消身份。"""
    from src.kernel.llm.model_client.gemini_codec import resolve_gemini_transport
    assert genai.__version__ == "2.33.0"
    primary = httpx.ReadError("offline read") if read_kind == "transport" else asyncio.CancelledError("offline read cancel")
    body_error = ValueError("offline body close") if body_failure else None
    body = _Body(read_error=primary, close_error=body_error)
    transport = _Transport(body)

    def factory(*, force_ipv4: bool) -> _Transport:
        """交付自有传输以走真实 SDK 请求及响应捕获。"""
        return transport

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    audit = _CloseAudit(monkeypatch, close_stage)
    with pytest.raises(type(primary)) as raised:
        await gemini_client.GeminiProviderProbe()._check_group(resolve_gemini_transport(_entry()), 2)
    assert raised.value is primary
    assert len(transport.requests) == 1 and transport.close_calls == body.close_calls == 1
    audit.assert_closed()
    assert gemini_client._response_owner.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("close_stage", ["body", "sdk_async", "sdk_sync", "http", "all"])
async def test_successful_read_reports_close_failure(
    monkeypatch: pytest.MonkeyPatch, close_stage: str,
) -> None:
    """读取成功但关闭失败不能报告可用；单组保持关闭异常对象且公共结果安全。"""
    from src.kernel.llm.model_client.gemini_codec import resolve_gemini_transport
    body_error = ValueError("offline body close")
    body = _Body(close_error=body_error if close_stage == "body" else None)
    transport = _Transport(body)

    def factory(*, force_ipv4: bool) -> _Transport:
        """为单组请求提供真实响应体。"""
        return transport

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    audit = _CloseAudit(monkeypatch, close_stage)
    with pytest.raises(Exception) as raised:
        await gemini_client.GeminiProviderProbe()._check_group(resolve_gemini_transport(_entry()), 2)
    expected = {"body": body_error, "sdk_async": audit.async_error,
                "sdk_sync": audit.sync_error, "http": audit.http_error}.get(close_stage)
    leaves = _leaves(raised.value)
    if expected is not None:
        assert any(item is expected for item in leaves)
    else:
        assert leaves == [audit.async_error, audit.sync_error, audit.http_error]
    assert body.close_calls == transport.close_calls == 1
    audit.assert_closed()
    assert gemini_client._response_owner.get() is None


@pytest.mark.asyncio
async def test_public_probe_isolates_cleanup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """公共预检把关闭普通失败安全归为未验证，并继续下一独立组。"""
    bodies = [_Body(close_error=ValueError("private-close-text")), _Body()]
    transports: list[_Transport] = []

    def factory(*, force_ipv4: bool) -> _Transport:
        """为两个分组分配独立响应和传输。"""
        transport = _Transport(bodies[len(transports)])
        transports.append(transport)
        return transport

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    results = await gemini_client.GeminiProviderProbe().check([_entry(), _entry("other-key")], 2)
    assert [item.status for item in results] == ["unverified", "available"]
    assert all("private-close-text" not in item.detail for item in results)
    assert all(body.close_calls == 1 for body in bodies)
    assert len(transports) == 2 and all(item.close_calls == 1 for item in transports)


@pytest.mark.asyncio
async def test_two_probes_and_generation_have_independent_owners_and_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事件固定两预检与生成同时读取，预检关闭不越权生成 owner 或池。"""
    bodies = [_Body(blocked=True), _Body(blocked=True), _Body(
        b'{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP"}]}',
        blocked=True,
    )]
    transports: list[_Transport] = []

    def factory(*, force_ipv4: bool) -> _Transport:
        """按受事件约束的启动顺序分配三个独立自有传输。"""
        transport = _Transport(bodies[len(transports)])
        transports.append(transport)
        return transport

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    client = gemini_client.GeminiChatClient()
    manager = get_task_manager()
    tasks: list[asyncio.Task[Any]] = []
    try:
        for index in range(2):
            info = manager.create_task(
                gemini_client.GeminiProviderProbe().check([_entry()], 10), name=f"probe-owner-{index}",
            )
            assert info.task is not None
            tasks.append(info.task)
            await asyncio.wait_for(bodies[index].entered.wait(), 2)
        generation = manager.create_task(client.create(
            model_name="m", payloads=[LLMPayload(ROLE.USER, Text("hi"))], tools=[],
            request_name="probe-resource-generation", model_set=_entry(), stream=False,
        ), name="probe-resource-generation")
        assert generation.task is not None
        tasks.append(generation.task)
        await asyncio.wait_for(bodies[2].entered.wait(), 2)
        pool = client._pools[asyncio.get_running_loop()]
        assert len(pool) == 1
        lease = next(iter(pool.values()))
        assert lease.active == 1
        owners = [transport.owners[0] for transport in transports]
        assert all(owner is not None for owner in owners) and len({id(owner) for owner in owners}) == 3
        assert all(len(owner.responses) == 1 for owner in owners)
        for index in range(2):
            bodies[index].release.set()
            results = await asyncio.wait_for(tasks[index], 2)
            assert results[0].status == "available"
            assert bodies[index].close_calls == transports[index].close_calls == 1
            assert lease.active == 1 and not lease.http.is_closed
            assert bodies[2].close_calls == transports[2].close_calls == 0
            if index == 0:
                assert bodies[1].close_calls == 0
        bodies[2].release.set()
        result = await asyncio.wait_for(tasks[2], 2)
        assert result.message == "ok" and lease.active == 0
        assert bodies[2].close_calls == 1 and transports[2].close_calls == 0
        assert len(pool) == 1
    finally:
        for body in bodies:
            body.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
    assert len(transports) == 3 and all(item.close_calls == 1 for item in transports)
    assert gemini_client._response_owner.get() is None


def test_same_probe_across_sequential_event_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一无状态探测器可在两个顺序循环使用，每组资源在所属循环关闭。"""
    transports: list[_Transport] = []

    def factory(*, force_ipv4: bool) -> _Transport:
        """为每个运行循环创建独立的真实离线传输。"""
        transport = _Transport(_Body())
        transports.append(transport)
        return transport

    monkeypatch.setattr(gemini_client, "build_httpx_transport", factory)
    probe: Any = None

    async def run_one() -> None:
        """在当前循环执行探测，等待真实关闭后返回。"""
        nonlocal probe
        if probe is None:
            probe = gemini_client.GeminiProviderProbe()
        results = await probe.check([_entry()], 2)
        assert results[0].status == "available"
        assert gemini_client._response_owner.get() is None
        assert transports[-1].close_calls == transports[-1].body.close_calls == 1

    asyncio.run(run_one())
    asyncio.run(run_one())
    assert len(transports) == 2
    assert transports[0].loop is not transports[1].loop
    assert all(transport.loop.is_closed() for transport in transports)
