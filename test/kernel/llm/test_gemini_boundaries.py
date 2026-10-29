"""Regression cases from the Gemini stage-completeness review."""
import asyncio
import json

import httpx
import pytest

from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.exceptions import LLMConfigurationError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from test.kernel.llm.test_gemini_client import model, response


@pytest.mark.parametrize("options", [None, [], {"generation_config": None}, {"generation_config": {"top_p": "SECRET_INVALID"}}])
async def test_bad_config_is_safe_configuration_error(options: object) -> None:
    """Bad nested values must not leak Pydantic inputs or raw Python errors."""
    m = model()
    m["extra_params"] = {"gemini": options}
    client = GeminiChatClient()
    with pytest.raises(LLMConfigurationError) as exc:
        await client.create(model_name="test-model", payloads=[LLMPayload(ROLE.USER, Text("hi"))], tools=[], request_name="test", model_set=m, stream=False)
    assert "SECRET_INVALID" not in str(exc.value)


async def test_actual_http_timeout_and_unconsumed_stream_close() -> None:
    """Unstarted streams still close their prefetched source and release leases."""
    observed: list[dict] = []
    async def handler(req: httpx.Request) -> httpx.Response:
        observed.append(req.extensions["timeout"])
        return httpx.Response(200, text="data: "+json.dumps(response())+"\n\n", headers={"content-type":"text/event-stream"})
    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    result = await client.create(model_name="test-model", payloads=[LLMPayload(ROLE.USER, Text("hi"))], tools=[], request_name="test", model_set=model(), stream=True)
    assert observed[0]["read"] == 5.0
    await result.stream_iter.aclose()
    pool = client._pools[asyncio.get_running_loop()]
    assert all(lease.active == 0 for lease in pool.values())
    await client.aclose()
    assert not pool


async def test_borrowed_transport_not_closed() -> None:
    """A caller can safely share an injected transport with another client."""
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.closed = 0
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response())
        async def aclose(self) -> None:
            self.closed += 1
    transport = Transport()
    client = GeminiChatClient(transport=transport)
    await client.create(model_name="test-model",payloads=[LLMPayload(ROLE.USER,Text("hi"))],tools=[],request_name="test",model_set=model(),stream=False)
    await client.aclose()
    assert transport.closed == 0
