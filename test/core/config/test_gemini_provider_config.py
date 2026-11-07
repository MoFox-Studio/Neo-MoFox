"""Gemini provider aliases stay explicit and canonical in model configuration."""

from types import SimpleNamespace
from typing import Any

import pytest

from src.core.config.model_config import APIProviderSection, ModelConfig
from src.kernel.config.core import _merge_with_model_defaults


@pytest.mark.parametrize("alias", ["gemini", "aiohttp_gemini", "google", "gemini_v2"])
def test_gemini_alias_before_strict_choices(alias: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file-loader merge and Pydantic validation both accept native aliases."""
    monkeypatch.setattr("src.core.config.get_core_config", lambda: SimpleNamespace(
        advanced=SimpleNamespace(force_sync_http=False, trust_env=False)
    ))
    data: dict[str, Any] = {
        "api_providers": [{"name": "native", "client_type": alias, "api_key": "fixture-key"}],
        "models": [{"name": "fixture", "model_identifier": "gemini-fixture", "api_provider": "native"}],
        "model_tasks": {"utils": {"model_list": ["fixture"]}},
    }
    config = ModelConfig.from_dict(_merge_with_model_defaults(ModelConfig, data))
    assert config.api_providers[0].client_type == "gemini"
    assert config.get_task("utils")[0]["client_type"] == "gemini"


def test_vertex_is_explicitly_unsupported() -> None:
    """Vertex must not silently switch authentication or provider protocol."""
    with pytest.raises(ValueError, match="Vertex.*unsupported"):
        APIProviderSection(client_type="vertex_ai")


@pytest.mark.parametrize("value", ["gemni", "unknown", 123])
def test_unknown_provider_stays_strict(value: Any) -> None:
    """Invalid providers never fall back to OpenAI."""
    with pytest.raises(ValueError):
        APIProviderSection(client_type=value)


def test_openai_is_not_inferred_from_model_name() -> None:
    """Explicit OpenAI-compatible protocol remains unchanged."""
    assert APIProviderSection(client_type="openai").client_type == "openai"


def test_stream_finished_event_is_public() -> None:
    """Plugins can subscribe to the real stream terminal event."""
    from src.core.components.types import EventType

    assert EventType.ON_LLM_STREAM_FINISHED.value == "on_llm_stream_finished"
