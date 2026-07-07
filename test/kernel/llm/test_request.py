"""Tests for request.py."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest
from unittest.mock import patch

from src.kernel.llm.exceptions import (
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import LLMPayload, Text, ToolResult
from src.kernel.llm.payload.tooling import LLMUsable
from src.kernel.llm.policy import (
    LoadBalancedPolicy,
    RoundRobinPolicy,
    create_default_policy,
    create_policy,
    set_default_policy_factory,
)
from src.kernel.llm.context import LLMContextManager
from src.kernel.llm import request as request_module
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.roles import ROLE


@pytest.fixture(autouse=True)
def reset_default_policy_factory() -> Generator[None, None, None]:
    """隔离默认 policy 的全局缓存，避免测试间状态污染。"""
    set_default_policy_factory(None)
    yield
    set_default_policy_factory(None)


# ============================================================================
# Mock Client for Testing
# ============================================================================


class MockChatClient:
    """Mock ChatModelClient for testing."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        """Initialize with predefined responses."""
        self.responses = responses or []
        self.call_count = 0

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        """Return predefined response or default success response."""
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            if isinstance(response, Exception):
                raise response
            return response

        # Default success response
        if stream:
            async def stream_gen():
                for chunk in ["Hello", " world", "!"]:
                    yield StreamEvent(text_delta=chunk)
            return None, None, stream_gen()
        else:
            return "Success response!", None, None


class TrackingChatClient(MockChatClient):
    """记录每次 create 调用参数的测试客户端。"""

    def __init__(self, responses: list[Any] | None = None) -> None:
        super().__init__(responses=responses)
        self.calls: list[dict[str, Any]] = []

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        self.calls.append(
            {
                "model_name": model_name,
                "payloads": list(payloads),
                "tools": list(tools),
                "request_name": request_name,
                "model_set": dict(model_set),
                "stream": stream,
            }
        )
        return await super().create(
            model_name=model_name,
            payloads=payloads,
            tools=tools,
            request_name=request_name,
            model_set=model_set,
            stream=stream,
        )


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def mock_model_set() -> list[dict[str, Any]]:
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
            "max_context": 32768,
            "tool_call_compat": False,
            "extra_params": {"context_reserve_ratio": 0.1, "context_reserve_tokens": 0},
        },
        {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "gpt-3.5-turbo",
            "api_key": "sk-test-key-2",
            "client_type": "openai",
            "max_retry": 1,
            "timeout": 30.0,
            "retry_interval": 0.5,
            "price_in": 0.00001,
            "price_out": 0.00002,
            "temperature": 0.7,
            "max_tokens": 4096,
            "max_context": 32768,
            "tool_call_compat": False,
            "extra_params": {"context_reserve_ratio": 0.1, "context_reserve_tokens": 0},
        },
    ]


@pytest.fixture
def sample_payloads() -> list[LLMPayload]:
    """Sample payloads for testing."""
    return [
        LLMPayload(ROLE.SYSTEM, Text("You are helpful.")),
        LLMPayload(ROLE.USER, Text("Hello!")),
    ]


# ============================================================================
# LLMRequest Tests
# ============================================================================


class TestLLMRequest:
    """Test cases for LLMRequest."""

    def test_request_creation(self, mock_model_set: list[dict[str, Any]]) -> None:
        """Test creating LLMRequest."""
        request = LLMRequest(mock_model_set, "test_request")
        assert request.model_set == mock_model_set
        assert request.request_name == "test_request"
        assert request.payloads == []
        assert request.policy is not None
        assert request.clients is not None
        assert request.enable_metrics is True

    def test_request_with_custom_payloads(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test creating LLMRequest with custom payloads."""
        payloads = [LLMPayload(ROLE.USER, Text("Hello"))]
        request = LLMRequest(mock_model_set, "test", payloads=payloads)
        assert request.payloads == payloads

    def test_request_default_initialization(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that request initializes default values."""
        request = LLMRequest(mock_model_set, "test", payloads=None)
        assert request.payloads == []
        assert isinstance(request.policy, LoadBalancedPolicy)
        assert request.clients is not None  # ModelClientRegistry

    def test_create_default_policy_without_config_uses_load_balanced(self) -> None:
        """Test default policy fallback when model config is not initialized."""
        set_default_policy_factory(None)
        policy = create_default_policy()
        assert isinstance(policy, LoadBalancedPolicy)

    def test_create_default_policy_reuses_default_instance(self) -> None:
        """默认 policy 应跨请求复用，以保留轮询和负载均衡状态。"""
        set_default_policy_factory(None)
        try:
            first = create_default_policy()
            second = create_default_policy()
            assert first is second
        finally:
            set_default_policy_factory(None)

    def test_create_default_policy_uses_injected_factory(self) -> None:
        """Test default policy respects injected factory from upper layer."""
        try:
            set_default_policy_factory(lambda: create_policy("round_robin"))
            policy = create_default_policy()
            assert isinstance(policy, RoundRobinPolicy)
            assert create_default_policy() is policy
        finally:
            set_default_policy_factory(None)

    def test_add_payload(self, mock_model_set: list[dict[str, Any]]) -> None:
        """Test add_payload method."""
        request = LLMRequest(mock_model_set, "test")
        payload1 = LLMPayload(ROLE.USER, Text("Hello"))
        payload2 = LLMPayload(ROLE.USER, Text("World"))

        result = request.add_payload(payload1)
        assert result is request  # Returns self for chaining
        assert len(request.payloads) == 1

        request.add_payload(payload2)
        assert len(request.payloads) == 1
        assert request.payloads[0].role == ROLE.USER
        assert request.payloads[0].content[0].text == "Hello"
        assert request.payloads[0].content[1].text == "World"

    def test_add_payload_at_position(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test add_payload with position parameter."""
        request = LLMRequest(mock_model_set, "test")
        payload1 = LLMPayload(ROLE.USER, Text("First"))
        payload2 = LLMPayload(ROLE.ASSISTANT, Text("Second"))
        payload3 = LLMPayload(ROLE.USER, Text("Third"))

        request.add_payload(payload1)
        request.add_payload(payload2)
        request.add_payload(payload3, position=1)

        assert request.payloads[0].content[0].text == "First"
        assert request.payloads[1].content[0].text == "Third"
        assert request.payloads[2].content[0].text == "Second"

    def test_chaining_add_payload(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test chaining add_payload calls."""
        request = (
            LLMRequest(mock_model_set, "test")
            .add_payload(LLMPayload(ROLE.USER, Text("Hello")))
            .add_payload(LLMPayload(ROLE.ASSISTANT, Text("Hi")))
            .add_payload(LLMPayload(ROLE.USER, Text("How are you?")))
        )
        assert len(request.payloads) == 3


class TestValidateModelSet:
    """Test cases for _validate_model_set function."""

    def test_valid_model_set(self, mock_model_set: list[dict[str, Any]]) -> None:
        """Test validation of valid model set."""
        from src.kernel.llm.request import _validate_model_set

        result = _validate_model_set(mock_model_set)
        assert result == mock_model_set

    def test_empty_model_set(self) -> None:
        """Test validation of empty model set."""
        from src.kernel.llm.request import _validate_model_set

        with pytest.raises(LLMConfigurationError, match="model_set 必须是非空 list\\[dict\\]"):
            _validate_model_set([])

    def test_model_set_not_a_list(self) -> None:
        """Test validation when model_set is not a list."""
        from src.kernel.llm.request import _validate_model_set

        with pytest.raises(LLMConfigurationError, match="model_set 必须是非空 list\\[dict\\]"):
            _validate_model_set("not_a_list")  # type: ignore

    def test_model_set_with_non_dict_elements(self) -> None:
        """Test validation when model_set contains non-dict elements."""
        from src.kernel.llm.request import _validate_model_set

        with pytest.raises(LLMConfigurationError, match="model_set 必须是 list\\[dict\\]"):
            _validate_model_set([1, 2, 3])  # type: ignore


class TestValidateModelEntry:
    """Test cases for _validate_model_entry function."""

    def test_valid_model_entry(self) -> None:
        """Test validation of valid model entry."""
        from src.kernel.llm.request import _validate_model_entry

        model = {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "gpt-4",
            "api_key": "sk-test",
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
        result = _validate_model_entry(model)
        assert result == model
        assert result["force_stream_mode"] is False

    def test_missing_required_fields(self) -> None:
        """Test validation with missing required fields."""
        from src.kernel.llm.request import _validate_model_entry

        model = {"api_provider": "openai", "base_url": "https://api.openai.com/v1"}
        with pytest.raises(LLMConfigurationError, match="model_set 元素缺少字段"):
            _validate_model_entry(model)

    def test_invalid_extra_params(self) -> None:
        """Test validation with invalid extra_params."""
        from src.kernel.llm.request import _validate_model_entry

        model = {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "gpt-4",
            "api_key": "sk-test",
            "client_type": "openai",
            "max_retry": 2,
            "timeout": 30.0,
            "retry_interval": 1.0,
            "price_in": 0.00003,
            "price_out": 0.00006,
            "temperature": 0.7,
            "max_tokens": 4096,
            "extra_params": "not_a_dict",  # type: ignore
        }
        with pytest.raises(LLMConfigurationError, match="model.extra_params 必须是 dict"):
            _validate_model_entry(model)

    def test_invalid_tool_call_compat(self) -> None:
        """Test validation with invalid tool_call_compat type."""
        from src.kernel.llm.request import _validate_model_entry

        model = {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "gpt-4",
            "api_key": "sk-test",
            "client_type": "openai",
            "max_retry": 2,
            "timeout": 30.0,
            "retry_interval": 1.0,
            "price_in": 0.00003,
            "price_out": 0.00006,
            "temperature": 0.7,
            "max_tokens": 4096,
            "tool_call_compat": "true",  # type: ignore
            "extra_params": {},
        }
        with pytest.raises(LLMConfigurationError, match="model.tool_call_compat 必须是 bool"):
            _validate_model_entry(model)

    def test_force_stream_mode_validation(self) -> None:
        """Test validation of force_stream_mode type and value."""
        from src.kernel.llm.request import _validate_model_entry

        model = {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "gpt-4",
            "api_key": "sk-test",
            "client_type": "openai",
            "max_retry": 2,
            "timeout": 30.0,
            "retry_interval": 1.0,
            "price_in": 0.00003,
            "price_out": 0.00006,
            "temperature": 0.7,
            "max_tokens": 4096,
            "force_stream_mode": True,
            "extra_params": {},
        }

        result = _validate_model_entry(model)
        assert result["force_stream_mode"] is True

        model["force_stream_mode"] = "true"  # type: ignore[assignment]
        with pytest.raises(LLMConfigurationError, match="model.force_stream_mode 必须是 bool"):
            _validate_model_entry(model)


class TestNormalizeToolResultPayload:
    """Test cases for _normalize_tool_result_payload function."""

    def test_non_tool_result_payload_unchanged(self) -> None:
        """Test that non-TOOL_RESULT payloads are unchanged."""
        from src.kernel.llm.request import _normalize_tool_result_payload

        payload = LLMPayload(ROLE.USER, Text("Hello"))
        result = _normalize_tool_result_payload(payload)
        assert result is payload

    def test_tool_result_with_tool_result_content(self) -> None:
        """Test TOOL_RESULT payload with ToolResult content."""
        from src.kernel.llm.request import _normalize_tool_result_payload

        result = ToolResult(value={"output": "success"}, call_id="call_123")
        payload = LLMPayload(ROLE.TOOL_RESULT, result)
        normalized = _normalize_tool_result_payload(payload)

        assert normalized.role == ROLE.TOOL_RESULT
        assert len(normalized.content) == 1
        assert isinstance(normalized.content[0], ToolResult)

    def test_tool_result_with_text_content(self) -> None:
        """Test TOOL_RESULT payload with Text content."""
        from src.kernel.llm.request import _normalize_tool_result_payload

        payload = LLMPayload(ROLE.TOOL_RESULT, Text("Result text"))
        normalized = _normalize_tool_result_payload(payload)

        assert normalized.role == ROLE.TOOL_RESULT
        assert isinstance(normalized.content[0], Text)

    def test_tool_result_with_mixed_content(self) -> None:
        """Test TOOL_RESULT payload with mixed content types."""
        from src.kernel.llm.request import _normalize_tool_result_payload

        payload = LLMPayload(
            ROLE.TOOL_RESULT,
            [
                ToolResult(value={"result": "ok"}, call_id="call_123"),
                Text("Additional info"),
                "raw_string",  # Should be converted to Text
            ],
        )
        normalized = _normalize_tool_result_payload(payload)

        assert len(normalized.content) == 3
        assert isinstance(normalized.content[0], ToolResult)
        assert isinstance(normalized.content[1], Text)
        assert isinstance(normalized.content[2], Text)


class TestExtractTools:
    """Test cases for _extract_tools function."""

    def test_extract_tools_from_payloads(self) -> None:
        """Test extracting tools from payloads."""

        class MockTool:
            @classmethod
            def to_schema(cls) -> dict:
                return {"name": "mock_tool"}

        from src.kernel.llm.request import _extract_tools

        payloads = [
            LLMPayload(ROLE.USER, Text("Hello")),
            LLMPayload(ROLE.TOOL, MockTool),
            LLMPayload(ROLE.TOOL, MockTool),
        ]
        tools = _extract_tools(payloads)

        assert len(tools) == 2
        assert all(t is MockTool for t in tools)

    def test_extract_tools_from_non_tool_roles(self) -> None:
        """Test that non-TOOL roles don't extract tools."""
        from src.kernel.llm.request import _extract_tools

        payloads = [
            LLMPayload(ROLE.SYSTEM, Text("System")),
            LLMPayload(ROLE.USER, Text("User")),
            LLMPayload(ROLE.ASSISTANT, Text("Assistant")),
        ]
        tools = _extract_tools(payloads)

        assert len(tools) == 0

    def test_extract_tools_supports_class_and_instance(self) -> None:
        """Test that TOOL payload can contain both usable classes and instances."""

        class ClassTool:
            @classmethod
            def to_schema(cls) -> dict:
                return {"name": "class_tool"}

        class InstanceTool:
            def to_schema(self) -> dict:
                return {"name": "instance_tool"}

        class InvalidTool:
            pass

        from src.kernel.llm.request import _extract_tools

        payloads = [
            LLMPayload(ROLE.TOOL, [ClassTool, InstanceTool(), InvalidTool]),
        ]

        tools = _extract_tools(payloads)

        assert len(tools) == 2
        assert tools[0] is ClassTool
        assert isinstance(tools[1], InstanceTool)


class TestLLMRequestSend:
    """Test cases for LLMRequest.send method."""

    @pytest.mark.asyncio
    async def test_send_success_non_streaming(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test successful send with non-streaming."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        # Mock the client
        mock_client = MockChatClient()
        request.clients.openai = mock_client

        response = await request.send(stream=False)
        assert response.message == "Success response!"

    @pytest.mark.asyncio
    async def test_send_applies_token_budget_trimming(
        self, mock_model_set: list[dict[str, Any]], monkeypatch
    ) -> None:
        """Test that model max_context budget triggers payload trimming."""

        class CaptureClient(MockChatClient):
            def __init__(self) -> None:
                super().__init__()
                self.last_payloads: list[LLMPayload] = []

            async def create(self, **kwargs):  # type: ignore[override]
                self.last_payloads = kwargs["payloads"]
                return "ok", None, None

        mock_model_set[0]["max_context"] = 120
        mock_model_set[0]["max_tokens"] = 20
        mock_model_set[0]["extra_params"]["context_reserve_ratio"] = 0.0
        mock_model_set[0]["extra_params"]["context_reserve_tokens"] = 0

        request = LLMRequest(mock_model_set, "test")
        for idx in range(6):
            request.add_payload(LLMPayload(ROLE.USER, Text(f"q{idx}")))
            request.add_payload(LLMPayload(ROLE.ASSISTANT, Text(f"a{idx}")))

        monkeypatch.setattr(
            "src.kernel.llm.context.count_payload_tokens",
            lambda payloads, model_identifier: len(payloads) * 30,
        )

        capture_client = CaptureClient()
        request.clients.openai = capture_client

        await request.send(stream=False)

        assert len(capture_client.last_payloads) < 12

    @pytest.mark.asyncio
    async def test_send_success_streaming(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test successful send with streaming."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = MockChatClient()
        request.clients.openai = mock_client

        response = await request.send(stream=True)

        # Collect streamed content
        chunks = []
        async for chunk in response:
            chunks.append(chunk)

        assert " ".join(chunks) == "Hello  world !"

    @pytest.mark.asyncio
    async def test_force_stream_mode_overrides_false_stream_argument(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test model force_stream_mode makes stream=False requests use streaming."""
        forced_model_set = [dict(mock_model_set[0], force_stream_mode=True)]
        request = LLMRequest(forced_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = TrackingChatClient()
        request.clients.openai = mock_client

        response = await request.send(stream=False)
        text = await response

        assert mock_client.calls[0]["stream"] is True
        assert text == "Hello world!"

    @pytest.mark.asyncio
    async def test_false_stream_argument_remains_non_streaming_without_force(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test stream=False remains non-streaming when force_stream_mode is disabled."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = TrackingChatClient()
        request.clients.openai = mock_client

        response = await request.send(stream=False)

        assert mock_client.calls[0]["stream"] is False
        assert response.message == "Success response!"

    @pytest.mark.asyncio
    async def test_send_with_tool_calls(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test send with tool calls in response."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        # Set up response with tool calls
        mock_client = MockChatClient(
            responses=[
                (
                    "Let me check that",
                    [
                        {"id": "call_123", "name": "get_weather", "args": {"location": "Tokyo"}},
                        {"id": "call_456", "name": "get_time", "args": {"timezone": "UTC"}},
                    ],
                    None,
                )
            ]
        )
        request.clients.openai = mock_client

        response = await request.send(stream=False)

        assert response.message == "Let me check that"
        assert len(response.call_list) == 2
        assert response.call_list[0].name == "get_weather"
        assert response.call_list[1].name == "get_time"

    @pytest.mark.asyncio
    async def test_send_with_retry_on_error(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test retry mechanism on error."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        # First call fails, second succeeds
        mock_client = MockChatClient(
            responses=[LLMTimeoutError("Timeout"), ("Success!", None, None)]
        )
        request.clients.openai = mock_client

        response = await request.send(stream=False)
        assert response.message == "Success!"
        assert mock_client.call_count == 2

    @pytest.mark.asyncio
    async def test_send_model_switch_after_retries(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test switching to next model after retries exhausted."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        # First model fails all retries, second succeeds
        mock_client = MockChatClient(
            responses=[
                LLMTimeoutError("Timeout"),
                LLMTimeoutError("Timeout"),
                LLMTimeoutError("Timeout"),
                ("Fallback success!", None, None),
            ]
        )
        request.clients.openai = mock_client

        response = await request.send(stream=False)
        assert response.message == "Fallback success!"
        assert mock_client.call_count == 4

    @pytest.mark.asyncio
    async def test_send_all_models_exhausted(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test when all models are exhausted."""
        # Create model set with limited retries
        limited_model_set = [
            {
                "api_provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model_identifier": "gpt-4",
                "api_key": "sk-test",
                "client_type": "openai",
                "max_retry": 0,  # No retries
                "timeout": 30.0,
                "retry_interval": 1.0,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            }
        ]

        request = LLMRequest(limited_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = MockChatClient(responses=[LLMTimeoutError("Timeout")])
        request.clients.openai = mock_client

        with pytest.raises(LLMTimeoutError):
            await request.send(stream=False)

    @pytest.mark.asyncio
    async def test_send_with_delay_between_retries(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that delay is applied between retries."""
        request = LLMRequest(mock_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = MockChatClient(
            responses=[LLMTimeoutError("Timeout"), ("Success!", None, None)]
        )
        request.clients.openai = mock_client

        start = asyncio.get_event_loop().time()
        response = await request.send(stream=False)
        elapsed = asyncio.get_event_loop().time() - start

        assert response.message == "Success!"
        # Should have at least retry_interval delay (1.0 second)
        assert elapsed >= 0.9  # Allow small tolerance

    @pytest.mark.asyncio
    async def test_send_compresses_context_when_token_usage_reaches_threshold(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that send performs context compression when token usage reaches 95% threshold."""
        from src.core.utils.context_compression import default_chat_context_compression_handler

        class CompressionTool:
            @classmethod
            def to_schema(cls) -> dict[str, Any]:
                return {"name": "compression_tool"}

        threshold_model_set = [{**mock_model_set[0], "max_context": 60, "extra_params": {}}]
        request = LLMRequest(
            threshold_model_set,
            "test_request",
            context_manager=LLMContextManager(
                context_compression_handler=default_chat_context_compression_handler
            ),
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text("sys")))
        request.add_payload(LLMPayload(ROLE.TOOL, CompressionTool))
        request.add_payload(LLMPayload(ROLE.USER, Text("q1")))
        request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("a1")))
        request.add_payload(LLMPayload(ROLE.USER, Text("q2")))
        request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("a2")))
        request.add_payload(LLMPayload(ROLE.USER, Text("q3")))
        request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("a3")))

        client = TrackingChatClient(
            responses=[
                ("<analysis>ignored</analysis><summary>压缩后的历史</summary>", None, None),
                ("Final answer", None, None),
            ]
        )
        request.clients.openai = client

        with patch(
            "src.kernel.llm.context.count_payload_tokens",
            side_effect=lambda items, model_identifier=None: len(items) * 10,
        ):
            response = await request.send(stream=False)

        assert response.message == "Final answer"
        assert len(client.calls) == 2
        assert client.calls[0]["request_name"] == "test_request:context_compression"
        assert client.calls[0]["model_set"]["timeout"] == 120.0
        assert len(client.calls[0]["tools"]) == 1

        compression_prompt = client.calls[0]["payloads"][-1]
        assert compression_prompt.role == ROLE.USER
        assert "创建一个迄今为止对话的详细摘要" in compression_prompt.content[0].text

        compression_payload_texts = [
            part.text
            for payload in client.calls[0]["payloads"]
            for part in payload.content
            if isinstance(part, Text)
        ]
        assert "sys" in compression_payload_texts
        assert "q1" in compression_payload_texts
        assert "q2" in compression_payload_texts
        assert "q3" in compression_payload_texts

        final_payload_texts = [
            part.text
            for payload in client.calls[1]["payloads"]
            for part in payload.content
            if isinstance(part, Text)
        ]
        assert any("压缩后的历史" in text for text in final_payload_texts)
        assert "q1" not in final_payload_texts
        assert "q2" not in final_payload_texts
        assert "q3" not in final_payload_texts

        request_payload_texts = [
            part.text
            for payload in request.payloads
            for part in payload.content
            if isinstance(part, Text)
        ]
        assert any("压缩后的历史" in text for text in request_payload_texts)
        assert "q1" not in request_payload_texts
        assert "q2" not in request_payload_texts
        assert "q3" not in request_payload_texts

    @pytest.mark.asyncio
    async def test_send_skips_compression_before_token_threshold(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that send does not compress before token usage reaches 95% threshold."""
        from src.core.utils.context_compression import default_chat_context_compression_handler

        threshold_model_set = [{**mock_model_set[0], "max_context": 100, "extra_params": {}}]
        request = LLMRequest(
            threshold_model_set,
            "test_request",
            context_manager=LLMContextManager(
                context_compression_handler=default_chat_context_compression_handler
            ),
        )
        request.add_payload(LLMPayload(ROLE.USER, Text("q1")))
        request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("a1")))
        request.add_payload(LLMPayload(ROLE.USER, Text("q2")))
        request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("a2")))

        client = TrackingChatClient(responses=[("Final answer", None, None)])
        request.clients.openai = client

        with patch(
            "src.kernel.llm.context.count_payload_tokens",
            side_effect=lambda items, model_identifier=None: len(items) * 10,
        ):
            response = await request.send(stream=False)

        assert response.message == "Final answer"
        assert len(client.calls) == 1
        assert client.calls[0]["request_name"] == "test_request"

    @pytest.mark.asyncio
    async def test_send_metrics_collection(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that metrics are collected on send."""
        from src.kernel.llm.monitor import get_global_collector

        # Clear global collector
        collector = get_global_collector()
        collector.clear()

        request = LLMRequest(mock_model_set, "test_request")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = MockChatClient(responses=[("Success!", None, None)])
        request.clients.openai = mock_client

        await request.send(stream=False)

        # Check metrics were recorded
        history = collector.get_recent_history(limit=10)
        assert len(history) == 1
        assert history[0].model_name == "gpt-4"
        assert history[0].request_name == "test_request"
        assert history[0].success is True

    @pytest.mark.asyncio
    async def test_send_with_metrics_disabled(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test send with metrics disabled."""
        from src.kernel.llm.monitor import get_global_collector

        collector = get_global_collector()
        collector.clear()

        request = LLMRequest(mock_model_set, "test", enable_metrics=False)
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        mock_client = MockChatClient(responses=[("Success!", None, None)])
        request.clients.openai = mock_client

        await request.send(stream=False)

        # No metrics should be recorded
        history = collector.get_recent_history(limit=10)
        assert len(history) == 0

    def test_init_accepts_meta_data(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that request keeps caller-provided meta_data intact."""
        request = LLMRequest(
            mock_model_set,
            "test_request",
            meta_data={"stream_id": "stream-456", "trace_id": "trace-1"},
        )

        assert request.meta_data == {"stream_id": "stream-456", "trace_id": "trace-1"}

    def test_calculate_request_cost_prefers_cache_miss_tokens(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that request cost supports a dedicated cache-hit input price."""
        model = dict(mock_model_set[0])
        model["cache_hit_price_in"] = 0.00001

        cost = request_module._calculate_request_cost(
            model=model,
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "cache_hit_tokens": 700,
                "cache_miss_tokens": 300,
            },
        )

        expected = round((300 * 0.00003 + 700 * 0.00001 + 500 * 0.00006) / 1_000_000, 8)
        assert cost == expected

    def test_calculate_request_cost_falls_back_to_normal_input_price(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that cache-hit input price falls back to price_in when omitted."""
        cost = request_module._calculate_request_cost(
            model=mock_model_set[0],
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "cache_hit_tokens": 700,
                "cache_miss_tokens": 300,
            },
        )

        expected = round((1000 * 0.00003 + 500 * 0.00006) / 1_000_000, 8)
        assert cost == expected

    @pytest.mark.asyncio
    async def test_stream_stats_record_after_response_consumed(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that stream usage stats are recorded after the response is fully consumed."""

        class StreamUsageClient:
            async def create(
                self,
                *,
                model_name: str,
                payloads: list[LLMPayload],
                tools: list[LLMUsable],
                request_name: str,
                model_set: Any,
                stream: bool,
            ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None, str | None, dict[str, Any] | None]:
                del model_name, payloads, tools, request_name, model_set

                async def stream_gen() -> AsyncIterator[StreamEvent]:
                    yield StreamEvent(text_delta="hello")
                    if stream:
                        yield StreamEvent(
                            usage={
                                "prompt_tokens": 120,
                                "completion_tokens": 30,
                                "total_tokens": 150,
                                "cache_hit_tokens": 80,
                                "cache_miss_tokens": 40,
                            }
                        )

                return None, None, stream_gen(), None, None

        request = LLMRequest(mock_model_set[:1], "stream_stats", meta_data={"stream_id": "stream-1"})
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
        request.clients.openai = StreamUsageClient()

        captured: list[dict[str, Any]] = []

        def fake_record(**kwargs: Any) -> None:
            captured.append(kwargs)

        with patch("src.kernel.llm.request._record_llm_stats", side_effect=fake_record):
            response = await request.send(stream=True)
            assert captured == []

            text = await response

        assert text == "hello"
        assert len(captured) == 1
        assert captured[0]["stream"] is True
        assert captured[0]["usage"]["total_tokens"] == 150
        assert captured[0]["meta_data"]["stream_id"] == "stream-1"

    @pytest.mark.asyncio
    async def test_force_stream_stats_use_actual_stream_after_consumption(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test force_stream_mode records stats as streaming after response consumption."""

        class StreamUsageClient:
            async def create(
                self,
                *,
                model_name: str,
                payloads: list[LLMPayload],
                tools: list[LLMUsable],
                request_name: str,
                model_set: Any,
                stream: bool,
            ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None, str | None, dict[str, Any] | None]:
                assert stream is True
                del model_name, payloads, tools, request_name, model_set

                async def stream_gen() -> AsyncIterator[StreamEvent]:
                    yield StreamEvent(text_delta="hello")
                    yield StreamEvent(
                        usage={
                            "prompt_tokens": 120,
                            "completion_tokens": 30,
                            "total_tokens": 150,
                            "cache_hit_tokens": 80,
                            "cache_miss_tokens": 40,
                        }
                    )

                return None, None, stream_gen(), None, None

        forced_model_set = [dict(mock_model_set[0], force_stream_mode=True)]
        request = LLMRequest(forced_model_set, "stream_stats", meta_data={"stream_id": "stream-1"})
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
        request.clients.openai = StreamUsageClient()

        captured: list[dict[str, Any]] = []

        def fake_record(**kwargs: Any) -> None:
            captured.append(kwargs)

        with patch("src.kernel.llm.request._record_llm_stats", side_effect=fake_record):
            response = await request.send(stream=False)
            assert captured == []

            text = await response

        assert text == "hello"
        assert len(captured) == 1
        assert captured[0]["stream"] is True
        assert captured[0]["usage"]["total_tokens"] == 150
        assert captured[0]["meta_data"]["stream_id"] == "stream-1"

    @pytest.mark.asyncio
    async def test_send_invalid_model_identifier(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test send with invalid model_identifier."""
        invalid_model_set = [
            {
                "api_provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model_identifier": "",  # Empty identifier
                "api_key": "sk-test",
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

        request = LLMRequest(invalid_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        with pytest.raises(LLMConfigurationError, match="model.model_identifier 必须是非空字符串"):
            await request.send(stream=False)

    @pytest.mark.asyncio
    async def test_send_exception_classification(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that exceptions are properly classified."""
        # Create a model set with max_retry=0 to ensure exception is raised immediately
        no_retry_model_set = [
            {**model, "max_retry": 0} for model in mock_model_set[:1]
        ]
        request = LLMRequest(no_retry_model_set, "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

        # Use raw exception that should be classified
        mock_client = MockChatClient(responses=[ValueError("rate limit exceeded")])
        request.clients.openai = mock_client

        with pytest.raises(LLMRateLimitError):
            await request.send(stream=False)


# ============================================================================
# Error Logging Level Tests
# ============================================================================


class TestLLMRequestErrorLogging:
    """Test that per-attempt errors are logged at the correct level."""

    @staticmethod
    def _no_retry_model_set(mock_model_set: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a single-model set with max_retry=0 for immediate failure."""
        return [{**mock_model_set[0], "max_retry": 0}]

    @pytest.mark.asyncio
    async def test_5xx_api_error_logs_warning_not_error(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that LLMAPIError with status_code>=500 is logged as WARNING."""
        from unittest.mock import MagicMock, patch

        from src.kernel.llm.exceptions import LLMAPIError

        request = LLMRequest(self._no_retry_model_set(mock_model_set), "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
        request.clients.openai = MockChatClient(
            responses=[LLMAPIError("Internal server error", status_code=500)]
        )

        mock_logger = MagicMock()
        with patch("src.kernel.llm.request.logger", mock_logger):
            with pytest.raises(LLMAPIError):
                await request.send(stream=False)

        # Per-attempt log must be a warning
        assert mock_logger.warning.called, "Expected warning for transient 5xx error"
        warning_msgs = " ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "暂时失败" in warning_msgs, "Warning should mention transient failure"
        # Per-attempt must NOT produce a bare 'LLM 请求失败' error
        error_msgs = " ".join(str(c) for c in mock_logger.error.call_args_list)
        assert "请求失败" not in error_msgs or "重试已耗尽" in error_msgs

    @pytest.mark.asyncio
    async def test_503_api_error_logs_warning_not_error(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that LLMAPIError with status_code=503 is logged as WARNING."""
        from unittest.mock import MagicMock, patch

        from src.kernel.llm.exceptions import LLMAPIError

        request = LLMRequest(self._no_retry_model_set(mock_model_set), "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
        request.clients.openai = MockChatClient(
            responses=[LLMAPIError("Service unavailable", status_code=503)]
        )

        mock_logger = MagicMock()
        with patch("src.kernel.llm.request.logger", mock_logger):
            with pytest.raises(LLMAPIError):
                await request.send(stream=False)

        assert mock_logger.warning.called, "Expected warning for transient 503 error"
        warning_msgs = " ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "暂时失败" in warning_msgs
        error_msgs = " ".join(str(c) for c in mock_logger.error.call_args_list)
        assert "请求失败" not in error_msgs or "重试已耗尽" in error_msgs

    @pytest.mark.asyncio
    async def test_4xx_api_error_logs_error(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that LLMAPIError with status_code<500 is logged as ERROR."""
        from unittest.mock import MagicMock, patch

        from src.kernel.llm.exceptions import LLMAPIError

        request = LLMRequest(self._no_retry_model_set(mock_model_set), "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
        request.clients.openai = MockChatClient(
            responses=[LLMAPIError("Bad request", status_code=400)]
        )

        mock_logger = MagicMock()
        with patch("src.kernel.llm.request.logger", mock_logger):
            with pytest.raises(LLMAPIError):
                await request.send(stream=False)

        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_api_error_without_status_code_logs_error(
        self, mock_model_set: list[dict[str, Any]]
    ) -> None:
        """Test that LLMAPIError with no status_code is logged as ERROR."""
        from unittest.mock import MagicMock, patch

        from src.kernel.llm.exceptions import LLMAPIError

        request = LLMRequest(self._no_retry_model_set(mock_model_set), "test")
        request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
        request.clients.openai = MockChatClient(
            responses=[LLMAPIError("Unknown API error")]
        )

        mock_logger = MagicMock()
        with patch("src.kernel.llm.request.logger", mock_logger):
            with pytest.raises(LLMAPIError):
                await request.send(stream=False)

        mock_logger.error.assert_called()
