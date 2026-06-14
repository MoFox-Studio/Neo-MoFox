"""Tests for OpenAI usage extraction, especially reasoning_tokens.

Verifies that _extract_usage_from_obj correctly reads
completion_tokens_details.reasoning_tokens and sets the
completion_includes_reasoning flag.
"""

from __future__ import annotations

import pytest

from src.kernel.llm.model_client.openai_client import _extract_usage_from_obj


class _AttrDict(dict):
    """Helper dict that allows attribute access for mock OpenAI objects."""

    def __getattr__(self, key: str) -> object:
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _make_usage(**kwargs: object) -> _AttrDict:
    """Build a mock usage object with nested details."""
    return _AttrDict(kwargs)


# ── Basic fields ──────────────────────────────────────────────────────


class TestBasicUsageExtraction:
    """Verify that basic token fields are extracted correctly."""

    def test_none_returns_empty(self) -> None:
        result = _extract_usage_from_obj(None)
        assert result == {}

    def test_basic_fields(self) -> None:
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        result = _extract_usage_from_obj(usage)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150

    def test_zero_fields_default(self) -> None:
        """Missing fields should default to 0."""
        usage = _make_usage()
        result = _extract_usage_from_obj(usage)
        assert result["prompt_tokens"] == 0
        assert result["completion_tokens"] == 0
        assert result["total_tokens"] == 0


# ── Cache fields ──────────────────────────────────────────────────────


class TestCacheFields:
    """Verify cache-related token extraction."""

    def test_prompt_tokens_details_cached_tokens(self) -> None:
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            prompt_tokens_details=_make_usage(cached_tokens=30),
        )
        result = _extract_usage_from_obj(usage)
        assert result["cache_hit_tokens"] == 30

    def test_input_tokens_details_cached_tokens(self) -> None:
        """Some providers use input_tokens_details instead of prompt_tokens_details."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            input_tokens_details=_make_usage(cached_tokens=25),
        )
        result = _extract_usage_from_obj(usage)
        assert result["cache_hit_tokens"] == 25

    def test_explicit_cache_hit_tokens(self) -> None:
        """DeepSeek-style prompt_cache_hit_tokens."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            prompt_cache_hit_tokens=40,
        )
        result = _extract_usage_from_obj(usage)
        assert result["cache_hit_tokens"] == 40

    def test_cache_write_tokens(self) -> None:
        """Anthropic-style cache_creation_input_tokens."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cache_creation_input_tokens=10,
        )
        result = _extract_usage_from_obj(usage)
        assert result["cache_write_tokens"] == 10


# ── Reasoning tokens ─────────────────────────────────────────────────


class TestReasoningTokens:
    """Verify reasoning_tokens extraction and completion_includes_reasoning flag."""

    def test_completion_tokens_details_reasoning(self) -> None:
        """OpenAI-style: completion_tokens_details.reasoning_tokens."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            completion_tokens_details=_make_usage(reasoning_tokens=80),
        )
        result = _extract_usage_from_obj(usage)
        assert result["reasoning_tokens"] == 80
        assert result["completion_includes_reasoning"] is True

    def test_output_tokens_details_reasoning(self) -> None:
        """Alternative field name: output_tokens_details.reasoning_tokens."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            output_tokens_details=_make_usage(reasoning_tokens=60),
        )
        result = _extract_usage_from_obj(usage)
        assert result["reasoning_tokens"] == 60
        assert result["completion_includes_reasoning"] is True

    def test_no_reasoning_tokens(self) -> None:
        """When no reasoning tokens are present, no flag should be set."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        result = _extract_usage_from_obj(usage)
        assert "reasoning_tokens" not in result
        assert "completion_includes_reasoning" not in result

    def test_zero_reasoning_tokens_no_flag(self) -> None:
        """Zero reasoning_tokens should not set the flag."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            completion_tokens_details=_make_usage(reasoning_tokens=0),
        )
        result = _extract_usage_from_obj(usage)
        assert "reasoning_tokens" not in result
        assert "completion_includes_reasoning" not in result

    def test_reasoning_tokens_with_cache(self) -> None:
        """reasoning_tokens and cache fields should coexist."""
        usage = _make_usage(
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            prompt_tokens_details=_make_usage(cached_tokens=30),
            completion_tokens_details=_make_usage(reasoning_tokens=80),
            cache_creation_input_tokens=10,
        )
        result = _extract_usage_from_obj(usage)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 200
        assert result["cache_hit_tokens"] == 30
        assert result["cache_write_tokens"] == 10
        assert result["reasoning_tokens"] == 80
        assert result["completion_includes_reasoning"] is True