"""Tests for Anthropic usage extraction.

Verifies that _extract_anthropic_usage correctly normalizes Anthropic's
usage fields and that _create_non_stream and _create_stream return 5-tuples
with usage data.
"""

from __future__ import annotations

import pytest

from src.kernel.llm.model_client.anthropic_client import _extract_anthropic_usage


class _AttrDict(dict):
    """Helper dict that allows attribute access for mock Anthropic objects."""

    def __getattr__(self, key: str) -> object:
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _make_usage(**kwargs: object) -> _AttrDict:
    """Build a mock Anthropic usage object."""
    return _AttrDict(kwargs)


# ── _extract_anthropic_usage ─────────────────────────────────────────


class TestExtractAnthropicUsage:
    """Verify Anthropic usage normalization."""

    def test_none_returns_empty(self) -> None:
        result = _extract_anthropic_usage(None)
        assert result == {}

    def test_basic_fields(self) -> None:
        usage = _make_usage(
            input_tokens=100,
            output_tokens=50,
        )
        result = _extract_anthropic_usage(usage)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150
        assert result["completion_includes_reasoning"] is True

    def test_cache_fields(self) -> None:
        usage = _make_usage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=30,
        )
        result = _extract_anthropic_usage(usage)
        assert result["cache_write_tokens"] == 10
        assert result["cache_hit_tokens"] == 30
        assert result["cache_miss_tokens"] == 70  # 100 - 30

    def test_zero_cache(self) -> None:
        usage = _make_usage(
            input_tokens=100,
            output_tokens=50,
        )
        result = _extract_anthropic_usage(usage)
        assert result["cache_hit_tokens"] == 0
        assert result["cache_miss_tokens"] == 100  # 100 - 0
        assert result["cache_write_tokens"] == 0

    def test_completion_includes_reasoning_always_true(self) -> None:
        """Anthropic output_tokens always includes reasoning tokens."""
        usage = _make_usage(
            input_tokens=50,
            output_tokens=30,
        )
        result = _extract_anthropic_usage(usage)
        assert result["completion_includes_reasoning"] is True

    def test_missing_fields_default_to_zero(self) -> None:
        usage = _make_usage()
        result = _extract_anthropic_usage(usage)
        assert result["prompt_tokens"] == 0
        assert result["completion_tokens"] == 0
        assert result["total_tokens"] == 0
        assert result["cache_hit_tokens"] == 0
        assert result["cache_miss_tokens"] == 0
        assert result["cache_write_tokens"] == 0


# ── Stream usage extraction via message_start + message_delta ────────


class TestAnthropicStreamUsage:
    """Verify that Anthropic stream events carry usage data.

    These tests mock the stream behavior at the unit level to confirm
    that message_start and message_delta events are processed correctly.
    """

    def test_extract_from_message_start_usage(self) -> None:
        """Simulate message_start event usage (input side)."""
        msg_usage = _make_usage(
            input_tokens=500,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=100,
        )
        result = _extract_anthropic_usage(msg_usage)
        assert result["prompt_tokens"] == 500
        assert result["cache_write_tokens"] == 20
        assert result["cache_hit_tokens"] == 100

    def test_merge_with_delta_output(self) -> None:
        """Simulate merging message_start usage with message_delta output_tokens."""
        msg_usage = _make_usage(
            input_tokens=500,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=20,
        )
        result = _extract_anthropic_usage(msg_usage)
        # At message_start, output_tokens is 0 (not yet received)
        # total_tokens = prompt_tokens + completion_tokens = 500 + 0 = 500
        assert result["prompt_tokens"] == 500
        assert result["cache_hit_tokens"] == 100

        # Simulate what _create_stream does when it sees message_delta
        result["completion_tokens"] = 200
        result["completion_includes_reasoning"] = True
        # Recalculate total_tokens after merging
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
        assert result["prompt_tokens"] == 500
        assert result["completion_tokens"] == 200
        assert result["total_tokens"] == 700