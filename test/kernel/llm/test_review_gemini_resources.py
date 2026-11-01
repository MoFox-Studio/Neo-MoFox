"""使用真实 Gemini SDK 验证请求级响应关闭和池回收责任。"""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google import genai

from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.exceptions import classify_exception, decide_retry
from src.kernel.llm.model_client.base import ModelClientResult
from src.kernel.llm.model_client.gemini_client import (
    GeminiChatClient,
    _ClientLease,
    _OwnedStream,
    _ResponseBody,
    _ResponseOwner,
    _await_sdk,
)


class _Wire(httpx.AsyncByteStream):
    """提供独立 SSE 块及未读尾部，并记录真实 body 关闭次数。"""

    def __init__(
        self,
        *,
        fail_first: bool = False,
        malformed: bool = False,
        block_at: int | None = None,
        close_error: bool = False,
        feedback: bool = False,
        block_close: bool = False,
        fail_at: int | None = None,
        read_error: BaseException | None = None,
        close_control: BaseException | None = None,
    ) -> None:
        """配置首读失败、畸形首块、阻塞位置和关闭错误。"""
        self.fail_first = fail_first
        self.fail_at = 0 if fail_first else fail_at
        self.read_error = read_error if read_error is not None else httpx.ReadError("wire read failed")
        self.malformed = malformed
        self.block_at = block_at
        self.close_error = close_error
        self.close_control = close_control
        self.feedback = feedback
        self.block_close = block_close
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.closed = 0
        self.reads = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """逐块交给 SDK 解码，不把尾部合并进首块。"""
        for index in range(3):
            self.reads += 1
            if self.block_at == index:
                self.entered.set()
                await self.release.wait()
            if self.fail_at == index:
                raise self.read_error
            if self.malformed and index == 0:
                yield b"data: {invalid-json}\n\n"
            else:
                chunk = {
                    "candidates": [{
                        "index": 0,
                        "content": {"role": "model", "parts": [{"text": str(index)}]},
                        **({"finishReason": "STOP"} if index == 2 else {}),
                    }],
                }
                if self.feedback and index == 0:
                    chunk["promptFeedback"] = {"blockReason": "SAFETY"}
                yield ("data: " + json.dumps(chunk) + "\n\n").encode()

    async def aclose(self) -> None:
        """记录底层关闭；可注入失败以验证错误和幂等性。"""
        self.closed += 1
        self.close_entered.set()
        if self.block_close:
            await self.close_release.wait()
        if self.close_control is not None:
            raise self.close_control
        if self.close_error:
            raise RuntimeError("wire close failed")


def _model() -> dict[str, Any]:
    """仅使用不可路由域名及占位凭据。"""
    return {
        "api_provider": "offline", "base_url": "https://offline.invalid",
        "model_identifier": "test-model", "api_key": "dummy",
        "client_type": "gemini", "timeout": 5.0, "max_tokens": 32,
        "temperature": 0.0, "extra_params": {},
    }


async def _create(client: GeminiChatClient) -> ModelClientResult:
    """通过适配器调用真实 SDK，返回拥有响应的流。"""
    return await client.create(
        model_name="test-model", payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        tools=[], request_name="resource-review", model_set=_model(), stream=True,
    )


def _adapter(wires: list[_Wire]) -> GeminiChatClient:
    """按请求分配独立 wire，传输层不接触网络。"""
    remaining = iter(wires)

    async def handler(request: httpx.Request) -> httpx.Response:
        """让真实 HTTPX hook 和 SDK SSE 解析都参与测试。"""
        return httpx.Response(
            200, stream=next(remaining), headers={"content-type": "text/event-stream"},
        )

    return GeminiChatClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("consume_first", [False, True])
async def test_explicit_close_waits_for_unread_wire(consume_first: bool) -> None:
    """无论是否开始消费，关闭返回前实际 wire 必须恰好关闭一次。"""
    wire = _Wire()
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert genai.__version__ == "2.33.0"
        assert result.stream_iter is not None
        if consume_first:
            assert (await anext(result.stream_iter)).text_delta == "0"
        assert wire.reads == 1
        assert wire.closed == 0
        await result.stream_iter.aclose()
        assert wire.closed == 1
        await result.stream_iter.aclose()
        assert wire.closed == 1
        lease = next(iter(client._pools[asyncio.get_running_loop()].values()))
        assert lease.active == 0
        assert not lease.http.is_closed
    finally:
        await client.aclose()


