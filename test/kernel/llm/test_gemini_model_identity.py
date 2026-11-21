"""通过真实 Gemini SDK 和内存传输验证出站模型与签名回放身份一致。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

import httpx
import pytest
import pytest_asyncio

from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text, ToolResult
from src.kernel.llm.exceptions import LLMContextError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model, response


_SOURCE_TARGET = ("test", "https://offline.invalid/v1beta", "test-model")


@pytest_asyncio.fixture
async def offline_gemini() -> AsyncIterator[
    tuple[GeminiChatClient, list[httpx.Request]]
]:
    """只替换 HTTP 传输，首轮返回签名工具调用，后续返回普通文本。"""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """捕获 SDK 实际请求，按出站方法返回 JSON 或单块 SSE。"""
        requests.append(request)
        body: dict[str, Any] = response(tool=len(requests) == 1)
        if request.url.path.endswith(":streamGenerateContent"):
            return httpx.Response(
                200,
                text="data: " + json.dumps(body) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    async with httpx.MockTransport(handler) as transport:
        client = GeminiChatClient(transport=transport)
        try:
            yield client, requests
        finally:
            await client.aclose()


def _request(client: GeminiChatClient, config: dict[str, Any]) -> LLMRequest:
    """使用指定适配器与配置建立公共请求，不启用指标或上下文压缩。"""
    request = LLMRequest(
        model_set=[config],
        clients=ModelClientRegistry(gemini=client),
        policy=RoundRobinPolicy(),
        enable_metrics=False,
    )
    request.context_manager = None
    request.add_payload(LLMPayload(ROLE.USER, Text("hello")))
    return request


@pytest.mark.parametrize("model_name", ["other-model", "models/other-model"])
async def test_signed_history_rejects_direct_model_override_before_http(
    offline_gemini: tuple[GeminiChatClient, list[httpx.Request]],
    model_name: str,
) -> None:
    """A 模型生成的签名不得借用 A 配置直接发给 B，拒绝必须早于 HTTP。"""
    client, requests = offline_gemini
    config: dict[str, Any] = model()
    request = _request(client, config)
    first = await request.send(stream=False)
    await first
    assert first.provider_state is not None
    assert first.provider_state.target == _SOURCE_TARGET
    assert len(first.call_list) == 1
    first.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult({"weather": "sunny"}, call_id=first.call_list[0].id),
        )
    )
    assert len(requests) == 1

    original = deepcopy(config)
    with pytest.raises(LLMContextError):
        await client.create(
            model_name=model_name,
            payloads=first.payloads,
            tools=[],
            request_name="signed-model-override",
            model_set=config,
            stream=False,
        )

    assert len(requests) == 1
    assert config == original


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model_name", ["other-model", "models/other-model"])
async def test_unsigned_direct_override_records_actual_model_identity(
    offline_gemini: tuple[GeminiChatClient, list[httpx.Request]],
    model_name: str,
    stream: bool,
) -> None:
    """无签名历史允许直接选 B，出站与新状态均标 B，原 A 配置保持不变。"""
    client, requests = offline_gemini
    config: dict[str, Any] = model()
    original = deepcopy(config)
    result = await client.create(
        model_name=model_name,
        payloads=[LLMPayload(ROLE.USER, Text("hello"))],
        tools=[],
        request_name="unsigned-model-override",
        model_set=config,
        stream=stream,
    )
    if stream:
        assert result.stream_iter is not None
        events = [event async for event in result.stream_iter]
        assert events
        state = events[-1].provider_state
    else:
        assert result.stream_iter is None
        state = result.provider_state

    assert len(requests) == 1
    method = "streamGenerateContent" if stream else "generateContent"
    assert requests[0].url.path == f"/v1beta/models/other-model:{method}"
    assert config == original
    assert state is not None
    assert state.target == ("test", "https://offline.invalid/v1beta", "other-model")


@pytest.mark.parametrize(
    ("configured_name", "model_name"),
    [
        ("test-model", "models/test-model"),
        ("models/test-model", "test-model"),
    ],
)
async def test_direct_model_prefix_normalization_preserves_signed_history(
    offline_gemini: tuple[GeminiChatClient, list[httpx.Request]],
    configured_name: str,
    model_name: str,
) -> None:
    """同一模型带或不带 models/ 前缀均可续轮，且真实请求保留原始签名。"""
    client, requests = offline_gemini
    config: dict[str, Any] = model()
    config["model_identifier"] = configured_name
    first = await _request(client, config).send(stream=False)
    await first
    assert first.provider_state is not None
    assert first.provider_state.target == _SOURCE_TARGET
    first.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult({"weather": "sunny"}, call_id=first.call_list[0].id),
        )
    )

    original = deepcopy(config)
    second = await client.create(
        model_name=model_name,
        payloads=first.payloads,
        tools=[],
        request_name="normalized-model-identity",
        model_set=config,
        stream=False,
    )

    assert second.message == "ok"
    assert second.provider_state is not None
    assert second.provider_state.target == _SOURCE_TARGET
    assert len(requests) == 2
    assert all(
        request.url.path == "/v1beta/models/test-model:generateContent"
        for request in requests
    )
    body: dict[str, Any] = json.loads(requests[1].content)
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "c2ln"
    assert config == original


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("configured_name", ["test-model", "models/test-model"])
async def test_public_request_roundtrip_preserves_normalized_model_identity(
    offline_gemini: tuple[GeminiChatClient, list[httpx.Request]],
    configured_name: str,
    stream: bool,
) -> None:
    """正常公共请求链在非流式和流式续轮中保持来源锁、签名及前缀规范化。"""
    client, requests = offline_gemini
    config: dict[str, Any] = model()
    config["model_identifier"] = configured_name
    first = await _request(client, config).send(stream=stream)
    await first
    assert first.provider_state is not None
    assert first.provider_state.target == _SOURCE_TARGET
    first.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult({"weather": "sunny"}, call_id=first.call_list[0].id),
        )
    )
    second = await first.send(stream=stream)
    await second

    assert second.message == "ok"
    assert second.provider_state is not None
    assert second.provider_state.target == _SOURCE_TARGET
    assert len(requests) == 2
    method = "streamGenerateContent" if stream else "generateContent"
    assert all(
        request.url.path == f"/v1beta/models/test-model:{method}"
        for request in requests
    )
    body: dict[str, Any] = json.loads(requests[1].content)
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "c2ln"
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "weather"
