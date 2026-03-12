"""Fixtures for LLM module tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.kernel.llm.model_client.base import ChatModelClient, StreamEvent
from src.kernel.llm.payload import LLMPayload, Text, ToolResult, ToolCall
from src.kernel.llm.roles import ROLE


# ============================================================================
# Model Set Fixtures
# ============================================================================


@pytest.fixture
def mock_model_config() -> dict[str, Any]:
    """A valid single model configuration."""
    return {
        "api_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": "gpt-4",
        "api_key": "sk-test-key-12345",
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


@pytest.fixture
def mock_model_set(mock_model_config: dict[str, Any]) -> list[dict[str, Any]]:
    """A valid model set with single model."""
    return [mock_model_config.copy()]


@pytest.fixture
def mock_multi_model_set(mock_model_config: dict[str, Any]) -> list[dict[str, Any]]:
    """A valid model set with multiple models."""
    model1 = mock_model_config.copy()
    model1["model_identifier"] = "gpt-4"
    model1["api_key"] = "sk-key-1"

    model2 = mock_model_config.copy()
    model2["model_identifier"] = "gpt-3.5-turbo"
    model2["api_key"] = "sk-key-2"
    model2["max_retry"] = 1

    return [model1, model2]


# ============================================================================
# Payload Fixtures
# ============================================================================


@pytest.fixture
def sample_payloads() -> list[LLMPayload]:
    """Sample LLM payloads for testing."""
    return [
        LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")),
        LLMPayload(ROLE.USER, Text("Hello!")),
    ]


# ============================================================================
# Mock Client Fixtures
# ============================================================================


@pytest.fixture
def mock_stream_text() -> AsyncIterator[StreamEvent]:
    """Mock streaming text response."""

    async def _gen() -> AsyncIterator[StreamEvent]:
        for chunk in ["Hello", " there", "! How", " can", " I", " help", " you", "?"]:
            yield StreamEvent(text_delta=chunk)
            await asyncio.sleep(0.01)

    return _gen()


@pytest.fixture
def mock_stream_with_tool() -> AsyncIterator[StreamEvent]:
    """Mock streaming response with tool calls."""

    async def _gen() -> AsyncIterator[StreamEvent]:
        # Text first
        for chunk in ["Let", " me", " check", " that", " for", " you", "."]:
            yield StreamEvent(text_delta=chunk)

        # Tool call
        call_id = "call_123"
        yield StreamEvent(tool_call_id=call_id, tool_name="get_weather")
        yield StreamEvent(tool_call_id=call_id, tool_args_delta='{"')
        yield StreamEvent(tool_call_id=call_id, tool_args_delta='location"')
        yield StreamEvent(tool_call_id=call_id, tool_args_delta=': "')
        yield StreamEvent(tool_call_id=call_id, tool_args_delta='Tokyo')
        yield StreamEvent(tool_call_id=call_id, tool_args_value='"}')

    return _gen()


@pytest.fixture
def mock_chat_client() -> ChatModelClient:
    """Mock ChatModelClient for testing."""

    client = Mock(spec=ChatModelClient)

    # Default: non-streaming text response
    async def _create(
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Tool],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        if stream:
            # Return streaming response
            async def _stream_gen() -> AsyncIterator[StreamEvent]:
                for chunk in ["Hello", " world", "!"]:
                    yield StreamEvent(text_delta=chunk)

            return None, None, _stream_gen()
        else:
            # Return non-streaming response
            return "Hello world!", None, None

    client.create = AsyncMock(side_effect=_create)
    return client


@pytest.fixture
def mock_openai_client() -> MagicMock:
    """Mock OpenAI AsyncOpenAI client."""
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()

    # Mock non-streaming response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = "Test response"
    mock_message.tool_calls = None
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    return mock_client


# ============================================================================
# OpenAI SDK Exception Mocks
# ============================================================================


@pytest.fixture
def mock_openai_rate_limit_error() -> MagicMock:
    """Mock OpenAI RateLimitError."""
    exc = MagicMock()
    exc.__class__.__name__ = "RateLimitError"
    exc.__str__ = lambda self: "Rate limit exceeded"
    exc.retry_after = 60
    return exc


@pytest.fixture
def mock_openai_timeout_error() -> MagicMock:
    """Mock OpenAI APITimeoutError."""
    exc = MagicMock()
    exc.__class__.__name__ = "APITimeoutError"
    exc.__str__ = lambda self: "Request timeout"
    return exc


@pytest.fixture
def mock_openai_auth_error() -> MagicMock:
    """Mock OpenAI AuthenticationError."""
    exc = MagicMock()
    exc.__class__.__name__ = "AuthenticationError"
    exc.__str__ = lambda self: "Invalid API key"
    return exc


@pytest.fixture
def mock_openai_bad_request() -> MagicMock:
    """Mock OpenAI BadRequestError."""
    exc = MagicMock()
    exc.__class__.__name__ = "BadRequestError"
    exc.__str__ = lambda self: "Bad request"
    return exc


@pytest.fixture
def mock_openai_api_error() -> MagicMock:
    """Mock OpenAI APIError."""
    exc = MagicMock()
    exc.__class__.__name__ = "APIError"
    exc.__str__ = lambda self: "API error occurred"
    exc.status_code = 500
    exc.code = "server_error"
    return exc


# ============================================================================
# Tool Fixtures
# ============================================================================


@pytest.fixture
def sample_tool_call() -> ToolCall:
    """Sample tool call for testing."""
    return ToolCall(id="call_123", name="get_weather", args={"location": "Tokyo"})


@pytest.fixture
def sample_tool_result() -> ToolResult:
    """Sample tool result for testing."""
    return ToolResult(
        value={"temperature": 25, "condition": "sunny"},
        call_id="call_123",
        name="get_weather",
    )


# ============================================================================
# Custom Assertions
# ============================================================================


def assert_payloads_equal(
    payloads1: list[LLMPayload],
    payloads2: list[LLMPayload],
    *,
    compare_content: bool = True,
) -> None:
    """Assert two lists of payloads are equal."""
    assert len(payloads1) == len(payloads2), f"Payload count mismatch: {len(payloads1)} != {len(payloads2)}"

    for p1, p2 in zip(payloads1, payloads2):
        assert p1.role == p2.role, f"Role mismatch: {p1.role} != {p2.role}"
        if compare_content:
            assert len(p1.content) == len(p2.content)
            for c1, c2 in zip(p1.content, p2.content):
                assert type(c1) == type(c2), f"Content type mismatch: {type(c1)} != {type(c2)}"
                if isinstance(c1, Text) and isinstance(c2, Text):
                    assert c1.text == c2.text, f"Text content mismatch: {c1.text} != {c2.text}"
