"""原生请求监视器集成测试，以及模拟 SDK 对象的离线冒烟测试夹具。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Self

import pytest

from src.kernel.llm import ROLE, LLMPayload, LLMRequest, Text, get_inspector
from src.kernel.llm import request_inspector as inspector_module
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.model_client.anthropic_client import AnthropicChatClient
from src.kernel.llm.model_client.openai_client import OpenAIChatClient
from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient
from src.kernel.llm.policy import RoundRobinPolicy
from src.kernel.llm.request_inspector import RequestInspector
from src.kernel.llm.request_trace import Attempt, LogicalRequest, attempt_scope


class JSON(SimpleNamespace):
    """模拟 SDK 对象，提供确定性的 JSON 序列化，不访问网络。"""

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """像 SDK 的 Pydantic 模型一样转换嵌套测试夹具。"""

        def convert(value: Any) -> Any:
            """递归转换测试夹具内容。"""
            if isinstance(value, SimpleNamespace):
                return {k: convert(v) for k, v in vars(value).items()}
            if isinstance(value, list):
                return [convert(v) for v in value]
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            return value

        return convert(self)


class FakeStream:
    """模拟异步流与 Anthropic 上下文管理器，并记录资源关闭次数。"""

    def __init__(self, events: list[Any], error: BaseException | None = None) -> None:
        self.events = events
        self.error = error
        self.closed = 0

    async def __aiter__(self) -> AsyncIterator[Any]:
        """依次产生受控事件，并按配置在结束后抛出异常。"""
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        """记录关闭次数，用于验证资源清理的幂等性。"""
        self.closed += 1

    async def __aenter__(self) -> Self:
        """进入模拟的 Anthropic 原生流上下文。"""
        return self

    async def __aexit__(self, *_: object) -> None:
        """关闭模拟的 Anthropic 原生流上下文。"""
        await self.aclose()


def model(protocol: str = "openai", **overrides: Any) -> dict[str, Any]:
    """返回用于离线测试的有效模型配置。"""
    return {
        "api_provider": "fixture",
        "base_url": "https://offline.invalid/v1",
        "model_identifier": "demo",
        "api_key": "fixture-secret-key",
        "client_type": protocol,
        "max_retry": 0,
        "timeout": 1,
        "retry_interval": 0,
        "price_in": 0,
        "price_out": 0,
        "temperature": 0.1,
        "max_tokens": 100,
        "extra_params": {},
        **overrides,
    }


def sdk_response(protocol: str) -> JSON:
    """为各原生聊天接口构造成功的非流式响应。"""
    if protocol == "openai":
        return JSON(
            choices=[
                JSON(
                    message=JSON(
                        content="hello",
                        reasoning_content="thinking",
                        tool_calls=[
                            JSON(
                                id="call_1",
                                function=JSON(
                                    name="lookup", arguments='{"city":"上海"}'
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=JSON(prompt_tokens=3, completion_tokens=2),
        )
    if protocol == "anthropic":
        return JSON(
            content=[
                JSON(type="text", text="hello"),
                JSON(type="thinking", thinking="thinking"),
                JSON(
                    type="tool_use", id="call_1", name="lookup", input={"city": "上海"}
                ),
            ],
            usage=JSON(input_tokens=3, output_tokens=2),
            stop_reason="tool_use",
        )
    return JSON(
        output=[
            JSON(type="message", content=[JSON(type="output_text", text="hello")]),
            JSON(
                type="reasoning", content=[JSON(type="reasoning_text", text="thinking")]
            ),
            JSON(
                type="function_call",
                call_id="call_1",
                name="lookup",
                arguments='{"city":"上海"}',
            ),
        ],
        usage=JSON(input_tokens=3, output_tokens=2),
        status="completed",
    )


def sdk_events(protocol: str) -> list[JSON]:
    """构造包含文本、推理、工具参数分片及用量的原生事件。"""
    if protocol == "openai":
        return [
            JSON(choices=[], extension="unrecognized event"),
            JSON(
                choices=[
                    JSON(
                        delta=JSON(content="hel", reasoning_content="think"),
                        finish_reason=None,
                    )
                ]
            ),
            JSON(
                choices=[
                    JSON(
                        delta=JSON(
                            content="lo",
                            tool_calls=[
                                JSON(
                                    index=0,
                                    id="call_1",
                                    function=JSON(name="lookup", arguments='{"city":'),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ]
            ),
            JSON(
                choices=[
                    JSON(
                        delta=JSON(
                            tool_calls=[
                                JSON(
                                    index=0,
                                    id=None,
                                    function=JSON(name=None, arguments='"上海"}'),
                                )
                            ]
                        ),
                        finish_reason="tool_calls",
                    )
                ]
            ),
            JSON(choices=[], usage=JSON(prompt_tokens=3, completion_tokens=2)),
        ]
    if protocol == "anthropic":
        return [
            JSON(type="message_start", message=JSON(usage=JSON(input_tokens=3))),
            JSON(type="unrecognized", content="keep me"),
            JSON(
                type="content_block_delta",
                index=0,
                delta=JSON(type="text_delta", text="hello"),
            ),
            JSON(
                type="content_block_delta",
                index=1,
                delta=JSON(type="thinking_delta", thinking="think"),
            ),
            JSON(
                type="content_block_start",
                index=2,
                content_block=JSON(type="tool_use", id="call_1", name="lookup"),
            ),
            JSON(
                type="content_block_delta",
                index=2,
                delta=JSON(type="input_json_delta", partial_json='{"city":'),
            ),
            JSON(
                type="content_block_delta",
                index=2,
                delta=JSON(type="input_json_delta", partial_json='"上海"}'),
            ),
            JSON(
                type="message_delta",
                delta=JSON(stop_reason="tool_use"),
                usage=JSON(output_tokens=2),
            ),
        ]
    return [
        JSON(type="response.web_search_call.completed", id="web_1"),
        JSON(type="response.output_text.delta", delta="hello"),
        JSON(type="response.reasoning_text.delta", delta="think"),
        JSON(
            type="response.output_item.added",
            item=JSON(
                type="function_call", id="item_1", call_id="call_1", name="lookup"
            ),
        ),
        JSON(
            type="response.function_call_arguments.delta",
            item_id="item_1",
            delta='{"city":',
        ),
        JSON(
            type="response.function_call_arguments.delta",
            item_id="item_1",
            delta='"上海"}',
        ),
        JSON(
            type="response.completed",
            response=JSON(
                status="completed", usage=JSON(input_tokens=3, output_tokens=2)
            ),
        ),
    ]


def make_client(
    patch: pytest.MonkeyPatch,
    protocol: str,
    *,
    sdk_stream: FakeStream | None = None,
    error: BaseException | None = None,
    calls: list[dict[str, Any]] | None = None,
) -> Any:
    """仅替换 SDK 调用入口，保留真实适配器、解析器和监视器。"""
    client = {
        "openai": OpenAIChatClient,
        "anthropic": AnthropicChatClient,
        "responses": OpenAIResponsesClient,
    }[protocol]()

    async def create(**kwargs: Any) -> Any:
        """记录最终 SDK 参数，不连接真实提供商。"""
        if calls is not None:
            calls.append(kwargs)
        if error is not None:
            raise error
        return sdk_stream if kwargs.get("stream") else sdk_response(protocol)

    def stream(**kwargs: Any) -> FakeStream:
        """构造延迟进入的 Anthropic SDK 流上下文。"""
        if calls is not None:
            calls.append(kwargs)
        if error is not None:
            raise error
        assert sdk_stream is not None
        return sdk_stream

    sdk = JSON(
        chat=JSON(completions=JSON(create=create)),
        responses=JSON(create=create),
        messages=JSON(create=create, stream=stream),
    )
    patch.setattr(client, "_get_client", lambda **_: sdk)
    return client


async def invoke(
    patch: pytest.MonkeyPatch,
    protocol: str,
    *,
    stream: bool = False,
    sdk_stream: FakeStream | None = None,
    error: BaseException | None = None,
    calls: list[dict[str, Any]] | None = None,
) -> tuple[Any, ...]:
    """调用真实原生适配器，也供 WebUI 离线冒烟测试服务使用。"""
    client = make_client(
        patch, protocol, sdk_stream=sdk_stream, error=error, calls=calls
    )
    return await client.create(
        model_name="demo",
        payloads=[LLMPayload(ROLE.USER, Text("question"))],
        tools=[],
        request_name="fixture-request",
        model_set=model(protocol),
        stream=stream,
    )


@pytest.fixture(autouse=True)
def isolated_inspector(monkeypatch: pytest.MonkeyPatch) -> RequestInspector:
    """在测试之间隔离进程级监视器单例。"""
    inspector = RequestInspector()
    monkeypatch.setattr(inspector_module, "_inspector", inspector)
    return inspector


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("stream", [False, True])
async def test_native_chat_capture(
    monkeypatch: pytest.MonkeyPatch, protocol: str, stream: bool
) -> None:
    """验证真实适配器记录准确的 SDK 参数、内容、工具与原始事件。"""
    calls: list[dict[str, Any]] = []
    sdk = FakeStream(sdk_events(protocol))
    result = await invoke(
        monkeypatch, protocol, stream=stream, sdk_stream=sdk, calls=calls
    )
    if stream:
        assert get_inspector().get_record(1)["status"] == "running"
        async for _ in result[2]:
            pass
        assert sdk.closed == 1
    record = get_inspector().get_record(1)
    assert record["status"] == "succeeded"
    assert record["response"]["message"] == "hello"
    assert record["response"]["reasoning_content"]
    assert record["response"]["tool_calls"][0]["args"] == {"city": "上海"}
    assert (record["tokens_in"], record["tokens_out"]) == (3, 2)
    assert record["request_name"] == "fixture-request"
    assert record["params"] == (
        {**calls[0], "stream": True} if protocol == "anthropic" and stream else calls[0]
    )
    assert record["capture_info"]["raw_unavailable"] is False
    if stream:
        assert len(record["raw_events"]) == len(sdk.events)
        assert record["first_token_ms"] is not None
    else:
        assert record["raw_response"] == sdk_response(protocol).model_dump()


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
async def test_native_vector_calls(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """验证向量调用保留输入、原始结果及实际返回的用量。"""
    client = OpenAIChatClient()
    response = (
        JSON(data=[JSON(embedding=[0.1, 0.2])], usage=JSON(prompt_tokens=4))
        if kind == "embedding"
        else JSON(results=[JSON(index=0, relevance_score=0.9)])
    )

    async def create(**_: Any) -> JSON:
        """返回受控的向量响应。"""
        return response

    monkeypatch.setattr(
        client,
        "_get_client",
        lambda **_: JSON(embeddings=JSON(create=create), rerank=JSON(create=create)),
    )
    if kind == "embedding":
        await client.create_embedding(
            model_name="demo",
            inputs=["query"],
            request_name="vector",
            model_set=model(),
        )
    else:
        await client.create_rerank(
            model_name="demo",
            query="query",
            documents=["doc"],
            top_n=1,
            request_name="vector",
            model_set=model(),
        )
    record = get_inspector().get_record(1)
    assert record["status"] == "succeeded"
    assert record["request_name"] == "vector"
    assert record["response"]["kind"] == kind
    assert record["raw_response"] == response.model_dump()
    assert record["response"]["embeddings" if kind == "embedding" else "results"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize(
    "error", [RuntimeError("provider error"), asyncio.CancelledError()]
)
async def test_stream_failure_preserves_partial_output(
    monkeypatch: pytest.MonkeyPatch, protocol: str, error: BaseException
) -> None:
    """验证原生生成器清理不会吞掉失败或取消异常。"""
    sdk = FakeStream(sdk_events(protocol), error)
    result = await invoke(monkeypatch, protocol, stream=True, sdk_stream=sdk)
    with pytest.raises(type(error)):
        async for _ in result[2]:
            pass
    record = get_inspector().get_record(1)
    assert record["status"] == (
        "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
    )
    assert record["response"]["message"] == "hello"
    assert sdk.closed == 1


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("consume_first", [False, True])
async def test_response_close_without_draining(
    monkeypatch: pytest.MonkeyPatch, protocol: str, consume_first: bool
) -> None:
    """验证关闭响应会取消未完成的观测，并且仅关闭一次已打开的 SDK 资源。"""
    sdk = FakeStream(sdk_events(protocol))
    client = make_client(monkeypatch, protocol, sdk_stream=sdk)
    registry = ModelClientRegistry(**{protocol: client})
    request = LLMRequest([model(protocol)], clients=registry, enable_metrics=False)
    request.add_payload(LLMPayload(ROLE.USER, Text("question")))
    response = await request.send(stream=True)
    if consume_first:
        await anext(response._stream)
    await response.aclose()
    await response.aclose()
    record = get_inspector().get_record(1)
    assert record["status"] == "cancelled"
    assert sdk.closed == (0 if protocol == "anthropic" and not consume_first else 1)
    assert len(get_inspector().snapshot()["records"]) == 1


async def test_creation_timeout_is_failure_not_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 wait_for 引发的取消归入外层超时，而非用户取消。"""
    client = OpenAIChatClient()

    async def blocked(**_: Any) -> None:
        """等待请求执行超时。"""
        await asyncio.sleep(30)

    monkeypatch.setattr(
        client,
        "_get_client",
        lambda **_: JSON(chat=JSON(completions=JSON(create=blocked))),
    )
    request = LLMRequest(
        [model(timeout=0.02)],
        clients=ModelClientRegistry(openai=client),
        enable_metrics=False,
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    with pytest.raises(TimeoutError):
        await request.send(stream=False)
    record = get_inspector().get_record(1)
    assert record["status"] == "failed"
    assert record["error"]["type"] == "TimeoutError"


async def test_retry_and_switch_link_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证框架重试分别建档，但共享同一逻辑请求 ID。"""
    client = OpenAIChatClient()
    calls = 0

    async def create(**_: Any) -> JSON:
        """先模拟一次失败，再返回普通补全响应。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return sdk_response("openai")

    monkeypatch.setattr(
        client,
        "_get_client",
        lambda **_: JSON(chat=JSON(completions=JSON(create=create))),
    )
    request = LLMRequest(
        [model(model_identifier="first"), model(model_identifier="second")],
        clients=ModelClientRegistry(openai=client),
        policy=RoundRobinPolicy(),
        enable_metrics=False,
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    await request.send(stream=False)
    records = get_inspector().snapshot()["records"]
    assert [r["status"] for r in records] == ["failed", "succeeded"]
    assert [r["attempt"] for r in records] == [1, 2]
    assert len({r["logical_request_id"] for r in records}) == 1
    await request.send(stream=False)
    assert (
        get_inspector().snapshot()["records"][-1]["logical_request_id"]
        != records[0]["logical_request_id"]
    )


async def test_stream_retry_reuses_logical_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LLMResponse 内部重发保留逻辑 ID，并递增尝试序号。"""
    streams = [
        FakeStream(sdk_events("openai")[:2], TimeoutError("temporary")),
        FakeStream(sdk_events("openai")),
    ]

    async def create(**_: Any) -> FakeStream:
        """先返回失败的原生流，再返回成功的原生流。"""
        return streams.pop(0)

    monkeypatch.setattr(
        OpenAIChatClient,
        "_get_client",
        lambda *_, **__: JSON(chat=JSON(completions=JSON(create=create))),
    )
    request = LLMRequest([model(max_retry=1)], enable_metrics=False)
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    response = await request.send(stream=True)
    assert await response == "hello"
    records = get_inspector().snapshot()["records"]
    assert [r["attempt"] for r in records] == [1, 2]
    assert [r["status"] for r in records] == ["failed", "succeeded"]
    assert len({r["logical_request_id"] for r in records}) == 1


async def test_consumption_in_different_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证原生流使用已保存的句柄，不误用消费任务的上下文变量。"""
    result = await invoke(
        monkeypatch, "openai", stream=True, sdk_stream=FakeStream(sdk_events("openai"))
    )
    other = Attempt(LogicalRequest(), model(), "other")
    with attempt_scope(other):
        async for _ in result[2]:
            pass
    assert get_inspector().get_record(1)["status"] == "succeeded"
    assert get_inspector().get_record(2)["status"] == "pending"


async def test_observation_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证监视器故障不影响模型结果或原生资源清理。"""

    def broken(*_: Any, **__: Any) -> None:
        """模拟采集器故障。"""
        raise RuntimeError("collector failure")

    monkeypatch.setattr(get_inspector(), "capture", broken)
    assert (await invoke(monkeypatch, "openai"))[0] == "hello"
    result = await invoke(
        monkeypatch,
        "responses",
        stream=True,
        sdk_stream=FakeStream(sdk_events("responses")),
    )
    async for _ in result[2]:
        pass


def test_buffer_atomicity_isolation_eviction_and_clear() -> None:
    """验证并发写入下 ID 与版本不重复，且快照与内部状态隔离。"""
    inspector = RequestInspector()
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(
            executor.map(
                lambda i: inspector.capture(
                    "test", {"model": "m", "input": [i]}, native=True
                ),
                range(240),
            )
        )
    assert len(set(ids)) == 240
    snapshot = inspector.snapshot()
    assert snapshot["stats"]["total"] == 200
    assert snapshot["revision"] == 240
    assert inspector.get_record(min(ids)) is None
    record_id = max(ids)
    detail = inspector.get_record(record_id)
    detail["params"]["input"].append("mutated")
    assert "mutated" not in inspector.get_record(record_id)["params"]["input"]
    inspector.start(record_id)
    inspector.update(record_id, {"message": "partial"})
    inspector.finish(record_id)
    before = inspector.get_record(record_id)
    inspector.finish(record_id, error=RuntimeError("late"))
    assert inspector.get_record(record_id) == before
    assert inspector.clear_snapshot()["cleared_count"] == 200
    inspector.finish(record_id)
    inspector.raw(record_id, {"late": True})
    assert inspector.snapshot()["records"] == []
    assert inspector.capture("new", {}) > record_id
    assert RequestInspector().snapshot()["instance_id"] != snapshot["instance_id"]


def test_values_are_preserved_in_all_views_and_error() -> None:
    """请求、响应、派生视图与错误原样保存，不扫描或替换凭据内容。"""
    inspector = get_inspector()
    secret = "offline-credential-123"
    params = {
        "model": "m",
        "messages": [{"role": "user", "content": f"echo {secret}"}],
        "extra_headers": {"Authorization": f"Bearer {secret}"},
        "url": "https://user:password@example.invalid/v1?api_key=another-secret&version=1",
        "tools": [
            {
                "type": "function",
                "function": {"name": "lookup", "parameters": {"type": "object"}},
            }
        ],
    }
    record_id = inspector.capture("test", params, native=True)
    inspector.start(record_id)
    inspector.raw(record_id, JSON(text=f"echo {secret}"))
    inspector.update(record_id, {"message": secret, "usage": {"prompt_tokens": 5}})
    inspector.finish(record_id, error=RuntimeError(f"key was {secret}"))
    record = inspector.get_record(record_id)
    assert record["params"] == params
    assert record["raw_response"] == {"text": f"echo {secret}"}
    assert record["response"]["message"] == secret
    assert record["error"]["message"] == f"key was {secret}"
    assert secret in json.dumps(record["rendered"])
    assert record["tokens_in"] == 5
    assert record["capture_info"]["redacted"] is False


@pytest.mark.parametrize("event", [False, True])
def test_raw_budget_keeps_legal_json_and_normalized_response(event: bool) -> None:
    """验证预算截断限制序列化字节数，但不丢弃标准化文本。"""
    inspector = RequestInspector(raw_limit_bytes=128)
    record_id = inspector.capture("test", {}, native=True, stream=event)
    inspector.start(record_id)
    inspector.raw(record_id, {"text": "中文🙂" * 10000}, event=event)
    inspector.raw(record_id, {"later": True}, event=event)
    inspector.update(record_id, {"message": "complete normalized response"})
    inspector.finish(record_id)
    detail = inspector.get_record(record_id)
    assert detail["capture_info"]["raw_truncated"]
    assert detail["capture_info"]["raw_bytes"] <= 128
    assert detail["response"]["message"] == "complete normalized response"
    json.loads(json.dumps(detail))


def test_unserializable_objects_do_not_use_repr() -> None:
    """验证未知对象的 str/repr 即使会泄露凭据，也不会在转换时被调用。"""

    class Unsafe:
        """用于验证不会调用对象文本表示方法的哨兵对象。"""

        def __repr__(self) -> str:
            raise AssertionError("repr called")

        def __str__(self) -> str:
            raise AssertionError("str called")

    inspector = get_inspector()
    record_id = inspector.capture(
        "test", {"model": "m", "unknown": Unsafe()}, {"unknown": Unsafe()}, native=True
    )
    inspector.raw(record_id, Unsafe())
    detail = inspector.get_record(record_id)
    assert detail["params"]["model"] == "m"
    assert detail["capture_info"]["request_unavailable"]
    assert detail["capture_info"]["metadata_unavailable"]
    assert detail["capture_info"]["raw_unavailable"]


def test_responses_request_structure_and_unknown_fields_are_preserved() -> None:
    """验证 Responses 输入与工具结果生成语义视图，且不改变请求参数。"""
    params = {
        "model": "m",
        "instructions": "system",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            },
            {
                "type": "function_call",
                "call_id": "c",
                "name": "lookup",
                "arguments": '{"q":1}',
            },
            {"type": "function_call_output", "call_id": "c", "output": "done"},
        ],
        "extra_body": {"unknown": {"nested": [1, None, False]}},
    }
    record_id = get_inspector().capture("responses.create", params)
    detail = get_inspector().get_record(record_id)
    assert detail["params"] == params
    assert [m["role"] for m in detail["rendered"]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert detail["rendered"]["messages"][1]["blocks"][0]["text"] == "hi"
    assert detail["status"] == "captured"


@pytest.mark.parametrize(
    "method", ["text", "events", "buffer", "text_callback", "event_callback", "await"]
)
async def test_all_response_consumers_finish_and_close(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """验证 LLMResponse 各消费接口均完成同一条原生记录。"""
    sdk = FakeStream(sdk_events("openai"))
    client = make_client(monkeypatch, "openai", sdk_stream=sdk)
    request = LLMRequest(
        [model()], clients=ModelClientRegistry(openai=client), enable_metrics=False
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    response = await request.send(stream=True)

    async def callback(_: Any) -> None:
        """接收观测到的分块，不产生额外副作用。"""

    if method == "await":
        await response
    elif method == "text_callback":
        await response.stream_with_callback(callback)
    elif method == "event_callback":
        await response.stream_events_with_callback(callback)
    else:
        iterator = {
            "text": response.__aiter__,
            "events": response.stream_events,
            "buffer": response.stream_with_buffer,
        }[method]()
        async for _ in iterator:
            pass
    await response.aclose()
    assert get_inspector().get_record(1)["status"] == "succeeded"
    assert sdk.closed == 1


@pytest.mark.parametrize("event_callback", [False, True])
async def test_callback_failure_closes_native_stream(
    monkeypatch: pytest.MonkeyPatch, event_callback: bool
) -> None:
    """验证原生迭代之外的异常也不会遗留未关闭的提供商资源。"""
    sdk = FakeStream(sdk_events("openai"))
    client = make_client(monkeypatch, "openai", sdk_stream=sdk)
    request = LLMRequest(
        [model()], clients=ModelClientRegistry(openai=client), enable_metrics=False
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    response = await request.send(stream=True)

    async def fail(_: Any) -> None:
        """模拟提供商流尚未关闭时用户回调抛出异常。"""
        raise ValueError("callback failed")

    with pytest.raises(ValueError, match="callback failed"):
        if event_callback:
            await response.stream_events_with_callback(fail)
        else:
            await response.stream_with_callback(fail)
    assert get_inspector().get_record(1)["status"] == "failed"
    assert sdk.closed == 1


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_native_creation_errors(
    monkeypatch: pytest.MonkeyPatch, protocol: str, cancel: bool
) -> None:
    """验证绕过 LLMRequest 直接调用时，创建异常仍使记录进入终态。"""
    error = asyncio.CancelledError() if cancel else RuntimeError("api_key=confidential")
    with pytest.raises(type(error)):
        await invoke(monkeypatch, protocol, error=error)
    record = get_inspector().get_record(1)
    assert record["status"] == ("cancelled" if cancel else "failed")
    if not cancel:
        assert record["error"]["message"] == "api_key=confidential"
    assert record["capture_info"]["raw_unavailable"]


async def test_native_preparation_error_is_visible() -> None:
    """验证输入转换失败仍有记录，但不伪造 SDK 参数。"""
    with pytest.raises(TypeError):
        await OpenAIChatClient().create(
            model_name="m",
            payloads=[],
            tools=[],
            request_name="invalid",
            model_set=None,
            stream=False,
        )
    record = get_inspector().get_record(1)
    assert record["status"] == "failed"
    assert record["capture_info"]["request_unavailable"]


async def test_missing_usage_counter_remains_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证观测不沿用业务统计将缺失用量补零的行为。"""
    client = OpenAIChatClient()

    async def create(**_: Any) -> JSON:
        """返回仅包含输入用量的嵌入结果。"""
        return JSON(data=[JSON(embedding=[1.0])], usage=JSON(prompt_tokens=4))

    monkeypatch.setattr(
        client, "_get_client", lambda **_: JSON(embeddings=JSON(create=create))
    )
    await client.create_embedding(
        model_name="m", inputs=["q"], request_name="embedding", model_set=model()
    )
    record = get_inspector().get_record(1)
    assert record["tokens_in"] == 4
    assert record["tokens_out"] is None


async def test_sdk_parse_error_keeps_raw_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证空输出解析错误不会抹去已经收到的 SDK 原始响应。"""
    client = OpenAIChatClient()

    async def create(**_: Any) -> JSON:
        """返回结构模拟提供商、但会被现有解析器拒绝的响应。"""
        return JSON(choices=[], extra="diagnostic body")

    monkeypatch.setattr(
        client,
        "_get_client",
        lambda **_: JSON(chat=JSON(completions=JSON(create=create))),
    )
    from src.kernel.llm.exceptions import LLMContentFilterError

    with pytest.raises(LLMContentFilterError):
        await client.create(
            model_name="m",
            payloads=[],
            tools=[],
            request_name="empty",
            model_set=model(),
            stream=False,
        )
    record = get_inspector().get_record(1)
    assert record["status"] == "failed"
    assert record["raw_response"]["extra"] == "diagnostic body"


def test_json_strings_and_urls_are_preserved() -> None:
    """JSON 字符串、认证 URL、编码方式及普通正文均不被改写。"""
    inspector = get_inspector()
    params = {
        "arguments": '{"api_key":"tool-secret","password":"private"}',
        "ordinary_url": "https://example.invalid/path?q=a%20b&version=1",
        "secure_url": "https://user:password@example.invalid/path?access_token=access-secret",
        "content": "password=example-value，后面还有普通说明。",
    }
    record_id = inspector.capture("test", params)
    record = inspector.get_record(record_id)
    assert record["params"] == params
    assert record["capture_info"]["redacted"] is False


def test_raw_budget_exhaustion_and_terminal_immutability() -> None:
    """验证原始数据预算耗尽后不再保存事件，终态数据也不会被覆盖。"""
    inspector = RequestInspector(raw_limit_bytes=20)
    record_id = inspector.capture("test", {}, native=True, stream=True)
    inspector.start(record_id)
    for _ in range(10):
        inspector.raw(record_id, {"n": 1}, event=True)
    record = inspector.get_record(record_id)
    assert record["capture_info"]["raw_truncated"]
    assert record["capture_info"]["raw_bytes"] <= 20
    inspector.finish(record_id)
    version = inspector.get_record(record_id)["version"]
    inspector.update(record_id, {"message": "late"})
    inspector.start(record_id, {"late": True})
    inspector.mark_unavailable(record_id, raw=True)
    assert inspector.get_record(record_id)["version"] == version


@pytest.mark.parametrize(
    "value", [None, float("nan"), {"number": float("inf")}, {1: "invalid key"}]
)
def test_non_json_scalars_have_safe_snapshot(value: Any) -> None:
    """验证 JSON 快照不包含非数值、无穷值、非法键或不安全的对象文本表示。"""
    record_id = get_inspector().capture("test", {}, native=True)
    get_inspector().raw(record_id, value)
    detail = get_inspector().get_record(record_id)
    json.dumps(detail, allow_nan=False)
    if value is None:
        assert detail["capture_info"]["raw_bytes"] == 4


def test_cycle_and_deep_input_are_marked_unavailable() -> None:
    """验证自引用元数据不会导致无限递归或中断请求。"""
    circular: dict[str, Any] = {}
    circular["self"] = circular
    deep: dict[str, Any] = {}
    cursor = deep
    for _ in range(80):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    record_id = get_inspector().capture("test", deep, circular)
    record = get_inspector().get_record(record_id)
    assert record["capture_info"]["metadata_unavailable"]
    assert record["capture_info"]["request_unavailable"]


async def test_clear_during_live_native_stream_does_not_resurrect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证淘汰和清空仅影响观测，不影响 SDK 流消费。"""
    result = await invoke(
        monkeypatch, "openai", stream=True, sdk_stream=FakeStream(sdk_events("openai"))
    )
    get_inspector().clear()
    chunks = [event.text_delta async for event in result[2] if event.text_delta]
    assert "".join(chunks) == "hello"
    assert get_inspector().snapshot()["stats"]["total"] == 0


def test_retry_context_is_consumed_before_nested_requests() -> None:
    """验证重试内部嵌套的上下文压缩或插件发送获得独立逻辑 ID。"""
    from src.kernel.llm.request_trace import new_logical_request, retry_scope

    logical = LogicalRequest()
    with retry_scope(logical):
        assert new_logical_request() is logical
        assert new_logical_request().id != logical.id
    assert new_logical_request().id != logical.id
