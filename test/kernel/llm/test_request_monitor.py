"""Native request monitor integration tests and SDK-shaped, offline smoke fixtures."""

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
    """SDK-shaped object with deterministic JSON serialization, no network access."""

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """Convert nested fixtures like SDK Pydantic models."""

        def convert(value: Any) -> Any:
            """Convert recursive fixture contents."""
            if isinstance(value, SimpleNamespace):
                return {k: convert(v) for k, v in vars(value).items()}
            if isinstance(value, list):
                return [convert(v) for v in value]
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            return value

        return convert(self)


class FakeStream:
    """Async iterable and Anthropic context manager that records resource closure."""

    def __init__(self, events: list[Any], error: BaseException | None = None) -> None:
        self.events = events
        self.error = error
        self.closed = 0

    async def __aiter__(self) -> AsyncIterator[Any]:
        """Yield controlled events, then optionally fail."""
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        """Count close calls to assert idempotent resource cleanup."""
        self.closed += 1

    async def __aenter__(self) -> Self:
        """Enter Anthropic's native stream context."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close Anthropic's native stream context."""
        await self.aclose()


def model(protocol: str = "openai", **overrides: Any) -> dict[str, Any]:
    """Return a valid offline model configuration."""
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
    """Return a successful non-streaming response for each native chat API."""
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
    """Return native events with text, reasoning, fragmented tools and usage."""
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
    """Replace only SDK endpoints; keep the real adapter, parser and inspector."""
    client = {
        "openai": OpenAIChatClient,
        "anthropic": AnthropicChatClient,
        "responses": OpenAIResponsesClient,
    }[protocol]()

    async def create(**kwargs: Any) -> Any:
        """Record final SDK arguments without contacting a provider."""
        if calls is not None:
            calls.append(kwargs)
        if error is not None:
            raise error
        return sdk_stream if kwargs.get("stream") else sdk_response(protocol)

    def stream(**kwargs: Any) -> FakeStream:
        """Build the SDK's deferred Anthropic stream context."""
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
    """Invoke a real native adapter (also used by the WebUI offline smoke server)."""
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
    """Isolate the process singleton between tests."""
    inspector = RequestInspector()
    monkeypatch.setattr(inspector_module, "_inspector", inspector)
    return inspector


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("stream", [False, True])
async def test_native_chat_capture(
    monkeypatch: pytest.MonkeyPatch, protocol: str, stream: bool
) -> None:
    """Real adapters publish exact SDK params, content, tools and original events."""
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
    """Vector calls retain their inputs, raw result and actual usage, if present."""
    client = OpenAIChatClient()
    response = (
        JSON(data=[JSON(embedding=[0.1, 0.2])], usage=JSON(prompt_tokens=4))
        if kind == "embedding"
        else JSON(results=[JSON(index=0, relevance_score=0.9)])
    )

    async def create(**_: Any) -> JSON:
        """Return a controlled vector response."""
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
    """Failure/cancellation cannot be swallowed by native generator cleanup."""
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
    """Closing a response cancels pending observations and closes an open SDK once."""
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
    """wait_for cancellation belongs to its outer timeout, not user cancellation."""
    client = OpenAIChatClient()

    async def blocked(**_: Any) -> None:
        """Wait for the execution deadline."""
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
    """Framework retries use separate records but one logical request ID."""
    client = OpenAIChatClient()
    calls = 0

    async def create(**_: Any) -> JSON:
        """Fail once before returning an ordinary completion."""
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
    """LLMResponse's internal re-send must retain logical ID and increment attempt."""
    streams = [
        FakeStream(sdk_events("openai")[:2], TimeoutError("temporary")),
        FakeStream(sdk_events("openai")),
    ]

    async def create(**_: Any) -> FakeStream:
        """Return failing then successful native streams."""
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
    """A native stream's captured handle must not use the consumer's ContextVar."""
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
    """Broken monitoring must not change model result or native resource cleanup."""

    def broken(*_: Any, **__: Any) -> None:
        """Simulate a failing collector."""
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
    """IDs/revisions remain unique under concurrent writers and snapshots detach."""
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


def test_credentials_removed_from_all_views_and_error() -> None:
    """Serialization and derived views cannot reintroduce secrets."""
    inspector = get_inspector()
    secret = "private-credential-123"
    record_id = inspector.capture(
        "test",
        {
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
        },
        native=True,
    )
    inspector.start(record_id)
    inspector.raw(record_id, JSON(text=f"echo {secret}"))
    inspector.update(record_id, {"message": secret, "usage": {"prompt_tokens": 5}})
    inspector.finish(record_id, error=RuntimeError(f"key was {secret}"))
    serialized = json.dumps(inspector.get_record(record_id))
    assert secret not in serialized
    assert "another-secret" not in serialized
    assert "user:password" not in serialized
    assert inspector.get_record(record_id)["tokens_in"] == 5
    assert inspector.get_record(record_id)["capture_info"]["redacted"]


