"""Tests for model_client module."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.kernel.llm.model_client.base import ChatModelClient, StreamEvent
from src.kernel.llm.model_client.openai_client import OpenAIChatClient, _image_to_data_url, _is_data_url, _payloads_to_openai_messages
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.payload import Image, LLMPayload, Text, Tool, ToolResult
from src.kernel.llm.roles import ROLE


# ============================================================================
# Test LLMUsable Implementations
# ============================================================================


class MockTool:
    """Mock tool for testing."""

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "mock_tool",
                "description": "A mock tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "arg": {"type": "string"},
                    },
                },
            },
        }


# ============================================================================
# StreamEvent Tests
# ============================================================================


class TestStreamEvent:
    """Test cases for StreamEvent."""

    def test_stream_event_creation(self) -> None:
        """Test creating StreamEvent."""
        event = StreamEvent(text_delta="Hello")
        assert event.text_delta == "Hello"
        assert event.tool_call_id is None
        assert event.tool_name is None
        assert event.tool_args_delta is None

    def test_stream_event_with_text_only(self) -> None:
        """Test StreamEvent with text only."""
        event = StreamEvent(text_delta="World")
        assert event.text_delta == "World"

    def test_stream_event_with_tool_call(self) -> None:
        """Test StreamEvent with tool call."""
        event = StreamEvent(
            tool_call_id="call_123",
            tool_name="get_weather",
            tool_args_delta='{"loc',
        )
        assert event.tool_call_id == "call_123"
        assert event.tool_name == "get_weather"
        assert event.tool_args_delta == '{"loc'

    def test_stream_event_is_frozen(self) -> None:
        """Test that StreamEvent is frozen."""
        event = StreamEvent(text_delta="test")
        with pytest.raises(Exception):
            event.text_delta = "modified"

    def test_stream_event_default_values(self) -> None:
        """Test StreamEvent default values."""
        event = StreamEvent()
        assert event.text_delta is None
        assert event.tool_call_id is None
        assert event.tool_name is None
        assert event.tool_args_delta is None


# ============================================================================
# OpenAI Client Utility Tests
# ============================================================================


class TestIsDataURL:
    """Test cases for _is_data_url function."""

    def test_is_data_url_with_valid_data_url(self) -> None:
        """Test with valid data URL."""
        assert _is_data_url("data:image/png;base64,ABC123") is True
        assert _is_data_url("data:image/jpeg;base64,XYZ789") is True

    def test_is_data_url_with_file_path(self) -> None:
        """Test with file path."""
        assert _is_data_url("pic.jpg") is False
        assert _is_data_url("/path/to/image.png") is False

    def test_is_data_url_with_base64_prefix(self) -> None:
        """Test with base64| prefix."""
        assert _is_data_url("base64|ABC123") is False

    def test_is_data_url_with_empty_string(self) -> None:
        """Test with empty string."""
        assert _is_data_url("") is False


class TestImageToDataURL:
    """Test cases for _image_to_data_url function."""

    def test_with_base64_prefix(self) -> None:
        """Test with base64| prefix format."""
        b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        result = _image_to_data_url(f"base64|{b64}")
        assert result.startswith("data:image/png;base64,")
        assert b64 in result

    def test_with_data_url(self) -> None:
        """Test with existing data URL."""
        data_url = "data:image/png;base64,ABC123"
        result = _image_to_data_url(data_url)
        assert result == data_url

    def test_with_file_path(self, tmp_path: Path) -> None:
        """Test with file path."""
        # Create a temporary image file
        img_file = tmp_path / "test.png"
        img_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # Minimal PNG
        img_file.write_bytes(img_data)

        result = _image_to_data_url(str(img_file))
        assert result.startswith("data:image/png;base64,")

    def test_with_nonexistent_file(self) -> None:
        """Test with nonexistent file."""
        with pytest.raises(FileNotFoundError, match="Image file not found"):
            _image_to_data_url("nonexistent.jpg")


class TestPayloadsToOpenAIMessages:
    """Test cases for _payloads_to_openai_messages function."""

    def test_simple_text_payload(self) -> None:
        """Test simple text payload."""
        payloads = [LLMPayload(ROLE.USER, Text("Hello"))]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert len(tools) == 0

    def test_system_payload(self) -> None:
        """Test system payload."""
        payloads = [LLMPayload(ROLE.SYSTEM, Text("You are helpful."))]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."

    def test_multimodal_payload(self) -> None:
        """Test multimodal payload with text and image."""
        payloads = [
            LLMPayload(
                ROLE.USER,
                [Text(text="What's this?"), Image(value="base64|ABC123")],
            )
        ]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], list)
        assert len(messages[0]["content"]) == 2
        assert messages[0]["content"][0]["type"] == "text"
        assert messages[0]["content"][1]["type"] == "image_url"

    def test_tool_declaration(self) -> None:
        """Test tool declaration payload."""
        payloads = [LLMPayload(ROLE.TOOL, Tool(tool=MockTool))]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 0  # TOOL doesn't add messages
        assert len(tools) == 1
        assert tools[0]["type"] == "function"

    def test_tool_result_payload(self) -> None:
        """Test tool result payload."""
        payloads = [
            LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult(value={"result": "ok"}, call_id="call_123"),
            )
        ]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "call_123"
        assert '"result": "ok"' in messages[0]["content"]

    def test_multiple_messages(self) -> None:
        """Test multiple messages."""
        payloads = [
            LLMPayload(ROLE.SYSTEM, Text("System")),
            LLMPayload(ROLE.USER, Text("Hello")),
            LLMPayload(ROLE.ASSISTANT, Text("Hi there!")),
            LLMPayload(ROLE.USER, Text("How are you?")),
        ]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        assert messages[3]["role"] == "user"

    def test_mixed_tools_and_messages(self) -> None:
        """Test mixing tool declarations and messages."""
        payloads = [
            LLMPayload(ROLE.SYSTEM, Text("System")),
            LLMPayload(ROLE.TOOL, Tool(tool=MockTool)),
            LLMPayload(ROLE.USER, Text("Hello")),
        ]
        messages, tools = _payloads_to_openai_messages(payloads)
        assert len(messages) == 2  # SYSTEM and USER
        assert len(tools) == 1  # TOOL


# ============================================================================
# OpenAIChatClient Tests
# ============================================================================


class TestOpenAIChatClient:
    """Test cases for OpenAIChatClient."""

    @pytest.fixture
    def client(self) -> OpenAIChatClient:
        """Create OpenAIChatClient instance."""
        return OpenAIChatClient()

    @pytest.fixture
    def mock_model_config(self) -> dict[str, Any]:
        """Mock model configuration."""
        return {
            "api_key": "sk-test-key",
            "base_url": "https://api.openai.com/v1",
            "timeout": 30.0,
            "max_tokens": 4096,
            "temperature": 0.7,
            "extra_params": {},
        }

    @pytest.fixture
    def sample_payloads(self) -> list[LLMPayload]:
        """Sample payloads for testing."""
        return [
            LLMPayload(ROLE.SYSTEM, Text("You are helpful.")),
            LLMPayload(ROLE.USER, Text("Hello!")),
        ]

    def test_client_creation(self, client: OpenAIChatClient) -> None:
        """Test creating OpenAIChatClient."""
        assert isinstance(client, OpenAIChatClient)
        assert len(client._clients) == 0

    def test_get_loop_key(self, client: OpenAIChatClient) -> None:
        """Test _get_loop_key method."""
        key = client._get_loop_key()
        assert isinstance(key, int)

    @patch("openai.AsyncOpenAI")
    async def test_create_non_streaming(
        self,
        mock_openai_class: Mock,
        client: OpenAIChatClient,
        mock_model_config: dict[str, Any],
        sample_payloads: list[LLMPayload],
    ) -> None:
        """Test create method with non-streaming."""
        # Mock the OpenAI client
        mock_openai = AsyncMock()
        mock_openai_class.return_value = mock_openai

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "Hello! How can I help you?"
        mock_message.tool_calls = None
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        message, tool_calls, stream_iter = await client.create(
            model_name="gpt-4",
            payloads=sample_payloads,
            tools=[],
            request_name="test",
            model_set=mock_model_config,
            stream=False,
        )

        assert message == "Hello! How can I help you?"
        assert tool_calls == []
        assert stream_iter is None

    @patch("openai.AsyncOpenAI")
    async def test_create_streaming(
        self,
        mock_openai_class: Mock,
        client: OpenAIChatClient,
        mock_model_config: dict[str, Any],
        sample_payloads: list[LLMPayload],
    ) -> None:
        """Test create method with streaming."""

        async def mock_stream_generator():
            """Mock streaming response."""
            mock_chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content=" world"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="!"))]),
            ]
            for chunk in mock_chunks:
                yield chunk

        mock_openai = AsyncMock()
        mock_openai_class.return_value = mock_openai

        mock_stream = mock_stream_generator()
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_stream)

        message, tool_calls, stream_iter = await client.create(
            model_name="gpt-4",
            payloads=sample_payloads,
            tools=[],
            request_name="test",
            model_set=mock_model_config,
            stream=True,
        )

        assert message is None
        assert tool_calls is None
        assert stream_iter is not None

        # Consume stream
        chunks = []
        async for event in stream_iter:
            chunks.append(event.text_delta)

        assert chunks == ["Hello", " world", "!"]

    @patch("openai.AsyncOpenAI")
    async def test_create_with_tool_calls(
        self,
        mock_openai_class: Mock,
        client: OpenAIChatClient,
        mock_model_config: dict[str, Any],
        sample_payloads: list[LLMPayload],
    ) -> None:
        """Test create with tool calls."""
        mock_openai = AsyncMock()
        mock_openai_class.return_value = mock_openai

        # Mock response with tool calls
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = "Let me check the weather."

        mock_tc1 = MagicMock()
        mock_tc1.id = "call_123"
        mock_tc1.function.name = "get_weather"
        mock_tc1.function.arguments = '{"location": "Tokyo"}'

        mock_tc2 = MagicMock()
        mock_tc2.id = "call_456"
        mock_tc2.function.name = "get_time"
        mock_tc2.function.arguments = '{"timezone": "UTC"}'

        mock_message.tool_calls = [mock_tc1, mock_tc2]
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

        message, tool_calls, stream_iter = await client.create(
            model_name="gpt-4",
            payloads=sample_payloads,
            tools=[Tool(tool=MockTool)],
            request_name="test",
            model_set=mock_model_config,
            stream=False,
        )

        assert message == "Let me check the weather."
        assert tool_calls is not None
        assert len(tool_calls) == 2
        assert tool_calls[0]["id"] == "call_123"
        assert tool_calls[0]["name"] == "get_weather"
        assert tool_calls[0]["args"] == {"location": "Tokyo"}

    async def test_create_with_invalid_model_set(
        self, client: OpenAIChatClient, sample_payloads: list[LLMPayload]
    ) -> None:
        """Test create with invalid model_set."""
        with pytest.raises(TypeError, match="期望 model_set 为单个模型配置 dict"):
            await client.create(
                model_name="gpt-4",
                payloads=sample_payloads,
                tools=[],
                request_name="test",
                model_set="invalid",  # type: ignore
                stream=False,
            )

    async def test_create_with_empty_api_key(
        self, client: OpenAIChatClient, sample_payloads: list[LLMPayload]
    ) -> None:
        """Test create with empty API key."""
        model_config = {"api_key": "", "base_url": "https://api.openai.com/v1", "timeout": 30, "max_tokens": 4096, "temperature": 0.7, "extra_params": {}}

        with pytest.raises(ValueError, match="model.api_key 不能为空"):
            await client.create(
                model_name="gpt-4",
                payloads=sample_payloads,
                tools=[],
                request_name="test",
                model_set=model_config,
                stream=False,
            )

    async def test_openai_not_installed(
        self, client: OpenAIChatClient, mock_model_config: dict[str, Any], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test behavior when openai SDK is not installed."""
        # Patch the import to raise ImportError
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="openai SDK 未安装"):
                await client.create(
                    model_name="gpt-4",
                    payloads=sample_payloads,
                    tools=[],
                    request_name="test",
                    model_set=mock_model_config,
                    stream=False,
                )

    async def test_client_caching_per_event_loop(
        self, client: OpenAIChatClient, mock_model_config: dict[str, Any], sample_payloads: list[LLMPayload]
    ) -> None:
        """Test that clients are cached per event loop."""
        with patch("openai.AsyncOpenAI") as mock_openai_class:
            mock_openai = AsyncMock()
            mock_openai_class.return_value = mock_openai
            mock_response = MagicMock()
            mock_choice = MagicMock()
            mock_message = MagicMock()
            mock_message.content = "Response"
            mock_message.tool_calls = None
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

            # First call
            await client.create(
                model_name="gpt-4",
                payloads=sample_payloads,
                tools=[],
                request_name="test",
                model_set=mock_model_config,
                stream=False,
            )

            # Second call - should reuse client
            await client.create(
                model_name="gpt-4",
                payloads=sample_payloads,
                tools=[],
                request_name="test2",
                model_set=mock_model_config,
                stream=False,
            )

            # AsyncOpenAI should only be called once due to caching
            assert mock_openai_class.call_count == 1


