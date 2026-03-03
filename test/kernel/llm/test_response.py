"""Tests for response.py."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.kernel.llm.exceptions import LLMResponseConsumedError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import LLMPayload, Text
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.response import LLMResponse, _ToolCallAccumulator
from src.kernel.llm.roles import ROLE


# ============================================================================
# Mock Stream Generators
# ============================================================================


async def mock_text_stream() -> AsyncIterator[StreamEvent]:
    """Mock streaming text response."""
    chunks = ["Hello", " there", "!", " How", " can", " I", " help", "?"]
    for chunk in chunks:
        yield StreamEvent(text_delta=chunk)


async def mock_text_stream_end_error() -> AsyncIterator[StreamEvent]:
    """Mock stream that raises after emitting final text.

    用于模拟部分 provider/SDK 在流尾抛出“连接关闭”等异常的情况。
    """
    yield StreamEvent(text_delta="Hello")
    yield StreamEvent(text_delta=" world")
    raise RuntimeError("stream closed")


async def mock_tool_call_stream() -> AsyncIterator[StreamEvent]:
    """Mock streaming response with tool calls."""
    # Text first
    yield StreamEvent(text_delta="Let me check")
    yield StreamEvent(text_delta=" that")

    # Tool call 1
    call_id = "call_123"
    yield StreamEvent(tool_call_id=call_id, tool_name="get_weather")
    yield StreamEvent(tool_call_id=call_id, tool_args_delta='{"')
    yield StreamEvent(tool_call_id=call_id, tool_args_delta='location"')
    yield StreamEvent(tool_call_id=call_id, tool_args_delta=': "')
    yield StreamEvent(tool_call_id=call_id, tool_args_delta='Tokyo')
    yield StreamEvent(tool_call_id=call_id, tool_args_delta='"}')

    # Tool call 2
    call_id2 = "call_456"
    yield StreamEvent(tool_call_id=call_id2, tool_name="get_time")
    yield StreamEvent(tool_call_id=call_id2, tool_args_delta='{"timezone":"UTC"}')


async def mock_mixed_stream() -> AsyncIterator[StreamEvent]:
    """Mock streaming with both text and tools."""
    yield StreamEvent(text_delta="I'll help")
    yield StreamEvent(text_delta=" you")

    call_id = "call_789"
    yield StreamEvent(tool_call_id=call_id, tool_name="search")
    yield StreamEvent(tool_call_id=call_id, tool_args_delta='{"query":"test"}')

    yield StreamEvent(text_delta=" with that")


# ============================================================================
# LLMResponse Tests
# ============================================================================


class TestLLMResponse:
    """Test cases for LLMResponse."""

    @pytest.fixture
    def mock_model_set(self) -> list[dict[str, Any]]:
        """Valid model set for testing."""
        return [
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

    @pytest.fixture
    def sample_payloads(self) -> list[LLMPayload]:
        """Sample payloads for testing."""
        return [
            LLMPayload(ROLE.SYSTEM, Text("You are helpful.")),
            LLMPayload(ROLE.USER, Text("Hello!")),
        ]

    def test_response_creation_with_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test creating LLMResponse with stream."""
        response = LLMResponse(
            _stream=mock_text_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )
        assert response._stream is not None
        assert response.message is None
        assert response._consumed is False
        assert len(response.payloads) == 2

    def test_response_creation_without_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test creating LLMResponse without stream."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Hello world!",
            call_list=[],
        )
        assert response._stream is None
        assert response.message == "Hello world!"
        assert response._consumed is False

    def test_response_default_call_list(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test that call_list defaults to empty list."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Test",
            call_list=None,  # type: ignore
        )
        assert response.call_list == []


