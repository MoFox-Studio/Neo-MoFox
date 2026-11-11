"""Final review regressions: thought chunk projection and best-effort cleanup."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
from src.kernel.llm.model_client.gemini_client import GeminiChatClient, _ClientLease, _OwnedStream
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.model_client.gemini_codec import encode_payloads
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model


async def test_multiple_thought_chunks_match_replay_fingerprint() -> None:
    """Chunk coalescing must not make an untouched signed payload look edited."""
    chunks=[
        {"candidates":[{"content":{"role":"model","parts":[{"text":"first", "thought":True}]}}]},
        {"candidates":[{"content":{"role":"model","parts":[{"text":"second", "thought":True}]}}]},
        {"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"f","args":{}},"thoughtSignature":"c2ln"}]},"finishReason":"STOP"}]},
    ]
    async def handler(req: httpx.Request) -> httpx.Response:
        """将多个思考片段和带签名工具调用编码为 SSE 响应。"""
        return httpx.Response(200,text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks),headers={'content-type':'text/event-stream'})
    client=GeminiChatClient(transport=httpx.MockTransport(handler))
    req=LLMRequest(model_set=[model()],clients=ModelClientRegistry(gemini=client),policy=RoundRobinPolicy(),enable_metrics=False)
    req.context_manager = None
    req.payloads = [LLMPayload(ROLE.USER, Text('hi'))]
    try:
        result = await req.send(stream=True)
        await result
        payload=result.to_payload()
        assert result.provider_state.matches(payload.content)
        encoded,_,_=encode_payloads([payload],result.provider_state.target,[])
        assert [p.text for p in encoded[0].parts[:2]] == ['first','second']
    finally:
        await client.aclose()


async def test_lease_close_failure_still_closes_remaining_resources() -> None:
    """An SDK cleanup failure cannot skip the MoFox-owned HTTP client."""
    sdk=SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError('close'))),close=Mock())
    http=SimpleNamespace(aclose=AsyncMock())
    lease=_ClientLease(sdk,http)
    with pytest.raises(RuntimeError):
        await lease.close()
    sdk.close.assert_called_once()
    http.aclose.assert_awaited_once()


async def test_stream_close_failure_still_closes_source() -> None:
    """Both iterator ownership layers are cleaned even on exceptional close."""
    events=SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError('close')))
    source=SimpleNamespace(aclose=AsyncMock())
    lease=SimpleNamespace(active=1)
    stream=_OwnedStream(events,source,lease)
    with pytest.raises(RuntimeError):
        await stream.aclose()
    source.aclose.assert_awaited_once()
    assert lease.active == 0
