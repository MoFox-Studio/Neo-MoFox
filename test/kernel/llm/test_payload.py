"""Tests for payload/payload.py."""

from __future__ import annotations

import pytest

from src.kernel.llm.payload.content import Action, Audio, Image, Text
from src.kernel.llm.payload.payload import LLMPayload, _normalize_content
from src.kernel.llm.roles import ROLE


class TestNormalizeContent:
    """Test cases for _normalize_content function."""

    def test_normalize_single_content(self) -> None:
        """Test normalizing a single Content item."""
        text = Text(text="Hello")
        result = _normalize_content(text)
        assert result == [text]
        assert isinstance(result, list)

    def test_normalize_content_list(self) -> None:
        """Test normalizing a list of Content items."""
        content_list = [Text(text="Hello"), Image(value="pic.jpg")]
        result = _normalize_content(content_list)
        assert result == content_list
        assert isinstance(result, list)

    def test_normalize_empty_list(self) -> None:
        """Test normalizing an empty list."""
        result = _normalize_content([])
        assert result == []

    def test_normalize_mixed_content(self) -> None:
        """Test normalizing mixed content types."""
        content_list = [
            Text(text="Text"),
            Image(value="image.jpg"),
            Audio(value="audio.mp3"),
            Action(action=object),
        ]
        result = _normalize_content(content_list)
        assert len(result) == 4
        assert isinstance(result[0], Text)
        assert isinstance(result[1], Image)
        assert isinstance(result[2], Audio)
        assert isinstance(result[3], Action)


class TestLLMPayload:
    """Test cases for LLMPayload class."""

    def test_payload_creation_with_single_content(self) -> None:
        """Test creating payload with single content."""
        text = Text(text="Hello, world!")
        payload = LLMPayload(ROLE.USER, text)
        assert payload.role == ROLE.USER
        assert payload.content == [text]

    def test_payload_creation_with_content_list(self) -> None:
        """Test creating payload with content list."""
        content_list = [
            Text(text="Hello"),
            Image(value="pic.jpg"),
        ]
        payload = LLMPayload(ROLE.USER, content_list)
        assert payload.role == ROLE.USER
        assert payload.content == content_list

    def test_payload_with_system_role(self) -> None:
        """Test creating payload with SYSTEM role."""
        payload = LLMPayload(ROLE.SYSTEM, Text(text="You are helpful."))
        assert payload.role == ROLE.SYSTEM
        assert len(payload.content) == 1

    def test_payload_with_user_role(self) -> None:
        """Test creating payload with USER role."""
        payload = LLMPayload(ROLE.USER, Text(text="Hello"))
        assert payload.role == ROLE.USER

    def test_payload_with_assistant_role(self) -> None:
        """Test creating payload with ASSISTANT role."""
        payload = LLMPayload(ROLE.ASSISTANT, Text(text="Hi there!"))
        assert payload.role == ROLE.ASSISTANT

    def test_payload_with_tool_role(self) -> None:
        """Test creating payload with TOOL role."""
        from src.kernel.llm.payload import Tool

        class MockTool:
            pass

        payload = LLMPayload(ROLE.TOOL, Tool(tool=MockTool))
        assert payload.role == ROLE.TOOL

    def test_payload_with_tool_result_role(self) -> None:
        """Test creating payload with TOOL_RESULT role."""
        from src.kernel.llm.payload import ToolResult

        result = ToolResult(value={"result": "ok"}, call_id="call_123")
        payload = LLMPayload(ROLE.TOOL_RESULT, result)
        assert payload.role == ROLE.TOOL_RESULT

    def test_payload_multimodal_content(self) -> None:
        """Test payload with multiple content types."""
        content = [
            Text(text="What's in this image?"),
            Image(value="photo.jpg"),
        ]
        payload = LLMPayload(ROLE.USER, content)
        assert len(payload.content) == 2
        assert isinstance(payload.content[0], Text)
        assert isinstance(payload.content[1], Image)

    def test_payload_empty_content_list(self) -> None:
        """Test payload with empty content list."""
        payload = LLMPayload(ROLE.USER, [])
        assert payload.role == ROLE.USER
        assert payload.content == []

    def test_payload_equality(self) -> None:
        """Test payload equality."""
        text = Text(text="Hello")
        payload1 = LLMPayload(ROLE.USER, text)
        payload2 = LLMPayload(ROLE.USER, text)
        # dataclass with slots uses default equality
        assert payload1 == payload2

    def test_payload_inequality_different_role(self) -> None:
        """Test payload inequality with different roles."""
        text = Text(text="Hello")
        payload1 = LLMPayload(ROLE.USER, text)
        payload2 = LLMPayload(ROLE.ASSISTANT, text)
        assert payload1 != payload2

    def test_payload_inequality_different_content(self) -> None:
        """Test payload inequality with different content."""
        payload1 = LLMPayload(ROLE.USER, Text(text="Hello"))
        payload2 = LLMPayload(ROLE.USER, Text(text="World"))
        assert payload1 != payload2

    def test_payload_has_slots(self) -> None:
        """Test that LLMPayload uses slots."""
        payload = LLMPayload(ROLE.USER, Text(text="test"))
        with pytest.raises(AttributeError):
            payload.__dict__

    def test_payload_mutation_allowed(self) -> None:
        """Test that LLMPayload allows mutation (not frozen)."""
        payload = LLMPayload(ROLE.USER, Text(text="test"))
        # slots=True allows mutation by default
        # This test verifies the payload is not frozen
        payload.role = ROLE.ASSISTANT
        assert payload.role == ROLE.ASSISTANT


class TestLLMPayloadIntegration:
    """Integration tests for LLMPayload usage scenarios."""

    def test_conversation_history(self) -> None:
        """Test building a conversation history."""
        history = [
            LLMPayload(ROLE.SYSTEM, Text(text="You are a helpful assistant.")),
            LLMPayload(ROLE.USER, Text(text="Hello!")),
            LLMPayload(ROLE.ASSISTANT, Text(text="Hi! How can I help?")),
            LLMPayload(ROLE.USER, Text(text="What's the weather?")),
        ]

        assert len(history) == 4
        assert history[0].role == ROLE.SYSTEM
        assert history[1].role == ROLE.USER
        assert history[2].role == ROLE.ASSISTANT
        assert history[3].role == ROLE.USER

    def test_multimodal_user_message(self) -> None:
        """Test user message with text and image."""
        payload = LLMPayload(
            ROLE.USER,
            [
                Text(text="Describe this image:"),
                Image(value="photo.jpg"),
            ],
        )
        assert len(payload.content) == 2

    def test_text_only_messages(self) -> None:
        """Test simple text-only messages."""
        system_msg = LLMPayload(ROLE.SYSTEM, Text(text="System prompt"))
        user_msg = LLMPayload(ROLE.USER, Text(text="User message"))
        assistant_msg = LLMPayload(ROLE.ASSISTANT, Text(text="Assistant response"))

        assert len(system_msg.content) == 1
        assert len(user_msg.content) == 1
        assert len(assistant_msg.content) == 1
