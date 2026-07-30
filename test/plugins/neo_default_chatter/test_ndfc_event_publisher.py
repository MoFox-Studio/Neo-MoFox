"""``NdfcEvent`` 枚举与 ``NdfcPublisher`` 发布器单元测试。

覆盖：

- ``NdfcEvent`` 用 ``StrEnum`` 而非 ``str, Enum``——``str(member)`` 返回事件名，
  字符串字面量与枚举成员等价（``==`` 与 ``in`` 都成立）。
- ``NdfcPublisher`` 16 个方法的 payload 预填：所有期望 key 都在初始 params 中，
  缺字段会让 EventBus 的 key 集合稳定约束（``core.py:334-338``）丢弃 handler 影响。
- 不真正发布事件——通过 monkeypatch ``publish_event`` 拦截并断言 payload。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.neo_default_chatter.components.config import NeoChatterConfig
from plugins.neo_default_chatter.utils.event_publisher import NdfcEvent, NdfcPublisher


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_config() -> NeoChatterConfig:
    """构造一份默认配置的 :class:`NeoChatterConfig`。"""
    return NeoChatterConfig()


def _patch_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """把 ``event_publisher.publish_event`` 替换为返回 ``{"params": params}`` 的桩。

    桩原样回传 params（模拟「无订阅者 / handler 都 PASS」的 EventBus 行为：
    返回原 params 的浅拷贝，但 key 集合不变）。
    """

    async def _fake_publish(event: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"decision": None, "params": dict(params or {})}

    mock = AsyncMock(side_effect=_fake_publish)
    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event", mock
    )
    return mock


def _await_args(mock: AsyncMock) -> tuple[Any, dict[str, Any]]:
    """取 ``mock.await_args`` 并断言非空，返回 ``(event_name, params)``。"""
    assert mock.await_args is not None, "publish_event 未被调用"
    return mock.await_args.args[0], mock.await_args.args[1]


# ---------------------------------------------------------------------------
# NdfcEvent StrEnum 语义
# ---------------------------------------------------------------------------


def test_ndfc_event_is_str_enum() -> None:
    """``NdfcEvent`` 应是 ``StrEnum`` 的子类。"""
    from enum import StrEnum

    assert issubclass(NdfcEvent, StrEnum)


def test_ndfc_event_str_returns_value_not_repr() -> None:
    """``str(member)`` 必须返回事件名字符串，而不是 ``NdfcEvent.X``。

    这是 ``EventManager._coerce_event_name`` 对非 ``EventType`` 走 ``str(event)``
    分支的硬要求——若返回 ``NdfcEvent.X``，第三方字符串字面量订阅就无法匹配发布方。
    """

    assert str(NdfcEvent.PREPROCESS) == "neo_default_chatter:preprocess"
    assert str(NdfcEvent.FETCH_UNREADS) == "neo_default_chatter:fetch_unreads"
    assert str(NdfcEvent.SESSION_TRANSITION) == "neo_default_chatter:session_transition"


def test_ndfc_event_member_equals_string_literal() -> None:
    """枚举成员必须与对应字符串字面量 ``==``。"""

    assert NdfcEvent.PREPROCESS == "neo_default_chatter:preprocess"
    assert NdfcEvent.FETCH_UNREADS == "neo_default_chatter:fetch_unreads"
    assert NdfcEvent.FORMAT_UNREAD_LINE == "neo_default_chatter:format_unread_line"
    assert NdfcEvent.FLUSH_UNREADS == "neo_default_chatter:flush_unreads"
    assert NdfcEvent.CREATE_REQUEST == "neo_default_chatter:create_request"
    assert NdfcEvent.INJECT_USABLES == "neo_default_chatter:inject_usables"
    assert NdfcEvent.RUN_TOOL_CALL == "neo_default_chatter:run_tool_call"
    assert NdfcEvent.INJECT_UNREAD_PAYLOAD == "neo_default_chatter:inject_unread_payload"
    assert NdfcEvent.BUILD_HISTORY_TEXT == "neo_default_chatter:build_history_text"
    assert NdfcEvent.BUILD_NEGATIVE_EXTRA == "neo_default_chatter:build_negative_extra"
    assert NdfcEvent.PICK_TRIGGER_MESSAGE == "neo_default_chatter:pick_trigger_message"
    assert NdfcEvent.BUILD_RESUME_PROMPT == "neo_default_chatter:build_resume_prompt"
    assert NdfcEvent.DEDUPE_TOOL_CALL == "neo_default_chatter:dedupe_tool_call"
    assert NdfcEvent.FORMAT_TOOL_RESULT == "neo_default_chatter:format_tool_result"
    assert NdfcEvent.COMPUTE_STOP_WAKE == "neo_default_chatter:compute_stop_wake"
    assert NdfcEvent.COMPUTE_COOLDOWN == "neo_default_chatter:compute_cooldown"
    assert NdfcEvent.SESSION_TRANSITION == "neo_default_chatter:session_transition"


def test_ndfc_event_member_in_list_of_strings() -> None:
    """枚举成员必须能放进字符串列表并被 ``in`` 命中（订阅器常用模式）。"""

    subscriptions: list[str] = ["neo_default_chatter:preprocess"]
    assert NdfcEvent.PREPROCESS in subscriptions

    subscriptions.extend([
        "neo_default_chatter:fetch_unreads",
        "neo_default_chatter:format_unread_line",
    ])
    assert NdfcEvent.FETCH_UNREADS in subscriptions
    assert NdfcEvent.FORMAT_UNREAD_LINE in subscriptions


def test_ndfc_event_total_member_count() -> None:
    """应有 17 个成员（16 Tier II + 1 Tier III ``:preprocess``）。"""

    members = list(NdfcEvent)
    assert len(members) == 17


def test_ndfc_event_all_members_have_neo_prefix() -> None:
    """所有成员值都应以 ``neo_default_chatter:`` 前缀开头。"""

    for member in NdfcEvent:
        assert member.value.startswith("neo_default_chatter:"), (
            f"{member.name}={member.value!r} 缺少前缀"
        )


def test_ndfc_event_member_values_unique() -> None:
    """所有成员值必须唯一（避免两个枚举指向同一事件名）。"""

    values = [m.value for m in NdfcEvent]
    assert len(set(values)) == len(values)


# ---------------------------------------------------------------------------
# NdfcPublisher payload 预填——key 集合稳定约束
# ---------------------------------------------------------------------------


async def test_fetch_unreads_prefills_stream_id_and_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch_unreads`` 必须预填 ``stream_id`` + ``messages=[]``。"""

    mock = _patch_publish(monkeypatch)
    await NdfcPublisher.fetch_unreads("s1")

    mock.assert_awaited_once()
    event_name, params = _await_args(mock)
    assert event_name == NdfcEvent.FETCH_UNREADS
    assert set(params.keys()) == {"stream_id", "messages"}
    assert params["stream_id"] == "s1"
    assert params["messages"] == []


