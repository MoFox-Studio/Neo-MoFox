"""Regression tests for provider error identity and unstarted request timers."""
from unittest.mock import patch

import pytest

from src.kernel.llm.exceptions import (
    LLMConfigurationError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    classify_exception,
    decide_retry,
)
from src.kernel.llm.monitor import RequestTimer


@pytest.mark.parametrize("text", ["authentication-model", "token-limit-model", "content-filter", "timeout"])
def test_standard_provider_error_keeps_identity(text: str) -> None:
    """Diagnostic identifiers must never override the exception's meaning."""
    error = LLMProviderUnavailableError(text)
    assert classify_exception(error, model=text) is error
    assert decide_retry(classify_exception(error)).retryable


def test_existing_rate_limit_retains_retry_after() -> None:
    """Reclassification must not erase the server's delay."""
    error = LLMRateLimitError("busy", retry_after=2.0)
    assert classify_exception(error) is error
    assert error.retry_after == 2.0


def test_plain_configuration_error_keeps_identity() -> None:
    """A configuration error containing a keyword is still configuration."""
    error = LLMConfigurationError("authentication setting invalid")
    assert classify_exception(error) is error
    assert not decide_retry(error).retryable


def test_unstarted_timer_has_zero_elapsed() -> None:
    """Failure before entering the timing context is not process uptime."""
    assert RequestTimer().elapsed == 0.0


def test_timer_running_completed_and_reentered() -> None:
    """Reusing a timer resets its previous end time."""
    timer = RequestTimer()
    with patch("src.kernel.llm.monitor.time.perf_counter", side_effect=[10.0, 10.5, 11.0, 20.0, 20.25, 20.5]):
        with timer:
            assert timer.elapsed == 0.5
        assert timer.elapsed == 1.0
        with timer:
            assert timer.elapsed == 0.25
        assert timer.elapsed == 0.5
