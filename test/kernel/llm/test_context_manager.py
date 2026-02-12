"""Tests for LLMContextManager behavior."""

from __future__ import annotations

from typing import Any

from src.kernel.llm.context import LLMContextManager
from src.kernel.llm.payload import LLMPayload, Text, ToolResult
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.roles import ROLE


class DummyTool:
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {"name": "dummy"}


def dummy_model() -> dict[str, Any]:
    return {
        "api_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": "gpt-4",
        "api_key": "sk-test",
        "client_type": "openai",
        "max_retry": 0,
        "timeout": 1,
        "retry_interval": 0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.1,
        "max_tokens": 10,
        "extra_params": {},
    }


def test_context_manager_trims_full_groups() -> None:
    manager = LLMContextManager(max_payloads=5)
    payloads = [
        LLMPayload(ROLE.SYSTEM, Text("sys")),
        LLMPayload(ROLE.TOOL, DummyTool),
        LLMPayload(ROLE.USER, Text("q1")),
        LLMPayload(ROLE.ASSISTANT, Text("a1")),
        LLMPayload(ROLE.TOOL_RESULT, ToolResult({"ok": True})),
        LLMPayload(ROLE.USER, Text("q2")),
        LLMPayload(ROLE.ASSISTANT, Text("a2")),
    ]

    trimmed = manager.maybe_trim(payloads)

    assert len(trimmed) == 4
    assert trimmed[0].role == ROLE.SYSTEM
    assert trimmed[1].role == ROLE.TOOL
    assert trimmed[2].role == ROLE.USER
    assert trimmed[2].content[0].text == "q2"
    assert trimmed[3].role == ROLE.ASSISTANT


def test_context_manager_applies_hook() -> None:
    called = {"value": False}

    def hook(dropped_groups, remaining_payloads):
        called["value"] = True
        return [LLMPayload(ROLE.ASSISTANT, Text("summary"))]

    manager = LLMContextManager(max_payloads=4, compression_hook=hook)
    payloads = [
        LLMPayload(ROLE.SYSTEM, Text("sys")),
        LLMPayload(ROLE.USER, Text("q1")),
        LLMPayload(ROLE.ASSISTANT, Text("a1")),
        LLMPayload(ROLE.USER, Text("q2")),
        LLMPayload(ROLE.ASSISTANT, Text("a2")),
    ]

    trimmed = manager.maybe_trim(payloads)

    assert called["value"] is True
    assert len(trimmed) == 4
    assert trimmed[0].role == ROLE.SYSTEM
    assert trimmed[1].role == ROLE.ASSISTANT
    assert trimmed[1].content[0].text == "summary"
    assert trimmed[2].role == ROLE.USER
    assert trimmed[2].content[0].text == "q2"


def test_llm_request_uses_custom_context_manager() -> None:
    class CustomManager(LLMContextManager):
        def __init__(self) -> None:
            super().__init__(max_payloads=10)
            self.called = False

        def maybe_trim(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
            self.called = True
            return payloads

    manager = CustomManager()
    request = LLMRequest([dummy_model()], context_manager=manager)
    request.add_payload(LLMPayload(ROLE.USER, Text("hello")))

    assert manager.called is True
