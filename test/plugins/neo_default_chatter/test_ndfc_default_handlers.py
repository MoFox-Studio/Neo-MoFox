"""16 个 Tier II 默认 EventHandler 的单元测试。

每个 handler 用以下模式测试：

1. **happy path**——构造合规 params，断言 ``execute`` 写回正确字段并返回 ``SUCCESS``
   （或观察类 handler 返回 ``PASS``）。
2. **edge cases**——配置缺失 / 字段类型错 / 异常路径，断言 handler fail-open 为
   ``PASS`` 不抛错（依据 ``event_manager.py:337-362`` safe_execute 兜底）。

不发布真实事件——直接调 ``handler.execute(event_name, params)``。委托 ``_runtime``
的 6 个 handler 通过 monkeypatch ``_runtime_helper.get_runtime`` 注入桩。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.neo_default_chatter.components.config import NeoChatterConfig
from plugins.neo_default_chatter.components.event_handlers.defaults import (
    BuildHistoryTextDefaultHandler,
    BuildNegativeExtraDefaultHandler,
    BuildResumePromptDefaultHandler,
    ComputeCooldownDefaultHandler,
    ComputeStopWakeDefaultHandler,
    CreateRequestDefaultHandler,
    DedupeToolCallDefaultHandler,
    FetchUnreadsDefaultHandler,
    FlushUnreadsDefaultHandler,
    FormatToolResultDefaultHandler,
    FormatUnreadLineDefaultHandler,
    InjectUnreadPayloadDefaultHandler,
    InjectUsablesDefaultHandler,
    PickTriggerMessageDefaultHandler,
    RunToolCallDefaultHandler,
    SessionTransitionDefaultHandler,
)
from src.app.plugin_system.types import (
    ChatStream,
    LLMPayload,
    Message,
    ROLE,
    Text,
)
from src.kernel.event import EventDecision


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------


def _make_config(
    *,
    enable_cooldown: bool = True,
    enable_stop_wake: bool = True,
    stop_wake_prob: float = 0.5,
    reinforce_negative: bool = True,
    native_multimodal: bool = False,
) -> NeoChatterConfig:
    """构造一份指定开关的 :class:`NeoChatterConfig`。"""
    cfg = NeoChatterConfig()
    cfg.plugin.enable_cooldown = enable_cooldown
    cfg.plugin.enable_stop_direct_message_wake = enable_stop_wake
    cfg.plugin.stop_direct_message_wake_probability = stop_wake_prob
    cfg.plugin.reinforce_negative_behaviors = reinforce_negative
    cfg.plugin.native_multimodal = native_multimodal
    return cfg


def _make_plugin(cfg: NeoChatterConfig | None = None) -> MagicMock:
    """构造一个 ``config`` 字段已注入的 mock 插件实例。"""
    plugin = MagicMock()
    plugin.config = cfg if cfg is not None else NeoChatterConfig()
    return plugin


def _make_stream(
    *,
    stream_id: str = "s_test",
    chat_type: str = "group",
) -> ChatStream:
    return ChatStream(
        stream_id=stream_id,
        platform="qq",
        chat_type=chat_type,
        bot_id="bot1",
        bot_nickname="小狐狸",
        stream_name="test",
    )


def _make_msg(text: str = "hi", *, sender: str = "Alice") -> Message:
    return Message(
        message_id="m1",
        content=text,
        processed_plain_text=text,
        sender_name=sender,
        chat_type="group",
    )


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    runtime: Any | None = None,
) -> Any:
    """把所有需要 ``_runtime`` 的默认 handler 模块里的 ``get_runtime`` 替换为返回桩。

    handler 模块用 ``from ._runtime_helper import get_runtime`` 导入，把 ``get_runtime``
    绑定到自身命名空间，因此必须在每个消费模块里分别 patch。
    """
    if runtime is None:
        runtime = MagicMock()
    for module_suffix in (
        "fetch_unreads",
        "format_unread_line",
        "flush_unreads",
        "create_request",
        "inject_usables",
        "run_tool_call",
    ):
        monkeypatch.setattr(
            f"plugins.neo_default_chatter.components.event_handlers.defaults.{module_suffix}.get_runtime",
            lambda stream_id, plugin: runtime,
        )
    return runtime


# ---------------------------------------------------------------------------
# FetchUnreadsDefaultHandler
# ---------------------------------------------------------------------------


async def test_fetch_unreads_default_fills_messages_from_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch_unreads`` 默认 handler 应把 ``runtime.fetch_unreads()`` 返回的 messages 填入。"""
    runtime = MagicMock()
    expected_msgs = [_make_msg("hello"), _make_msg("world")]
    runtime.fetch_unreads = AsyncMock(return_value=("formatted", expected_msgs))
    _patch_runtime(monkeypatch, runtime)

    handler = FetchUnreadsDefaultHandler(_make_plugin())
    params = {"stream_id": "s1", "messages": []}

    decision, out = await handler.execute("neo_default_chatter:fetch_unreads", params)

    assert decision == EventDecision.SUCCESS
    assert out["messages"] is expected_msgs
    runtime.fetch_unreads.assert_awaited_once()


