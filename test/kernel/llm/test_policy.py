"""Tests for policy module."""

from __future__ import annotations

import threading

import pytest

from src.kernel.llm.exceptions import LLMTimeoutError
from src.kernel.llm.policy.base import ModelStep, Policy, PolicySession
from src.kernel.llm.policy.round_robin import RoundRobinPolicy, _RoundRobinSession


class TestModelStep:
    """Test cases for ModelStep dataclass."""

    def test_model_step_with_model(self) -> None:
        """Test ModelStep with a model."""
        step = ModelStep(model={"name": "gpt-4"}, delay_seconds=0.0, meta={"idx": 0})
        assert step.model is not None
        assert step.model["name"] == "gpt-4"
        assert step.delay_seconds == 0.0
        assert step.meta == {"idx": 0}

    def test_model_step_without_model(self) -> None:
        """Test ModelStep without model (exhausted)."""
        step = ModelStep(model=None, delay_seconds=0.0, meta={"reason": "exhausted"})
        assert step.model is None
        assert step.meta["reason"] == "exhausted"

    def test_model_step_default_values(self) -> None:
        """Test ModelStep default values."""
        step = ModelStep(model={"name": "gpt-4"})
        assert step.delay_seconds == 0.0
        assert step.meta is None

    def test_model_step_with_delay(self) -> None:
        """Test ModelStep with delay."""
        step = ModelStep(model={"name": "gpt-4"}, delay_seconds=2.5)
        assert step.delay_seconds == 2.5

    def test_model_step_is_frozen(self) -> None:
        """Test that ModelStep is frozen."""
        step = ModelStep(model={"name": "gpt-4"})
        with pytest.raises(Exception):  # FrozenInstanceError
            step.delay_seconds = 1.0


class TestPolicySession:
    """Test cases for PolicySession protocol."""

    def test_policy_session_is_protocol(self) -> None:
        """Test that PolicySession is a Protocol."""
        # Check that protocol has required methods
        assert hasattr(PolicySession, "first")
        assert hasattr(PolicySession, "next_after_error")


class TestPolicy:
    """Test cases for Policy protocol."""

    def test_policy_is_protocol(self) -> None:
        """Test that Policy is a Protocol."""
        # Check that protocol has required method
        assert hasattr(Policy, "new_session")