class TestLLMResponseAwait:
    """Test cases for awaiting LLMResponse."""

    @pytest.mark.asyncio
    async def test_await_with_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test awaiting response with stream."""
        response = LLMResponse(
            _stream=mock_text_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        result = await response
        assert result == "Hello there! How can I help?"
        assert response.message == result
        assert response._consumed is True

    @pytest.mark.asyncio
    async def test_await_without_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test awaiting response without stream."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Static response",
            call_list=[],
        )

        result = await response
        assert result == "Static response"
        assert response._consumed is True

    @pytest.mark.asyncio
    async def test_await_already_consumed(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test awaiting already consumed response."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        await response  # First await
        assert response._consumed is True

        with pytest.raises(LLMResponseConsumedError, match="Response has already been consumed"):
            await response  # Second await should fail


class TestLLMResponseAsyncIteration:
    """Test cases for async iterating LLMResponse."""

    @pytest.mark.asyncio
    async def test_async_iter_with_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test async iterating response with stream."""
        response = LLMResponse(
            _stream=mock_text_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        chunks = []
        async for chunk in response:
            chunks.append(chunk)

        assert chunks == ["Hello", " there", "!", " How", " can", " I", " help", "?"]
        assert response._consumed is True
        assert response.message == "Hello there! How can I help?"

    @pytest.mark.asyncio
    async def test_async_iter_without_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test async iterating response without stream."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Static response",
            call_list=[],
        )

        chunks = []
        async for chunk in response:
            chunks.append(chunk)

        assert chunks == ["Static response"]
        assert response._consumed is True

    @pytest.mark.asyncio
    async def test_async_iter_already_consumed(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test async iterating already consumed response."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        async for _ in response:
            pass  # Consume once

        with pytest.raises(LLMResponseConsumedError):
            async for _ in response:
                pass  # Should fail


class TestLLMResponseAutoAppend:
    """Test cases for auto_append_response functionality."""

    @pytest.mark.asyncio
    async def test_auto_append_disabled(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test response with auto_append_response disabled."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Assistant response",
            call_list=[],
        )

        await response

        # Payloads should not be modified
        assert len(response.payloads) == 2
        assert response.payloads[-1].role == ROLE.USER

    @pytest.mark.asyncio
    async def test_auto_append_enabled(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test response with auto_append_response enabled."""
        initial_payload_count = len(sample_payloads)

        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=True,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Assistant response",
            call_list=[],
        )

        await response

        # Assistant response should be appended
        assert len(response.payloads) == initial_payload_count + 1
        assert response.payloads[-1].role == ROLE.ASSISTANT
        assert response.payloads[-1].content[0].text == "Assistant response"

    @pytest.mark.asyncio
    async def test_auto_append_with_empty_message(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test that empty message is not appended."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=True,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="",
            call_list=[],
        )

        await response

        # Empty message should not be appended
        assert len(response.payloads) == len(sample_payloads)


class TestLLMResponseToPayload:
    """Test cases for to_payload method."""

    def test_to_payload(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test converting response to payload."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message="Hello world!",
            call_list=[],
        )

        payload = response.to_payload()
        assert payload.role == ROLE.ASSISTANT
        assert isinstance(payload.content[0], Text)
        assert payload.content[0].text == "Hello world!"

    def test_to_payload_with_none_message(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test to_payload with None message."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=sample_payloads,
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        payload = response.to_payload()
        assert payload.role == ROLE.ASSISTANT
        assert payload.content[0].text == ""


class TestLLMResponseAddPayload:
    """Test cases for add_payload method."""

    def test_add_payload(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test adding payload to response."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        new_payload = LLMPayload(ROLE.USER, Text("New message"))
        result = response.add_payload(new_payload)

        assert result is response  # Returns self
        assert len(response.payloads) == len(sample_payloads)
        assert response.payloads[-1].role == ROLE.USER
        assert response.payloads[-1].content[0].text == "Hello!"
        assert response.payloads[-1].content[1].text == "New message"

    def test_add_payload_with_position(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test adding payload at specific position."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        new_payload = LLMPayload(ROLE.USER, Text("Inserted"))
        response.add_payload(new_payload, position=1)

        assert response.payloads[1] == new_payload

    def test_add_payload_with_response(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test adding LLMResponse as payload."""
        response1 = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Response 1",
            call_list=[],
        )

        response2 = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=[],
            model_set=mock_model_set,
            message="Response 2",
            call_list=[],
        )

        response1.add_payload(response2)
        assert len(response1.payloads) == len(sample_payloads) + 1
        assert response1.payloads[-1].role == ROLE.ASSISTANT
        assert response1.payloads[-1].content[0].text == "Response 2"