async def test_fetch_unreads_default_fail_open_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``runtime.fetch_unreads`` 抛错时应 fail-open 为 PASS，messages 保持空。"""
    runtime = MagicMock()
    runtime.fetch_unreads = AsyncMock(side_effect=RuntimeError("boom"))
    _patch_runtime(monkeypatch, runtime)

    handler = FetchUnreadsDefaultHandler(_make_plugin())
    params = {"stream_id": "s1", "messages": []}

    decision, out = await handler.execute("neo_default_chatter:fetch_unreads", params)

    assert decision == EventDecision.PASS
    assert out["messages"] == []


# ---------------------------------------------------------------------------
# FormatUnreadLineDefaultHandler
# ---------------------------------------------------------------------------


async def test_format_unread_line_default_fills_formatted_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = MagicMock()
    runtime.format_message_line = MagicMock(return_value="【10:00】Alice: hi")
    _patch_runtime(monkeypatch, runtime)

    handler = FormatUnreadLineDefaultHandler(_make_plugin())
    msg = _make_msg("hi")
    params = {
        "stream_id": "s1",
        "message": msg,
        "time_format": "%H:%M",
        "formatted_line": "",
    }

    decision, out = await handler.execute(
        "neo_default_chatter:format_unread_line", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["formatted_line"] == "【10:00】Alice: hi"
    runtime.format_message_line.assert_called_once_with(msg, "%H:%M")


async def test_format_unread_line_default_uses_default_time_format_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``time_format`` key 缺失时应回退 ``"%H:%M"``。"""
    runtime = MagicMock()
    runtime.format_message_line = MagicMock(return_value="line")
    _patch_runtime(monkeypatch, runtime)

    handler = FormatUnreadLineDefaultHandler(_make_plugin())
    msg = _make_msg()
    params = {"stream_id": "s1", "message": msg, "formatted_line": ""}
    # 模拟 handler 用 .get("time_format") or "%H:%M" 兜底
    params["time_format"] = ""

    await handler.execute("neo_default_chatter:format_unread_line", params)
    runtime.format_message_line.assert_called_once_with(msg, "%H:%M")


# ---------------------------------------------------------------------------
# FlushUnreadsDefaultHandler
# ---------------------------------------------------------------------------


async def test_flush_unreads_default_fills_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = MagicMock()
    runtime.flush_unreads = AsyncMock(return_value=3)
    _patch_runtime(monkeypatch, runtime)

    handler = FlushUnreadsDefaultHandler(_make_plugin())
    msgs = [_make_msg(), _make_msg(), _make_msg()]
    params = {"stream_id": "s1", "messages": msgs, "flushed_count": 0}

    decision, out = await handler.execute("neo_default_chatter:flush_unreads", params)

    assert decision == EventDecision.SUCCESS
    assert out["flushed_count"] == 3
    runtime.flush_unreads.assert_awaited_once_with(msgs)


# ---------------------------------------------------------------------------
# CreateRequestDefaultHandler
# ---------------------------------------------------------------------------


async def test_create_request_default_fills_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = MagicMock()
    fake_request = MagicMock(name="LLMRequest")
    runtime.create_request = MagicMock(return_value=fake_request)
    _patch_runtime(monkeypatch, runtime)

    handler = CreateRequestDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "task_name": "actor",
        "request_name": "",
        "with_reminder": "actor",
        "request": None,
    }

    decision, out = await handler.execute("neo_default_chatter:create_request", params)

    assert decision == EventDecision.SUCCESS
    assert out["request"] is fake_request
    runtime.create_request.assert_called_once_with("actor", "", "actor")


# ---------------------------------------------------------------------------
# InjectUsablesDefaultHandler
# ---------------------------------------------------------------------------


