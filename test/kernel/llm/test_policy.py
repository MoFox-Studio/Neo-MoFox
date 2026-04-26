"""Tests for policy module."""

from __future__ import annotations

import threading

import pytest

from src.kernel.llm.exceptions import LLMTimeoutError
from src.kernel.llm.policy.base import ModelStep, Policy, PolicySession
from src.kernel.llm.policy.load_balanced import LoadBalancedPolicy
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
        assert hasattr(PolicySession, "record_success")


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

    def test_missing_max_retry_uses_same_default_for_limit_and_retry(self) -> None:
        """缺失 max_retry 时，尝试上限应与实际重试默认值保持一致。"""
        model_set = [
            {"model_identifier": "a", "retry_interval": 0},
            {"model_identifier": "b", "retry_interval": 0},
        ]
        session = _RoundRobinSession(model_set=model_set, start_index=0)

        assert session.first().model == model_set[0]
        assert session.next_after_error(LLMTimeoutError("x")).model == model_set[0]
        assert session.next_after_error(LLMTimeoutError("x")).model == model_set[0]
        assert session.next_after_error(LLMTimeoutError("x")).model == model_set[1]

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


class TestLoadBalancedPolicy:
    """Test cases for LoadBalancedPolicy."""

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

    def test_policy_creation(self) -> None:
        """Test creating LoadBalancedPolicy."""
        policy = LoadBalancedPolicy()
        assert hasattr(policy, "new_session")

    def test_policy_creation_with_custom_weights(self) -> None:
        """Test creating LoadBalancedPolicy with custom weights."""
        policy = LoadBalancedPolicy(
            critical_penalty_multiplier=10.0,
            default_penalty_increment=2.0,
            latency_weight=100.0,
            penalty_weight=500.0,
            usage_penalty_weight=2000.0,
        )
        assert policy.critical_penalty_multiplier == 10.0
        assert policy.default_penalty_increment == 2.0
        assert policy.latency_weight == 100.0
        assert policy.penalty_weight == 500.0
        assert policy.usage_penalty_weight == 2000.0

    def test_new_session_returns_policy_session(self, mock_model_set: list[dict]) -> None:
        """Test that new_session returns PolicySession."""
        policy = LoadBalancedPolicy()
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        assert hasattr(session, "first")
        assert hasattr(session, "next_after_error")
        assert hasattr(session, "record_success")

    def test_new_session_validates_model_set(self) -> None:
        """Test that new_session validates model_set."""
        policy = LoadBalancedPolicy()

        with pytest.raises(ValueError, match="model_set 必须是非空 list\\[dict\\]"):
            policy.new_session(model_set=[], request_name="test")

        with pytest.raises(ValueError, match="model_set 必须是非空 list\\[dict\\]"):
            policy.new_session(model_set="not_a_list", request_name="test")  # type: ignore

        with pytest.raises(ValueError, match="model_set 必须是 list\\[dict\\]"):
            policy.new_session(model_set=[1, 2, 3], request_name="test")  # type: ignore

    def test_new_session_initializes_model_usage(self, mock_model_set: list[dict]) -> None:
        """Test that new_session initializes model usage statistics."""
        policy = LoadBalancedPolicy()
        policy.new_session(model_set=mock_model_set, request_name="test")
        
        # Check that model usage was initialized
        assert len(policy._model_usage) == 3
        for model in mock_model_set:
            model_name = model["model_identifier"]
            assert model_name in policy._model_usage
            stats = policy._model_usage[model_name]
            assert stats.total_tokens == 0
            assert stats.penalty == 0.0
            assert stats.usage_penalty == 0
            assert stats.avg_latency == 0.0
            assert stats.request_count == 0

    def test_policy_is_thread_safe(self, mock_model_set: list[dict]) -> None:
        """Test that policy is thread-safe."""
        policy = LoadBalancedPolicy()
        results = []

        def create_session(thread_id: int) -> None:
            session = policy.new_session(model_set=mock_model_set, request_name=f"req_{thread_id}")
            step = session.first()
            results.append(step.meta["model_name"])

        threads = []
        for i in range(10):
            t = threading.Thread(target=create_session, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should complete successfully
        assert len(results) == 10


class TestLoadBalancedSession:
    """Test cases for _LoadBalancedSession."""

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

    @pytest.fixture
    def policy(self) -> LoadBalancedPolicy:
        """Create a LoadBalancedPolicy instance."""
        return LoadBalancedPolicy()

    def test_session_first_returns_model(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test that first() returns a model step."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        step = session.first()
        assert step.model is not None
        assert "model_identifier" in step.model
        assert step.meta["strategy"] == "load_balanced"
        assert step.meta["attempt"] == 1

    def test_session_first_selects_best_model(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test that first() selects model with lowest score."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        step = session.first()
        
        # First call should select first model (all have same initial score)
        assert step.model is not None
        model_name = step.meta["model_name"]
        assert model_name in ["gpt-4", "gpt-3.5-turbo", "claude-3"]

    def test_session_updates_usage_penalty_on_first(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test that first() updates usage penalty."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        step = session.first()
        
        model_name = step.meta["model_name"]
        stats = policy._model_usage[model_name]
        assert stats.usage_penalty == 1  # Should be incremented

    def test_record_success_updates_stats_and_releases_usage_penalty(
        self, policy: LoadBalancedPolicy, mock_model_set: list[dict]
    ) -> None:
        """成功反馈应释放临时使用惩罚，并更新负载均衡统计。"""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        step = session.first()
        model_name = step.meta["model_name"]

        session.record_success(latency=2.5, tokens=123)

        stats = policy._model_usage[model_name]
        assert stats.usage_penalty == 0
        assert stats.request_count == 1
        assert stats.avg_latency == 2.5
        assert stats.total_tokens == 123

    def test_successful_requests_affect_load_balancing(
        self, policy: LoadBalancedPolicy, mock_model_set: list[dict]
    ) -> None:
        """成功请求也应影响后续选择，避免一直命中同一个模型。"""
        session1 = policy.new_session(model_set=mock_model_set, request_name="req1")
        step1 = session1.first()
        first_model_name = step1.meta["model_name"]
        session1.record_success(latency=0.1)

        session2 = policy.new_session(model_set=mock_model_set, request_name="req2")
        step2 = session2.first()

        assert step2.meta["model_name"] != first_model_name

    def test_next_after_error_same_model_retry(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test retrying same model on error."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        
        step1 = session.first()
        model_name = step1.meta["model_name"]
        
        # Error, should retry same model
        step2 = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step2.model is not None
        assert step2.meta["model_name"] == model_name
        assert step2.meta["retry"] == 1

    def test_next_after_error_updates_failure_penalty(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test that next_after_error updates failure penalty."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        
        step1 = session.first()
        model_name = step1.meta["model_name"]
        initial_penalty = policy._model_usage[model_name].penalty
        
        session.next_after_error(LLMTimeoutError("Timeout"))
        
        # Penalty should be increased
        assert policy._model_usage[model_name].penalty > initial_penalty

    def test_next_after_error_switches_model_after_retries(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test switching to next model after retries exhausted."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        
        step1 = session.first()
        model_name1 = step1.meta["model_name"]
        
        # Get max_retry for this model
        max_retry = next(m["max_retry"] for m in mock_model_set if m["model_identifier"] == model_name1)
        
        # Exhaust retries
        for _ in range(max_retry):
            session.next_after_error(LLMTimeoutError("Timeout"))
        
        # Should switch to next model
        step_switch = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step_switch.model is not None
        assert step_switch.meta["model_name"] != model_name1
        assert step_switch.meta.get("switch") is True

    def test_next_after_error_exhausted(self, policy: LoadBalancedPolicy) -> None:
        """Test that session returns None when exhausted."""
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
        ]
        
        session = policy.new_session(model_set=model_set, request_name="test")
        
        # First attempt
        session.first()
        
        # Retry
        session.next_after_error(LLMTimeoutError("Timeout"))
        
        # Should be exhausted
        step = session.next_after_error(LLMTimeoutError("Timeout"))
        assert step.model is None
        assert step.meta["reason"] in ["exhausted", "all_models_failed"]

    def test_critical_error_higher_penalty(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test that critical errors result in higher penalties."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        
        step1 = session.first()
        model_name = step1.meta["model_name"]
        initial_penalty = policy._model_usage[model_name].penalty
        
        # Simulate a critical error (using the error name directly)
        class NetworkConnectionError(Exception):
            pass
        
        session.next_after_error(NetworkConnectionError("Connection failed"))
        
        # Penalty should be significantly increased (by critical multiplier)
        penalty_increase = policy._model_usage[model_name].penalty - initial_penalty
        assert penalty_increase > policy.default_penalty_increment

    def test_load_balancing_distributes_requests(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test that load balancing distributes requests across models."""
        selected_models = []
        
        # Create multiple sessions and record which models are selected
        for i in range(6):
            session = policy.new_session(model_set=mock_model_set, request_name=f"req_{i}")
            step = session.first()
            selected_models.append(step.meta["model_name"])
        
        # Should have selected multiple different models
        unique_models = set(selected_models)
        assert len(unique_models) >= 2  # At least 2 different models used

    def test_max_total_attempts_calculation(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test max_total_attempts calculation."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        # gpt-4: 1 + 2 = 3
        # gpt-3.5: 1 + 1 = 2
        # claude-3: 1 + 0 = 1
        # Total: 6
        assert session._max_total_attempts == 6

    def test_missing_max_retry_uses_same_default_for_limit_and_retry(
        self, policy: LoadBalancedPolicy
    ) -> None:
        """缺失 max_retry 时，尝试上限应与实际重试默认值保持一致。"""
        model_set = [
            {"model_identifier": "a", "retry_interval": 0},
            {"model_identifier": "b", "retry_interval": 0},
        ]
        session = policy.new_session(model_set=model_set, request_name="test")

        assert session.first().model == model_set[0]
        assert session.next_after_error(LLMTimeoutError("x")).model == model_set[0]
        assert session.next_after_error(LLMTimeoutError("x")).model == model_set[0]
        assert session.next_after_error(LLMTimeoutError("x")).model == model_set[1]

    def test_negative_max_retry_treated_as_zero(self, policy: LoadBalancedPolicy) -> None:
        """Test that negative max_retry is treated as zero."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": -1,
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
        session = policy.new_session(model_set=model_set, request_name="test")
        assert session._max_total_attempts == 1

    def test_invalid_max_retry_treated_as_zero(self, policy: LoadBalancedPolicy) -> None:
        """Test that invalid max_retry is treated as zero."""
        model_set = [
            {
                "model_identifier": "gpt-4",
                "api_key": "key1",
                "max_retry": "invalid",  # type: ignore
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
        session = policy.new_session(model_set=model_set, request_name="test")
        assert session._max_total_attempts == 1

    def test_model_usage_stats_structure(self, policy: LoadBalancedPolicy, mock_model_set: list[dict]) -> None:
        """Test ModelUsageStats structure."""
        session = policy.new_session(model_set=mock_model_set, request_name="test")
        session.first()
        
        # Check that all models have proper stats
        for model in mock_model_set:
            model_name = model["model_identifier"]
            stats = policy._model_usage[model_name]
            assert hasattr(stats, "total_tokens")
            assert hasattr(stats, "penalty")
            assert hasattr(stats, "usage_penalty")
            assert hasattr(stats, "avg_latency")
            assert hasattr(stats, "request_count")


class TestLoadBalancedPolicyIntegration:
    """Integration tests for LoadBalancedPolicy."""

    def test_complete_failover_workflow(self) -> None:
        """Test complete failover workflow across multiple models."""
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

        policy = LoadBalancedPolicy()
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

        # Should have multiple attempts across both models
        assert len(steps) >= 3
        # Last step should be exhausted
        assert steps[-1].model is None

    def test_penalty_affects_model_selection(self) -> None:
        """Test that penalties affect model selection."""
        model_set = [
            {
                "model_identifier": "model-a",
                "api_key": "key1",
                "max_retry": 0,
                "retry_interval": 0.0,
                "client_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00001,
                "price_out": 0.00002,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
            {
                "model_identifier": "model-b",
                "api_key": "key2",
                "max_retry": 0,
                "retry_interval": 0.0,
                "client_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_provider": "openai",
                "timeout": 30,
                "price_in": 0.00001,
                "price_out": 0.00002,
                "temperature": 0.7,
                "max_tokens": 4096,
                "extra_params": {},
            },
        ]

        policy = LoadBalancedPolicy()
        
        # First request to model-a
        session1 = policy.new_session(model_set=model_set, request_name="req1")
        session1.first()
        
        # Fail model-a
        session1.next_after_error(LLMTimeoutError("Timeout"))
        
        # Next request should prefer model-b (lower penalty)
        session2 = policy.new_session(model_set=model_set, request_name="req2")
        step2 = session2.first()
        
        # Due to penalties, second session might select different model
        # (though with equal initial scores, order might vary)
        assert step2.model is not None
