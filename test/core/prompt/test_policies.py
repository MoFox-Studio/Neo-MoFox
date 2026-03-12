"""Tests for core/prompt/policies.py."""

from __future__ import annotations

import pytest

from src.core.prompt.policies import (
    RenderPolicy,
    _is_effectively_empty,
    header,
    join_blocks,
    min_len,
    optional,
    trim,
    wrap,
)


class TestIsEffectivelyEmpty:
    """Test cases for _is_effectively_empty function."""

    def test_none_is_empty(self) -> None:
        """Test that None is considered empty."""
        assert _is_effectively_empty(None) is True

    def test_empty_string_is_empty(self) -> None:
        """Test that empty string is considered empty."""
        assert _is_effectively_empty("") is True
        assert _is_effectively_empty("   ") is True
        assert _is_effectively_empty("\t\n") is True

    def test_non_empty_string_is_not_empty(self) -> None:
        """Test that non-empty string is not considered empty."""
        assert _is_effectively_empty("hello") is False
        assert _is_effectively_empty(" hello ") is False

    def test_empty_list_is_empty(self) -> None:
        """Test that empty list is considered empty."""
        assert _is_effectively_empty([]) is True
        assert _is_effectively_empty(()) is True
        assert _is_effectively_empty(set()) is True

    def test_non_empty_list_is_not_empty(self) -> None:
        """Test that non-empty list is not considered empty."""
        assert _is_effectively_empty([1]) is False
        assert _is_effectively_empty((1,)) is False
        assert _is_effectively_empty({1}) is False

    def test_empty_dict_is_empty(self) -> None:
        """Test that empty dict is considered empty."""
        assert _is_effectively_empty({}) is True

    def test_non_empty_dict_is_not_empty(self) -> None:
        """Test that non-empty dict is not considered empty."""
        assert _is_effectively_empty({"key": "value"}) is False


class TestRenderPolicy:
    """Test cases for RenderPolicy class."""

    def test_render_policy_creation(self) -> None:
        """Test creating a RenderPolicy."""
        policy = RenderPolicy(lambda v: str(v).upper())
        assert policy("hello") == "HELLO"

    def test_render_policy_is_frozen(self) -> None:
        """Test that RenderPolicy is frozen."""
        policy = RenderPolicy(lambda v: v)
        with pytest.raises(Exception):  # FrozenInstanceError
            policy.fn = lambda v: v

    def test_render_policy_then(self) -> None:
        """Test chaining policies with then."""
        trim_policy = RenderPolicy(lambda v: str(v).strip())
        upper_policy = RenderPolicy(lambda v: str(v).upper())
        combined = trim_policy.then(upper_policy)

        assert combined("  hello  ") == "HELLO"

    def test_render_policy_multiple_then(self) -> None:
        """Test chaining multiple policies."""
        policy = (
            RenderPolicy(lambda v: str(v).strip())
            .then(RenderPolicy(lambda v: v.upper()))
            .then(RenderPolicy(lambda v: f"[{v}]"))
        )

        assert policy("  hello  ") == "[HELLO]"


class TestOptional:
    """Test cases for optional policy."""

    def test_optional_with_none(self) -> None:
        """Test optional with None value."""
        policy = optional("默认值")
        assert policy(None) == "默认值"

    def test_optional_with_empty_string(self) -> None:
        """Test optional with empty string."""
        policy = optional("默认值")
        assert policy("") == "默认值"
        assert policy("   ") == "默认值"

    def test_optional_with_non_empty_string(self) -> None:
        """Test optional with non-empty string."""
        policy = optional("默认值")
        assert policy("有效值") == "有效值"

    def test_optional_with_empty_list(self) -> None:
        """Test optional with empty list."""
        policy = optional("默认值")
        assert policy([]) == "默认值"

    def test_optional_with_non_empty_list(self) -> None:
        """Test optional with non-empty list."""
        policy = optional("默认值")
        assert policy([1, 2, 3]) == "[1, 2, 3]"

    def test_optional_default_empty(self) -> None:
        """Test optional with default empty string."""
        policy = optional()
        assert policy("") == ""
        assert policy("值") == "值"