class TestRoundRobinPolicy:
    """Test cases for RoundRobinPolicy."""

    @pytest.fixture
    def mock_model_set(self) -> list[dict]:
        """Create a mock model set for testing."""
        return [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": 2,
                "retry_interval": 1.0,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
            {
                "model_identifier": "gpt-3.5-turbo",
                "api_key": "key2",
                "max_retry": 1,
                "retry_interval": 0.5,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00001,
                "price_out": 0.00002,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]

    def test_policy_creation(self) -> None:
        """Test creating RoundRobinPolicy."""
        policy = RoundRobinPolicy()
        # Check that policy has required method
        assert hasattr(policy, "new_session")

    def test_new_session_returns_policy_session(self) -> None:
        """Test that new_session returns PolicySession."""
        policy = RoundRobinPolicy()
        model_set = [{"model_identifier": "gpt-4"}]
        session = policy.new_session(model_set=model_set, request_name="test")
        # Check that session has required methods
        assert hasattr(session, "first")
        assert hasattr(session, "next_after_error")

    def test_new_session_validates_model_set(self) -> None:
        """Test that new_session validates model_set."""
        policy = RoundRobinPolicy()

        with pytest.raises(ValueError, match="model_set 必须是非空 list\\[dict\\]"):
            policy.new_session(model_set=[], request_name="test")

        with pytest.raises(ValueError, match="model_set 必须是非空 list\\[dict\\]"):
            policy.new_session(model_set="not_a_list", request_name="test")  # type: ignore

        with pytest.raises(ValueError, match="model_set 必须是 list\\[dict\\]"):
            policy.new_session(model_set=[1, 2, 3], request_name="test")  # type: ignore

    def test_multiple_sessions_have_independent_counters(self, mock_model_set: list[dict]) -> None:
        """Test that different request names have independent counters."""
        policy = RoundRobinPolicy()

        session1 = policy.new_session(model_set=mock_model_set, request_name="req1")
        session2 = policy.new_session(model_set=mock_model_set, request_name="req2")

        step1 = session1.first()
        step2 = session2.first()

        # Both should start at index 0
        assert step1.meta["model_index"] == 0
        assert step2.meta["model_index"] == 0

    def test_policy_is_thread_safe(self, mock_model_set: list[dict]) -> None:
        """Test that policy is thread-safe."""
        policy = RoundRobinPolicy()
        results = []

        def create_session(thread_id: int) -> None:
            session = policy.new_session(model_set=mock_model_set, request_name=f"req_{thread_id}")
            step = session.first()
            results.append(step.meta["model_index"])

        threads = []
        for i in range(10):
            t = threading.Thread(target=create_session, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should get index 0 (first time for each request name)
        assert all(idx == 0 for idx in results)

    def test_default_request_name(self, mock_model_set: list[dict]) -> None:
        """Test session with default request name."""
        policy = RoundRobinPolicy()

        session1 = policy.new_session(model_set=mock_model_set, request_name="")
        session2 = policy.new_session(model_set=mock_model_set, request_name="")

        # Empty request names should use default key
        step1 = session1.first()
        step2 = session2.first()
        # With default key, they should share counter, so second gets index 1
        assert step1.meta["model_index"] == 0
        assert step2.meta["model_index"] == 1


class TestRoundRobinSession:
    """Test cases for _RoundRobinSession."""

    @pytest.fixture
    def mock_model_set(self) -> list[dict]:
        """Create a mock model set for testing."""
        return [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": 2,
                "retry_interval": 1.0,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
            {
                "model_identifier": "gpt-3.5-turbo",
                "api_key": "key2",
                "max_retry": 1,
                "retry_interval": 0.5,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00001,
                "price_out": 0.00002,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
            {
                "model_identifier": "claude-3",
                "api_key": "key3",
                "max_retry": 0,
                "retry_interval": 0.0,
                "client_type": "openai",
                "base_url": "https://api.anthropic.com/v1",
                "api_provider": "anthropic",
                "timeout": 30,
                "price_in": 0.00002,
                "price_out": 0.00004,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]

    def test_session_first_returns_model(self, mock_model_set: list[dict]) -> None:
        """Test that first() returns a model step."""
        session = _RoundRobinSession(model_set=mock_model_set, start_index=0)
        step = session.first()
        assert step.model is not None
        assert step.model == mock_model_set[0]
        assert step.meta["model_index"] == 0
        assert step.meta["attempt"] == 1

    def test_session_first_with_start_index(self, mock_model_set: list[dict]) -> None:
        """Test first() with different start indices."""
        session = _RoundRobinSession(model_set=mock_model_set, start_index=1)
        step = session.first()
        assert step.model == mock_model_set[1]
        assert step.meta["model_index"] == 1

    def test_session_start_index_wraps(self, mock_model_set: list[dict]) -> None:
        """Test that start_index wraps around model list."""
        # Start beyond the list length
        session = _RoundRobinSession(model_set=mock_model_set, start_index=5)
        step = session.first()
        # Should wrap to index 5 % 3 = 2
        assert step.model == mock_model_set[2]
        assert step.meta["model_index"] == 2

    def test_next_after_error_same_model_retry(self, mock_model_set: list[dict]) -> None:
        """Test retrying same model on error."""
        session = _RoundRobinSession(model_set=mock_model_set, start_index=0)

        # First attempt
        step1 = session.first()
        assert step1.model == mock_model_set[0]

        # Error, should retry same model (has max_retry=2)
        step2 = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step2.model == mock_model_set[0]
        assert step2.delay_seconds == 1.0
        assert step2.meta["retry"] == 1

    def test_next_after_error_switches_model_after_retries(
        self, mock_model_set: list[dict]
    ) -> None:
        """Test switching to next model after retries exhausted."""
        session = _RoundRobinSession(model_set=mock_model_set, start_index=0)

        # First attempt
        session.first()

        # Retry 1
        session.next_after_error(LLMTimeoutError("Timeout"))

        # Retry 2
        session.next_after_error(LLMTimeoutError("Timeout"))

        # Should switch to next model
        step = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step.model == mock_model_set[1]
        assert step.meta["switch"] is True

    def test_next_after_error_no_retry(self, mock_model_set: list[dict]) -> None:
        """Test model with max_retry=0 switches immediately."""
        # Create session starting at claude-3 (index 2, max_retry=0)
        session = _RoundRobinSession(model_set=mock_model_set, start_index=2)

        session.first()
        step = session.next_after_error(LLMTimeoutError("Timeout"))

        # Should switch to first model
        assert step.model == mock_model_set[0]
        assert step.meta["switch"] is True

    def test_next_after_error_wraps_to_first_model(self, mock_model_set: list[dict]) -> None:
        """Test that model selection wraps around."""
        session = _RoundRobinSession(model_set=mock_model_set, start_index=2)

        # Start at last model
        session.first()

        # This model has no retries, should switch to index 0
        step = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step.model == mock_model_set[0]

    def test_next_after_error_exhausted(self, mock_model_set: list[dict]) -> None:
        """Test that session returns None when exhausted."""
        # Create a session with limited attempts
        session = _RoundRobinSession(model_set=mock_model_set[:1], start_index=0)

        # Model 0 has max_retry=2, so total attempts = 1 + 2 = 3
        session.first()
        session.next_after_error(LLMTimeoutError("Timeout"))
        session.next_after_error(LLMTimeoutError("Timeout"))

        # Should be exhausted now
        step = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step.model is None
        assert step.meta["reason"] == "exhausted"

    def test_max_total_attempts_calculation(self, mock_model_set: list[dict]) -> None:
        """Test max_total_attempts calculation."""
        session = _RoundRobinSession(model_set=mock_model_set, start_index=0)
        # gpt-4: 1 + 2 = 3
        # gpt-3.5: 1 + 1 = 2
        # claude-3: 1 + 0 = 1
        # Total: 6
        assert session._max_total_attempts == 6

    def test_negative_max_retry_treated_as_zero(self) -> None:
        """Test that negative max_retry is treated as zero."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": -1,  # Invalid
                "retry_interval": 1.0,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]
        session = _RoundRobinSession(model_set=model_set, start_index=0)
        # Should have 1 + 0 = 1 attempts
        assert session._max_total_attempts == 1

    def test_invalid_max_retry_treated_as_zero(self) -> None:
        """Test that invalid max_retry is treated as zero."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": "invalid",  # type: ignore - Invalid type
                "retry_interval": 1.0,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]
        session = _RoundRobinSession(model_set=model_set, start_index=0)
        # Should have 1 + 0 = 1 attempts
        assert session._max_total_attempts == 1

    def test_negative_retry_interval_treated_as_zero(self) -> None:
        """Test that negative retry_interval is treated as zero."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": 2,
                "retry_interval": -1.0,  # Invalid
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]
        session = _RoundRobinSession(model_set=model_set, start_index=0)
        session.first()
        step = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step.delay_seconds == 0.0

    def test_invalid_retry_interval_treated_as_zero(self) -> None:
        """Test that invalid retry_interval is treated as zero."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": 2,
                "retry_interval": "invalid",  # type: ignore - Invalid type
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]
        session = _RoundRobinSession(model_set=model_set, start_index=0)
        session.first()
        step = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step.delay_seconds == 0.0


class TestPolicyIntegration:
    """Integration tests for policy system."""

    def test_complete_retry_workflow(self) -> None:
        """Test complete retry workflow across multiple models."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": 1,
                "retry_interval": 0.1,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
            {
                "model_identifier": "gpt-3.5-turbo",
                "api_key": "key2",
                "max_retry": 1,
                "retry_interval": 0.1,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00001,
                "price_out": 0.00002,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]

        policy = RoundRobinPolicy()
        session = policy.new_session(model_set=model_set, request_name="test")

        steps = []
        step = session.first()
        steps.append(step)

        # Simulate retries and switches
        for _ in range(4):
            step = session.next_after_error(LLMTimeoutError("Timeout"))
            steps.append(step)
            if step.model is None:
                break

        # Should have: gpt-4 (1st), gpt-4 (retry), gpt-3.5 (switch), gpt-3.5 (retry), exhausted
        assert len(steps) == 5
        assert steps[0].model["model_identifier"] == "gpt-4"
        assert steps[1].model["model_identifier"] == "gpt-4"
        assert steps[2].model["model_identifier"] == "gpt-3.5-turbo"
        assert steps[3].model["model_identifier"] == "gpt-3.5-turbo"
        assert steps[4].model is None

    def test_multiple_requests_with_same_policy(self) -> None:
        """Test multiple requests using the same policy instance."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": 0,
                "retry_interval": 0.0,
                "client_type": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00003,
                "price_out": 0.00006,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]

        policy = RoundRobinPolicy()

        # First request
        session1 = policy.new_session(model_set=model_set, request_name="req1")
        step1 = session1.first()
        assert step1.meta["model_index"] == 0

        # Second request (different name)
        session2 = policy.new_session(model_set=model_set, request_name="req2")
        step2 = session2.first()
        assert step2.meta["model_index"] == 0

        # Third request (same name as first - should increment)
        session3 = policy.new_session(model_set=model_set, request_name="req1")
        step3 = session3.first()
        # With only 1 model, wraps to 0
        assert step3.meta["model_index"] == 0