@pytest.mark.parametrize("event", [False, True])
def test_raw_budget_keeps_legal_json_and_normalized_response(event: bool) -> None:
    """Budget truncation limits serialized bytes without dropping normalized text."""
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
    """Unknown object conversion must remain safe even if str/repr leak credentials."""

    class Unsafe:
        """A sentinel that must never be represented."""

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
    """Responses input and tool results get semantic views without changing params."""
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
    """Every LLMResponse consumption API must complete the same native record."""
    sdk = FakeStream(sdk_events("openai"))
    client = make_client(monkeypatch, "openai", sdk_stream=sdk)
    request = LLMRequest(
        [model()], clients=ModelClientRegistry(openai=client), enable_metrics=False
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    response = await request.send(stream=True)

    async def callback(_: Any) -> None:
        """Accept observed chunks without side effects."""

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
    """Exceptions outside native iteration must not leave provider resources open."""
    sdk = FakeStream(sdk_events("openai"))
    client = make_client(monkeypatch, "openai", sdk_stream=sdk)
    request = LLMRequest(
        [model()], clients=ModelClientRegistry(openai=client), enable_metrics=False
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("q")))
    response = await request.send(stream=True)

    async def fail(_: Any) -> None:
        """Simulate user callback failure while a provider stream remains open."""
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
    """Creation exceptions are terminal even when bypassing LLMRequest."""
    error = asyncio.CancelledError() if cancel else RuntimeError("api_key=confidential")
    with pytest.raises(type(error)):
        await invoke(monkeypatch, protocol, error=error)
    record = get_inspector().get_record(1)
    assert record["status"] == ("cancelled" if cancel else "failed")
    assert "confidential" not in json.dumps(record)
    assert record["capture_info"]["raw_unavailable"]


async def test_native_preparation_error_is_visible() -> None:
    """Input conversion failure has a record but no fabricated SDK parameters."""
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
    """Business metrics may default missing usage to zero; inspection must not."""
    client = OpenAIChatClient()

    async def create(**_: Any) -> JSON:
        """Return an embedding result with only input usage."""
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
    """Empty output parser errors must not erase a successfully received SDK body."""
    client = OpenAIChatClient()

    async def create(**_: Any) -> JSON:
        """Return a provider-shaped response that the existing parser rejects."""
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


def test_redaction_of_json_strings_and_url_encoding() -> None:
    """Serialized function arguments and auth query strings need redaction too."""
    inspector = get_inspector()
    record_id = inspector.capture(
        "test",
        {
            "arguments": '{"api_key":"tool-secret","password":"private"}',
            "ordinary_url": "https://example.invalid/path?q=a%20b&version=1",
            "secure_url": "https://example.invalid/path?access_token=access-secret",
        },
    )
    record = inspector.get_record(record_id)
    serialized = json.dumps(record)
    assert "tool-secret" not in serialized
    assert "access-secret" not in serialized
    assert "private" not in serialized
    assert (
        record["params"]["ordinary_url"]
        == "https://example.invalid/path?q=a%20b&version=1"
    )
    assert json.loads(record["params"]["arguments"])["api_key"] == "[REDACTED]"


def test_raw_budget_exhaustion_and_terminal_immutability() -> None:
    """A full buffer rejects further events and terminal data remains unchanged."""
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
    """JSON snapshots never expose NaN, infinity, invalid keys or unsafe repr."""
    record_id = get_inspector().capture("test", {}, native=True)
    get_inspector().raw(record_id, value)
    detail = get_inspector().get_record(record_id)
    json.dumps(detail, allow_nan=False)
    if value is None:
        assert detail["capture_info"]["raw_bytes"] == 4


def test_cycle_and_deep_input_are_marked_unavailable() -> None:
    """Self-referential user metadata must not recurse forever or break a request."""
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
    """Eviction/clear affects observations only, never the SDK consumer."""
    result = await invoke(
        monkeypatch, "openai", stream=True, sdk_stream=FakeStream(sdk_events("openai"))
    )
    get_inspector().clear()
    chunks = [event.text_delta async for event in result[2] if event.text_delta]
    assert "".join(chunks) == "hello"
    assert get_inspector().snapshot()["stats"]["total"] == 0


def test_retry_context_is_consumed_before_nested_requests() -> None:
    """Context-compression or plugin sends nested inside a retry get their own IDs."""
    from src.kernel.llm.request_trace import new_logical_request, retry_scope

    logical = LogicalRequest()
    with retry_scope(logical):
        assert new_logical_request() is logical
        assert new_logical_request().id != logical.id
    assert new_logical_request().id != logical.id
