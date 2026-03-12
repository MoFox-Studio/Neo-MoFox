"""Tests for monitor.py."""

from __future__ import annotations

import threading
import time
from datetime import datetime


from src.kernel.llm.monitor import (
    MetricsCollector,
    ModelStats,
    RequestMetrics,
    RequestTimer,
    get_global_collector,
)


class TestRequestMetrics:
    """Test cases for RequestMetrics dataclass."""

    def test_metrics_creation(self) -> None:
        """Test creating RequestMetrics."""
        metrics = RequestMetrics(
            model_name="gpt-4",
            request_name="test_request",
            latency=1.5,
            success=True,
        )
        assert metrics.model_name == "gpt-4"
        assert metrics.request_name == "test_request"
        assert metrics.latency == 1.5
        assert metrics.success is True

    def test_metrics_with_all_fields(self) -> None:
        """Test RequestMetrics with all fields."""
        now = datetime.now()
        metrics = RequestMetrics(
            model_name="gpt-4",
            request_name="test",
            latency=1.0,
            tokens_in=100,
            tokens_out=50,
            cost=0.001,
            success=True,
            error=None,
            error_type=None,
            timestamp=now,
            stream=True,
            retry_count=2,
            model_index=0,
            extra={"key": "value"},
        )
        assert metrics.tokens_in == 100
        assert metrics.tokens_out == 50
        assert metrics.cost == 0.001
        assert metrics.stream is True
        assert metrics.retry_count == 2
        assert metrics.extra == {"key": "value"}

    def test_metrics_with_error(self) -> None:
        """Test RequestMetrics with error information."""
        metrics = RequestMetrics(
            model_name="gpt-4",
            request_name="test",
            latency=0.5,
            success=False,
            error="Rate limit exceeded",
            error_type="LLMRateLimitError",
        )
        assert metrics.success is False
        assert metrics.error == "Rate limit exceeded"
        assert metrics.error_type == "LLMRateLimitError"

    def test_metrics_default_timestamp(self) -> None:
        """Test that timestamp defaults to current time."""
        before = datetime.now()
        metrics = RequestMetrics(model_name="gpt-4", request_name="test", latency=1.0)
        after = datetime.now()
        assert before <= metrics.timestamp <= after

    def test_metrics_default_values(self) -> None:
        """Test RequestMetrics default values."""
        metrics = RequestMetrics(
            model_name="gpt-4", request_name="test", latency=1.0
        )
        assert metrics.tokens_in is None
        assert metrics.tokens_out is None
        assert metrics.cost is None
        assert metrics.success is True
        assert metrics.error is None
        assert metrics.error_type is None
        assert metrics.stream is False
        assert metrics.retry_count == 0
        assert metrics.model_index == 0
        assert metrics.extra == {}