async def test_format_unread_line_prefills_all_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``format_unread_line`` 必须预填全部 4 个 key。"""

    mock = _patch_publish(monkeypatch)
    msg = MagicMock()
    await NdfcPublisher.format_unread_line(
        stream_id="s1", message=msg, time_format="%H:%M"
    )

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "message", "time_format", "formatted_line",
    }
    assert params["formatted_line"] == ""
    assert params["time_format"] == "%H:%M"


async def test_format_unread_line_default_time_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``time_format`` 缺省时应填 ``"%H:%M"``。"""

    mock = _patch_publish(monkeypatch)
    await NdfcPublisher.format_unread_line(stream_id="s1", message=MagicMock())

    _, params = _await_args(mock)
    assert params["time_format"] == "%H:%M"


async def test_flush_unreads_prefills_fields_and_returns_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``flush_unreads`` 预填 + 把 ``flushed_count`` 读回为 int。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["flushed_count"] = 42
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    count = await NdfcPublisher.flush_unreads(
        stream_id="s1", messages=[MagicMock(), MagicMock()]
    )
    assert count == 42


async def test_create_request_prefills_request_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_request`` 必须预填 ``request=None`` 供默认 handler 填。"""

    mock = _patch_publish(monkeypatch)
    await NdfcPublisher.create_request(
        stream_id="s1", task_name="actor", with_reminder="actor"
    )

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "task_name", "request_name", "with_reminder", "request",
    }
    assert params["request"] is None
    assert params["request_name"] == ""
    assert params["with_reminder"] == "actor"


async def test_inject_usables_prefills_tool_registry_none_and_extra_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``inject_usables`` 必须预填 ``tool_registry=None`` + ``extra_tools=[]``。"""

    mock = _patch_publish(monkeypatch)
    await NdfcPublisher.inject_usables(stream_id="s1", request=MagicMock())

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "request", "tool_registry", "extra_tools",
    }
    assert params["tool_registry"] is None
    assert params["extra_tools"] == []


