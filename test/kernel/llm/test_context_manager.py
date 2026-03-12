"""Tests for LLMContextManager behavior."""

from __future__ import annotations

from typing import Any, cast

import pytest

from src.kernel.llm.context import LLMContextManager
from src.kernel.llm.payload import LLMPayload, Text, ToolCall, ToolResult
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.exceptions import LLMContextError
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


def test_context_manager_trims_by_token_budget() -> None:
    manager = LLMContextManager(max_payloads=10)
    payloads = [
        LLMPayload(ROLE.USER, Text("q1")),
        LLMPayload(ROLE.ASSISTANT, Text("a1")),
        LLMPayload(ROLE.USER, Text("q2")),
        LLMPayload(ROLE.ASSISTANT, Text("a2")),
        LLMPayload(ROLE.USER, Text("q3")),
        LLMPayload(ROLE.ASSISTANT, Text("a3")),
    ]

    # 每条消息按 10 token 计，预算 25 时只能保留最后一组（2条消息）
    trimmed = manager.maybe_trim(
        payloads,
        max_token_budget=25,
        token_counter=lambda items: len(items) * 10,
    )

    assert len(trimmed) == 2
    assert trimmed[0].role == ROLE.USER
    assert trimmed[0].content[0].text == "q3"
    assert trimmed[1].role == ROLE.ASSISTANT


def test_context_manager_system_tool_equivalent_add_payload() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads: list[LLMPayload] = []

    payloads = manager.system(payloads, Text("sys"))
    payloads = manager.tool(payloads, DummyTool)

    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[0].content[0].text == "sys"
    assert payloads[1].role == ROLE.TOOL


def test_context_manager_reminder_only_registers_until_next_payload() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.SYSTEM, Text("sys"))]

    manager.reminder("你必须先输出结论")

    assert len(payloads) == 1
    assert payloads[0].role == ROLE.SYSTEM

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))
    assert len(payloads) == 2
    assert payloads[1].role == ROLE.USER
    assert cast(Text, payloads[1].content[0]).text == "你必须先输出结论"
    assert cast(Text, payloads[1].content[1]).text == "你好"


def test_context_manager_register_reminder_defers_until_first_user() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads: list[LLMPayload] = []

    manager.reminder("先给结论")

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.SYSTEM, Text("sys")))
    assert len(payloads) == 1
    assert payloads[0].role == ROLE.SYSTEM

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))
    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[1].role == ROLE.USER
    assert cast(Text, payloads[1].content[0]).text == "先给结论"
    assert cast(Text, payloads[1].content[1]).text == "你好"


def test_context_manager_reminder_wraps_system_text() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads: list[LLMPayload] = []

    manager.reminder("[goal]\n先给结论", wrap_with_system_tag=True)

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.SYSTEM, Text("sys")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))

    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[1].role == ROLE.USER
    assert cast(Text, payloads[1].content[0]).text == "<system_reminder>\n[goal]\n先给结论\n</system_reminder>"
    assert cast(Text, payloads[1].content[1]).text == "你好"


def test_context_manager_reminder_waits_through_tool_until_first_user() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.SYSTEM, Text("sys"))]

    manager.reminder("先给结论", wrap_with_system_tag=True)

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.TOOL, DummyTool))
    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[1].role == ROLE.TOOL

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))
    assert len(payloads) == 3
    assert payloads[2].role == ROLE.USER
    assert cast(Text, payloads[2].content[0]).text == "<system_reminder>\n先给结论\n</system_reminder>"
    assert cast(Text, payloads[2].content[1]).text == "你好"


def test_context_manager_defers_missing_tool_result_placeholder_at_tail() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.USER, Text("帮我调用工具"))]

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("我将调用工具"),
                ToolCall(id="call_1", name="get_weather", args={"city": "上海"}),
            ],
        ),
    )

    assert len(payloads) == 2
    assert payloads[0].role == ROLE.USER
    assert payloads[1].role == ROLE.ASSISTANT


def test_context_manager_keeps_multiple_tool_results_in_merged_payload() -> None:
    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.USER, Text("请执行两个工具"))]

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("开始执行"),
                ToolCall(id="call_1", name="write_memory", args={"content": "A"}),
                ToolCall(id="call_2", name="finish_task", args={"content": "ok"}),
            ],
        ),
    )

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(value="写入成功", call_id="call_1", name="write_memory"),
        ),
    )
    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(value="任务完成", call_id="call_2", name="finish_task"),
        ),
    )

    assert len(payloads) == 3
    assert payloads[2].role == ROLE.TOOL_RESULT

    results = [part for part in payloads[2].content if isinstance(part, ToolResult)]
    assert len(results) == 2

    result_by_id = {result.call_id: result for result in results}
    assert result_by_id["call_1"].value == "写入成功"
    assert result_by_id["call_1"].name == "write_memory"
    assert result_by_id["call_2"].value == "任务完成"
    assert result_by_id["call_2"].name == "finish_task"


def test_context_manager_raises_when_tool_chain_is_broken_by_new_user() -> None:
    """strict 模式下：不自动补齐 tool_result；若 tool_calls 未闭合就进入下一条 USER，应直接报错。"""
    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.USER, Text("帮我调用工具"))]

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("我将调用工具"),
                ToolCall(id="call_1", name="get_weather", args={"city": "上海"}),
            ],
        ),
    )

    with pytest.raises(LLMContextError):
        manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("继续")))


def test_context_manager_raises_when_user_follows_tool_result_without_assistant() -> None:
    """strict 模式下：TOOL_RESULT 后必须由 ASSISTANT 承接；否则直接报错。"""
    manager = LLMContextManager(max_payloads=20)
    payloads = [LLMPayload(ROLE.USER, Text("先调用工具"))]

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("我将调用工具"),
                ToolCall(id="call_1", name="web_search", args={"query": "x"}),
            ],
        ),
    )

    payloads = manager.add_payload(
        payloads,
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(value="result", call_id="call_1", name="web_search"),
        ),
    )

    with pytest.raises(LLMContextError):
        manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("继续")))
