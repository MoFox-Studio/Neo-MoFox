"""Real SDK / fake HTTP tests for the Gemini adapter and public request chain."""
import json

import httpx
import pytest

from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text, ToolResult
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy


def model() -> dict:
    """Use only a dummy credential and non-routable service URL."""
    return dict(api_provider="test", base_url="https://offline.invalid", model_identifier="test-model", api_key="dummy",
                client_type="gemini", timeout=5.0, max_retry=1, retry_interval=0, price_in=0., price_out=0.,
                temperature=0., max_tokens=32, extra_params={})


def response(*, tool: bool = False) -> dict:
    """Return a complete native response with an opaque function signature."""
    parts = [{"functionCall": {"name": "weather", "args": {"city": "Paris"}}, "thoughtSignature": "c2ln"}] if tool else [{"text": "ok"}]
    return {"candidates": [{"index": 0, "content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3, "totalTokenCount": 5}}


@pytest.mark.parametrize("stream", [False, True])
async def test_request_nonstream_stream_and_signed_tool_roundtrip(stream: bool) -> None:
    """The request result survives to_payload and a second SDK generation."""
    requests: list[dict] = []
    async def handler(req: httpx.Request) -> httpx.Response:
        """记录两轮原生请求，并按请求路径返回流式或非流式响应。"""
        requests.append(json.loads(req.content))
        result = response(tool=len(requests) == 1)
        if ":streamGenerateContent" in req.url.path:
            return httpx.Response(200, text="data: " + json.dumps(result) + "\n\n", headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=result)
    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    req = LLMRequest(model_set=[model()], clients=ModelClientRegistry(gemini=client), policy=RoundRobinPolicy(), enable_metrics=False)
    req.context_manager = None
    req.add_payload(LLMPayload(ROLE.USER, Text("hello")))
    try:
        result = await req.send(stream=stream)
        await result
        assert result.provider_state is not None
        assert result.call_list[0].name == "weather"
        result.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult({"weather": "sunny"}, call_id=result.call_list[0].id)))
        second = await result.send(stream=False)
        assert second.message == "ok"
        signed = requests[1]["contents"][1]["parts"][0]
        assert signed["thoughtSignature"] == "c2ln"
        assert requests[1]["contents"][2]["parts"][0]["functionResponse"]["name"] == "weather"
    finally:
        await client.aclose()


async def test_default_registry_is_a_real_gemini_adapter() -> None:
    """No caller-side fake registry is required to route the native protocol."""
    assert isinstance(ModelClientRegistry().gemini, GeminiChatClient)
