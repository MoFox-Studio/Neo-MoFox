"""Pinned google-genai public transport/stream contracts, without external requests."""
import json

import httpx
import pytest
from google import genai
from google.genai import errors, types


@pytest.mark.parametrize("status", [429, 503])
async def test_custom_transport_one_attempt_and_ownership(status: int) -> None:
    """SDK must use the injected client once and leave its closure to MoFox."""
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": status, "message": "busy", "status": "UNAVAILABLE"}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    sdk = genai.Client(vertexai=False, api_key="dummy", http_options=types.HttpOptions(
        base_url="https://offline.invalid", api_version="v1beta", httpx_async_client=http,
        retry_options=types.HttpRetryOptions(attempts=1), timeout=1250,
    ))
    try:
        with pytest.raises(errors.APIError):
            await sdk.aio.models.generate_content(model="test-model", contents="hi")
        assert len(calls) == 1
        assert str(calls[0].url) == "https://offline.invalid/v1beta/models/test-model:generateContent"
        assert calls[0].headers["x-goog-api-key"] == "dummy"
        assert calls[0].extensions["timeout"]["read"] == 1.25
        await sdk.aio.aclose()
        assert not http.is_closed
    finally:
        await sdk.aio.aclose()
        sdk.close()
        await http.aclose()


async def test_stream_api_preserves_parts_and_signature() -> None:
    """The real SDK decodes SSE and preserves opaque Part signatures."""
    requests: list[httpx.Request] = []
    body = {"candidates": [{"index": 0, "content": {"role": "model", "parts": [
        {"text": "ok", "thoughtSignature": "c2ln"}
    ]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2}}

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="data: " + json.dumps(body) + "\n\n", headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sdk = genai.Client(vertexai=False, api_key="dummy", http_options=types.HttpOptions(
            httpx_async_client=http, base_url="https://offline.invalid", api_version="v1beta",
            retry_options=types.HttpRetryOptions(attempts=1),
        ))
        try:
            stream = await sdk.aio.models.generate_content_stream(model="test", contents="hi", config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                automatic_continuation=False,
            ))
            chunks = [chunk async for chunk in stream]
            assert len(requests) == 1
            assert requests[0].url.path.endswith(":streamGenerateContent")
            assert requests[0].url.params["alt"] == "sse"
            part = chunks[0].candidates[0].content.parts[0]
            assert part.thought_signature == b"sig"
            assert types.Part.model_validate_json(part.model_dump_json()).thought_signature == b"sig"
        finally:
            await sdk.aio.aclose()
            sdk.close()
