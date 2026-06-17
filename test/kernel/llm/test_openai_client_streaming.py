"""OpenAI 客户端流式输出与兼容供应商测试。

覆盖 finish_reason 暴露、缺失 tool_call id 时的合成 id、流式 reasoning 等场景。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.model_client.openai_client import OpenAIChatClient
from src.kernel.llm.stream_state import LLMStreamReducer


@pytest.mark.asyncio
async def test_openai_stream_emits_finish_reason():
    """流式响应应暴露 choice.finish_reason。"""

    class FakeStream:
        def __init__(self):
            self._index = 0
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._index > 0:
                raise StopAsyncIteration
            self._index += 1

            delta = MagicMock()
            delta.content = None
            delta.tool_calls = None
            delta.function_call = None

            choice = MagicMock()
            choice.delta = delta
            choice.finish_reason = "length"

            chunk = MagicMock()
            chunk.choices = [choice]
            chunk.usage = None
            return chunk

        async def aclose(self):
            self.closed = True

    fake_stream = FakeStream()

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=fake_stream)

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = mock_chat.completions.create

    client = OpenAIChatClient()
    client._clients = {}
    client._get_client = MagicMock(return_value=mock_openai_client)

    _, _, stream_iter, _, _ = await client.create(
        model_name="gpt-4",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        tools=[],
        request_name="test",
        model_set={
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        },
        stream=True,
    )

    events = []
    async for event in stream_iter:
        events.append(event)

    assert events
    assert events[-1].finish_reason == "length"

    reducer = LLMStreamReducer()
    for event in events:
        reducer.apply(event)
    result = reducer.finalize()
    assert result.stop_reason == "length"

    await cast(Any, stream_iter).aclose()
    assert fake_stream.closed is True


@pytest.mark.asyncio
async def test_openai_stream_synthesizes_tool_call_id_when_missing():
    """兼容供应商只给 index 不给 id 时，应合成稳定 id 并不丢弃参数增量。"""

    class FakeStream:
        def __init__(self):
            self._index = 0
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._index >= 2:
                raise StopAsyncIteration
            step = self._index
            self._index += 1

            fn = MagicMock()
            if step == 0:
                fn.name = "calculator"
                fn.arguments = '{"a":'
                tc = MagicMock()
                tc.id = None
                tc.index = 0
                tc.function = fn
            else:
                fn.name = None
                fn.arguments = '1}'
                tc = MagicMock()
                tc.id = None
                tc.index = 0
                tc.function = fn

            delta = MagicMock()
            delta.content = None
            delta.reasoning_content = None
            delta.reasoning = None
            delta.tool_calls = [tc]
            delta.function_call = None

            choice = MagicMock()
            choice.delta = delta
            choice.finish_reason = "tool_calls" if step == 1 else None

            chunk = MagicMock()
            chunk.choices = [choice]
            chunk.usage = None
            return chunk

        async def aclose(self):
            self.closed = True

    fake_stream = FakeStream()

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=fake_stream)

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = mock_chat.completions.create

    client = OpenAIChatClient()
    client._clients = {}
    client._get_client = MagicMock(return_value=mock_openai_client)

    _, _, stream_iter, _, _ = await client.create(
        model_name="gpt-4",
        payloads=[LLMPayload(ROLE.USER, Text("Calculate"))],
        tools=[],
        request_name="test",
        model_set={
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        },
        stream=True,
    )

    reducer = LLMStreamReducer()
    async for event in stream_iter:
        reducer.apply(event)
    result = reducer.finalize()

    assert len(result.call_list) == 1
    assert result.call_list[0].id == "call_stream_0"
    assert result.call_list[0].name == "calculator"
    assert result.call_list[0].args == {"a": 1}
    assert result.stop_reason == "tool_calls"

    await cast(Any, stream_iter).aclose()
    assert fake_stream.closed is True


@pytest.mark.asyncio
async def test_openai_stream_keeps_tool_call_id_when_present():
    """标准 OpenAI 在首包给出 id 后，应继续使用真实 id。"""

    class FakeStream:
        def __init__(self):
            self._index = 0
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._index >= 2:
                raise StopAsyncIteration
            step = self._index
            self._index += 1

            fn = MagicMock()
            if step == 0:
                fn.name = "calculator"
                fn.arguments = '{"a":'
                tc = MagicMock()
                tc.id = "call_real_123"
                tc.index = 0
                tc.function = fn
            else:
                fn.name = None
                fn.arguments = '1}'
                tc = MagicMock()
                tc.id = None
                tc.index = 0
                tc.function = fn

            delta = MagicMock()
            delta.content = None
            delta.reasoning_content = None
            delta.reasoning = None
            delta.tool_calls = [tc]
            delta.function_call = None

            choice = MagicMock()
            choice.delta = delta
            choice.finish_reason = None

            chunk = MagicMock()
            chunk.choices = [choice]
            chunk.usage = None
            return chunk

        async def aclose(self):
            self.closed = True

    fake_stream = FakeStream()

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=fake_stream)

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = mock_chat.completions.create

    client = OpenAIChatClient()
    client._clients = {}
    client._get_client = MagicMock(return_value=mock_openai_client)

    _, _, stream_iter, _, _ = await client.create(
        model_name="gpt-4",
        payloads=[LLMPayload(ROLE.USER, Text("Calculate"))],
        tools=[],
        request_name="test",
        model_set={
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        },
        stream=True,
    )

    reducer = LLMStreamReducer()
    async for event in stream_iter:
        reducer.apply(event)
    result = reducer.finalize()

    assert len(result.call_list) == 1
    assert result.call_list[0].id == "call_real_123"
    assert result.call_list[0].args == {"a": 1}

    await cast(Any, stream_iter).aclose()


@pytest.mark.asyncio
async def test_openai_stream_default_stream_options_include_usage():
    """未配置 stream_options 时，_create_stream 应补充默认 include_usage。"""

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            pass

    fake_stream = FakeStream()

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=fake_stream)

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = mock_chat.completions.create

    client = OpenAIChatClient()
    client._clients = {}
    client._get_client = MagicMock(return_value=mock_openai_client)

    await client.create(
        model_name="gpt-4",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        tools=[],
        request_name="test",
        model_set={
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        },
        stream=True,
    )

    call_kwargs = mock_chat.completions.create.call_args.kwargs
    assert call_kwargs["stream_options"] == {"include_usage": True}
    assert "stream_options" not in call_kwargs.get("extra_body", {})
