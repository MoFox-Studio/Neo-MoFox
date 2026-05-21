"""Tests for LLMContextManager behavior."""

from __future__ import annotations

from typing import Any, cast

import pytest

from src.core.prompt import SystemReminderInsertType, get_system_reminder_store, reset_system_reminder_store
from src.kernel.llm.context import LLMContextManager, ReminderSourceSpec
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


@pytest.fixture
def reminder_store():
    reset_system_reminder_store()
    store = get_system_reminder_store()
    yield store
    reset_system_reminder_store()


def make_manager(
    bucket: str,
    *,
    names: tuple[str, ...] | None = None,
    wrap_with_system_tag: bool = False,
) -> LLMContextManager:
    return LLMContextManager(
        reminder_sources=[
            ReminderSourceSpec(
                bucket=bucket,
                names=names,
                wrap_with_system_tag=wrap_with_system_tag,
            )
        ]
    )


def test_context_manager_trims_full_groups() -> None:
    manager = LLMContextManager()
    payloads = [
        LLMPayload(ROLE.SYSTEM, Text("sys")),
        LLMPayload(ROLE.TOOL, DummyTool),
        LLMPayload(ROLE.USER, Text("q1")),
        LLMPayload(ROLE.ASSISTANT, Text("a1")),
        LLMPayload(ROLE.TOOL_RESULT, ToolResult({"ok": True})),
        LLMPayload(ROLE.USER, Text("q2")),
        LLMPayload(ROLE.ASSISTANT, Text("a2")),
    ]

    trimmed = manager.maybe_trim(
        payloads,
        max_token_budget=45,
        token_counter=lambda items: len(items) * 10,
    )

    assert len(trimmed) == 4
    assert trimmed[0].role == ROLE.SYSTEM
    assert trimmed[1].role == ROLE.TOOL
    assert trimmed[2].role == ROLE.USER
    assert trimmed[2].content[0].text == "q2"
    assert trimmed[3].role == ROLE.ASSISTANT


def test_llm_request_uses_custom_context_manager() -> None:
    class CustomManager(LLMContextManager):
        def __init__(self) -> None:
            super().__init__()
            self.called = False

        def maybe_trim(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
            self.called = True
            return payloads

    manager = CustomManager()
    request = LLMRequest([dummy_model()], context_manager=manager)
    request.add_payload(LLMPayload(ROLE.USER, Text("hello")))

    assert manager.called is True


def test_context_manager_trims_by_token_budget() -> None:
    manager = LLMContextManager()
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


@pytest.mark.asyncio
async def test_context_manager_prepare_payloads_for_model_applies_budget(monkeypatch) -> None:
    manager = LLMContextManager()
    payloads = [
        LLMPayload(ROLE.USER, Text("q1")),
        LLMPayload(ROLE.ASSISTANT, Text("a1")),
        LLMPayload(ROLE.USER, Text("q2")),
        LLMPayload(ROLE.ASSISTANT, Text("a2")),
        LLMPayload(ROLE.USER, Text("q3")),
        LLMPayload(ROLE.ASSISTANT, Text("a3")),
    ]

    monkeypatch.setattr(
        "src.kernel.llm.context.count_payload_tokens",
        lambda items, model_identifier=None: len(items) * 10,
    )

    prepared = await manager.prepare_payloads_for_model(
        payloads,
        {**dummy_model(), "max_context": 25},
    )

    assert len(prepared) == 2
    assert prepared[0].role == ROLE.USER
    assert prepared[0].content[0].text == "q3"
    assert prepared[1].role == ROLE.ASSISTANT


def test_context_manager_system_tool_equivalent_add_payload() -> None:
    manager = LLMContextManager()
    payloads: list[LLMPayload] = []

    payloads = manager.system(payloads, Text("sys"))
    payloads = manager.tool(payloads, DummyTool)

    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[0].content[0].text == "sys"
    assert payloads[1].role == ROLE.TOOL


def test_context_manager_bucket_reminder_only_registers_until_next_payload(reminder_store) -> None:
    manager = make_manager("actor", names=("goal",))
    payloads = [LLMPayload(ROLE.SYSTEM, Text("sys"))]
    reminder_store.set("actor", "goal", "你必须先输出结论")

    assert len(payloads) == 1
    assert payloads[0].role == ROLE.SYSTEM

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))
    assert len(payloads) == 2
    assert payloads[1].role == ROLE.USER
    assert cast(Text, payloads[1].content[0]).text == "[goal]\n你必须先输出结论"
    assert cast(Text, payloads[1].content[1]).text == "你好"


