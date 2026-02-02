import pytest

from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.response import LLMResponse
from src.kernel.llm.roles import ROLE


def dummy_model_set():
    return [
        {
            "api_provider": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "dummy",
            "api_key": "dummy-key",
            "client_type": "openai",
            "max_retry": 0,
            "timeout": 1,
            "retry_interval": 0,
            "price_in": 0.0,
            "price_out": 0.0,
            "temperature": 0.1,
            "max_tokens": 10,
            "extra_params": {},
        }
    ]


async def _stream_events():
    yield StreamEvent(text_delta="hel")
    yield StreamEvent(text_delta="lo")


@pytest.mark.asyncio
async def test_response_await_collects_full_message():
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_stream_events(),
        _upper=req,
        _auto_append_response=True,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    text = await resp
    assert text == "hello"
    assert resp.message == "hello"
    assert resp.payloads[-1].role == ROLE.ASSISTANT


@pytest.mark.asyncio
async def test_response_async_for_yields_chunks_and_sets_message():
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_stream_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    chunks = []
    async for c in resp:
        chunks.append(c)

    assert chunks == ["hel", "lo"]
    assert resp.message == "hello"


@pytest.mark.asyncio
async def test_response_cannot_be_consumed_twice():
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_stream_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    _ = await resp
    with pytest.raises(Exception):
        _ = await resp
