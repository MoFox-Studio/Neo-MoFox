"""End-to-end SDK retry/terminal behavior through real MoFox execution."""
import json
from unittest.mock import patch

import httpx
import pytest

from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
from src.kernel.llm.exceptions import LLMRateLimitError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model, response


@pytest.mark.parametrize("consume", ["await", "events", "text_callback", "event_callback", "buffer"])
async def test_transient_stream_failure_retries_once_and_records_outcome(consume: str) -> None:
    """Every consumer shares a finite attempt budget and truthful observations."""
    calls: list[int] = []
    stats: list[dict] = []
    async def handler(req: httpx.Request) -> httpx.Response:
        """记录尝试次数，首次返回限流，随后提供成功的 SSE 响应。"""
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "busy"}}, headers={"retry-after": "0"})
        return httpx.Response(200, text="data: "+json.dumps(response())+"\n\n", headers={"content-type": "text/event-stream"})
    adapter = GeminiChatClient(transport=httpx.MockTransport(handler))
    request = LLMRequest(model_set=[model()], clients=ModelClientRegistry(gemini=adapter), policy=RoundRobinPolicy())
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("hi"))]
    async def ignore(value: object) -> None:
        """Consume without introducing external effects."""
    try:
        with patch("src.kernel.llm.request._record_llm_stats", side_effect=lambda **kw: stats.append(kw)):
            result = await request.send(stream=True)
            if consume == "await":
                assert await result == "ok"
            elif consume == "events":
                assert any([event.text_delta == "ok" async for event in result.stream_events()])
            elif consume == "text_callback":
                assert await result.stream_with_callback(ignore) == "ok"
            elif consume == "event_callback":
                assert await result.stream_events_with_callback(ignore) == "ok"
            else:
                assert "".join([value async for value in result.stream_with_buffer(1)]) == "ok"
        assert len(calls) == 2
        assert [r["success"] for r in stats] == [False, True]
    finally:
        await adapter.aclose()


async def test_exhaustion_does_not_reset_budget() -> None:
    """The SDK's own retry is off, and policy cannot be reset recursively."""
    calls: list[int] = []
    async def handler(req: httpx.Request) -> httpx.Response:
        """记录每次请求并持续返回限流，验证有限重试预算。"""
        calls.append(1)
        return httpx.Response(429, json={"error": {"code": 429, "message": "busy"}}, headers={"retry-after": "0"})
    adapter = GeminiChatClient(transport=httpx.MockTransport(handler))
    req = LLMRequest(model_set=[model()], clients=ModelClientRegistry(gemini=adapter), policy=RoundRobinPolicy(), enable_metrics=False)
    req.context_manager = None
    req.payloads = [LLMPayload(ROLE.USER, Text("hi"))]
    try:
        with pytest.raises(LLMRateLimitError):
            await req.send(stream=True)
        assert len(calls) == 2
    finally:
        await adapter.aclose()