class TestModelStats:
    """Test cases for ModelStats class."""

    def test_stats_creation(self) -> None:
        """Test creating ModelStats."""
        stats = ModelStats(model_name="gpt-4")
        assert stats.model_name == "gpt-4"
        assert stats.total_requests == 0
        assert stats.success_count == 0
        assert stats.error_count == 0
        assert stats.total_latency == 0.0

    def test_success_rate_zero_requests(self) -> None:
        """Test success_rate with zero requests."""
        stats = ModelStats(model_name="gpt-4")
        assert stats.success_rate == 0.0

    def test_success_rate_with_requests(self) -> None:
        """Test success_rate calculation."""
        stats = ModelStats(model_name="gpt-4")
        stats.total_requests = 10
        stats.success_count = 8
        assert stats.success_rate == 0.8

    def test_avg_latency_zero_requests(self) -> None:
        """Test avg_latency with zero requests."""
        stats = ModelStats(model_name="gpt-4")
        assert stats.avg_latency == 0.0

    def test_avg_latency_with_requests(self) -> None:
        """Test avg_latency calculation."""
        stats = ModelStats(model_name="gpt-4")
        stats.total_requests = 3
        stats.total_latency = 6.0
        assert stats.avg_latency == 2.0

    def test_avg_cost_zero_requests(self) -> None:
        """Test avg_cost with zero requests."""
        stats = ModelStats(model_name="gpt-4")
        assert stats.avg_cost == 0.0

    def test_avg_cost_with_requests(self) -> None:
        """Test avg_cost calculation."""
        stats = ModelStats(model_name="gpt-4")
        stats.total_requests = 5
        stats.total_cost = 0.5
        assert stats.avg_cost == 0.1

    def test_to_dict(self) -> None:
        """Test to_dict method."""
        stats = ModelStats(model_name="gpt-4")
        stats.total_requests = 10
        stats.success_count = 8
        stats.error_count = 2
        stats.total_latency = 5.0
        stats.total_tokens_in = 1000
        stats.total_tokens_out = 500
        stats.total_cost = 0.1

        result = stats.to_dict()
        assert result["model_name"] == "gpt-4"
        assert result["total_requests"] == 10
        assert result["success_count"] == 8
        assert result["error_count"] == 2
        assert result["success_rate"] == 0.8
        assert result["total_latency"] == 5.0
        assert result["avg_latency"] == 0.5
        assert result["total_tokens_in"] == 1000
        assert result["total_tokens_out"] == 500
        assert result["total_cost"] == 0.1
        assert result["avg_cost"] == 0.01

    def test_error_types_tracking(self) -> None:
        """Test error_types defaultdict."""
        stats = ModelStats(model_name="gpt-4")
        stats.error_types["LLMRateLimitError"] += 1
        stats.error_types["LLMTimeoutError"] += 2
        assert stats.error_types["LLMRateLimitError"] == 1
        assert stats.error_types["LLMTimeoutError"] == 2


class TestRequestTimer:
    """Test cases for RequestTimer class."""

    def test_timer_initial_state(self) -> None:
        """Test timer initial state."""
        timer = RequestTimer()
        assert timer.start_time == 0.0
        assert timer.end_time == 0.0

    def test_timer_context_manager(self) -> None:
        """Test timer as context manager."""
        timer = RequestTimer()
        with timer:
            assert timer.start_time > 0
            assert timer.end_time == 0
            time.sleep(0.01)
            # During context, elapsed should be calculated from start
            assert timer.elapsed > 0

        # After context, end_time should be set
        assert timer.end_time > 0
        assert timer.end_time > timer.start_time

    def test_timer_elapsed_during_context(self) -> None:
        """Test elapsed time during context execution."""
        timer = RequestTimer()
        with timer:
            time.sleep(0.01)
            elapsed1 = timer.elapsed
            time.sleep(0.01)
            elapsed2 = timer.elapsed
            assert elapsed2 > elapsed1

    def test_timer_elapsed_after_context(self) -> None:
        """Test elapsed time after context exit."""
        timer = RequestTimer()
        with timer:
            time.sleep(0.01)

        elapsed = timer.elapsed
        assert elapsed >= 0.01
        # Elapsed should be constant after context exit
        time.sleep(0.01)
        assert timer.elapsed == elapsed

    def test_timer_returns_self(self) -> None:
        """Test that __enter__ returns self."""
        timer = RequestTimer()
        with timer as t:
            assert t is timer


