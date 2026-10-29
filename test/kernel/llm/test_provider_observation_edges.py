"""Offline regressions for optional provider imports and stream observations."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import pytest

from src.kernel.llm import observation
from src.kernel.llm.exceptions import LLMTimeoutError, classify_exception
from src.kernel.llm.types import ModelEntry


@pytest.mark.parametrize("missing_module", ["google.genai.errors", "httpx"])
@pytest.mark.parametrize("message", ["unrecognized provider failure", "request timed out"])
def test_classification_survives_optional_import_failure(
    missing_module: str, message: str
) -> None:
    """Missing one optional library preserves fallback semantics and restores imports."""
    error = ValueError(message)
    # Warm unrelated SDK imports before installing the narrowly scoped import hook.
    baseline = classify_exception(error, model="edge-model")
    original_import = builtins.__import__
    blocked_imports: list[str] = []

    def import_without_optional_library(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        """Reject only the selected import and delegate every other import unchanged."""
        if name == missing_module:
            blocked_imports.append(name)
            raise ImportError(f"Simulated missing optional library: {name}")
        return original_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", new=import_without_optional_library):
        classified = classify_exception(error, model="edge-model")

    assert builtins.__import__ is original_import
    assert blocked_imports == [missing_module]
    if message == "request timed out":
        assert isinstance(baseline, LLMTimeoutError)
        assert isinstance(classified, LLMTimeoutError)
        assert classified.model == "edge-model"
        assert str(classified) == str(baseline)
    else:
        assert baseline is error
        assert classified is error


@pytest.fixture
def recorded_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> list[observation.RequestObservation]:
    """Capture observations without invoking metrics or database sinks."""
    recorded: list[observation.RequestObservation] = []

    def record_fake(item: observation.RequestObservation) -> None:
        """Keep the exact observation object in memory only."""
        recorded.append(item)

    monkeypatch.setattr(observation, "record_request_observation", record_fake)
    return recorded


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 5, "completion_tokens": 2}])
def test_stream_recorder_preserves_usage_and_error(
    mock_model_config: dict[str, Any],
    recorded_observations: list[observation.RequestObservation],
    usage: dict[str, Any] | None,
) -> None:
    """A failed stream still records its final usage and the original error object."""
    model = cast(ModelEntry, mock_model_config)
    metadata = {"stream_id": "failed-stream"}
    error = RuntimeError("stream interrupted")
    recorder = observation.make_stream_stats_recorder(
        model=model,
        model_identifier="edge-model",
        request_name="failed-request",
        meta_data=metadata,
        retry_count=2,
        model_index=3,
    )
    assert callable(recorder)
    assert recorded_observations == []

    recorder(usage, 1.25, error=error)

    assert len(recorded_observations) == 1
    recorded = recorded_observations[0]
    assert recorded.success is False
    assert recorded.error is error
    assert recorded.usage is usage
    assert recorded.latency == 1.25
    assert recorded.model is model
    assert recorded.meta_data is metadata
    assert recorded.model_identifier == "edge-model"
    assert recorded.request_name == "failed-request"
    assert recorded.stream is True
    assert recorded.retry_count == 2
    assert recorded.model_index == 3


def test_stream_recorder_creation_and_legacy_two_argument_call(
    mock_model_config: dict[str, Any],
    recorded_observations: list[observation.RequestObservation],
) -> None:
    """Factory creation is inert and legacy two-argument callbacks remain successful."""
    model = cast(ModelEntry, mock_model_config)
    recorder = observation.make_stream_stats_recorder(
        model=model,
        model_identifier="legacy-model",
        request_name="legacy-request",
        meta_data={},
        retry_count=0,
    )
    assert callable(recorder)
    assert recorded_observations == []

    usage = {"prompt_tokens": 3, "completion_tokens": 1}
    recorder(usage, 0.5)

    assert len(recorded_observations) == 1
    recorded = recorded_observations[0]
    assert recorded.success is True
    assert recorded.error is None
    assert recorded.usage is usage
    assert recorded.latency == 0.5
    assert recorded.stream is True
    assert recorded.retry_count == 0
    assert recorded.model_index == 0
