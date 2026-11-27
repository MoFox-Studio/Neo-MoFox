"""Regression coverage for timing embedding and rerank provider resolution."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from src.kernel.llm import monitor
from src.kernel.llm.embedding_request import EmbeddingRequest
from src.kernel.llm.exceptions import LLMConfigurationError
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.monitor import MetricsCollector
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from src.kernel.llm.rerank_request import RerankRequest
from src.kernel.llm.types import ModelEntry, ModelSet


class _Clock:
    """Advance only when provider resolution performs simulated work."""

    def __init__(self) -> None:
        """Start far from zero to distinguish latency from process uptime."""
        self.now = 100_000.0

    def perf_counter(self) -> float:
        """Return the current simulated monotonic timestamp."""
        return self.now


class _FailingRegistry:
    """Fail during client resolution after a small deterministic delay."""

    def __init__(self, clock: _Clock, error: BaseException) -> None:
        """Keep the simulated clock and the exact exception to propagate."""
        self.clock = clock
        self.error = error
        self.calls = 0

    def _resolve(self) -> NoReturn:
        """Simulate 25ms of resolution work before raising."""
        self.calls += 1
        self.clock.now += 0.025
        raise self.error

    def get_embedding_client_for_model(self, model: ModelEntry) -> NoReturn:
        """Resolve an embedding client without contacting a provider."""
        self._resolve()

    def get_rerank_client_for_model(self, model: ModelEntry) -> NoReturn:
        """Resolve a rerank client without contacting a provider."""
        self._resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_kind", ["embedding", "rerank"])
@pytest.mark.parametrize("enable_metrics", [True, False])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_resolution_failure_timing_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    mock_model_config: dict[str, Any],
    request_kind: str,
    enable_metrics: bool,
    cancelled: bool,
) -> None:
    """Time resolution failures while preserving metric opt-out and cancellation."""
    clock = _Clock()
    error = asyncio.CancelledError() if cancelled else LLMConfigurationError("provider unavailable")
    registry = _FailingRegistry(clock, error)
    collector = MetricsCollector()
    monkeypatch.setattr(monitor, "time", SimpleNamespace(perf_counter=clock.perf_counter))
    monkeypatch.setattr(monitor, "_global_collector", collector)
    model = {**mock_model_config, "max_retry": 0, "retry_interval": 0.0}
    model_set = cast(ModelSet, [model])
    clients = cast(ModelClientRegistry, registry)
    request: EmbeddingRequest | RerankRequest
    if request_kind == "embedding":
        request = EmbeddingRequest(
            model_set=model_set,
            request_name="resolution-timing",
            inputs=["hello"],
            clients=clients,
            policy=RoundRobinPolicy(),
            enable_metrics=enable_metrics,
        )
    else:
        request = RerankRequest(
            model_set=model_set,
            request_name="resolution-timing",
            query="hello",
            documents=["document"],
            clients=clients,
            policy=RoundRobinPolicy(),
            enable_metrics=enable_metrics,
        )

    with pytest.raises(type(error)) as caught:
        await request.send()

    assert caught.value is error
    assert registry.calls == 1
    history = collector.get_recent_history()
    if cancelled or not enable_metrics:
        assert history == []
    else:
        assert len(history) == 1
        metric = history[0]
        assert metric.latency == pytest.approx(0.025)
        assert 0.0 < metric.latency < 1.0
        assert not metric.success
        assert metric.error_type == "LLMConfigurationError"
        assert metric.error == str(error)
        assert metric.model_name == model["model_identifier"]
        assert metric.request_name == "resolution-timing"
        assert metric.retry_count == 0
        assert metric.model_index == 0
        assert not metric.stream