class TestLLMResponseAddCallReflex:
    """Test cases for add_call_reflex method."""

    def test_add_call_reflex(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test adding call reflex (tool results)."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        from src.kernel.llm.payload import ToolResult

        results = [
            LLMPayload(ROLE.TOOL_RESULT, ToolResult(value="Result 1", call_id="call_1")),
            LLMPayload(ROLE.TOOL_RESULT, ToolResult(value="Result 2", call_id="call_2")),
        ]

        result = response.add_call_reflex(results)
        assert result is response
        assert len(response.payloads) == len(sample_payloads)
        assert all(payload.role != ROLE.TOOL_RESULT for payload in response.payloads)


class TestLLMResponseSend:
    """Test cases for send method (chaining)."""

    @pytest.mark.asyncio
    async def test_send_creates_new_request(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test that send creates a new request with current payloads."""
        from unittest.mock import patch

        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        # Add more payloads
        response.add_payload(LLMPayload(ROLE.USER, Text("Follow up question")))

        # Mock the send method
        with patch.object(LLMRequest, "send", new=AsyncMock()) as mock_send:
            mock_response = LLMResponse(
                _stream=None,
                _upper=LLMRequest(mock_model_set, "test"),
                _auto_append_response=False,
                payloads=[],
                model_set=mock_model_set,
                message="Chained response",
                call_list=[],
            )
            mock_send.return_value = mock_response

            result = await response.send(stream=False)

            # Verify send was called with updated payloads
            assert mock_send.called
            call_kwargs = mock_send.call_args.kwargs
            assert call_kwargs["stream"] is False


class TestLLMResponseStreamWithCallback:
    """Test cases for stream_with_callback method."""

    @pytest.mark.asyncio
    async def test_stream_with_callback_on_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test stream_with_callback with streaming response."""
        received_chunks = []

        async def callback(chunk: str) -> None:
            received_chunks.append(chunk)

        response = LLMResponse(
            _stream=mock_text_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        result = await response.stream_with_callback(callback)

        assert result == "Hello there! How can I help?"
        assert received_chunks == ["Hello", " there", "!", " How", " can", " I", " help", "?"]
        assert response._consumed is True

    @pytest.mark.asyncio
    async def test_stream_with_callback_no_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test stream_with_callback without stream."""
        received_chunks = []

        async def callback(chunk: str) -> None:
            received_chunks.append(chunk)

        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Static",
            call_list=[],
        )

        result = await response.stream_with_callback(callback)

        assert result == "Static"
        assert received_chunks == ["Static"]

    @pytest.mark.asyncio
    async def test_stream_with_callback_already_consumed(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test stream_with_callback on already consumed response."""

        async def callback(chunk: str) -> None:
            pass

        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        await response  # Consume first

        with pytest.raises(LLMResponseConsumedError):
            await response.stream_with_callback(callback)


class TestLLMResponseStreamWithBuffer:
    """Test cases for stream_with_buffer method."""

    @pytest.mark.asyncio
    async def test_stream_with_buffer(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test stream_with_buffer method."""
        response = LLMResponse(
            _stream=mock_text_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        buffers = []
        async for buffer in response.stream_with_buffer(buffer_size=10):
            buffers.append(buffer)

        # Should buffer chunks until reaching 10 characters
        assert len(buffers) > 0
        assert response._consumed is True
        assert response.message == "Hello there! How can I help?"

    @pytest.mark.asyncio
    async def test_stream_with_buffer_flushes_tail_on_stream_error(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """stream_with_buffer should flush remaining buffer even if stream ends with an error."""
        response = LLMResponse(
            _stream=mock_text_stream_end_error(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        buffers: list[str] = []
        with pytest.raises(RuntimeError):
            async for buffer in response.stream_with_buffer(buffer_size=100):
                buffers.append(buffer)

        assert buffers == ["Hello world"]
        assert response.message == "Hello world"

    @pytest.mark.asyncio
    async def test_stream_with_buffer_no_stream(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test stream_with_buffer without stream."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Static response",
            call_list=[],
        )

        buffers = []
        async for buffer in response.stream_with_buffer(buffer_size=5):
            buffers.append(buffer)

        assert buffers == ["Static response"]

    @pytest.mark.asyncio
    async def test_stream_with_buffer_already_consumed(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test stream_with_buffer on already consumed response."""
        response = LLMResponse(
            _stream=None,
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message="Test",
            call_list=[],
        )

        async for _ in response:
            pass  # Consume

        with pytest.raises(LLMResponseConsumedError):
            async for _ in response.stream_with_buffer():
                pass


# ============================================================================
# Tool Call Accumulator Tests
# ============================================================================


class TestToolCallAccumulator:
    """Test cases for _ToolCallAccumulator."""

    def test_accumulate_text_only(self) -> None:
        """Test accumulator with text only (no tools)."""
        accumulator = _ToolCallAccumulator()

        accumulator.apply(StreamEvent(text_delta="Hello"))
        accumulator.apply(StreamEvent(text_delta=" world"))

        calls = accumulator.finalize()
        assert len(calls) == 0

    def test_accumulate_single_tool_call(self) -> None:
        """Test accumulating a single tool call."""
        accumulator = _ToolCallAccumulator()

        call_id = "call_123"
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_name="get_weather"))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta='{"'))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta='location'))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta='":"'))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta='Tokyo"}'))

        calls = accumulator.finalize()
        assert len(calls) == 1
        assert calls[0].id == call_id
        assert calls[0].name == "get_weather"
        assert calls[0].args == {"location": "Tokyo"}

    def test_accumulate_multiple_tool_calls(self) -> None:
        """Test accumulating multiple tool calls."""
        accumulator = _ToolCallAccumulator()

        # First tool
        call_id1 = "call_123"
        accumulator.apply(StreamEvent(tool_call_id=call_id1, tool_name="tool1"))
        accumulator.apply(StreamEvent(tool_call_id=call_id1, tool_args_delta='{"a":1}'))

        # Second tool
        call_id2 = "call_456"
        accumulator.apply(StreamEvent(tool_call_id=call_id2, tool_name="tool2"))
        accumulator.apply(StreamEvent(tool_call_id=call_id2, tool_args_delta='{"b":2}'))

        calls = accumulator.finalize()
        assert len(calls) == 2
        assert calls[0].id == call_id1
        assert calls[1].id == call_id2
        assert calls[0].name == "tool1"
        assert calls[1].name == "tool2"

    def test_accumulate_preserves_order(self) -> None:
        """Test that accumulator preserves tool call order."""
        accumulator = _ToolCallAccumulator()

        for i in range(5):
            call_id = f"call_{i}"
            accumulator.apply(StreamEvent(tool_call_id=call_id, tool_name=f"tool_{i}"))
            accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta="{}"))

        calls = accumulator.finalize()
        assert len(calls) == 5
        for i, call in enumerate(calls):
            assert call.id == f"call_{i}"
            assert call.name == f"tool_{i}"

    def test_finalize_with_invalid_json(self) -> None:
        """Test finalize with invalid JSON args."""
        accumulator = _ToolCallAccumulator()

        call_id = "call_123"
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_name="tool"))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta="not valid json"))

        calls = accumulator.finalize()
        assert len(calls) == 1
        # Invalid JSON should be returned as string
        assert calls[0].args == "not valid json"

    def test_finalize_with_empty_args(self) -> None:
        """Test finalize with empty args."""
        accumulator = _ToolCallAccumulator()

        call_id = "call_123"
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_name="tool"))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta=""))

        calls = accumulator.finalize()
        assert len(calls) == 1
        assert calls[0].args == {}

    def test_finalize_with_no_name(self) -> None:
        """Test finalize when tool name is not set."""
        accumulator = _ToolCallAccumulator()

        call_id = "call_123"
        accumulator.apply(StreamEvent(tool_call_id=call_id))
        accumulator.apply(StreamEvent(tool_call_id=call_id, tool_args_delta='{"a":1}'))

        calls = accumulator.finalize()
        assert len(calls) == 1
        assert calls[0].name == ""


# ============================================================================
# Integration Tests
# ============================================================================


class TestResponseIntegration:
    """Integration tests for LLMResponse with tool calls."""

    @pytest.mark.asyncio
    async def test_response_with_tool_calls_streaming(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test response with tool calls via streaming."""
        response = LLMResponse(
            _stream=mock_tool_call_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        full_text = await response

        assert full_text == "Let me check that"
        assert len(response.call_list) == 2
        assert response.call_list[0].name == "get_weather"
        assert response.call_list[0].args == {"location": "Tokyo"}
        assert response.call_list[1].name == "get_time"
        assert response.call_list[1].args == {"timezone": "UTC"}

    @pytest.mark.asyncio
    async def test_response_with_mixed_content(
        self, mock_model_set: list[dict[str, Any]], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test response with mixed text and tool calls."""
        response = LLMResponse(
            _stream=mock_mixed_stream(),
            _upper=LLMRequest(mock_model_set, "test"),
            _auto_append_response=False,
            payloads=list(sample_payloads),
            model_set=mock_model_set,
            message=None,
            call_list=[],
        )

        full_text = await response

        assert "I'll help" in full_text
        assert " with that" in full_text
        assert len(response.call_list) == 1
        assert response.call_list[0].name == "search"