async def test_inject_usables_returns_empty_registry_when_handler_left_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler 没填 ``tool_registry`` 时，应回退一个空 :class:`ToolRegistry`。"""

    _patch_publish(monkeypatch)  # 不修改 tool_registry
    from src.app.plugin_system.types import ToolRegistry

    result = await NdfcPublisher.inject_usables(
        stream_id="s1", request=MagicMock()
    )
    assert isinstance(result, ToolRegistry)
    assert result.get_all() == []


async def test_inject_usables_registers_extra_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``extra_tools`` 列表里的工具类应被统一 ``register`` 到返回的 registry。"""

    class _FakeTool:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {"function": {"name": "fake_tool"}}

    async def _fake_publish(event, params=None):
        params = params or {}
        # 模拟第三方 handler append 一个工具到 extra_tools
        params["extra_tools"].append(_FakeTool)
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    result = await NdfcPublisher.inject_usables(
        stream_id="s1", request=MagicMock()
    )
    assert "fake_tool" in result.get_all_names()


async def test_inject_usables_skips_invalid_extra_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``extra_tools`` 里无法 derive name 的项应被静默跳过，不抛错。"""

    class _BadTool:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {"function": {}}  # 缺 name

    async def _fake_publish(event, params=None):
        params = params or {}
        params["extra_tools"].append(_BadTool)
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    # 不应抛 ValueError
    result = await NdfcPublisher.inject_usables(
        stream_id="s1", request=MagicMock()
    )
    assert result.get_all() == []


async def test_run_tool_call_prefills_results_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_tool_call`` 预填 ``results=[]`` 并读回。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["results"] = [(True, True), (False, False)]
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    results = await NdfcPublisher.run_tool_call(
        stream_id="s1",
        calls=[MagicMock()],
        response=MagicMock(),
        usable_map=MagicMock(),
        trigger_msg=None,
    )
    assert results == [(True, True), (False, False)]


async def test_inject_unread_payload_passes_all_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``inject_unread_payload`` 必须传全部字段（含 ``skip=False``）。"""

    mock = _patch_publish(monkeypatch)
    response = MagicMock()
    msgs = [MagicMock()]
    await NdfcPublisher.inject_unread_payload(
        stream_id="s1",
        response=response,
        formatted_text="hi",
        unread_msgs=msgs,
        native_multimodal=True,
    )

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "response", "formatted_text", "unread_msgs",
        "native_multimodal", "skip",
    }
    assert params["skip"] is False
    assert params["native_multimodal"] is True
    # unread_msgs 应被复制为 list（不传原引用）
    assert params["unread_msgs"] == msgs
    assert params["unread_msgs"] is not msgs


async def test_inject_unread_payload_default_unread_msgs_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``unread_msgs=None`` 时应填空 list（避免 key 缺失）。"""

    mock = _patch_publish(monkeypatch)
    await NdfcPublisher.inject_unread_payload(
        stream_id="s1",
        response=MagicMock(),
        formatted_text="hi",
    )

    _, params = _await_args(mock)
    assert params["unread_msgs"] == []


async def test_build_history_text_joins_lines_with_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_history_text`` 应把 ``lines`` 列表用 ``\\n`` 拼接。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["lines"] = ["line1", "line2", "line3"]
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    text = await NdfcPublisher.build_history_text(
        stream_id="s1", chat_stream=MagicMock()
    )
    assert text == "line1\nline2\nline3"


async def test_build_history_text_returns_empty_when_no_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lines`` 为空时返回空字符串。"""

    _patch_publish(monkeypatch)
    text = await NdfcPublisher.build_history_text(
        stream_id="s1", chat_stream=MagicMock()
    )
    assert text == ""