def test_context_manager_bucket_reminder_defers_until_first_user(reminder_store) -> None:
    manager = make_manager("actor", names=("goal",))
    payloads: list[LLMPayload] = []
    reminder_store.set("actor", "goal", "先给结论")

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.SYSTEM, Text("sys")))
    assert len(payloads) == 1
    assert payloads[0].role == ROLE.SYSTEM

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))
    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[1].role == ROLE.USER
    assert cast(Text, payloads[1].content[0]).text == "[goal]\n先给结论"
    assert cast(Text, payloads[1].content[1]).text == "你好"


def test_context_manager_bucket_reminder_wraps_system_text(reminder_store) -> None:
    manager = make_manager("actor", names=("goal",), wrap_with_system_tag=True)
    payloads: list[LLMPayload] = []
    reminder_store.set("actor", "goal", "先给结论")

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.SYSTEM, Text("sys")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))

    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[1].role == ROLE.USER
    assert cast(Text, payloads[1].content[0]).text == "<system_reminder>\n[goal]\n先给结论\n</system_reminder>"
    assert cast(Text, payloads[1].content[1]).text == "你好"


def test_context_manager_bucket_reminder_waits_through_tool_until_first_user(reminder_store) -> None:
    manager = make_manager("actor", names=("goal",), wrap_with_system_tag=True)
    payloads = [LLMPayload(ROLE.SYSTEM, Text("sys"))]
    reminder_store.set("actor", "goal", "先给结论")

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.TOOL, DummyTool))
    assert len(payloads) == 2
    assert payloads[0].role == ROLE.SYSTEM
    assert payloads[1].role == ROLE.TOOL

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("你好")))
    assert len(payloads) == 3
    assert payloads[2].role == ROLE.USER
    assert cast(Text, payloads[2].content[0]).text == "<system_reminder>\n[goal]\n先给结论\n</system_reminder>"
    assert cast(Text, payloads[2].content[1]).text == "你好"


def test_context_manager_dynamic_bucket_targets_last_user(reminder_store) -> None:
    manager = make_manager("actor", names=("recent",))
    payloads = [LLMPayload(ROLE.USER, Text("第一条"))]
    reminder_store.set(
        "actor",
        "recent",
        "跟进最近一条",
        insert_type=SystemReminderInsertType.DYNAMIC,
    )

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("收到")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第二条")))

    assert cast(Text, payloads[0].content[0]).text == "第一条"
    assert cast(Text, payloads[2].content[0]).text == "[recent]\n跟进最近一条"
    assert cast(Text, payloads[2].content[1]).text == "第二条"


def test_context_manager_dynamic_bucket_moves_to_new_last_user(reminder_store) -> None:
    manager = make_manager("actor", names=("recent",))
    payloads: list[LLMPayload] = []
    reminder_store.set(
        "actor",
        "recent",
        "只跟最后一条",
        insert_type=SystemReminderInsertType.DYNAMIC,
    )

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第一条")))
    assert cast(Text, payloads[0].content[0]).text == "[recent]\n只跟最后一条"
    assert cast(Text, payloads[0].content[1]).text == "第一条"

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("回复")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第二条")))

    assert cast(Text, payloads[0].content[0]).text == "[recent]\n只跟最后一条"
    assert cast(Text, payloads[0].content[1]).text == "第一条"
    assert cast(Text, payloads[2].content[0]).text == "[recent]\n只跟最后一条"
    assert cast(Text, payloads[2].content[1]).text == "第二条"


def test_context_manager_fixed_and_dynamic_bucket_reminders_target_different_users(reminder_store) -> None:
    manager = make_manager("actor")
    payloads: list[LLMPayload] = []
    reminder_store.set("actor", "goal", "固定开头")
    reminder_store.set(
        "actor",
        "recent",
        "最近一条",
        insert_type=SystemReminderInsertType.DYNAMIC,
    )

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第一条")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("回复")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第二条")))

    assert cast(Text, payloads[0].content[0]).text == "[goal]\n固定开头"
    assert cast(Text, payloads[0].content[1]).text == "[recent]\n最近一条"
    assert cast(Text, payloads[0].content[2]).text == "第一条"
    assert cast(Text, payloads[2].content[0]).text == "[recent]\n最近一条"
    assert cast(Text, payloads[2].content[1]).text == "第二条"