class TestTrim:
    """Test cases for trim policy."""

    def test_trim_spaces(self) -> None:
        """Test trimming spaces."""
        policy = trim()
        assert policy("  hello  ") == "hello"

    def test_trim_tabs_and_newlines(self) -> None:
        """Test trimming tabs and newlines."""
        policy = trim()
        assert policy("\n\thello\t\n") == "hello"

    def test_trim_empty_string(self) -> None:
        """Test trimming empty string."""
        policy = trim()
        assert policy("") == ""
        assert policy("   ") == ""

    def test_trim_no_spaces(self) -> None:
        """Test trimming string without spaces."""
        policy = trim()
        assert policy("hello") == "hello"


class TestHeader:
    """Test cases for header policy."""

    def test_header_with_content(self) -> None:
        """Test header with non-empty content."""
        policy = header("# 标题")
        assert policy("内容") == "# 标题\n内容"

    def test_header_with_custom_separator(self) -> None:
        """Test header with custom separator."""
        policy = header("# 标题", sep=": ")
        assert policy("内容") == "# 标题: 内容"

    def test_header_with_empty_string(self) -> None:
        """Test header with empty string."""
        policy = header("# 标题")
        assert policy("") == ""

    def test_header_with_none(self) -> None:
        """Test header with None."""
        policy = header("# 标题")
        assert policy(None) == ""


class TestWrap:
    """Test cases for wrap policy."""

    def test_wrap_with_content(self) -> None:
        """Test wrap with non-empty content."""
        policy = wrap("[", "]")
        assert policy("内容") == "[内容]"

    def test_wrap_with_prefix_only(self) -> None:
        """Test wrap with only prefix."""
        policy = wrap(">>> ")
        assert policy("内容") == ">>> 内容"

    def test_wrap_with_suffix_only(self) -> None:
        """Test wrap with only suffix."""
        policy = wrap(suffix=" <<<")
        assert policy("内容") == "内容 <<<"

    def test_wrap_with_empty_string(self) -> None:
        """Test wrap with empty string."""
        policy = wrap("[", "]")
        assert policy("") == ""

    def test_wrap_with_code_block(self) -> None:
        """Test wrap for code blocks."""
        policy = wrap("```json\n", "\n```")
        assert policy('{"key": "value"}') == "```json\n{\"key\": \"value\"}\n```"


class TestJoinBlocks:
    """Test cases for join_blocks policy."""

    def test_join_blocks_with_list(self) -> None:
        """Test joining list of strings."""
        policy = join_blocks("\n")
        assert policy(["a", "b", "c"]) == "a\nb\nc"

    def test_join_blocks_skips_empty(self) -> None:
        """Test that empty strings are skipped."""
        policy = join_blocks("\n")
        assert policy(["a", "", "b", "", "c"]) == "a\nb\nc"

    def test_join_blocks_with_default_separator(self) -> None:
        """Test joining with default separator."""
        policy = join_blocks()
        assert policy(["a", "b", "c"]) == "a\n\nb\n\nc"

    def test_join_blocks_with_tuple(self) -> None:
        """Test joining tuple."""
        policy = join_blocks(", ")
        assert policy(("x", "y", "z")) == "x, y, z"

    def test_join_blocks_with_empty_list(self) -> None:
        """Test joining empty list."""
        policy = join_blocks("\n")
        assert policy([]) == ""

    def test_join_blocks_with_all_empty_strings(self) -> None:
        """Test joining list of all empty strings."""
        policy = join_blocks("\n")
        assert policy(["", "", ""]) == ""

    def test_join_blocks_with_non_list(self) -> None:
        """Test joining with non-list value."""
        policy = join_blocks("\n")
        assert policy("hello") == "hello"


class TestMinLen:
    """Test cases for min_len policy."""

    def test_min_len_pass(self) -> None:
        """Test string longer than minimum."""
        policy = min_len(5)
        assert policy("hello world") == "hello world"

    def test_min_len_exact(self) -> None:
        """Test string exactly minimum length."""
        policy = min_len(5)
        assert policy("hello") == "hello"

    def test_min_len_fail(self) -> None:
        """Test string shorter than minimum."""
        policy = min_len(5)
        assert policy("hi") == ""

    def test_min_len_with_whitespace(self) -> None:
        """Test that whitespace is trimmed before checking length."""
        policy = min_len(5)
        assert policy("  hi  ") == ""

    def test_min_len_with_empty_string(self) -> None:
        """Test with empty string."""
        policy = min_len(5)
        assert policy("") == ""
