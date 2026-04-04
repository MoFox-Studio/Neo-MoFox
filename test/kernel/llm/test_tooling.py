"""Tests for payload/tooling.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from src.kernel.llm.payload.tooling import (
    ToolCall,
    ToolRegistry,
    ToolResult,
)


# ============================================================================
# Test Tools (LLMUsable Protocol)
# ============================================================================


class GetTimeTool:
    """Example tool for testing."""

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "Get the current time",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "Timezone identifier",
                        },
                    },
                    "required": ["timezone"],
                },
            },
        }


class WeatherTool:
    """Example tool with simple schema."""

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": "get_weather",
            "description": "Get weather information",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                },
                "required": ["location"],
            },
        }


class BadTool:
    """Tool without proper schema (missing name)."""

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "description": "I have no name",
            "parameters": {},
        }


# ============================================================================
# LLMUsable Protocol Tests
# ============================================================================


class TestLLMUsable:
    """Test cases for LLMUsable protocol."""

    def test_get_time_tool_is_llm_usable(self) -> None:
        """Test that GetTimeTool implements LLMUsable."""
        assert hasattr(GetTimeTool, "to_schema")
        assert callable(GetTimeTool.to_schema)

    def test_tool_returns_valid_schema(self) -> None:
        """Test that tool returns valid schema."""
        schema = GetTimeTool.to_schema()
        assert isinstance(schema, dict)

    def test_schema_with_openai_format(self) -> None:
        """Test schema in OpenAI format."""
        schema = GetTimeTool.to_schema()
        assert schema.get("type") == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "get_time"

    def test_schema_simple_format(self) -> None:
        """Test schema in simple format."""
        schema = WeatherTool.to_schema()
        assert schema.get("name") == "get_weather"
        assert schema.get("description") == "Get weather information"


# ============================================================================
# ToolCall Tests
# ============================================================================


class TestToolCall:
    """Test cases for ToolCall class."""

    def test_tool_call_creation(self) -> None:
        """Test creating ToolCall."""
        call = ToolCall(id="call_123", name="get_time", args={"timezone": "UTC"})
        assert call.id == "call_123"
        assert call.name == "get_time"
        assert call.args == {"timezone": "UTC"}

    def test_tool_call_with_dict_args(self) -> None:
        """Test ToolCall with dict args."""
        call = ToolCall(id="call_1", name="weather", args={"location": "Tokyo"})
        assert isinstance(call.args, dict)
        assert call.args["location"] == "Tokyo"

    def test_tool_call_with_string_args(self) -> None:
        """Test ToolCall with string args."""
        call = ToolCall(id="call_1", name="weather", args='{"location": "Tokyo"}')
        assert isinstance(call.args, str)
        assert call.args == '{"location": "Tokyo"}'

    def test_tool_call_with_none_args(self) -> None:
        """Test ToolCall with None args."""
        call = ToolCall(id="call_1", name="ping", args={})
        assert call.args == {}

    def test_tool_call_with_none_id(self) -> None:
        """Test ToolCall with None id."""
        call = ToolCall(id=None, name="test", args={})
        assert call.id is None

    def test_tool_call_is_frozen(self) -> None:
        """Test that ToolCall is frozen."""
        call = ToolCall(id="call_1", name="test", args={})
        with pytest.raises(Exception):
            call.name = "modified"

    def test_tool_call_equality(self) -> None:
        """Test ToolCall equality."""
        call1 = ToolCall(id="call_1", name="test", args={"a": 1})
        call2 = ToolCall(id="call_1", name="test", args={"a": 1})
        assert call1 == call2

    def test_tool_call_inequality(self) -> None:
        """Test ToolCall inequality."""
        call1 = ToolCall(id="call_1", name="test", args={"a": 1})
        call2 = ToolCall(id="call_2", name="test", args={"a": 1})
        assert call1 != call2



# ============================================================================
# ToolResult Tests
# ============================================================================


class TestToolResult:
    """Test cases for ToolResult class."""

    def test_tool_result_with_string_value(self) -> None:
        """Test ToolResult with string value."""
        result = ToolResult(value="Operation successful", call_id="call_123")
        assert result.value == "Operation successful"
        assert result.call_id == "call_123"

    def test_tool_result_with_dict_value(self) -> None:
        """Test ToolResult with dict value."""
        result = ToolResult(
            value={"temperature": 25, "condition": "sunny"},
            call_id="call_123",
            name="get_weather",
        )
        assert result.value == {"temperature": 25, "condition": "sunny"}
        assert result.call_id == "call_123"
        assert result.name == "get_weather"

    def test_tool_result_to_text_with_string(self) -> None:
        """Test to_text with string value."""
        result = ToolResult(value="Hello, world!", call_id="call_1")
        assert result.to_text() == "Hello, world!"

    def test_tool_result_to_text_with_dict(self) -> None:
        """Test to_text with dict value."""
        result = ToolResult(value={"key": "value", "number": 42}, call_id="call_1")
        text = result.to_text()
        assert '"key": "value"' in text
        assert '"number": 42' in text

    def test_tool_result_to_text_with_non_serializable(self) -> None:
        """Test to_text with non-JSON-serializable value."""

        class CustomObject:
            def __str__(self) -> str:
                return "CustomObject()"

        result = ToolResult(value=CustomObject(), call_id="call_1")
        assert result.to_text() == "CustomObject()"

    def test_tool_result_with_none_call_id(self) -> None:
        """Test ToolResult with None call_id."""
        result = ToolResult(value="result")
        assert result.call_id is None

    def test_tool_result_with_none_name(self) -> None:
        """Test ToolResult with None name."""
        result = ToolResult(value="result", call_id="call_1")
        assert result.name is None

    def test_tool_result_is_frozen(self) -> None:
        """Test that ToolResult is frozen."""
        result = ToolResult(value="test")
        with pytest.raises(Exception):
            result.value = "modified"


# ============================================================================
# ToolRegistry Tests
# ============================================================================


class TestToolRegistry:
    """Test cases for ToolRegistry class."""

    def test_registry_creation(self) -> None:
        """Test creating ToolRegistry."""
        registry = ToolRegistry()
        assert len(registry.get_all_names()) == 0

    def test_register_tool_auto_name(self) -> None:
        """Test registering tool with auto name extraction."""
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        names = registry.get_all_names()
        assert "get_time" in names

    def test_register_tool_custom_name(self) -> None:
        """Test registering tool with custom name."""
        registry = ToolRegistry()
        registry.register(WeatherTool, name="weather_api")
        names = registry.get_all_names()
        assert "weather_api" in names
        assert "get_weather" not in names

    def test_register_multiple_tools(self) -> None:
        """Test registering multiple tools."""
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        registry.register(WeatherTool)
        names = registry.get_all_names()
        assert len(names) == 2
        assert "get_time" in names
        assert "get_weather" in names

    def test_register_tool_without_name_fails(self) -> None:
        """Test that registering tool without name raises error."""
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="无法确定工具名称"):
            registry.register(BadTool)

    def test_get_tool_by_name(self) -> None:
        """Test getting tool by name."""
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        tool_cls = registry.get("get_time")
        assert tool_cls == GetTimeTool

    def test_get_nonexistent_tool(self) -> None:
        """Test getting non-existent tool."""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_list_all_schemas(self) -> None:
        """Test listing all tool schemas."""
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        registry.register(WeatherTool)
        schemas = registry.list_all()
        assert len(schemas) == 2
        assert all(s.get("type") == "function" for s in schemas)

    def test_list_all_returns_openai_format(self) -> None:
        """Test that list_all returns OpenAI format."""
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        schemas = registry.list_all()
        assert schemas[0]["type"] == "function"
        assert "function" in schemas[0]


# ============================================================================
# ToolExecutor Tests - DISABLED (ToolExecutor class removed)
# ============================================================================


# class TestToolExecutor:
#     """Test cases for ToolExecutor class."""
#     # Tests commented out as ToolExecutor class has been removed from the codebase


# ============================================================================
# Integration Tests
# ============================================================================


class TestToolIntegration:
    """Integration tests for tool components."""

    def test_tool_workflow(self) -> None:
        """Test complete tool workflow: registry -> schema -> call -> result."""
        # 1. Register tool
        registry = ToolRegistry()
        registry.register(GetTimeTool)

        # 2. Get schema
        schemas = registry.list_all()
        assert len(schemas) == 1

        # 3. Create tool call (simulating LLM response)
        tool_call = ToolCall(
            id="call_123", name="get_time", args={"timezone": "UTC"}
        )

        # 4. Create tool result (simulating execution)
        result = ToolResult(
            value={"time": "12:00:00", "timezone": "UTC"},
            call_id="call_123",
            name="get_time",
        )

        assert tool_call.name == "get_time"
        assert result.call_id == tool_call.id
