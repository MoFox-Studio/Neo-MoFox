"""Gemini 安全请求摘要与通用结果观测分离的离线公共链路回归。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from google.genai import errors, types

from src.kernel.llm import ROLE, LLMPayload, LLMRequest, Text
from src.kernel.llm import request_inspector as inspector_module
from src.kernel.llm.exceptions import LLMAuthenticationError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.request_inspector import RequestInspector
from src.kernel.llm.request_trace import Attempt, current_attempt
from test.kernel.llm.test_gemini_client import model as gemini_model
from test.kernel.llm.test_request_monitor import (
    FakeStream,
    make_client,
    model,
    sdk_events,
)


class LookupTool:
    """仅提供 schema，不执行实际工具。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """声明一个用于核对摘要工具计数的工具。"""
        return {
            "name": "lookup",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }


@pytest.fixture
def inspector(monkeypatch: pytest.MonkeyPatch) -> RequestInspector:
    """隔离当前测试的内存监视器。"""
    instance = RequestInspector()
    monkeypatch.setattr(inspector_module, "_inspector", instance)
    return instance


def native_response() -> dict[str, Any]:
    """提供有签名的正文、推理、工具和 usage，不附加 metadata。"""
    return {
        "candidates": [{
            "index": 0,
            "content": {
                "role": "model",
                "parts": [
                    {"text": "thinking", "thought": True},
                    {"text": "hello"},
                    {
                        "functionCall": {
                            "id": "call_1", "name": "lookup", "args": {"city": "上海"},
                        },
                        "thoughtSignature": "c2ln",
                    },
                ],
            },
            "finishReason": "STOP",
        }],
        "usageMetadata": {
            "promptTokenCount": 7,
            "candidatesTokenCount": 3,
            "thoughtsTokenCount": 2,
            "totalTokenCount": 12,
        },
    }


def make_request(client: GeminiChatClient) -> LLMRequest:
    """使用假 key、不可路由地址与零重试构造公共请求。"""
    config = gemini_model()
    config.update(model_identifier="models/test-model", max_retry=0)
    request = LLMRequest(
        [config], request_name="gemini-summary",
        clients=ModelClientRegistry(gemini=client), enable_metrics=False,
    )
    request.context_manager = None
    request.add_payload(LLMPayload(ROLE.SYSTEM, Text("private-system-text")))
    request.add_payload(LLMPayload(ROLE.USER, Text("private-user-text")))
    request.add_payload(LLMPayload(ROLE.TOOL, [LookupTool]))
    return request


