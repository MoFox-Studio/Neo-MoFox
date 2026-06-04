from __future__ import annotations

import pytest

from src.kernel.llm import LLMPayload, LLMRequest, ROLE, StreamEvent, Text
from src.kernel.llm.exceptions import LLMResponseConsumedError
from src.kernel.llm.response import LLMResponse


def _model_set() -> list[dict[str, object]]:
    return [
        {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "gpt-test",
            "api_key": "sk-test",
            "client_type": "openai",
            "max_retry": 1,
            "timeout": 5.0,
            "retry_interval": 0.0,
            "price_in": 0.0,
            "cache_hit_price_in": 0.0,
            "price_out": 0.0,
            "temperature": 0.0,
            "max_tokens": 256,
            "max_context": 8192,
            "tool_call_compat": False,
            "extra_params": {},
        }
    ]


@pytest.mark.asyncio
async def test_stream_events_with_callback_collects_final_message_and_tool_calls() -> None:
    events = [
        StreamEvent(tool_call_id="call_1", tool_name="action-say"),
        StreamEvent(tool_call_id="call_1", tool_args_delta='{"content":"你好。"}'),
        StreamEvent(text_delta="done"),
    ]

    async def _stream():
        for event in events:
            yield event

    seen: list[StreamEvent] = []

    async def _on_event(event: StreamEvent) -> None:
        seen.append(event)

    request = LLMRequest(_model_set(), request_name="test")
    response = LLMResponse(
        _stream=_stream(),
        _upper=request,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=request.model_set,
    )

    message = await response.stream_events_with_callback(_on_event)

    assert message == "done"
    assert seen == events
    assert response.message == "done"
    assert response.call_list is not None
    assert len(response.call_list) == 1
    assert response.call_list[0].name == "action-say"
    assert response.call_list[0].args == {"content": "你好。"}


@pytest.mark.asyncio
async def test_stream_events_with_callback_is_single_consumer() -> None:
    request = LLMRequest(_model_set(), request_name="test")
    response = LLMResponse(
        _stream=None,
        _upper=request,
        _auto_append_response=False,
        payloads=[],
        model_set=request.model_set,
        message="ok",
    )

    async def _noop(_event: StreamEvent) -> None:
        return None

    await response.stream_events_with_callback(_noop)

    with pytest.raises(LLMResponseConsumedError):
        await response.stream_events_with_callback(_noop)
