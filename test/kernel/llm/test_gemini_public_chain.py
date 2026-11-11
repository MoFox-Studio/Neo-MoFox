"""Offline public app/config/registry contracts using the real Gemini SDK."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from google import genai

from src.app.plugin_system.api import llm_api
from src.core.config.model_config import (
    APIProviderSection,
    ModelConfig,
    ModelInfoSection,
    TaskConfigSection,
)
from src.kernel.llm import LLMPayload, ROLE, Text, ToolResult
from src.kernel.llm.model_client import gemini_client
from src.kernel.llm.policy.round_robin import RoundRobinPolicy


class Weather:
    """Declare a tool without giving the SDK an executable Python callable."""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """Expose the ordinary MoFox function schema."""
        return {
            "name": "weather",
            "description": "Look up weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }


def _response(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a complete native wire response, including cumulative usage."""
    return {
        "candidates": [{
            "index": 0,
            "content": {"role": "model", "parts": parts},
            "finishReason": "STOP",
        }],
        "usageMetadata": {
            "promptTokenCount": 2,
            "candidatesTokenCount": 3,
            "totalTokenCount": 5,
        },
    }


@dataclass
class _OfflineWire:
    """Queue native HTTP responses; never replace a registry or SDK client."""

    replies: list[list[dict[str, Any]]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Capture actual SDK serialization and return JSON or SSE wire data."""
        self.requests.append(request)
        assert self.replies, "Unexpected SDK request (including automatic tool execution)"
        chunks = self.replies.pop(0)
        if request.url.path.endswith(":streamGenerateContent"):
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks),
                headers={"content-type": "text/event-stream"},
            )
        assert request.url.path.endswith(":generateContent")
        assert len(chunks) == 1
        return httpx.Response(200, json=chunks[0])


@pytest_asyncio.fixture
async def offline_wire(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_OfflineWire]:
    """Patch only transport construction and isolate the real default adapter."""
    wire = _OfflineWire()
    transport = httpx.MockTransport(wire.handle)
    monkeypatch.setattr(gemini_client, "_default_client", None)
    monkeypatch.setattr(
        gemini_client, "build_httpx_transport", lambda **kwargs: transport,
    )

    async def deny_async_network(
        self: httpx.AsyncHTTPTransport, request: httpx.Request,
    ) -> httpx.Response:
        """Fail immediately if any provider bypasses the injected transport."""
        raise AssertionError(f"Unexpected external HTTP request: {request.url}")

    def deny_sync_network(
        self: httpx.HTTPTransport, request: httpx.Request,
    ) -> httpx.Response:
        """Also forbid accidental synchronous SDK/provider requests."""
        raise AssertionError(f"Unexpected synchronous HTTP request: {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_sync_network)
    try:
        yield wire
    finally:
        client = gemini_client._default_client
        if client is not None:
            pool = client._pools.get(asyncio.get_running_loop(), {})
            leases = list(pool.values())
            assert leases, "Public request never constructed the real Gemini SDK"
            assert all(isinstance(lease.sdk, genai.Client) for lease in leases)
            assert all(lease.active == 0 for lease in leases)
            await gemini_client.close_gemini_clients()
            assert not pool
            assert all(lease.http.is_closed for lease in leases)
        await transport.aclose()


@pytest.fixture
def public_config(monkeypatch: pytest.MonkeyPatch) -> ModelConfig:
    """Use real provider/model/task sections, not handwritten ModelEntry dicts."""
    providers = [
        APIProviderSection(
            name=name,
            base_url=f"https://{name}.invalid/proxy/v1beta",
            api_key=f"dummy-{name}",
            client_type="google",
            max_retry=0,
            retry_interval=0,
            timeout=5,
        )
        for name in ("primary", "secondary")
    ]
    models = [
        ModelInfoSection(
            name=name,
            model_identifier=f"gemini-{name}",
            api_provider=name,
            extra_params={"trust_env": False, "force_sync_http": False},
        )
        for name in ("primary", "secondary")
    ]
    config = ModelConfig(
        api_providers=providers,
        models=models,
        model_tasks=ModelConfig.ModelTasksConfig(
            utils=TaskConfigSection(
                model_list=["primary", "secondary"], temperature=0.25, max_tokens=48,
            ),
        ),
    )
    monkeypatch.setattr(llm_api, "get_model_config", lambda: config)
    return config


@pytest.mark.parametrize("stream", [False, True])
async def test_public_config_to_default_sdk_text(
    offline_wire: _OfflineWire, public_config: ModelConfig, stream: bool,
) -> None:
    """App model lookup, default routing and real SDK work for JSON and SSE."""
    if stream:
        first = _response([{"text": "hello "}])
        first["candidates"][0].pop("finishReason")
        offline_wire.replies.append([first, _response([{"text": "world"}])])
    else:
        offline_wire.replies.append([_response([{"text": "hello world"}])])
    model_set = llm_api.get_model_set_by_name("primary", temperature=0.25, max_tokens=48)
    assert model_set == public_config.get_model_set_by_name(
        "primary", temperature=0.25, max_tokens=48,
    )
    assert model_set[0]["client_type"] == "gemini"
    request = llm_api.create_llm_request(model_set, request_name=f"public-text-{stream}")
    request.enable_metrics = False
    request.add_payload(LLMPayload(ROLE.SYSTEM, Text("Be concise")))
    request.add_payload(LLMPayload(ROLE.USER, Text("hello")))
    response = await request.send(stream=stream)
    if stream:
        events = [event async for event in response.stream_events()]
        assert [event.text_delta for event in events if event.text_delta] == ["hello ", "world"]
    else:
        assert await response == "hello world"
    assert response.message == "hello world"
    assert response.stop_reason == "STOP"
    assert response.to_payload().role == ROLE.ASSISTANT
    assert response.provider_state is not None
    assert len(offline_wire.requests) == 1
    wire = offline_wire.requests[0]
    method = "streamGenerateContent" if stream else "generateContent"
    assert str(wire.url).split("?")[0] == (
        f"https://primary.invalid/proxy/v1beta/models/gemini-primary:{method}"
    )
    assert wire.method == "POST"
    assert wire.headers["x-goog-api-key"] == "dummy-primary"
    assert "authorization" not in wire.headers
    assert wire.extensions["timeout"]["read"] == 5.0
    if stream:
        assert wire.url.params["alt"] == "sse"
    body = json.loads(wire.content)
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["systemInstruction"]["parts"] == [{"text": "Be concise"}]
    assert body["generationConfig"]["temperature"] == 0.25
    assert body["generationConfig"]["maxOutputTokens"] == 48
    assert not offline_wire.replies


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("native_ids", [[], ["native-paris", "native-tokyo"]])
@pytest.mark.parametrize("source_name", ["primary", "secondary"])
async def test_public_signed_tool_round_locks_target_and_preserves_ids(
    offline_wire: _OfflineWire,
    public_config: ModelConfig,
    stream: bool,
    native_ids: list[str],
    source_name: str,
) -> None:
    """Signed tool replay stays on its source and returns parallel native IDs."""
    cities = ["Paris", "Tokyo"] if native_ids else ["Paris"]
    parts: list[dict[str, Any]] = []
    for index, city in enumerate(cities):
        call: dict[str, Any] = {"name": "weather", "args": {"city": city}}
        if native_ids:
            call["id"] = native_ids[index]
        parts.append({"functionCall": call, "thoughtSignature": "c2ln"})
    offline_wire.replies.extend([
        [_response(parts)], [_response([{"text": "Sunny"}])],
        [_response([{"text": "Independent primary"}])],
        [_response([{"text": "Other target"}])],
    ])
    model_set = llm_api.get_model_set_by_task("utils")
    assert model_set == public_config.get_task("utils")
    assert len(model_set) == 2
    request = llm_api.create_llm_request(model_set, request_name="public-signed-tools")
    request.enable_metrics = False
    # A real policy deliberately advances to the other target on its next session.
    request.policy = RoundRobinPolicy()
    if source_name == "secondary":
        # Advance a real session, leaving model_set order untouched. This catches
        # implementations which mistakenly bind replay to model_set[0].
        priming = request.policy.new_session(
            model_set=model_set, request_name="public-signed-tools",
        )
        assert priming.first().model["api_provider"] == "primary"
    request.add_payload(LLMPayload(ROLE.TOOL, Weather))
    request.add_payload(LLMPayload(ROLE.USER, Text("Check the weather")))
    response = await request.send(stream=stream)
    await response
    assert len(offline_wire.requests) == 1
    assert response.provider_state is not None
    assert response.provider_state.target == (
        source_name, f"https://{source_name}.invalid/proxy/v1beta", f"gemini-{source_name}",
    )
    assert [call.name for call in response.call_list] == ["weather"] * len(cities)
    assert [call.args for call in response.call_list] == [{"city": city} for city in cities]
    ids = [call.id for call in response.call_list]
    assert len(set(ids)) == len(cities)
    assert all(ids)
    if native_ids:
        assert ids == native_ids
    assert response.to_payload().provider_state is response.provider_state
    # Reverse results to verify correlation by ID, not accidental list position.
    for call in reversed(response.call_list):
        response.add_payload(LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult({"city": call.args["city"], "weather": "sunny"}, call_id=call.id),
        ))
    second = await response.send(stream=False)
    assert await second == "Sunny"
    assert len(offline_wire.requests) == 2
    assert [wire.url.host for wire in offline_wire.requests] == [f"{source_name}.invalid"] * 2
    assert all(
        wire.url.path.startswith(f"/proxy/v1beta/models/gemini-{source_name}:")
        for wire in offline_wire.requests
    )
    assert all(
        wire.headers["x-goog-api-key"] == f"dummy-{source_name}"
        for wire in offline_wire.requests
    )
    first_body, replay_body = [json.loads(wire.content) for wire in offline_wire.requests]
    declarations = first_body["tools"][0]["functionDeclarations"]
    assert len(declarations) == 1
    assert declarations[0]["name"] == "weather"
    schema = declarations[0]["parameters_json_schema"]
    assert schema["required"] == ["city", "reason"]
    assert schema["properties"]["city"] == {"type": "string"}
    assert schema["properties"]["reason"]["type"] == "string"
    model_turns = [item for item in replay_body["contents"] if item["role"] == "model"]
    assert len(model_turns) == 1
    assert model_turns[0]["parts"] == parts
    returned = [
        part["functionResponse"]
        for item in replay_body["contents"]
        for part in item["parts"]
        if "functionResponse" in part
    ]
    assert [item["name"] for item in returned] == ["weather"] * len(cities)
    assert [item["response"]["city"] for item in returned] == list(reversed(cities))
    assert [item.get("id") for item in returned] == (
        list(reversed(native_ids)) if native_ids else [None]
    )
    # The locked continuation consumes one policy session as well. Independent
    # requests still see the original two-target set and resume ordinary rotation.
    for expected in ("Independent primary", "Other target"):
        fresh = llm_api.create_llm_request(model_set, request_name="public-signed-tools")
        fresh.enable_metrics = False
        fresh.policy = request.policy
        fresh.add_payload(LLMPayload(ROLE.USER, Text("An unrelated request")))
        assert await (await fresh.send(stream=False)) == expected
    independent_order = (
        ["primary", "secondary"] if source_name == "primary" else ["secondary", "primary"]
    )
    assert [wire.url.host for wire in offline_wire.requests[2:]] == [
        f"{name}.invalid" for name in independent_order
    ]
    assert offline_wire.requests[3].headers["x-goog-api-key"] == f"dummy-{independent_order[1]}"
    assert not offline_wire.replies