async def test_inject_usables_default_fills_tool_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.plugin_system.types import ToolRegistry

    runtime = MagicMock()
    fake_registry = ToolRegistry()
    runtime.inject_usables = AsyncMock(return_value=fake_registry)
    _patch_runtime(monkeypatch, runtime)

    handler = InjectUsablesDefaultHandler(_make_plugin())
    request = MagicMock()
    params = {
        "stream_id": "s1",
        "request": request,
        "tool_registry": None,
        "extra_tools": [],
    }

    decision, out = await handler.execute("neo_default_chatter:inject_usables", params)

    assert decision == EventDecision.SUCCESS
    assert out["tool_registry"] is fake_registry
    runtime.inject_usables.assert_awaited_once_with(request)


# ---------------------------------------------------------------------------
# RunToolCallDefaultHandler
# ---------------------------------------------------------------------------


async def test_run_tool_call_default_fills_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = MagicMock()
    fake_results = [(True, True), (False, False)]
    runtime.run_tool_call = AsyncMock(return_value=fake_results)
    _patch_runtime(monkeypatch, runtime)

    handler = RunToolCallDefaultHandler(_make_plugin())
    calls = [MagicMock(), MagicMock()]
    response = MagicMock()
    usable_map = MagicMock()
    trigger = _make_msg()
    params = {
        "stream_id": "s1",
        "calls": calls,
        "response": response,
        "usable_map": usable_map,
        "trigger_msg": trigger,
        "results": [],
    }

    decision, out = await handler.execute("neo_default_chatter:run_tool_call", params)

    assert decision == EventDecision.SUCCESS
    assert out["results"] == fake_results
    runtime.run_tool_call.assert_awaited_once_with(
        calls, response, usable_map, trigger
    )


# ---------------------------------------------------------------------------
# InjectUnreadPayloadDefaultHandler
# ---------------------------------------------------------------------------


async def test_inject_unread_payload_default_appends_text_payload() -> None:
    """非多模态时把 ``formatted_text`` 作为纯 :class:`Text` 写入 USER payload。"""
    handler = InjectUnreadPayloadDefaultHandler(_make_plugin(_make_config(native_multimodal=False)))
    response = MagicMock()
    params = {
        "stream_id": "s1",
        "response": response,
        "formatted_text": "hello user",
        "unread_msgs": [],
        "native_multimodal": False,
        "skip": False,
    }

    decision, out = await handler.execute(
        "neo_default_chatter:inject_unread_payload", params
    )

    assert decision == EventDecision.SUCCESS
    response.add_payload.assert_called_once()
    payload = response.add_payload.call_args.args[0]
    assert isinstance(payload, LLMPayload)
    assert payload.role == ROLE.USER
    assert isinstance(payload.content[0], Text)
    assert payload.content[0].text == "hello user"


async def test_inject_unread_payload_default_skips_when_skip_true() -> None:
    """``skip=True`` 时不应调用 ``response.add_payload``。"""
    handler = InjectUnreadPayloadDefaultHandler(_make_plugin())
    response = MagicMock()
    params = {
        "stream_id": "s1",
        "response": response,
        "formatted_text": "hello",
        "unread_msgs": [],
        "native_multimodal": False,
        "skip": True,
    }

    decision, _ = await handler.execute(
        "neo_default_chatter:inject_unread_payload", params
    )

    assert decision == EventDecision.SUCCESS
    response.add_payload.assert_not_called()


async def test_inject_unread_payload_default_fail_open_on_exception() -> None:
    """``response.add_payload`` 抛错时降级为 PASS。"""
    handler = InjectUnreadPayloadDefaultHandler(_make_plugin())
    response = MagicMock()
    response.add_payload.side_effect = RuntimeError("broken")
    params = {
        "stream_id": "s1",
        "response": response,
        "formatted_text": "hi",
        "unread_msgs": [],
        "native_multimodal": False,
        "skip": False,
    }

    decision, out = await handler.execute(
        "neo_default_chatter:inject_unread_payload", params
    )

    assert decision == EventDecision.PASS


# ---------------------------------------------------------------------------
# BuildHistoryTextDefaultHandler
# ---------------------------------------------------------------------------