class TestMetricsCollector:
    """Test cases for MetricsCollector class."""

    def test_collector_creation(self) -> None:
        """Test creating MetricsCollector."""
        collector = MetricsCollector()
        assert len(collector._history) == 0
        assert len(collector._stats) == 0

    def test_collector_with_custom_max_history(self) -> None:
        """Test creating collector with custom max_history."""
        collector = MetricsCollector(max_history=100)
        assert collector._max_history == 100

    def test_record_request(self) -> None:
        """Test recording a request."""
        collector = MetricsCollector()
        metrics = RequestMetrics(
            model_name="gpt-4", request_name="test", latency=1.0, success=True
        )

        collector.record_request(metrics)
        assert len(collector._history) == 1
        assert collector._history[0] == metrics

    def test_record_multiple_requests(self) -> None:
        """Test recording multiple requests."""
        collector = MetricsCollector()

        for i in range(5):
            metrics = RequestMetrics(
                model_name="gpt-4", request_name=f"test_{i}", latency=float(i)
            )
            collector.record_request(metrics)

        assert len(collector._history) == 5

    def test_history_limit(self) -> None:
        """Test that history respects max_history limit."""
        collector = MetricsCollector(max_history=3)

        for i in range(5):
            metrics = RequestMetrics(
                model_name="gpt-4", request_name=f"test_{i}", latency=float(i)
            )
            collector.record_request(metrics)

        # Only last 3 should be kept
        assert len(collector._history) == 3
        assert collector._history[0].request_name == "test_2"
        assert collector._history[-1].request_name == "test_4"

    def test_stats_creation_on_first_request(self) -> None:
        """Test that stats are created on first request for a model."""
        collector = MetricsCollector()
        metrics = RequestMetrics(
            model_name="gpt-4", request_name="test", latency=1.0
        )

        collector.record_request(metrics)
        assert "gpt-4" in collector._stats
        assert collector._stats["gpt-4"].total_requests == 1

    def test_stats_aggregation(self) -> None:
        """Test that stats are aggregated correctly."""
        collector = MetricsCollector()

        # Record successful request
        collector.record_request(
            RequestMetrics(
                model_name="gpt-4",
                request_name="test1",
                latency=1.0,
                tokens_in=100,
                tokens_out=50,
                cost=0.001,
                success=True,
            )
        )

        # Record failed request
        collector.record_request(
            RequestMetrics(
                model_name="gpt-4",
                request_name="test2",
                latency=0.5,
                success=False,
                error="Rate limit",
                error_type="LLMRateLimitError",
            )
        )

        stats = collector._stats["gpt-4"]
        assert stats.total_requests == 2
        assert stats.success_count == 1
        assert stats.error_count == 1
        assert stats.total_latency == 1.5
        assert stats.total_tokens_in == 100
        assert stats.total_tokens_out == 50
        assert stats.total_cost == 0.001
        assert stats.error_types["LLMRateLimitError"] == 1

    def test_get_stats_for_model(self) -> None:
        """Test getting stats for a specific model."""
        collector = MetricsCollector()

        collector.record_request(
            RequestMetrics(
                model_name="gpt-4", request_name="test", latency=1.0, success=True
            )
        )

        stats = collector.get_stats(model_name="gpt-4")
        assert stats["model_name"] == "gpt-4"
        assert stats["total_requests"] == 1

    def test_get_stats_for_nonexistent_model(self) -> None:
        """Test getting stats for a model with no requests."""
        collector = MetricsCollector()
        stats = collector.get_stats(model_name="gpt-4")
        assert stats["total_requests"] == 0
        assert stats["success_count"] == 0
        assert stats["success_rate"] == 0.0

    def test_get_stats_all_models(self) -> None:
        """Test getting stats for all models."""
        collector = MetricsCollector()

        collector.record_request(
            RequestMetrics(model_name="gpt-4", request_name="test", latency=1.0)
        )
        collector.record_request(
            RequestMetrics(model_name="gpt-3.5", request_name="test", latency=0.5)
        )

        all_stats = collector.get_stats()
        assert len(all_stats) == 2
        model_names = {s["model_name"] for s in all_stats}
        assert model_names == {"gpt-4", "gpt-3.5"}

    def test_get_recent_history(self) -> None:
        """Test getting recent history."""
        collector = MetricsCollector()

        for i in range(10):
            collector.record_request(
                RequestMetrics(
                    model_name="gpt-4", request_name=f"test_{i}", latency=float(i)
                )
            )

        recent = collector.get_recent_history(limit=5)
        assert len(recent) == 5
        assert recent[0].request_name == "test_5"
        assert recent[-1].request_name == "test_9"

    def test_get_recent_history_limit_exceeds_total(self) -> None:
        """Test getting recent history when limit exceeds total."""
        collector = MetricsCollector()

        for i in range(3):
            collector.record_request(
                RequestMetrics(
                    model_name="gpt-4", request_name=f"test_{i}", latency=float(i)
                )
            )

        recent = collector.get_recent_history(limit=10)
        assert len(recent) == 3

    def test_clear(self) -> None:
        """Test clearing collector data."""
        collector = MetricsCollector()

        collector.record_request(
            RequestMetrics(model_name="gpt-4", request_name="test", latency=1.0)
        )

        collector.clear()
        assert len(collector._history) == 0
        assert len(collector._stats) == 0

    def test_thread_safety(self) -> None:
        """Test that collector is thread-safe."""

        def record_requests(collector: MetricsCollector, count: int, thread_id: int) -> None:
            for i in range(count):
                collector.record_request(
                    RequestMetrics(
                        model_name=f"model_{thread_id % 2}",
                        request_name=f"test_{thread_id}_{i}",
                        latency=1.0,
                    )
                )

        collector = MetricsCollector()
        threads = []
        for i in range(5):
            t = threading.Thread(target=record_requests, args=(collector, 10, i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Should have 50 total requests
        assert len(collector._history) == 50
        # Stats should be aggregated for 2 models
        assert len(collector._stats) == 2


class TestGlobalCollector:
    """Test cases for global collector."""

    def test_get_global_collector_returns_singleton(self) -> None:
        """Test that get_global_collector returns same instance."""
        collector1 = get_global_collector()
        collector2 = get_global_collector()
        assert collector1 is collector2

    def test_global_collector_persists_state(self) -> None:
        """Test that global collector persists state across calls."""
        collector = get_global_collector()

        # Clear to ensure clean state
        collector.clear()

        collector.record_request(
            RequestMetrics(model_name="gpt-4", request_name="test", latency=1.0)
        )

        # Get again and verify state persists
        collector2 = get_global_collector()
        assert len(collector2._history) == 1


class TestMetricsIntegration:
    """Integration tests for metrics system."""

    def test_complete_request_tracking_workflow(self) -> None:
        """Test complete workflow of tracking a request."""
        collector = MetricsCollector()

        # Simulate a request
        with RequestTimer() as timer:
            time.sleep(0.01)

        metrics = RequestMetrics(
            model_name="gpt-4",
            request_name="test_request",
            latency=timer.elapsed,
            tokens_in=100,
            tokens_out=50,
            cost=0.001,
            success=True,
            stream=True,
            retry_count=0,
        )

        collector.record_request(metrics)

        # Verify stats
        stats = collector.get_stats(model_name="gpt-4")
        assert stats["total_requests"] == 1
        assert stats["success_count"] == 1
        assert stats["total_latency"] > 0

    def test_multiple_models_tracking(self) -> None:
        """Test tracking requests across multiple models."""
        collector = MetricsCollector()

        models = ["gpt-4", "gpt-3.5-turbo", "claude-3"]
        for model in models:
            for i in range(5):
                collector.record_request(
                    RequestMetrics(
                        model_name=model,
                        request_name=f"request_{i}",
                        latency=1.0,
                        success=(i % 2 == 0),
                    )
                )

        all_stats = collector.get_stats()
        assert len(all_stats) == 3

        for stats in all_stats:
            assert stats["total_requests"] == 5
            assert stats["success_count"] == 3
            assert stats["error_count"] == 2

    def test_error_rate_calculation(self) -> None:
        """Test error rate calculation across models."""
        collector = MetricsCollector()

        # Record various errors
        for i in range(10):
            error = "LLMRateLimitError" if i % 2 == 0 else "LLMTimeoutError"
            collector.record_request(
                RequestMetrics(
                    model_name="gpt-4",
                    request_name=f"test_{i}",
                    latency=1.0,
                    success=False,
                    error=f"Error {i}",
                    error_type=error,
                )
            )

        stats = collector.get_stats(model_name="gpt-4")
        assert stats["error_count"] == 10
        assert stats["error_types"]["LLMRateLimitError"] == 5
        assert stats["error_types"]["LLMTimeoutError"] == 5