# ============================================================================
# ModelClientRegistry Tests
# ============================================================================


class TestModelClientRegistry:
    """Test cases for ModelClientRegistry."""

    def test_registry_creation(self) -> None:
        """Test creating ModelClientRegistry."""
        registry = ModelClientRegistry()
        assert registry.openai is not None
        assert registry.gemini is None
        assert registry.bedrock is None

    def test_get_client_for_openai_model(self) -> None:
        """Test getting client for OpenAI model."""
        registry = ModelClientRegistry()
        model = {
            "client_type": "openai",
            "model_identifier": "gpt-4",
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
            "api_provider": "openai",
            "timeout": 30,
            "max_retry": 2,
            "retry_interval": 1.0,
            "price_in": 0.00003,
            "price_out": 0.00006,
            "temperature": 0.7,
            "max_tokens": 4096,
            "extra_params": {},
        }

        client = registry.get_client_for_model(model)
        assert isinstance(client, OpenAIChatClient)

    def test_get_client_with_unknown_type_falls_back_to_openai(self) -> None:
        """Test that unknown client_type falls back to OpenAI."""
        registry = ModelClientRegistry()
        model = {
            "client_type": "unknown",
            "model_identifier": "model",
            "api_key": "key",
            "base_url": "https://api.example.com/v1",
            "api_provider": "unknown",
            "timeout": 30,
            "max_retry": 2,
            "retry_interval": 1.0,
            "price_in": 0.0,
            "price_out": 0.0,
            "temperature": 0.7,
            "max_tokens": 4096,
            "extra_params": {},
        }

        client = registry.get_client_for_model(model)
        assert isinstance(client, OpenAIChatClient)

    def test_registry_with_custom_clients(self) -> None:
        """Test registry with custom client instances."""
        custom_openai = Mock(spec=ChatModelClient)
        registry = ModelClientRegistry(openai=custom_openai)

        model = {
            "client_type": "openai",
            "model_identifier": "gpt-4",
            "api_key": "key",
            "base_url": "https://api.openai.com/v1",
            "api_provider": "openai",
            "timeout": 30,
            "max_retry": 2,
            "retry_interval": 1.0,
            "price_in": 0.00003,
            "price_out": 0.00006,
            "temperature": 0.7,
            "max_tokens": 4096,
            "extra_params": {},
        }

        client = registry.get_client_for_model(model)
        assert client is custom_openai


# ============================================================================
# ChatModelClient Protocol Tests
# ============================================================================


class TestChatModelClientProtocol:
    """Test cases for ChatModelClient protocol."""

    def test_protocol_is_defined(self) -> None:
        """Test that ChatModelClient protocol is defined."""
        # Check that protocol has required method
        assert hasattr(ChatModelClient, "create")

    def test_mock_client_satisfies_protocol(self, mock_chat_client: Mock) -> None:
        """Test that mock client satisfies the protocol."""
        # Check that mock has required method
        assert hasattr(mock_chat_client, "create")