async def test_close_waits_until_wire_close_finishes() -> None:
    """不能仅开始调用 aclose 就返回，必须等待底层关闭协程结束。"""
    wire = _Wire(block_close=True)
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert result.stream_iter is not None
        closing = asyncio.ensure_future(result.stream_iter.aclose())
        await wire.close_entered.wait()
        assert not closing.done()
        lease = next(iter(client._pools[asyncio.get_running_loop()].values()))
        assert lease.active == 1
        wire.close_release.set()
        await closing
        assert wire.closed == 1 and lease.active == 0
    finally:
        wire.close_release.set()
        await client.aclose()


async def test_same_lease_concurrent_streams_are_isolated() -> None:
    """同一 SDK 下并发关闭一条流不能截断另一条流。"""
    first_wire, second_wire = _Wire(), _Wire()
    client = _adapter([first_wire, second_wire])
    try:
        first, second = await asyncio.gather(_create(client), _create(client))
        assert first.stream_iter is not None and second.stream_iter is not None
        await first.stream_iter.aclose()
        assert first_wire.closed == 1 and second_wire.closed == 0
        events = [event async for event in second.stream_iter]
        assert "".join(event.text_delta or "" for event in events) == "012"
        assert second_wire.closed == 1
        assert len(client._pools[asyncio.get_running_loop()]) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("close_error", [False, True])
async def test_cancel_prefetch_or_later_pull_closes_wire(
    stage: int, close_error: bool,
) -> None:
    """在首读或后续拉取取消，普通关闭失败不能覆盖取消。"""
    wire = _Wire(block_at=stage, close_error=close_error)
    client = _adapter([wire])
    try:
        if stage == 0:
            operation = _create(client)
        else:
            result = await _create(client)
            assert result.stream_iter is not None
            await anext(result.stream_iter)
            operation = anext(result.stream_iter)
        # 测试任务只用于精确控制取消窗口，不创建生产后台任务。
        task = asyncio.ensure_future(operation)
        await wire.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wire.closed == 1
        assert next(iter(client._pools[asyncio.get_running_loop()].values())).active == 0
    finally:
        await client.aclose()


@pytest.mark.parametrize("historical", [False, True])
async def test_sdk_boundary_does_not_reinterpret_old_cancellation(historical: bool) -> None:
    """异常链含旧取消但本阶段无新取消时，必须保留普通错误。"""
    async def run() -> None:
        """在独立任务留下可选取消计数，不污染 pytest 所属任务。"""
        task = asyncio.current_task()
        assert task is not None
        if historical:
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        primary = RuntimeError("ordinary failure")
        primary.__context__ = asyncio.CancelledError("old cancellation")

        async def fail() -> None:
            """携带取消历史但没有新取消的普通 SDK 失败。"""
            raise primary

        with pytest.raises(RuntimeError) as raised:
            await _await_sdk(fail())
        assert raised.value is primary

    await asyncio.ensure_future(run())


async def test_sdk_boundary_requires_cancel_evidence_in_exception_chain() -> None:
    """本阶段计数增长但取消已被处理、错误链无取消时不改变普通错误。"""
    async def run() -> None:
        """在独立任务中模拟 SDK 明确处理取消后抛出不相关错误。"""
        task = asyncio.current_task()
        assert task is not None
        primary = RuntimeError("independent failure")

        async def fail() -> None:
            """主动处理取消并结束 except，再抛出无取消上下文的错误。"""
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            raise primary

        with pytest.raises(RuntimeError) as raised:
            await _await_sdk(fail())
        assert raised.value is primary

    await asyncio.ensure_future(run())


@pytest.mark.parametrize("malformed", [False, True])
async def test_prefetch_failure_closes_actual_response(malformed: bool) -> None:
    """真实 SDK 的读失败与 JSON 解码失败均释放实际响应。"""
    wire = _Wire(fail_first=not malformed, malformed=malformed)
    client = _adapter([wire])
    try:
        with pytest.raises(Exception):
            await _create(client)
        assert wire.closed == 1
        assert next(iter(client._pools[asyncio.get_running_loop()].values())).active == 0
    finally:
        await client.aclose()


