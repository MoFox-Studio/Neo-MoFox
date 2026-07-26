"""default_chatter.sub_agent 行为测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from plugins.default_chatter.components.actions.send_text import (
    SendTextAction,
    _LAST_SEND_TIME_ATTR,
    _TYPING_DELAY_MAX_SECONDS,
    _TYPING_DELAY_PER_CHAR,
)
from plugins.default_chatter.components.config import DefaultChatterConfig
from plugins.default_chatter.plugin import (
    DefaultChatter,
    DefaultChatterPlugin,
)
from src.core.models.message import Message
from src.core.models.stream import ChatStream


def _build_chatter() -> DefaultChatter:
    """构造默认聊天器实例。"""
    config = DefaultChatterConfig.from_dict({"plugin": {"enabled": True}})
    plugin = DefaultChatterPlugin(config=config)
    return DefaultChatter(stream_id="test_stream", plugin=plugin)


def _build_chatter_with_config(plugin_overrides: dict[str, object]) -> DefaultChatter:
    """使用指定插件配置覆盖项构造默认聊天器实例。"""
    config = DefaultChatterConfig.from_dict(
        {"plugin": {"enabled": True, **plugin_overrides}}
    )
    plugin = DefaultChatterPlugin(config=config)
    return DefaultChatter(stream_id="test_stream", plugin=plugin)


@pytest.mark.asyncio
async def test_sub_agent_is_disabled_in_private_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """私聊场景应跳过 decide_should_respond。"""
    chatter = _build_chatter()
    stream = ChatStream(stream_id="s_private", platform="qq", chat_type="private")

    called = {"value": False}

    async def _fake_decide(**_kwargs: Any) -> dict[str, object]:
        called["value"] = True
        return {"reason": "should not be called", "should_respond": False}

    monkeypatch.setattr("plugins.default_chatter.utils.interest_gate.decide_should_respond", _fake_decide)

    result = await chatter.sub_agent("hello", [], stream)

    assert result["should_respond"] is True
    assert "私聊场景" in result["reason"]
    assert called["value"] is False


@pytest.mark.asyncio
async def test_sub_agent_keeps_decision_flow_in_group_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """群聊场景应继续走 decide_should_respond。"""
    chatter = _build_chatter()
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")

    captured: dict[str, Any] = {}

    async def _fake_decide(**kwargs: Any) -> dict[str, object]:
        captured.update(kwargs)
        return {"reason": "group decision", "should_respond": False}

    monkeypatch.setattr("plugins.default_chatter.utils.interest_gate.decide_should_respond", _fake_decide)
    monkeypatch.setattr("plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.99)

    result = await chatter.sub_agent("group-msg", [], stream)

    assert result == {"reason": "group decision", "should_respond": False}
    assert captured["chatter"] is chatter
    assert captured["chat_stream"] is stream
    assert captured["unreads_text"] == "group-msg"
    assert captured["fallback_prompt"]
    assert "logger" in captured


@pytest.mark.asyncio
async def test_sub_agent_with_invalid_config_uses_llm_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置类型无效时跳过概率门并执行 LLM 决策。"""
    plugin = SimpleNamespace(config=None)
    chatter = DefaultChatter(stream_id="test_stream", plugin=plugin)
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    decide_mock = AsyncMock(
        return_value={"reason": "llm decision", "should_respond": False}
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.interest_gate.decide_should_respond",
        decide_mock,
    )

    result = await chatter.sub_agent("group-msg", [], stream)

    assert result == {"reason": "llm decision", "should_respond": False}
    decide_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_sub_agent_bypasses_llm_when_probability_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中概率门时应直接响应，不再经过 decide_should_respond。"""
    chatter = _build_chatter()
    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_nickname="Neo",
    )
    setattr(stream.context, "_default_chatter_next_tick_bonus", 0.5)

    unread_msgs = [
        Message(content="Neo 你在吗", processed_plain_text="Neo 你在吗"),
        Message(content="小狐狸来看看", processed_plain_text="小狐狸来看看"),
    ]

    called = {"value": False}

    async def _fake_decide(**_kwargs: Any) -> dict[str, object]:
        called["value"] = True
        return {"reason": "should not be called", "should_respond": False}

    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname="Neo",
                alias_names=["小狐狸"],
            )
        ),
    )
    monkeypatch.setattr("plugins.default_chatter.utils.interest_gate.decide_should_respond", _fake_decide)
    monkeypatch.setattr("plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.99)

    result = await chatter.sub_agent("group-msg", unread_msgs, stream)

    assert result["should_respond"] is True
    assert "概率直通响应" in result["reason"]
    assert called["value"] is False
    assert getattr(stream.context, "_default_chatter_next_tick_bonus", None) == 0.0


@pytest.mark.asyncio
async def test_send_text_marks_next_tick_bonus_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_text 成功后应为下一次 tick 写入概率加成。"""
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    action = SendTextAction(chat_stream=stream, plugin=DefaultChatterPlugin(config=DefaultChatterConfig()))

    monkeypatch.setattr(action, "_send_to_stream", AsyncMock(return_value=True))

    success, _detail = await action._wrap_execute(content="你好").wait_done()

    assert success is True
    assert getattr(stream.context, "_default_chatter_next_tick_bonus", None) == 0.5


