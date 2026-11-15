"""请求监视器回归：未消费流关闭与请求响应字段保真。"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any, Self

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from src.kernel.llm import ROLE, LLMPayload, Text
from src.kernel.llm import request_inspector as inspector_module
from src.kernel.llm.request_inspector import RequestInspector
from src.kernel.llm.request_trace import Attempt, LogicalRequest, attempt_scope
from test.kernel.llm.test_request_monitor import (
    FakeStream,
    invoke,
    make_client,
    model,
    sdk_events,
)


class TrackedStream(FakeStream):
    """跟踪读取和上下文进入次数，确保关闭操作不预读 SDK 事件。"""

    def __init__(self, events: list[Any], error: BaseException | None = None) -> None:
        """初始化受控流与资源计数。

        Args:
            events: 模拟 SDK 返回的事件。
            error: 完成事件读取后抛出的模拟异常。
        """
        super().__init__(events, error)
        self.reads = 0
        self.entered = 0

    async def __aiter__(self) -> AsyncIterator[Any]:
        """逐个记录并返回 SDK 事件，不在创建流时读取。"""
        async for event in super().__aiter__():
            self.reads += 1
            yield event

    async def __aenter__(self) -> Self:
        """记录进入 Anthropic 流上下文的次数并返回当前资源。"""
        self.entered += 1
        return self


@pytest.fixture(autouse=True)
def isolated_inspector(monkeypatch: pytest.MonkeyPatch) -> RequestInspector:
    """隔离每项回归测试的进程级监视器。"""
    inspector = RequestInspector()
    monkeypatch.setattr(inspector_module, "_inspector", inspector)
    return inspector


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
@pytest.mark.parametrize("consume_first", [False, True])
async def test_direct_stream_close_without_draining(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    protocol: str,
    consume_first: bool,
) -> None:
    """直接关闭适配器原生流，无论是否已消费都应可靠终结且不预读。"""
    sdk = TrackedStream(sdk_events(protocol))
    calls: list[dict[str, Any]] = []
    result = await invoke(
        monkeypatch, protocol, stream=True, sdk_stream=sdk, calls=calls
    )
    stream = result[2]
    assert inspect.isasyncgen(stream)
    assert sdk.reads == sdk.entered == 0
    record = isolated_inspector.get_record(1)
    assert record["status"] == "running"
    assert record["raw_events"] == []
    assert record["first_token_ms"] is None
    try:
        if consume_first:
            await anext(stream)
        reads = sdk.reads
        await stream.aclose()
        closed_record = isolated_inspector.get_record(1)
        await stream.aclose()
        assert closed_record["status"] == "cancelled"
        assert isolated_inspector.get_record(1) == closed_record
        assert sdk.closed == (0 if protocol == "anthropic" and not consume_first else 1)
        assert sdk.reads == reads
        if protocol == "anthropic" and not consume_first:
            assert calls == []
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()
        # 修复前失败时也释放测试资源，不把这一步计入上述关闭断言。
        if not sdk.closed:
            await sdk.aclose()


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
async def test_direct_stream_close_in_another_context(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    protocol: str,
) -> None:
    """跨上下文关闭未消费的流，不应结束消费方的其他请求记录。"""
    sdk = TrackedStream(sdk_events(protocol))
    result = await invoke(monkeypatch, protocol, stream=True, sdk_stream=sdk)
    other = Attempt(LogicalRequest(), model(protocol), "unrelated", stream=True)
    with attempt_scope(other):
        await result[2].aclose()
    assert isolated_inspector.get_record(1)["status"] == "cancelled"
    assert isolated_inspector.get_record(2)["status"] == "pending"
    assert sdk.reads == 0


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
async def test_startup_marker_never_becomes_a_model_event(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    protocol: str,
) -> None:
    """内部启动标记不对外产出、不进入原始事件、不影响正常完成。"""
    sdk = TrackedStream(sdk_events(protocol))
    result = await invoke(monkeypatch, protocol, stream=True, sdk_stream=sdk)
    events = [event async for event in result[2]]
    assert all(
        any(value is not None for value in asdict(event).values()) for event in events
    )
    completed = isolated_inspector.get_record(1)
    assert completed["status"] == "succeeded"
    assert len(completed["raw_events"]) == len(sdk.events)
    assert completed["response"]["message"] == "hello"
    assert sdk.closed == 1
    await result[2].aclose()
    assert isolated_inspector.get_record(1) == completed
    assert sdk.closed == 1


@pytest.mark.parametrize("protocol", ["openai", "responses"])
async def test_unconsumed_close_error_keeps_cancelled_state(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    protocol: str,
) -> None:
    """未消费流的资源关闭失败不妨碍取消终态，且不重复关闭。"""
    sdk = TrackedStream(sdk_events(protocol))

    async def broken_close() -> None:
        """模拟 SDK 关闭回调失败。"""
        sdk.closed += 1
        raise RuntimeError("offline close failure")

    monkeypatch.setattr(sdk, "aclose", broken_close)
    result = await invoke(monkeypatch, protocol, stream=True, sdk_stream=sdk)
    await result[2].aclose()
    await result[2].aclose()
    assert isolated_inspector.get_record(1)["status"] == "cancelled"
    assert sdk.closed == 1
    assert sdk.reads == 0


@pytest.mark.parametrize("cancel", [False, True])
async def test_anthropic_deferred_creation_error_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    cancel: bool,
) -> None:
    """延迟创建 Anthropic 流时的失败与取消仍应正确记录并传播。"""
    error = asyncio.CancelledError() if cancel else RuntimeError("offline failure")
    result = await invoke(monkeypatch, "anthropic", stream=True, error=error)
    with pytest.raises(type(error)):
        await anext(result[2])
    record = isolated_inspector.get_record(1)
    assert record["status"] == ("cancelled" if cancel else "failed")
    await result[2].aclose()
    assert isolated_inspector.get_record(1) == record


@pytest.mark.parametrize("mode", ["capture", "import"])
@pytest.mark.parametrize(
    "header",
    [
        "x-goog-api-key",
        "X-Goog-Api-Key",
        "x-vendor-api-key",
        "vendor_api_key",
        "xGoogApiKey",
        "x-api-key",
        "Ocp-Apim-Subscription-Key",
    ],
)
def test_vendor_headers_are_preserved_in_capture_and_import(
    mode: str, header: str
) -> None:
    """手工捕获与导入完整保留厂商请求头、业务字段及原始内容。"""
    inspector = RequestInspector()
    secret = "offline-independent-vendor-credential"
    params = {
        "model": "offline-demo",
        "messages": [{"role": "user", "content": f"echo {secret}"}],
        "extra_headers": {header: secret, "x-request-id": "safe-request-id"},
        "extra_body": {"nested": [{header: secret}], "max_tokens": 100},
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "api_key_count": 7,
    }
    if mode == "capture":
        record_id = inspector.capture("review.offline", params)
    else:
        imported = inspector.import_json(
            {"api_name": "review.offline", "params": params}
        )
        record_id = imported[0].id
        assert imported[0].to_full()["params"] == params
    record = inspector.get_record(record_id)
    headers = record["params"]["extra_headers"]
    assert headers[header] == secret
    assert headers["x-request-id"] == "safe-request-id"
    assert record["capture_info"]["redacted"] is False
    assert record["params"] == params
    for key in ("prompt_tokens", "completion_tokens", "api_key_count"):
        assert record["params"][key] == params[key]
    assert params["extra_headers"][header] == secret


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
@pytest.mark.parametrize("same_model_key", [False, True])
async def test_native_vendor_headers_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    protocol: str,
    same_model_key: bool,
) -> None:
    """无论厂商请求头是否与模型密钥相同，SDK 参数及快照都保持原值。"""
    config = model(protocol)
    secret = (
        config["api_key"] if same_model_key else "offline-independent-vendor-credential"
    )
    config["extra_params"] = {"headers": {"x-goog-api-key": secret}}
    calls: list[dict[str, Any]] = []
    client = make_client(monkeypatch, protocol, calls=calls)
    await client.create(
        model_name="demo",
        payloads=[LLMPayload(ROLE.USER, Text("offline review"))],
        tools=[],
        request_name="offline-review",
        model_set=config,
        stream=False,
    )
    record = isolated_inspector.get_record(1)
    assert calls[0]["extra_headers"]["x-goog-api-key"] == secret
    assert record["status"] == "succeeded"
    assert record["params"]["extra_headers"]["x-goog-api-key"] == secret
    assert record["params"] == calls[0]
    assert record["capture_info"]["redacted"] is False


def test_vendor_header_value_is_preserved_in_all_lifecycle_views() -> None:
    """请求头内容不会影响元数据、响应、工具参数及错误的原样保存。"""
    inspector = RequestInspector()
    secret = "offline-independent-vendor-credential"
    record_id = inspector.capture(
        "review.offline",
        {"model": "demo"},
        native=True,
    )
    inspector.start(
        record_id,
        {"extra_headers": {"x-goog-api-key": secret}},
        metadata={"echo": secret},
    )
    inspector.raw(record_id, {"echo": secret}, event=True)
    inspector.update(
        record_id,
        {
            "message": secret,
            "tool_calls": [{"args": json.dumps({"token": secret})}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
    )
    inspector.finish(record_id, error=RuntimeError(f"vendor rejected {secret}"))
    record = inspector.get_record(record_id)
    assert record["metadata"]["echo"] == secret
    assert record["raw_events"] == [{"echo": secret}]
    assert record["response"]["message"] == secret
    assert record["response"]["tool_calls"][0]["args"] == json.dumps({"token": secret})
    assert record["error"]["message"] == f"vendor rejected {secret}"
    assert record["capture_info"]["redacted"] is False
    assert (record["tokens_in"], record["tokens_out"]) == (3, 2)
    assert record["status"] == "failed"


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
async def test_real_sdk_unconsumed_stream_releases_http_response(
    monkeypatch: pytest.MonkeyPatch,
    isolated_inspector: RequestInspector,
    protocol: str,
) -> None:
    """通过离线 HTTP 传输验证真实 SDK 的未消费关闭，不访问网络。"""

    class ResponseBody(httpx.AsyncByteStream):
        """记录真实 SDK 是否预读响应及是否释放底层响应体。"""

        def __init__(self) -> None:
            """初始化响应体读取与关闭次数。"""
            self.reads = 0
            self.closed = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            """提供离线流终止事件；未消费就关闭时不应执行。"""
            self.reads += 1
            yield b"data: [DONE]\n\n"

        async def aclose(self) -> None:
            """记录底层 HTTP 响应体的释放次数。"""
            self.closed += 1

    body = ResponseBody()
    responses: list[httpx.Response] = []

    async def respond(_: httpx.Request) -> httpx.Response:
        """仅在内存中构造 HTTP 响应，不建立真实连接。"""
        response = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=body
        )
        responses.append(response)
        return response

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    )
    sdk_type = AsyncAnthropic if protocol == "anthropic" else AsyncOpenAI
    sdk = sdk_type(
        api_key="offline-sdk-credential",
        base_url="https://offline.invalid/v1",
        max_retries=0,
        http_client=http_client,
    )
    client = make_client(monkeypatch, protocol)
    monkeypatch.setattr(client, "_get_client", lambda **_: sdk)
    async with sdk:
        result = await client.create(
            model_name="demo",
            payloads=[LLMPayload(ROLE.USER, Text("offline review"))],
            tools=[],
            request_name="offline-sdk-review",
            model_set=model(protocol),
            stream=True,
        )
        await result[2].aclose()
        await result[2].aclose()
        assert body.reads == 0
        assert body.closed == (0 if protocol == "anthropic" else 1)
        assert len(responses) == (0 if protocol == "anthropic" else 1)
        assert all(response.is_closed for response in responses)
        assert isolated_inspector.get_record(1)["status"] == "cancelled"