class _ReadControl(BaseException):
    """模拟非取消的读取控制异常，不允许关闭普通错误将其覆盖。"""


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("kind", ["transport", "ordinary", "control"])
async def test_read_primary_survives_sdk_finally_close_failure(
    stage: int, kind: str,
) -> None:
    """真实 SDK 首读与后续读失败时保留主异常身份和上层重试分类。"""
    primary = {
        "transport": httpx.ReadError("primary transport failure"),
        "ordinary": ValueError("primary ordinary failure"),
        "control": _ReadControl("primary control failure"),
    }[kind]
    wire = _Wire(
        fail_first=stage == 0, fail_at=stage, read_error=primary, close_error=True,
    )
    client = _adapter([wire])
    try:
        with pytest.raises(type(primary)) as raised:
            if stage == 0:
                await _create(client)
            else:
                result = await _create(client)
                assert result.stream_iter is not None
                await anext(result.stream_iter)
                await anext(result.stream_iter)
        assert raised.value is primary
        classified = classify_exception(raised.value)
        if kind == "transport":
            assert isinstance(classified, ConnectionError)
            assert decide_retry(classified).reason == "transient_transport_error"
        elif kind == "control":
            assert classified is primary
        else:
            assert not decide_retry(classified).retryable
        assert wire.closed == 1
        assert next(iter(client._pools[asyncio.get_running_loop()].values())).active == 0
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["cancel", "control"])
@pytest.mark.parametrize("exhaust", [False, True])
async def test_body_close_control_error_is_not_deferred_as_ordinary(
    kind: str, exhaust: bool,
) -> None:
    """显式关闭或 SDK 正常耗尽中的关闭控制异常立即传播并缓存身份。"""
    primary = asyncio.CancelledError("close cancelled") if kind == "cancel" else _ReadControl("close control")
    wire = _Wire(close_control=primary)
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert result.stream_iter is not None
        with pytest.raises(type(primary)) as first:
            if exhaust:
                _ = [event async for event in result.stream_iter]
            else:
                await result.stream_iter.aclose()
        with pytest.raises(type(primary)) as second:
            await result.stream_iter.aclose()
        assert first.value is second.value is primary
        assert wire.closed == 1
    finally:
        await client.aclose()


async def test_new_cancellation_while_body_close_waits_propagates() -> None:
    """底层关闭 await 期间的新取消不能被代理当作普通关闭错误吞掉。"""
    wire = _Wire(block_close=True)
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert result.stream_iter is not None
        closing = asyncio.ensure_future(result.stream_iter.aclose())
        await wire.close_entered.wait()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError) as first:
            await closing
        with pytest.raises(asyncio.CancelledError) as second:
            await result.stream_iter.aclose()
        assert first.value is second.value
        assert wire.closed == 1
    finally:
        wire.close_release.set()
        await client.aclose()


async def test_normal_exhaustion_still_reports_only_close_failure() -> None:
    """读取正常时不能吞掉唯一的关闭失败，重复 close 保留可诊断错误。"""
    wire = _Wire(close_error=True)
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert result.stream_iter is not None
        with pytest.raises(RuntimeError, match="wire close failed") as first:
            _ = [event async for event in result.stream_iter]
        with pytest.raises(RuntimeError) as second:
            await result.stream_iter.aclose()
        assert first.value is second.value
        assert wire.closed == 1
    finally:
        await client.aclose()


async def test_close_failure_is_not_retried_or_reported_as_success() -> None:
    """HTTPX 已标记关闭后失败也必须保留错误，不重复关闭或扣减引用。"""
    wire = _Wire(close_error=True)
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert result.stream_iter is not None
        with pytest.raises(Exception) as first:
            await result.stream_iter.aclose()
        with pytest.raises(Exception) as second:
            await result.stream_iter.aclose()
        assert first.value is second.value
        assert wire.closed == 1
        assert next(iter(client._pools[asyncio.get_running_loop()].values())).active == 0
    finally:
        await client.aclose()


async def test_primary_decode_error_survives_close_failure() -> None:
    """流解码主错误不能被后续关闭错误覆盖。"""
    wire = _Wire(close_error=True, feedback=True)
    client = _adapter([wire])
    try:
        result = await _create(client)
        assert result.stream_iter is not None
        with pytest.MonkeyPatch.context() as patcher:
            def fail_decode(*args: Any, **kwargs: Any) -> Any:
                """在 SDK 首块反馈的领域解码边界注入主错误。"""
                raise ValueError("primary decode failure")

            patcher.setattr("src.kernel.llm.model_client.gemini_client.decode_response", fail_decode)
            with pytest.raises(ValueError, match="primary decode failure"):
                await anext(result.stream_iter)
        assert wire.closed == 1
        assert wire.reads == 1
    finally:
        await client.aclose()


