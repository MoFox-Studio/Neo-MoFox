from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.response import LLMResponse
from src.kernel.llm.roles import ROLE


async def mock_reasoning_stream() -> AsyncIterator[StreamEvent]:
    yield StreamEvent(reasoning_delta="先")
    yield StreamEvent(text_delta="答")
    yield StreamEvent(reasoning_delta="想")
    yield StreamEvent(text_delta="案")


def test_async_iter_updates_reasoning_content_incrementally() -> None:
    async def scenario() -> None:
        model_set = [
            {
                "api_provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model_identifier": "gpt-4",
                "api_key": "sk-test-key-1",
                "client_type": "openai",
                "max_retry": 2,
                "timeout": 30.0,
                "retry_interval": 1.0,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            }
        ]
        payloads = [
            LLMPayload(ROLE.SYSTEM, Text("You are helpful.")),
            LLMPayload(ROLE.USER, Text("Hello!")),
        ]
        response = LLMResponse(
            _stream=mock_reasoning_stream(),
            _upper=LLMRequest(model_set, "test"),
            _auto_append_response=False,
            payloads=payloads,
            model_set=model_set,
            message=None,
            call_list=[],
        )

        snapshots: list[str | None] = []
        async for _ in response.stream_events():
            snapshots.append(response.reasoning_content)

        assert snapshots == ["先", "先", "先想", "先想"]
        assert response.message == "答案"
        assert response.reasoning_content == "先想"

    asyncio.run(scenario())