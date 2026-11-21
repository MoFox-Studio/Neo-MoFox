"""Explicit protocol-dispatch boundary for the native Gemini implementation."""
import pytest

from src.kernel.llm.exceptions import LLMProviderUnavailableError
from src.kernel.llm.model_client import GeminiChatClient, ModelClientRegistry


def test_vertex_requires_a_distinct_adapter() -> None:
    """Developer API credentials are not a fallback for Vertex authentication."""
    registry = ModelClientRegistry()
    assert isinstance(registry.gemini, GeminiChatClient)
    with pytest.raises(LLMProviderUnavailableError, match="Vertex AI"):
        registry.get_client_for_model({"client_type": "vertex_ai"})