async def test_later_sdk_pull_keeps_response_hook_association() -> None:
    """SDK 在预取以后再发出的 HTTP 请求仍归属于原 owner。"""
    first_wire, later_wire = _Wire(), _Wire()
    client = _adapter([first_wire, later_wire])
    lease = await client._get_client(_model())
    lease.active -= 1
    original = lease.sdk.aio.models.generate_content_stream

    async def wrapped(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """保留真实 SDK 流，在后续拉取边界附加一个真实 HTTPX 响应。"""
        source = await original(*args, **kwargs)

        async def chunks() -> AsyncIterator[Any]:
            """第二次拉取才创建附加响应，以验证 owner 上下文恢复。"""
            yield await anext(source)
            await lease.http.send(
                httpx.Request("GET", "https://offline.invalid/later"), stream=True,
            )
            async for chunk in source:
                yield chunk

        return chunks()

    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(lease.sdk.aio.models, "generate_content_stream", wrapped)
            result = await _create(client)
            assert result.stream_iter is not None
            assert (await anext(result.stream_iter)).text_delta == "0"
            assert (await anext(result.stream_iter)).text_delta == "1"
            assert later_wire.reads == 0
            await result.stream_iter.aclose()
        assert first_wire.closed == later_wire.closed == 1
    finally:
        await client.aclose()


async def test_build_stream_failure_after_response_capture_closes_wire() -> None:
    """建流尚未返回 source 即失败，已登记的真实响应也必须关闭。"""
    wire = _Wire(close_error=True)
    client = _adapter([wire])
    lease = await client._get_client(_model())
    lease.active -= 1

    async def fail_build(*args: Any, **kwargs: Any) -> Any:
        """模拟 SDK 获取响应后、返回流之前失败，并保留该主异常。"""
        await lease.http.send(
            httpx.Request("GET", "https://offline.invalid/build"), stream=True,
        )
        raise ValueError("primary build failure")

    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(lease.sdk.aio.models, "generate_content_stream", fail_build)
            with pytest.raises(ValueError, match="primary build failure"):
                await _create(client)
        assert wire.closed == 1 and lease.active == 0
    finally:
        await client.aclose()


async def test_owned_stream_preserves_old_constructor_and_cancel_priority() -> None:
    """旧三参数构造仍可用，关闭取消优先于普通关闭错误且只扣一次引用。"""
    events = Mock()
    events.aclose = AsyncMock(side_effect=RuntimeError("events close failed"))
    source = Mock()
    source.aclose = AsyncMock(side_effect=asyncio.CancelledError())
    lease = _lease(active=1)
    owned = _OwnedStream(events, source, lease)
    with pytest.raises(asyncio.CancelledError):
        await owned.aclose()
    with pytest.raises(asyncio.CancelledError):
        await owned.aclose()
    assert lease.active == 0
    source.aclose.assert_awaited_once()


def _lease(*, active: int = 0, fail: bool = False) -> _ClientLease:
    """构造可记录 SDK 与 HTTP 关闭顺序的池条目。"""
    sdk = Mock()
    sdk.aio.aclose = AsyncMock(side_effect=RuntimeError("sdk close failed") if fail else None)
    http = Mock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    return _ClientLease(sdk, http, active=active)


async def test_owner_aggregates_each_body_error_and_repeats_same_group() -> None:
    """owner 必须报告每个 body 的实际错误，重复关闭返回同一聚合且不重试。"""
    owner = _ResponseOwner()
    wires = [_Wire(close_error=True), _Wire(close_error=True), _Wire()]
    for wire in wires:
        body = _ResponseBody(wire)
        owner.responses.append(httpx.Response(200, stream=body))
        owner.bodies.append(body)
    with pytest.raises(ExceptionGroup) as first:
        await owner.aclose()
    errors = tuple(body.close_error for body in owner.bodies[:2])
    assert first.value.message == "Gemini 资源关闭失败"
    assert first.value.exceptions == errors
    assert all(isinstance(error, RuntimeError) for error in errors)
    assert all(str(error) == "wire close failed" for error in errors)
    assert owner.bodies[2].close_error is None
    assert [wire.closed for wire in wires] == [1, 1, 1]
    assert all(response.is_closed for response in owner.responses)
    with pytest.raises(ExceptionGroup) as second:
        await owner.aclose()
    assert second.value is first.value
    assert [wire.closed for wire in wires] == [1, 1, 1]








class _BorrowedWireTransport(httpx.AsyncBaseTransport):
    """记录调用方传输关闭次数，用于跨循环和借用所有权验证。"""

    def __init__(self) -> None:
        """初始化关闭计数，不创建事件循环资源。"""
        self.closed = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """资源池测试不应产生实际请求。"""
        raise AssertionError("unexpected request")

    async def aclose(self) -> None:
        """仅由调用方显式调用时记录关闭。"""
        self.closed += 1