def assert_gemini_request(record: dict[str, Any]) -> None:
    """核对 Gemini 最终 SDK 入参及 WebUI 派生消息/工具视图。"""
    assert record["api_name"] == "gemini.generate_content"
    assert record["model"] == "test-model"
    assert record["params"]["model"] == "test-model"
    assert len(record["params"]["contents"]) == 1
    assert record["params"]["config"]["tools"]
    assert record["metadata"]["request_name"] == "gemini-summary"
    assert record["metadata"]["capture_kind"] == "sdk_request"
    assert record["capture_info"]["request_unavailable"] is False
    assert record["rendered"]["messages"]
    assert record["rendered"]["tools"]
    text = json.dumps(record, ensure_ascii=False)
    for forbidden in (
        '"provider_state"', "GOOGLE_REPR_CALLED", "GOOGLE_STR_CALLED",
    ):
        assert forbidden not in text


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
@pytest.mark.parametrize("cross_task", [False, True], ids=["same-task", "cross-task"])
async def test_public_gemini_summary_preserves_standard_result(
    monkeypatch: pytest.MonkeyPatch, inspector: RequestInspector, stream: bool, cross_task: bool,
) -> None:
    """同一 Attempt 保存一次安全摘要，后续通用正文/工具/usage 仍完整入档。"""
    calls: list[dict[str, Any]] = []
    traces: list[Attempt] = []
    before_response: list[dict[str, Any]] = []
    representations = {"repr": 0, "str": 0}

    def counted_repr(_: Any) -> str:
        """可观察的 Google 原生对象 repr 哨兵。"""
        representations["repr"] += 1
        return "GOOGLE_REPR_CALLED"

    def counted_str(_: Any) -> str:
        """可观察的 Google 原生对象 str 哨兵。"""
        representations["str"] += 1
        return "GOOGLE_STR_CALLED"

    for native_type in (types.Part, types.Content, types.GenerateContentResponse):
        monkeypatch.setattr(native_type, "__repr__", counted_repr)
        monkeypatch.setattr(native_type, "__str__", counted_str)

    async def respond(request: httpx.Request) -> httpx.Response:
        """通过真实 SDK 的内存传输边界取得当前尝试和实际请求。"""
        calls.append(json.loads(request.content))
        trace = current_attempt()
        assert trace is not None
        traces.append(trace)
        record = inspector.get_record(trace.record_id)
        assert record is not None
        before_response.append(record)
        body = native_response()
        if stream:
            return httpx.Response(
                200, text="data: " + json.dumps(body) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    client = GeminiChatClient(transport=httpx.MockTransport(respond))
    try:
        response = await make_request(client).send(stream=stream)

        async def consume() -> str | None:
            """在指定任务中消费同一托管响应，不能依赖创建时的 ContextVar。"""
            return await response

        if cross_task:
            assert await asyncio.create_task(consume()) == "hello"
        else:
            assert await consume() == "hello"
        assert response.provider_state is not None
        assert len(calls) == len(traces) == 1
        assert len(calls[0]["contents"]) == 1
        assert len(calls[0]["tools"]) == 1
        assert_gemini_request(before_response[0])
        assert before_response[0]["status"] == "running"
        trace = traces[0]
        assert trace.request_captured is True
        assert trace.native_result_observed is False
        assert trace.bound is False
        record = inspector.get_record(trace.record_id)
        assert record is not None
        assert_gemini_request(record)
        assert record["status"] == "succeeded"
        assert record["stream"] is stream
        assert record["attempt"] == 1
        assert record["logical_request_id"] == trace.logical.id
        assert record["response"]["message"] == "hello"
        assert record["response"]["reasoning_content"] == "thinking"
        assert record["response"]["tool_calls"] == [
            {"id": "call_1", "name": "lookup", "args": {"city": "上海"}},
        ]
        assert record["response"]["usage"] == {
            "prompt_tokens": 7, "completion_tokens": 3, "reasoning_tokens": 2,
            "completion_includes_reasoning": False, "total_tokens": 12,
            "cache_hit_tokens": 0, "cache_miss_tokens": 7, "cache_write_tokens": 0,
        }
        assert record["response"]["stop_reason"] == "STOP"
        assert (record["tokens_in"], record["tokens_out"]) == (7, 3)
        assert record["capture_info"]["raw_unavailable"] is False
        raw = record["raw_events"][0] if stream else record["raw_response"]
        assert isinstance(raw, dict)
        assert raw["candidates"]
        snapshot = inspector.snapshot()
        assert snapshot["stats"] == {
            "total": 1, "by_status": {"succeeded": 1}, "tokens_in": 7, "tokens_out": 3,
        }
        assert [item["id"] for item in snapshot["records"]] == [trace.record_id]
        assert representations == {"repr": 0, "str": 0}
    finally:
        await client.aclose()


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
async def test_public_gemini_failure_keeps_safe_request_summary(
    inspector: RequestInspector, stream: bool,
) -> None:
    """SDK 调用失败也保留同一尝试的安全请求，原始 Google 结果仍不可用。"""
    calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        """返回不可重试的离线认证错误。"""
        nonlocal calls
        calls += 1
        return httpx.Response(
            401, json={"error": {"code": 401, "message": "offline authentication"}},
        )

    client = GeminiChatClient(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(LLMAuthenticationError) as raised:
            await make_request(client).send(stream=stream)
        # Inspector 记录 SDK 抛出的原始错误；公开请求层随后执行既有分类。
        assert isinstance(raised.value.__context__, errors.ClientError)
        assert calls == 1
        records = inspector.list_summaries()
        assert len(records) == 1
        record = inspector.get_record(records[0]["id"])
        assert record is not None
        assert_gemini_request(record)
        assert record["status"] == "failed"
        assert record["error"]["type"] == type(raised.value.__context__).__name__
        assert record["error"]["status_code"] is None
        assert record["capture_info"]["raw_unavailable"] is True
        assert record["response"]["message"] is None
        assert record["tokens_in"] is record["tokens_out"] is None
    finally:
        await client.aclose()


async def test_public_gemini_partial_error_preserves_text_and_summary(
    inspector: RequestInspector,
) -> None:
    """统一流的中途异常不覆盖已观察正文，也不丢失已捕获请求摘要。"""
    class BrokenBody(httpx.AsyncByteStream):
        """提供一个有效文本块后失败，不测试或改变 SDK 关闭仲裁。"""

        async def __aiter__(self) -> AsyncIterator[bytes]:
            """先发出标准文本，再模拟离线传输错误。"""
            body = {"candidates": [{"index": 0, "content": {
                "role": "model", "parts": [{"text": "partial"}],
            }}]}
            yield ("data: " + json.dumps(body) + "\n\n").encode("utf-8")
            raise httpx.ReadError("offline interrupted stream")

    async def respond(_: httpx.Request) -> httpx.Response:
        """注入内存流，无真实连接或存储。"""
        return httpx.Response(
            200, stream=BrokenBody(), headers={"content-type": "text/event-stream"},
        )

    client = GeminiChatClient(transport=httpx.MockTransport(respond))
    try:
        response = await make_request(client).send(stream=True)
        with pytest.raises(Exception) as caught:
            await response
        records = inspector.list_summaries()
        assert len(records) == 1
        record = inspector.get_record(records[0]["id"])
        assert record is not None
        assert_gemini_request(record)
        assert record["status"] == "failed"
        assert record["error"]["type"] == type(caught.value).__name__
        assert record["response"]["message"] == "partial"
        assert record["capture_info"]["raw_unavailable"] is False
        assert record["raw_events"][0]["candidates"]
    finally:
        await client.aclose()


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
async def test_direct_gemini_adapter_retains_capture_only_summary(
    inspector: RequestInspector, stream: bool,
) -> None:
    """无外层 Attempt 时保留既有手工捕获路径，不新增原生结果采集。"""
    async def respond(_: httpx.Request) -> httpx.Response:
        """为直接调用提供真实 SDK 可解码的响应。"""
        body = native_response()
        if stream:
            return httpx.Response(
                200, text="data: " + json.dumps(body) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    client = GeminiChatClient(transport=httpx.MockTransport(respond))
    request = make_request(client)
    try:
        result = await client.create(
            model_name="models/test-model", payloads=request.payloads, tools=[],
            request_name="gemini-summary", model_set=request.model_set[0], stream=stream,
        )
        if stream:
            assert result.stream_iter is not None
            events = [event async for event in result.stream_iter]
            assert "".join(event.text_delta or "" for event in events) == "hello"
            assert StreamEvent() not in events
        else:
            assert result.message == "hello"
        records = inspector.list_summaries()
        assert len(records) == 1
        record = inspector.get_record(records[0]["id"])
        assert record is not None
        assert_gemini_request(record)
        assert record["status"] == "captured"
        assert record["response"]["message"] is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
async def test_native_adapters_keep_request_and_result_ownership(
    monkeypatch: pytest.MonkeyPatch, inspector: RequestInspector,
    protocol: str, stream: bool,
) -> None:
    """原生适配器同时拥有请求与结果观测，通用层不得再次归并同一结果。"""
    event_calls: list[StreamEvent] = []
    result_calls: list[dict[str, Any]] = []
    traces: list[Attempt] = []
    original_event, original_result = Attempt.event, Attempt.result

    def observe_event(trace: Attempt, event: StreamEvent) -> StreamEvent:
        """跟踪实际观测调用次数，而不只验证最终正文碰巧相同。"""
        event_calls.append(event)
        traces.append(trace)
        return original_event(trace, event)

    def observe_result(trace: Attempt, **kwargs: Any) -> None:
        """跟踪非流式结果是否被重复写入。"""
        result_calls.append(kwargs)
        traces.append(trace)
        original_result(trace, **kwargs)

    monkeypatch.setattr(Attempt, "event", observe_event)
    monkeypatch.setattr(Attempt, "result", observe_result)
    sdk = FakeStream(sdk_events(protocol))
    client = make_client(monkeypatch, protocol, sdk_stream=sdk)
    request = LLMRequest(
        [model(protocol)], clients=ModelClientRegistry(**{protocol: client}),
        enable_metrics=False,
    )
    request.context_manager = None
    request.add_payload(LLMPayload(ROLE.USER, Text("offline")))
    response = await request.send(stream=stream)
    if stream:
        events = [event async for event in response.stream_events()]
        assert event_calls == events
        assert len(event_calls) == len(events) > 0
        assert result_calls == []
        assert StreamEvent() not in events
    else:
        assert await response == "hello"
        assert len(result_calls) == 1
        assert event_calls == []
    assert traces and all(trace is traces[0] for trace in traces)
    trace = traces[0]
    assert trace.request_captured is True
    assert trace.native_result_observed is True
    assert trace.bound is True
    assert len(inspector.list_summaries()) == 1
    record = inspector.get_record(trace.record_id)
    assert record is not None
    assert record["response"]["message"] == "hello"
    assert record["response"]["tool_calls"] == [
        {"id": "call_1", "name": "lookup", "args": {"city": "上海"}},
    ]
    assert (record["tokens_in"], record["tokens_out"]) == (3, 2)
    assert record["capture_info"]["request_unavailable"] is False
    assert record["capture_info"]["raw_unavailable"] is False
    if stream:
        assert len(record["raw_events"]) == len(sdk.events)


async def test_gemini_retry_attempts_keep_one_logical_safe_summary(inspector: RequestInspector) -> None:
    """一次可重试 SDK 失败后，两次 attempt 各自保存安全摘要与真实终态。"""
    calls = 0

    async def respond(_: httpx.Request) -> httpx.Response:
        """首个实际调用返回 503，后续调用返回合法模型结果。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"code": 503, "message": "offline unavailable"}})
        return httpx.Response(200, json=native_response())

    client = GeminiChatClient(transport=httpx.MockTransport(respond))
    request = make_request(client)
    request.model_set[0]["max_retry"] = 1
    request.model_set[0]["retry_interval"] = 0
    try:
        assert await (await request.send(stream=False)) == "hello"
        records = sorted(
            (inspector.get_record(row["id"]) for row in inspector.list_summaries()),
            key=lambda row: row["attempt"],
        )
        assert calls == len(records) == 2
        assert len({row["logical_request_id"] for row in records}) == 1
        assert [row["attempt"] for row in records] == [1, 2]
        assert [row["status"] for row in records] == ["failed", "succeeded"]
        for record in records:
            assert_gemini_request(record)
        assert records[0]["capture_info"]["raw_unavailable"] is True
        assert records[1]["capture_info"]["raw_unavailable"] is False
        assert records[0]["response"]["message"] is None
        assert records[1]["response"]["message"] == "hello"
    finally:
        await client.aclose()


async def test_gemini_failure_before_request_marks_request_unavailable(
    monkeypatch: pytest.MonkeyPatch, inspector: RequestInspector,
) -> None:
    """最终配置校验未通过时，不伪造已发送请求或安全数量摘要。"""
    from src.kernel.llm.exceptions import LLMConfigurationError

    client = GeminiChatClient(transport=httpx.MockTransport(lambda _: pytest.fail("不应发出请求")))
    request = make_request(client)
    request.model_set[0]["extra_params"] = {"unsupported_field": True}
    try:
        with pytest.raises(LLMConfigurationError):
            await request.send(stream=False)
        summaries = inspector.list_summaries()
        assert len(summaries) == 1
        record = inspector.get_record(summaries[0]["id"])
        assert record is not None
        assert record["status"] == "failed"
        assert record["api_name"] == "provider.create"
        assert "contents_count" not in record["params"]
        assert record["capture_info"]["request_unavailable"] is True
        assert record["capture_info"]["raw_unavailable"] is True
    finally:
        await client.aclose()
