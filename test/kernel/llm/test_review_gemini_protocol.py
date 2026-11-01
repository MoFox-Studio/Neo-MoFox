"""验证 Gemini 工具身份、签名回放与原始模型别名的离线协议契约。"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from google.genai import types

from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text, ToolResult
from src.kernel.llm.exceptions import LLMAPIError, LLMContextError, LLMProviderUnavailableError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.gemini_codec import decode_response, encode_payloads, target_identity
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from src.kernel.llm.types import ModelEntry


def _model(client_type: str = "gemini") -> ModelEntry:
    """构造保留原始 client_type 的公开模型条目，只使用离线凭据。"""
    return {
        "api_provider": "offline",
        "base_url": "https://offline.invalid",
        "model_identifier": "test-model",
        "api_key": "dummy",
        "client_type": client_type,
        "timeout": 5.0,
        "max_retry": 0,
        "retry_interval": 0,
        "price_in": 0.0,
        "cache_hit_price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.0,
        "max_tokens": 32,
        "max_context": 0,
        "tool_call_compat": False,
        "force_stream_mode": False,
        "extra_params": {},
    }


def _parts(native_ids: list[str | None]) -> list[dict[str, Any]]:
    """按给定原生 ID 构造工具 Part，并保留不透明签名字节与混合内容顺序。"""
    parts: list[dict[str, Any]] = []
    for index, native_id in enumerate(native_ids):
        call: dict[str, Any] = {"name": f"tool_{index}", "args": {"index": index}}
        if native_id is not None:
            call["id"] = native_id
        parts.append({
            "functionCall": call,
            "thoughtSignature": base64.b64encode(bytes([0, 255, index])).decode("ascii"),
        })
    parts.extend([
        {"text": "reason", "thought": True, "thoughtSignature": "AP8="},
        {"text": "working"},
        {"thoughtSignature": "c2ln"},
    ])
    return parts


def _response(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """把给定 Part 封装为 SDK 可解析的完整终态响应。"""
    return {"candidates": [{
        "index": 0,
        "content": {"role": "model", "parts": parts},
        "finishReason": "STOP",
    }]}


def _wire_response(request: httpx.Request, parts: list[dict[str, Any]]) -> httpx.Response:
    """按实际端点返回离线 JSON 或逐 Part SSE，流式仅在最后一块提交终态。"""
    if ":streamGenerateContent" not in request.url.path:
        return httpx.Response(200, json=_response(parts))
    chunks: list[str] = []
    for index, part in enumerate(parts):
        body = _response([part])
        if index != len(parts) - 1:
            body["candidates"][0].pop("finishReason")
        chunks.append("data: " + json.dumps(body) + "\n\n")
    return httpx.Response(
        200,
        text="".join(chunks),
        headers={"content-type": "text/event-stream"},
    )


def _request(model: ModelEntry, client: GeminiChatClient) -> LLMRequest:
    """通过真实注册表构造无上下文裁剪的公开请求，避免配置层重写别名。"""
    request = LLMRequest(
        model_set=[model],
        clients=ModelClientRegistry(gemini=client),
        policy=RoundRobinPolicy(),
        enable_metrics=False,
    )
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("hello"))]
    return request


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("native_ids", [
    ["gemini_call_1", None],
    [None, None, None],
    [None, "gemini_call_0"],
    [None, "gemini_call_0", "gemini_call_0_1"],
    ["", None],
])
async def test_unique_internal_ids_and_reverse_parallel_replay(
    stream: bool, native_ids: list[str | None],
) -> None:
    """流非流均保留每个调用，反序结果续轮时原生 ID、Part、签名与指纹不变。"""
    original_parts = _parts(native_ids)
    observed: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录真实 SDK 请求，并分别返回工具轮和文本终态。"""
        observed.append(json.loads(request.content))
        parts = original_parts if len(observed) == 1 else [{"text": "done"}]
        return _wire_response(request, parts)

    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    model = _model()
    try:
        response = await _request(model, client).send(stream=stream)
        await response
        assert len(response.call_list) == len(native_ids)
        internal_ids = [call.id for call in response.call_list]
        assert all(internal_ids)
        assert len(set(internal_ids)) == len(native_ids)
        assert [call.name for call in response.call_list] == [
            f"tool_{index}" for index in range(len(native_ids))
        ]
        assert [call.args for call in response.call_list] == [
            {"index": index} for index in range(len(native_ids))
        ]
        state = response.provider_state
        assert state is not None
        assert state.call_ids == tuple(zip(internal_ids, native_ids))
        assert state.matches(response.to_payload().content)
        snapshot = state.parts_json, state.content_fingerprint, state.call_ids
        decoded_parts = [types.Part.model_validate(part) for part in json.loads(state.parts_json)]
        assert [part.thought_signature for part in decoded_parts[:len(native_ids)]] == [
            bytes([0, 255, index]) for index in range(len(native_ids))
        ]
        contents, system, tools = encode_payloads([response.to_payload()], target_identity(model), [])
        assert contents[0].parts == decoded_parts
        assert system == [] and tools == []
        for call in reversed(response.call_list):
            response.add_payload(LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult({"index": call.args["index"]}, call_id=call.id),
            ))
        second = await response.send(stream=stream)
        assert await second == "done"
        assert len(observed) == 2
        assert observed[1]["contents"][1]["parts"] == original_parts
        responses = observed[1]["contents"][2]["parts"]
        assert [part["functionResponse"]["name"] for part in responses] == [
            f"tool_{index}" for index in reversed(range(len(native_ids)))
        ]
        assert [part["functionResponse"].get("id") for part in responses] == list(reversed(native_ids))
        assert [part["functionResponse"]["response"] for part in responses] == [
            {"index": index} for index in reversed(range(len(native_ids)))
        ]
        assert (state.parts_json, state.content_fingerprint, state.call_ids) == snapshot
    finally:
        await client.aclose()


@pytest.mark.parametrize("stream", [False, True])
async def test_duplicate_nonempty_native_ids_are_rejected(stream: bool) -> None:
    """原生 ID 重复时明确拒绝，不能归并参数或生成可回放的歧义工具轮。"""
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """始终返回重复原生 ID，不访问真实网络。"""
        observed.append(request)
        return _wire_response(request, _parts(["duplicate", "duplicate"]))

    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    response = None
    try:
        with pytest.raises(LLMAPIError, match="Duplicate Gemini function call ID"):
            response = await _request(_model(), client).send(stream=stream)
            await response
        assert len(observed) == 1
        if response is not None:
            assert response.call_list == []
            assert response.provider_state is None
    finally:
        await client.aclose()


def test_decode_intermediate_chunk_and_duplicate_validation() -> None:
    """中间块可以没有 finish，但重复非空原生 ID 的拒绝与终态一致。"""
    model = _model()
    body = _response(_parts([None, None]))
    body["candidates"][0].pop("finishReason")
    result = decode_response(
        types.GenerateContentResponse.model_validate(body), target_identity(model), require_finish=False,
    )
    assert result.tool_calls is not None
    assert len({call["id"] for call in result.tool_calls}) == 2
    duplicate = types.GenerateContentResponse.model_validate(_response(_parts(["same", "same"])))
    with pytest.raises(LLMAPIError, match="Duplicate Gemini function call ID"):
        decode_response(duplicate, target_identity(model), require_finish=False)