@pytest.mark.asyncio
async def test_send_text_yields_before_typing_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_text 应先进入 READY，再执行 typing delay。"""

    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    action = SendTextAction(
        chat_stream=stream,
        plugin=DefaultChatterPlugin(config=DefaultChatterConfig()),
    )

    sleep_mock = AsyncMock()
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(action, "_sleep_for_typing_delay", sleep_mock)
    monkeypatch.setattr(action, "_send_to_stream", send_mock)

    execution = action.execute(content="你好")

    first = await anext(execution)
    assert first is None
    sleep_mock.assert_not_awaited()
    send_mock.assert_not_awaited()

    second = await anext(execution)
    assert second == (True, "已发送消息:你好")
    sleep_mock.assert_awaited_once_with("你好")
    send_mock.assert_awaited_once_with("你好")


def test_send_text_typing_delay_uses_length_and_max_cap() -> None:
    """send_text 打字延迟应随字符长度增长，并受最大等待时间限制。"""
    short_delay = SendTextAction._typing_delay_seconds("你好呀")
    long_delay = SendTextAction._typing_delay_seconds("x" * 10_000)

    assert short_delay == pytest.approx(3 * _TYPING_DELAY_PER_CHAR)
    assert long_delay == _TYPING_DELAY_MAX_SECONDS


@pytest.mark.asyncio
async def test_send_text_first_message_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首条文本发送不等待。"""
    action = SendTextAction(
        chat_stream=ChatStream(stream_id="s_group", platform="qq", chat_type="group"),
        plugin=DefaultChatterPlugin(config=DefaultChatterConfig()),
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr("plugins.default_chatter.components.actions.send_text.asyncio.sleep", sleep_mock)

    await action._sleep_for_typing_delay("你好")

    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_text_waits_only_for_remaining_typing_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续发送只等待尚未经过的模拟打字时间。"""
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    action = SendTextAction(
        chat_stream=stream,
        plugin=DefaultChatterPlugin(config=DefaultChatterConfig()),
    )
    setattr(stream.context, _LAST_SEND_TIME_ATTR, 100.0)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("plugins.default_chatter.components.actions.send_text.time.monotonic", lambda: 100.5)
    monkeypatch.setattr("plugins.default_chatter.components.actions.send_text.asyncio.sleep", sleep_mock)

    await action._sleep_for_typing_delay("你好")

    sleep_mock.assert_awaited_once_with(pytest.approx(0.5))


@pytest.mark.asyncio
async def test_send_text_skips_wait_when_typing_interval_has_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """距上次发送的间隔足够时不等待。"""
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    action = SendTextAction(
        chat_stream=stream,
        plugin=DefaultChatterPlugin(config=DefaultChatterConfig()),
    )
    setattr(stream.context, _LAST_SEND_TIME_ATTR, 100.0)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("plugins.default_chatter.components.actions.send_text.time.monotonic", lambda: 102.0)
    monkeypatch.setattr("plugins.default_chatter.components.actions.send_text.asyncio.sleep", sleep_mock)

    await action._sleep_for_typing_delay("你好")

    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_text_reply_to_uses_quoted_group_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    quoted = Message(
        message_id="msg_a",
        sender_id="user_a",
        sender_name="A",
        platform="qq",
        chat_type="group",
        stream_id="s_group",
        group_id="group_a",
        group_name="Group A",
    )
    later = Message(
        message_id="msg_b",
        sender_id="user_b",
        sender_name="B",
        platform="qq",
        chat_type="group",
        stream_id="s_group",
        group_id="group_b",
        group_name="Group B",
    )
    stream.context.history_messages = [quoted]
    stream.context.unread_messages = [later]

    sent: dict[str, Message] = {}
    monkeypatch.setattr(SendTextAction, "_sleep_for_typing_delay", AsyncMock())
    monkeypatch.setattr(
        "plugins.default_chatter.components.actions.send_text.get_bot_info_by_platform",
        AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.components.actions.send_text.send_message",
        AsyncMock(
            side_effect=lambda message: (sent.__setitem__("message", message), True)[1]
        ),
    )

    action = SendTextAction(
        chat_stream=stream,
        plugin=DefaultChatterPlugin(config=DefaultChatterConfig()),
    )
    success, _detail = await action._wrap_execute(
        content="reply",
        reply_to="msg_a",
    ).wait_done()

    assert success is True
    assert sent["message"].reply_to == "msg_a"
    assert sent["message"].extra["target_group_id"] == "group_a"
    assert sent["message"].extra["target_group_name"] == "Group A"


@pytest.mark.asyncio
async def test_send_text_reply_to_uses_quoted_private_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = ChatStream(stream_id="s_private", platform="qq", chat_type="private")
    quoted = Message(
        message_id="msg_a",
        sender_id="user_a",
        sender_name="A",
        platform="qq",
        chat_type="private",
        stream_id="s_private",
    )
    later = Message(
        message_id="msg_b",
        sender_id="user_b",
        sender_name="B",
        platform="qq",
        chat_type="private",
        stream_id="s_private",
    )
    stream.context.history_messages = [quoted]
    stream.context.unread_messages = [later]
    stream.context.triggering_user_id = "user_b"

    sent: dict[str, Message] = {}
    monkeypatch.setattr(SendTextAction, "_sleep_for_typing_delay", AsyncMock())
    monkeypatch.setattr(
        "plugins.default_chatter.components.actions.send_text.get_bot_info_by_platform",
        AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.components.actions.send_text.send_message",
        AsyncMock(
            side_effect=lambda message: (sent.__setitem__("message", message), True)[1]
        ),
    )

    action = SendTextAction(
        chat_stream=stream,
        plugin=DefaultChatterPlugin(config=DefaultChatterConfig()),
    )
    success, _detail = await action._wrap_execute(
        content="reply",
        reply_to="msg_a",
    ).wait_done()

    assert success is True
    assert sent["message"].reply_to == "msg_a"
    assert sent["message"].extra["target_user_id"] == "user_a"
    assert sent["message"].extra["target_user_name"] == "A"


@pytest.mark.asyncio
async def test_sub_agent_skips_programmatic_controller_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭程序化控制器后，群聊应始终回退到 decide_should_respond。"""
    chatter = _build_chatter_with_config({"enable_programmatic_controller": False})
    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_nickname="Neo",
    )
    setattr(stream.context, "_default_chatter_next_tick_bonus", 0.5)
    unread_msgs = [Message(content="Neo 你在吗", processed_plain_text="Neo 你在吗")]

    captured: dict[str, Any] = {}

    async def _fake_decide(**kwargs: Any) -> dict[str, object]:
        captured.update(kwargs)
        return {"reason": "llm only", "should_respond": False}

    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname="Neo",
                alias_names=["小狐狸"],
            )
        ),
    )
    monkeypatch.setattr("plugins.default_chatter.utils.interest_gate.decide_should_respond", _fake_decide)
    monkeypatch.setattr("plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.0)

    result = await chatter.sub_agent("group-msg", unread_msgs, stream)

    assert result == {"reason": "llm only", "should_respond": False}
    assert captured["chatter"] is chatter
    assert getattr(stream.context, "_default_chatter_next_tick_bonus", None) == 0.5


@pytest.mark.asyncio
async def test_send_text_does_not_mark_bonus_when_controller_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭程序化控制器后，send_text 不应写入下一 tick 加成。"""
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    plugin = DefaultChatterPlugin(
        config=DefaultChatterConfig.from_dict(
            {"plugin": {"enable_programmatic_controller": False}}
        )
    )
    action = SendTextAction(chat_stream=stream, plugin=plugin)

    monkeypatch.setattr(action, "_send_to_stream", AsyncMock(return_value=True))

    success, _detail = await action._wrap_execute(content="你好").wait_done()

    assert success is True
    assert getattr(stream.context, "_default_chatter_next_tick_bonus", None) in (None, 0.0)


@pytest.mark.asyncio
async def test_sub_agent_interest_only_skips_probability_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """interest_only 模式下不应触发概率直通，应走兴趣值判断。"""
    chatter = _build_chatter_with_config(
        {"enable_sub_agent": False, "enable_interest_filter": True, "enable_programmatic_controller": True}
    )
    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_nickname="Neo",
    )
    setattr(stream.context, "_default_chatter_next_tick_bonus", 0.5)
    unread_msgs = [Message(content="Neo 你在吗", processed_plain_text="Neo 你在吗")]

    probability_called = {"value": False}

    def _fail_bypass(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
        probability_called["value"] = True
        return False, "should not be consulted"

    async def _no_decide(**_kwargs: Any) -> dict[str, object]:
        raise AssertionError("decide_should_respond should not be called in interest_only")

    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname="Neo",
                alias_names=["小狐狸"],
            )
        ),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.interest_gate.should_bypass_via_probability",
        _fail_bypass,
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.interest_gate.decide_should_respond", _no_decide
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.0
    )

    result = await chatter.sub_agent("group-msg", unread_msgs, stream)

    assert probability_called["value"] is False
    assert result.get("source") == "interest"
    assert result["should_respond"] is False
    assert getattr(stream.context, "_default_chatter_next_tick_bonus", None) == 0.5


@pytest.mark.asyncio
async def test_sub_agent_interest_then_sub_uses_probability_gate_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """interest_then_sub 模式下概率直通作为最前置门控，命中时直接放行。"""
    chatter = _build_chatter_with_config(
        {"enable_sub_agent": True, "enable_interest_filter": True, "enable_programmatic_controller": True}
    )
    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_nickname="Neo",
    )
    setattr(stream.context, "_default_chatter_next_tick_bonus", 0.5)
    unread_msgs = [Message(content="hello", processed_plain_text="hello")]

    async def _no_decide(**_kwargs: Any) -> dict[str, object]:
        raise AssertionError("decide_should_respond should not be called when probability bypasses")

    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname="Neo",
                alias_names=["小狐狸"],
            )
        ),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.interest_gate.decide_should_respond", _no_decide
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.0
    )

    result = await chatter.sub_agent("group-msg", unread_msgs, stream)

    assert result["should_respond"] is True
    assert result.get("source") == "probability"
    assert "概率直通响应" in result["reason"]


@pytest.mark.asyncio
async def test_sub_agent_interest_then_sub_falls_through_to_interest_when_probability_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """interest_then_sub 模式下概率直通未命中时，应继续走兴趣值初筛。"""
    chatter = _build_chatter_with_config(
        {"enable_sub_agent": True, "enable_interest_filter": True, "enable_programmatic_controller": True}
    )
    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_nickname="Neo",
    )
    setattr(stream.context, "_default_chatter_next_tick_bonus", 0.5)
    unread_msgs = [Message(content="hello", processed_plain_text="hello")]

    async def _no_decide(**_kwargs: Any) -> dict[str, object]:
        raise AssertionError("decide_should_respond should not be called when interest filter rejects")

    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname="Neo",
                alias_names=["小狐狸"],
            )
        ),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.interest_gate.decide_should_respond", _no_decide
    )
    # random.random() 返回 0.99 > base_bypass_probability(0.1) → 概率直通未命中
    # 不 mock should_bypass_via_probability，让真实逻辑运行以消耗 next_tick_bonus
    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.99
    )

    result = await chatter.sub_agent("group-msg", unread_msgs, stream)

    assert result.get("source") == "interest"
    assert result["should_respond"] is False
    # 概率门真实执行时会 consume next_tick_bonus，即使未命中也会消耗
    assert getattr(stream.context, "_default_chatter_next_tick_bonus", None) == 0.0


@pytest.mark.asyncio
async def test_sub_agent_sub_only_still_uses_probability_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sub_only 模式下概率直通仍应生效。"""
    chatter = _build_chatter_with_config(
        {"enable_sub_agent": True, "enable_interest_filter": False, "enable_programmatic_controller": True}
    )
    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_nickname="Neo",
    )
    setattr(stream.context, "_default_chatter_next_tick_bonus", 0.5)
    unread_msgs = [Message(content="Neo 你在吗", processed_plain_text="Neo 你在吗")]

    async def _no_decide(**_kwargs: Any) -> dict[str, object]:
        raise AssertionError("decide_should_respond should not be called when probability bypasses")

    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname="Neo",
                alias_names=["小狐狸"],
            )
        ),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.interest_gate.decide_should_respond", _no_decide
    )
    monkeypatch.setattr(
        "plugins.default_chatter.utils.probability_gate.random.random", lambda: 0.0
    )

    result = await chatter.sub_agent("group-msg", unread_msgs, stream)

    assert result["should_respond"] is True
    assert result.get("source") == "probability"
    assert "概率直通响应" in result["reason"]