async def test_build_history_text_default_splits_into_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_history_text`` 默认 handler 把返回文本按 ``\\n`` 拆成 list 填入 ``lines``。"""
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.build_history_text."
        "NeoChatterPromptBuilder.build_history_text",
        staticmethod(lambda chat_stream, formatter: "l1\nl2\nl3"),
    )

    handler = BuildHistoryTextDefaultHandler(_make_plugin())
    stream = _make_stream()
    params = {"stream_id": "s1", "chat_stream": stream, "lines": []}

    decision, out = await handler.execute(
        "neo_default_chatter:build_history_text", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["lines"] == ["l1", "l2", "l3"]


async def test_build_history_text_default_empty_text_yields_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.build_history_text."
        "NeoChatterPromptBuilder.build_history_text",
        staticmethod(lambda chat_stream, formatter: ""),
    )

    handler = BuildHistoryTextDefaultHandler(_make_plugin())
    params = {"stream_id": "s1", "chat_stream": _make_stream(), "lines": []}

    decision, out = await handler.execute(
        "neo_default_chatter:build_history_text", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["lines"] == []


# ---------------------------------------------------------------------------
# BuildNegativeExtraDefaultHandler
# ---------------------------------------------------------------------------


async def test_build_negative_extra_default_appends_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reinforce_negative_behaviors=True`` 时应 append 一段约束文本。"""
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.build_negative_extra."
        "NeoChatterPromptBuilder.build_negative_behaviors_extra",
        staticmethod(lambda config: "约束A"),
    )

    handler = BuildNegativeExtraDefaultHandler(_make_plugin())
    params = {"stream_id": "s1", "config": _make_config(reinforce_negative=True), "fragments": []}

    decision, out = await handler.execute(
        "neo_default_chatter:build_negative_extra", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["fragments"] == ["约束A"]


async def test_build_negative_extra_default_appends_nothing_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_negative_behaviors_extra`` 返回空时不应 append。"""
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.build_negative_extra."
        "NeoChatterPromptBuilder.build_negative_behaviors_extra",
        staticmethod(lambda config: ""),
    )

    handler = BuildNegativeExtraDefaultHandler(_make_plugin())
    params = {"stream_id": "s1", "config": _make_config(), "fragments": []}

    decision, out = await handler.execute(
        "neo_default_chatter:build_negative_extra", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["fragments"] == []


# ---------------------------------------------------------------------------
# PickTriggerMessageDefaultHandler
# ---------------------------------------------------------------------------


async def test_pick_trigger_message_default_picks_last_unread() -> None:
    handler = PickTriggerMessageDefaultHandler(_make_plugin())
    last_msg = _make_msg("last")
    stream = _make_stream()
    params = {
        "stream_id": "s1",
        "chat_stream": stream,
        "unreads": [_make_msg("first"), last_msg],
        "current_message": None,
        "history": [],
        "trigger": None,
    }

    decision, out = await handler.execute(
        "neo_default_chatter:pick_trigger_message", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["trigger"] is last_msg


async def test_pick_trigger_message_default_falls_back_to_current_message() -> None:
    """未读空时回退 ``context.current_message``。"""
    handler = PickTriggerMessageDefaultHandler(_make_plugin())
    current = _make_msg("current")
    stream = _make_stream()
    stream.context.current_message = current
    stream.context.unread_messages = []
    stream.context.history_messages = []

    params = {
        "stream_id": "s1",
        "chat_stream": stream,
        "unreads": [],
        "current_message": current,
        "history": [],
        "trigger": None,
    }

    decision, out = await handler.execute(
        "neo_default_chatter:pick_trigger_message", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["trigger"] is current


# ---------------------------------------------------------------------------
# BuildResumePromptDefaultHandler
# ---------------------------------------------------------------------------


async def test_build_resume_prompt_default_timer_source_uses_timer_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.build_resume_prompt."
        "_build_timer_resume_prompt",
        lambda event: "TIMER PROMPT",
    )

    handler = BuildResumePromptDefaultHandler(_make_plugin())
    event = MagicMock()
    event.source = "timer"
    params = {"stream_id": "s1", "resume_event": event, "source": "timer", "prompt": ""}

    decision, out = await handler.execute(
        "neo_default_chatter:build_resume_prompt", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["prompt"] == "TIMER PROMPT"


async def test_build_resume_prompt_default_message_source_yields_empty() -> None:
    """``source="message"`` 时 prompt 应留空（消息本身走未读路径）。"""
    handler = BuildResumePromptDefaultHandler(_make_plugin())
    event = MagicMock()
    event.source = "message"
    params = {
        "stream_id": "s1",
        "resume_event": event,
        "source": "message",
        "prompt": "",
    }

    decision, out = await handler.execute(
        "neo_default_chatter:build_resume_prompt", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["prompt"] == ""


async def test_build_resume_prompt_default_generic_source_uses_generic_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.build_resume_prompt."
        "_build_generic_resume_prompt",
        lambda event: "GENERIC PROMPT",
    )

    handler = BuildResumePromptDefaultHandler(_make_plugin())
    event = MagicMock()
    event.source = "sub_agent"
    params = {
        "stream_id": "s1",
        "resume_event": event,
        "source": "sub_agent",
        "prompt": "",
    }

    decision, out = await handler.execute(
        "neo_default_chatter:build_resume_prompt", params
    )

    assert decision == EventDecision.SUCCESS
    assert out["prompt"] == "GENERIC PROMPT"


# ---------------------------------------------------------------------------
# DedupeToolCallDefaultHandler
# ---------------------------------------------------------------------------


async def test_dedupe_tool_call_default_first_call_not_duplicate() -> None:
    handler = DedupeToolCallDefaultHandler(_make_plugin())
    call = MagicMock()
    call.name = "send_text"
    call.args = {"text": "hi"}
    seen: set[str] = set()
    params = {"stream_id": "s1", "call": call, "seen_signatures": seen, "is_duplicate": False}

    decision, out = await handler.execute("neo_default_chatter:dedupe_tool_call", params)

    assert decision == EventDecision.SUCCESS
    assert out["is_duplicate"] is False
    assert len(seen) == 1


async def test_dedupe_tool_call_default_second_identical_call_is_duplicate() -> None:
    handler = DedupeToolCallDefaultHandler(_make_plugin())
    seen = set()
    # 第一次
    call1 = MagicMock()
    call1.name = "send_text"
    call1.args = {"text": "hi"}
    await handler.execute("neo_default_chatter:dedupe_tool_call", {
        "stream_id": "s1", "call": call1, "seen_signatures": seen, "is_duplicate": False,
    })
    # 第二次相同调用
    call2 = MagicMock()
    call2.name = "send_text"
    call2.args = {"text": "hi"}
    decision, out = await handler.execute("neo_default_chatter:dedupe_tool_call", {
        "stream_id": "s1", "call": call2, "seen_signatures": seen, "is_duplicate": False,
    })
    assert decision == EventDecision.SUCCESS
    assert out["is_duplicate"] is True
    assert len(seen) == 1  # 第二次未新增签名


async def test_dedupe_tool_call_default_ignores_reason_field() -> None:
    """``reason`` 不同但其他参数相同时仍视为重复。"""
    handler = DedupeToolCallDefaultHandler(_make_plugin())
    seen = set()
    call1 = MagicMock()
    call1.name = "send_text"
    call1.args = {"text": "hi", "reason": "A"}
    await handler.execute("neo_default_chatter:dedupe_tool_call", {
        "stream_id": "s1", "call": call1, "seen_signatures": seen, "is_duplicate": False,
    })
    call2 = MagicMock()
    call2.name = "send_text"
    call2.args = {"text": "hi", "reason": "B"}
    _, out = await handler.execute("neo_default_chatter:dedupe_tool_call", {
        "stream_id": "s1", "call": call2, "seen_signatures": seen, "is_duplicate": False,
    })
    assert out["is_duplicate"] is True


# ---------------------------------------------------------------------------
# FormatToolResultDefaultHandler
# ---------------------------------------------------------------------------


async def test_format_tool_result_default_pass_with_seconds() -> None:
    handler = FormatToolResultDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "call_name": "action-pass_and_wait",
        "kind": "pass",
        "args": {"seconds": 30},
        "result_text": "",
    }
    _, out = await handler.execute("neo_default_chatter:format_tool_result", params)
    assert "30" in out["result_text"]
    assert "等待" in out["result_text"]


async def test_format_tool_result_default_pass_without_seconds() -> None:
    handler = FormatToolResultDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "call_name": "action-pass_and_wait",
        "kind": "pass",
        "args": {},
        "result_text": "",
    }
    _, out = await handler.execute("neo_default_chatter:format_tool_result", params)
    assert "等待用户新消息" in out["result_text"]


async def test_format_tool_result_default_stop() -> None:
    handler = FormatToolResultDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "call_name": "action-stop_conversation",
        "kind": "stop",
        "args": {"minutes": 10},
        "result_text": "",
    }
    _, out = await handler.execute("neo_default_chatter:format_tool_result", params)
    assert "10" in out["result_text"]
    assert "对话已结束" in out["result_text"]


async def test_format_tool_result_default_duplicate() -> None:
    handler = FormatToolResultDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "call_name": "some_tool",
        "kind": "duplicate",
        "args": {},
        "result_text": "",
    }
    _, out = await handler.execute("neo_default_chatter:format_tool_result", params)
    assert "重复" in out["result_text"]


async def test_format_tool_result_default_normal_yields_empty() -> None:
    handler = FormatToolResultDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "call_name": "send_text",
        "kind": "normal",
        "args": {},
        "result_text": "",
    }
    _, out = await handler.execute("neo_default_chatter:format_tool_result", params)
    assert out["result_text"] == ""


# ---------------------------------------------------------------------------
# ComputeStopWakeDefaultHandler
# ---------------------------------------------------------------------------


async def test_compute_stop_wake_default_private_returns_config_probability() -> None:
    cfg = _make_config(enable_stop_wake=True, stop_wake_prob=0.6)
    handler = ComputeStopWakeDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "config": cfg,
        "chat_type": "private",
        "probability": 0.0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_stop_wake", params)
    assert out["probability"] == 0.6


async def test_compute_stop_wake_default_group_returns_zero() -> None:
    """群聊应返回 0.0（仅私聊启用 wake）。"""
    cfg = _make_config(enable_stop_wake=True, stop_wake_prob=0.6)
    handler = ComputeStopWakeDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "config": cfg,
        "chat_type": "group",
        "probability": 0.0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_stop_wake", params)
    assert out["probability"] == 0.0


async def test_compute_stop_wake_default_disabled_returns_zero() -> None:
    """``enable_stop_direct_message_wake=False`` 时返回 0.0。"""
    cfg = _make_config(enable_stop_wake=False, stop_wake_prob=0.6)
    handler = ComputeStopWakeDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "config": cfg,
        "chat_type": "private",
        "probability": 0.0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_stop_wake", params)
    assert out["probability"] == 0.0


async def test_compute_stop_wake_default_clamps_to_one() -> None:
    """概率 > 1 应被 clamp 到 1.0。"""
    cfg = _make_config(enable_stop_wake=True, stop_wake_prob=1.5)
    handler = ComputeStopWakeDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "config": cfg,
        "chat_type": "private",
        "probability": 0.0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_stop_wake", params)
    assert out["probability"] == 1.0


async def test_compute_stop_wake_default_clamps_to_zero() -> None:
    """概率 < 0 应被 clamp 到 0.0。"""
    cfg = _make_config(enable_stop_wake=True, stop_wake_prob=-0.3)
    handler = ComputeStopWakeDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "config": cfg,
        "chat_type": "private",
        "probability": 0.0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_stop_wake", params)
    assert out["probability"] == 0.0


# ---------------------------------------------------------------------------
# ComputeCooldownDefaultHandler
# ---------------------------------------------------------------------------


async def test_compute_cooldown_default_enabled_returns_seconds() -> None:
    cfg = _make_config(enable_cooldown=True)
    handler = ComputeCooldownDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "minutes": 5.0,
        "config": cfg,
        "cooldown_seconds": 0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_cooldown", params)
    assert out["cooldown_seconds"] == 300


async def test_compute_cooldown_default_disabled_returns_zero() -> None:
    cfg = _make_config(enable_cooldown=False)
    handler = ComputeCooldownDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "minutes": 5.0,
        "config": cfg,
        "cooldown_seconds": 0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_cooldown", params)
    assert out["cooldown_seconds"] == 0


async def test_compute_cooldown_default_invalid_minutes_yields_zero() -> None:
    """``minutes`` 非数字时应回退 0。"""
    cfg = _make_config(enable_cooldown=True)
    handler = ComputeCooldownDefaultHandler(_make_plugin(cfg))
    params = {
        "stream_id": "s1",
        "minutes": "not a number",
        "config": cfg,
        "cooldown_seconds": 0,
    }

    _, out = await handler.execute("neo_default_chatter:compute_cooldown", params)
    assert out["cooldown_seconds"] == 0


# ---------------------------------------------------------------------------
# SessionTransitionDefaultHandler
# ---------------------------------------------------------------------------


async def test_session_transition_default_returns_pass() -> None:
    """观察类 handler 应返回 ``PASS`` 让后续观察者继续。"""
    handler = SessionTransitionDefaultHandler(_make_plugin())
    params = {
        "stream_id": "s1",
        "from_phase": "wait_user",
        "to_phase": "model_turn",
        "turn_result": None,
    }

    decision, out = await handler.execute("neo_default_chatter:session_transition", params)

    assert decision == EventDecision.PASS
    # 不修改任何字段
    assert out == params
