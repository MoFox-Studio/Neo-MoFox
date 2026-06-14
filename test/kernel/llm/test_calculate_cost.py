"""Tests for calculate_request_cost with reasoning_tokens support.

Verifies that:
- reasoning_tokens are added to output cost when completion_includes_reasoning is False
- reasoning_tokens are NOT double-counted when completion_includes_reasoning is True
- Basic cost calculation still works correctly
"""

from __future__ import annotations

import pytest

from src.kernel.llm.observation import calculate_request_cost


def _model(**overrides: float) -> dict:
    """Create a minimal model entry for cost calculation."""
    base = {
        "price_in": 3.0,      # $3/M input tokens
        "price_out": 15.0,    # $15/M output tokens
        "cache_hit_price_in": 0.3,  # $0.30/M cache hit tokens
    }
    base.update(overrides)
    return base


class TestBasicCostCalculation:
    """Verify basic cost calculation without reasoning tokens."""

    def test_simple_cost(self) -> None:
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        # input: 1000 * 3.0 / 1M = 0.003
        # output: 500 * 15.0 / 1M = 0.0075
        # total: 0.0105
        assert cost == pytest.approx(0.0105, abs=1e-8)

    def test_cache_hit_cost(self) -> None:
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "cache_hit_tokens": 300,
            "cache_miss_tokens": 700,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        # billable_prompt = cache_miss_tokens = 700 (since cache_miss_tokens > 0)
        # input: 700 * 3.0 + 300 * 0.3 = 2100 + 90 = 2190 / 1M
        # output: 500 * 15.0 = 7500 / 1M
        # total: (2190 + 7500) / 1M = 0.00969
        assert cost == pytest.approx(0.00969, abs=1e-8)


class TestReasoningTokensCost:
    """Verify reasoning_tokens handling in cost calculation."""

    def test_reasoning_included_no_double_count(self) -> None:
        """When completion_includes_reasoning=True, reasoning_tokens should NOT be added."""
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,  # already includes reasoning
            "total_tokens": 1500,
            "reasoning_tokens": 200,
            "completion_includes_reasoning": True,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        # output cost should be 500 * 15.0 / 1M (NOT 700 * 15.0 / 1M)
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_reasoning_not_included_adds_to_output(self) -> None:
        """When completion_includes_reasoning=False, reasoning_tokens should be added."""
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 300,  # does NOT include reasoning
            "total_tokens": 1500,
            "reasoning_tokens": 200,
            "completion_includes_reasoning": False,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        # output cost = (300 + 200) * 15.0 / 1M = 0.0075
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_no_reasoning_tokens_unchanged(self) -> None:
        """Without reasoning_tokens, cost calculation should be unchanged."""
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_zero_reasoning_tokens_no_effect(self) -> None:
        """Zero reasoning_tokens should not affect cost even with the flag."""
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "reasoning_tokens": 0,
            "completion_includes_reasoning": True,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_reasoning_with_cache(self) -> None:
        """reasoning_tokens + cache should work together correctly."""
        model = _model()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "cache_hit_tokens": 300,
            "cache_miss_tokens": 700,
            "reasoning_tokens": 200,
            "completion_includes_reasoning": True,
        }
        cost = calculate_request_cost(model=model, usage=usage)
        # billable_prompt = 700 (cache_miss_tokens)
        # input: 700 * 3.0 + 300 * 0.3 = 2190 / 1M
        # output: 500 * 15.0 = 7500 / 1M (reasoning included, no double count)
        expected = (2190 + 7500) / 1_000_000
        assert cost == pytest.approx(expected, abs=1e-8)