async def test_build_negative_extra_joins_fragments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_negative_extra`` 应把 ``fragments`` 列表用 ``\\n`` 拼接。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["fragments"] = ["约束A", "约束B"]
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    text = await NdfcPublisher.build_negative_extra(
        stream_id="s1", config=_make_config()
    )
    assert text == "约束A\n约束B"


async def test_build_negative_extra_empty_fragments_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fragments`` 为空时返回空字符串。"""

    _patch_publish(monkeypatch)
    text = await NdfcPublisher.build_negative_extra(
        stream_id="s1", config=_make_config()
    )
    assert text == ""


async def test_pick_trigger_message_prefills_trigger_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pick_trigger_message`` 必须预填 ``trigger=None``。"""

    mock = _patch_publish(monkeypatch)
    chat_stream = MagicMock()
    chat_stream.context.current_message = None
    chat_stream.context.history_messages = []

    await NdfcPublisher.pick_trigger_message(
        stream_id="s1", chat_stream=chat_stream, unreads=[]
    )

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "chat_stream", "unreads", "current_message",
        "history", "trigger",
    }
    assert params["trigger"] is None


async def test_build_resume_prompt_passes_source_and_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_resume_prompt`` 必须传 ``source`` + ``resume_event`` + ``prompt``。"""

    mock = _patch_publish(monkeypatch)
    event = MagicMock()
    event.source = "timer"

    await NdfcPublisher.build_resume_prompt(
        stream_id="s1", resume_event=event, source="timer"
    )

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "resume_event", "source", "prompt",
    }
    assert params["source"] == "timer"
    assert params["prompt"] == ""


async def test_dedupe_tool_call_returns_is_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dedupe_tool_call`` 应把 ``is_duplicate`` 字段读回为 bool。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["is_duplicate"] = True
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    result = await NdfcPublisher.dedupe_tool_call(
        stream_id="s1", call=MagicMock(), seen_signatures=set()
    )
    assert result is True


async def test_format_tool_result_returns_result_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``format_tool_result`` 应把 ``result_text`` 读回为 str。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["result_text"] = "some text"
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    text = await NdfcPublisher.format_tool_result(
        stream_id="s1", call_name="action-pass_and_wait", kind="pass", args={}
    )
    assert text == "some text"


async def test_compute_stop_wake_returns_float(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``compute_stop_wake`` 应把 ``probability`` 读回为 float。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["probability"] = 0.75
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    prob = await NdfcPublisher.compute_stop_wake(
        stream_id="s1", config=_make_config(), chat_type="private"
    )
    assert prob == 0.75
    assert isinstance(prob, float)


async def test_compute_cooldown_returns_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``compute_cooldown`` 应把 ``cooldown_seconds`` 读回为 int。"""

    async def _fake_publish(event, params=None):
        params = params or {}
        params["cooldown_seconds"] = 180
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        AsyncMock(side_effect=_fake_publish),
    )

    seconds = await NdfcPublisher.compute_cooldown(
        stream_id="s1", minutes=3.0, config=_make_config()
    )
    assert seconds == 180
    assert isinstance(seconds, int)


async def test_session_transition_passes_all_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``session_transition`` 应传 ``from_phase`` + ``to_phase`` + ``turn_result``。"""

    mock = _patch_publish(monkeypatch)
    await NdfcPublisher.session_transition(
        stream_id="s1",
        from_phase="wait_user",
        to_phase="model_turn",
        turn_result=None,
    )

    _, params = _await_args(mock)
    assert set(params.keys()) == {
        "stream_id", "from_phase", "to_phase", "turn_result",
    }
