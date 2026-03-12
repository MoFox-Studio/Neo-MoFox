"""Tests for roles.py."""

from __future__ import annotations

import pytest

from src.kernel.llm.roles import ROLE


class TestROLE:
    """Test cases for ROLE enum."""

    def test_role_values(self) -> None:
        """Test that ROLE enum has correct string values."""
        assert ROLE.SYSTEM.value == "system"
        assert ROLE.USER.value == "user"
        assert ROLE.ASSISTANT.value == "assistant"
        assert ROLE.TOOL.value == "tool"
        assert ROLE.TOOL_RESULT.value == "tool_result"

    def test_role_is_string_subclass(self) -> None:
        """Test that ROLE is a string subclass."""
        assert issubclass(ROLE, str)

    def test_role_comparison(self) -> None:
        """Test that ROLE can be compared as strings."""
        assert ROLE.SYSTEM == "system"
        assert ROLE.USER == "user"
        assert ROLE.ASSISTANT == "assistant"
        assert ROLE.TOOL == "tool"
        assert ROLE.TOOL_RESULT == "tool_result"

    def test_role_in_dict(self) -> None:
        """Test that ROLE can be used as dict keys."""
        role_dict = {
            ROLE.SYSTEM: "System message",
            ROLE.USER: "User message",
            ROLE.ASSISTANT: "Assistant message",
        }
        assert role_dict[ROLE.SYSTEM] == "System message"
        assert role_dict[ROLE.USER] == "User message"
        assert role_dict[ROLE.ASSISTANT] == "Assistant message"

    def test_role_iteration(self) -> None:
        """Test iterating over all roles."""
        roles = list(ROLE)
        assert len(roles) == 5
        assert ROLE.SYSTEM in roles
        assert ROLE.USER in roles
        assert ROLE.ASSISTANT in roles
        assert ROLE.TOOL in roles
        assert ROLE.TOOL_RESULT in roles

    def test_role_string_methods(self) -> None:
        """Test that ROLE supports string methods."""
        role = ROLE.SYSTEM
        assert role.upper() == "SYSTEM"
        assert role.lower() == "system"
        assert role.capitalize() == "System"
        assert "system" in role
        assert len(role) == len("system")

    def test_role_from_string(self) -> None:
        """Test creating ROLE from string."""
        assert ROLE("system") == ROLE.SYSTEM
        assert ROLE("user") == ROLE.USER
        assert ROLE("assistant") == ROLE.ASSISTANT
        assert ROLE("tool") == ROLE.TOOL
        assert ROLE("tool_result") == ROLE.TOOL_RESULT

    def test_role_invalid_string(self) -> None:
        """Test that invalid string raises error."""
        with pytest.raises(ValueError, match="is not a valid ROLE"):
            ROLE("invalid")

    def test_role_json_serialization(self) -> None:
        """Test that ROLE can be JSON serialized."""
        import json

        role_dict = {"role": ROLE.USER}
        serialized = json.dumps(role_dict)
        assert '"role": "user"' in serialized

        # Deserialize
        deserialized = json.loads(serialized)
        assert ROLE(deserialized["role"]) == ROLE.USER
