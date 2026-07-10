import math

import pytest

from src.kernel.llm import LLMRateLimitError
from src.kernel.llm.policy import backoff


@pytest.mark.parametrize(
    ("ordinal", "expected_upper_bound"),
    [(1, 2.0), (2, 4.0), (3, 8.0), (10, 60.0)],
)
def test_retry_delay_uses_exponential_full_jitter(
    monkeypatch, ordinal, expected_upper_bound
):
    monkeypatch.setattr(backoff.random, "uniform", lambda low, high: high / 2)

    delay = backoff.retry_delay(
        model={"retry_interval": 2},
        error=TimeoutError("timeout"),
        retry_ordinal=ordinal,
    )

    assert delay == expected_upper_bound / 2


def test_retry_after_takes_priority_without_jitter(monkeypatch):
    def fail_uniform(low, high):
        raise AssertionError("Retry-After 不应使用 jitter")

    monkeypatch.setattr(backoff.random, "uniform", fail_uniform)

    delay = backoff.retry_delay(
        model={"retry_interval": 2},
        error=LLMRateLimitError("limited", retry_after=12.5),
        retry_ordinal=3,
    )

    assert delay == 12.5


def test_retry_delay_caps_retry_after_and_backoff(monkeypatch):
    monkeypatch.setattr(backoff.random, "uniform", lambda low, high: high)

    retry_after_delay = backoff.retry_delay(
        model={"retry_interval": 1},
        error=LLMRateLimitError("limited", retry_after=120),
        retry_ordinal=1,
    )
    backoff_delay = backoff.retry_delay(
        model={"retry_interval": 30},
        error=TimeoutError("timeout"),
        retry_ordinal=4,
    )

    assert retry_after_delay == backoff.MAX_RETRY_DELAY_SECONDS
    assert backoff_delay == backoff.MAX_RETRY_DELAY_SECONDS


@pytest.mark.parametrize("value", [-1, "invalid", float("nan"), float("inf")])
def test_retry_delay_handles_invalid_base_interval(value):
    delay = backoff.retry_delay(
        model={"retry_interval": value},
        error=TimeoutError("timeout"),
        retry_ordinal=1,
    )

    assert delay == 0.0
    assert math.isfinite(delay)