def test_context_manager_reminder_bucket_refreshes_updated_dynamic_content() -> None:
    reset_system_reminder_store()
    store = get_system_reminder_store()
    store.set("actor", "screen", "第一次", insert_type=SystemReminderInsertType.DYNAMIC)

    manager = make_manager("actor", wrap_with_system_tag=True)
    payloads: list[LLMPayload] = []

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第一条")))
    assert cast(Text, payloads[0].content[0]).text == "<system_reminder>\n[screen]\n第一次\n</system_reminder>"

    store.set("actor", "screen", "第二次", insert_type=SystemReminderInsertType.DYNAMIC)
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("回复")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第二条")))

    assert cast(Text, payloads[0].content[0]).text == "<system_reminder>\n[screen]\n第一次\n</system_reminder>"
    assert cast(Text, payloads[0].content[1]).text == "第一条"
    assert cast(Text, payloads[2].content[0]).text == "<system_reminder>\n[screen]\n第二次\n</system_reminder>"
    assert cast(Text, payloads[2].content[1]).text == "第二条"

    reset_system_reminder_store()


def test_context_manager_dynamic_bucket_multiple_updates_do_not_accumulate() -> None:
    reset_system_reminder_store()
    store = get_system_reminder_store()
    store.set("actor", "screen", "第一次", insert_type=SystemReminderInsertType.DYNAMIC)

    manager = make_manager("actor", wrap_with_system_tag=True)
    payloads: list[LLMPayload] = []

    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第一条")))
    store.set("actor", "screen", "第二次", insert_type=SystemReminderInsertType.DYNAMIC)
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("回复一")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第二条")))

    store.set("actor", "screen", "第三次", insert_type=SystemReminderInsertType.DYNAMIC)
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("回复二")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第三条")))

    assert cast(Text, payloads[0].content[0]).text == "<system_reminder>\n[screen]\n第一次\n</system_reminder>"
    assert cast(Text, payloads[0].content[1]).text == "第一条"
    assert cast(Text, payloads[2].content[0]).text == "<system_reminder>\n[screen]\n第二次\n</system_reminder>"
    assert cast(Text, payloads[2].content[1]).text == "第二条"
    assert cast(Text, payloads[4].content[0]).text == "<system_reminder>\n[screen]\n第三次\n</system_reminder>"
    assert cast(Text, payloads[4].content[1]).text == "第三条"
    assert len(
        [
            part for payload in payloads if payload.role == ROLE.USER for part in payload.content
            if isinstance(part, Text) and part.text.startswith("<system_reminder>\n[screen]\n")
        ]
    ) == 3


def test_context_manager_dynamic_bucket_keeps_historical_prefix_stable(reminder_store) -> None:
    manager = make_manager("actor")
    payloads: list[LLMPayload] = []

    reminder_store.set(
        "actor",
        "dynamic_a",
        "动态前缀A",
        insert_type=SystemReminderInsertType.DYNAMIC,
    )
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第一条")))

    reminder_store.set(
        "actor",
        "dynamic_b",
        "动态前缀B",
        insert_type=SystemReminderInsertType.DYNAMIC,
    )
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.ASSISTANT, Text("回复")))
    payloads = manager.add_payload(payloads, LLMPayload(ROLE.USER, Text("第二条")))

    assert cast(Text, payloads[0].content[0]).text == "[dynamic_a]\n动态前缀A"
    assert cast(Text, payloads[0].content[1]).text == "第一条"
    assert cast(Text, payloads[2].content[0]).text == "[dynamic_a]\n动态前缀A"
    assert cast(Text, payloads[2].content[1]).text == "[dynamic_b]\n动态前缀B"
    assert cast(Text, payloads[2].content[2]).text == "第二条"

def test_context_manager_defers_missing_tool_result_placeholder_at_tail() -> None:
    manager = LLMContextManager()
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
    manager = LLMContextManager()
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
    manager = LLMContextManager()
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
    manager = LLMContextManager()
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
