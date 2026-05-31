from __future__ import annotations

import time
from types import SimpleNamespace
from typing import cast

from src.core.models.message import Message
from src.core.models.stream import StreamContext
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager
from src.core.components.base.chatter import Wait, Stop, WaitResumeEvent


def test_wait_state_check_requires_new_message_after_stop() -> None:
    """Stop 状态应在冷却后仅被“新未读消息”唤醒。"""
    manager = StreamLoopManager()
    stream_id = "stream-stop-check"

    manager._wait_states[stream_id] = (Stop(time=0.0), 0.0, 2)

    # 冷却时间已过，但没有新消息（仍是 2 条）
    context_same = StreamContext(stream_id=stream_id)
    context_same.unread_messages = cast(list, [1, 2])
    assert manager._wait_state_check(stream_id, context_same) is False

    # 出现新消息（从 2 -> 3）后才恢复
    context_new = StreamContext(stream_id=stream_id)
    context_new.unread_messages = cast(list, [1, 2, 3])
    assert manager._wait_state_check(stream_id, context_new) is True


def test_wait_state_check_stop_direct_message_wake_disabled() -> None:
    """Stop 直接唤醒机制默认应由配置显式开启。"""
    manager = StreamLoopManager()
    stream_id = "stream-stop-direct-disabled"

    manager._wait_states[stream_id] = (
        Stop(
            time=3600.0,
            direct_message_wake_enabled=False,
            direct_message_wake_probability=1.0,
        ),
        time.time(),
        0,
    )

    context = StreamContext(stream_id=stream_id, chat_type="private")
    context.unread_messages = [Message(content="hello", chat_type="private")]

    assert manager._wait_state_check(stream_id, context) is False


def test_wait_state_check_stop_direct_message_wakes_private(
    monkeypatch,
) -> None:
    """启用后，私聊消息可在冷却结束前唤醒 Stop。"""
    manager = StreamLoopManager()
    stream_id = "stream-stop-private-wake"

    manager._wait_states[stream_id] = (
        Stop(
            time=3600.0,
            direct_message_wake_enabled=True,
            direct_message_wake_probability=1.0,
        ),
        time.time(),
        0,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.random.random",
        lambda: 0.0,
    )

    context = StreamContext(stream_id=stream_id, chat_type="private")
    context.unread_messages = [Message(content="hello", chat_type="private")]

    assert manager._wait_state_check(stream_id, context) is True


def test_wait_state_check_stop_direct_message_wakes_bot_mention(
    monkeypatch,
) -> None:
    """启用后，@Bot 消息可在冷却结束前唤醒 Stop。"""
    manager = StreamLoopManager()
    stream_id = "stream-stop-at-wake"

    manager._wait_states[stream_id] = (
        Stop(
            time=3600.0,
            direct_message_wake_enabled=True,
            direct_message_wake_probability=1.0,
        ),
        time.time(),
        0,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.random.random",
        lambda: 0.0,
    )

    context = StreamContext(stream_id=stream_id, chat_type="group")
    context.unread_messages = [
        Message(
            content="@<Neo:10001> hello",
            processed_plain_text="@<Neo:10001> hello",
            chat_type="group",
            raw_data={"self_id": "10001"},
            at_users=[{"nickname": "Neo", "user_id": "10001"}],
        )
    ]

    assert manager._wait_state_check(stream_id, context) is True


def test_wait_state_check_stop_direct_message_wakes_bot_mention_without_raw_self_id(
    monkeypatch,
) -> None:
    """raw_data 缺少 self_id 时，应回退到流上的 bot_id 判断 @Bot。"""
    manager = StreamLoopManager()
    stream_id = "stream-stop-at-wake-fallback"

    manager._wait_states[stream_id] = (
        Stop(
            time=3600.0,
            direct_message_wake_enabled=True,
            direct_message_wake_probability=1.0,
        ),
        time.time(),
        0,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.random.random",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: SimpleNamespace(
            _streams={stream_id: SimpleNamespace(bot_id="10001")}
        ),
    )

    context = StreamContext(stream_id=stream_id, chat_type="group")
    context.unread_messages = [
        Message(
            content="@<Neo:10001> hello",
            processed_plain_text="@<Neo:10001> hello",
            chat_type="group",
            stream_id=stream_id,
            raw_data=None,
            at_users=[{"nickname": "Neo", "user_id": "10001"}],
        )
    ]

    assert manager._wait_state_check(stream_id, context) is True


def test_wait_state_check_stop_direct_message_respects_probability(
    monkeypatch,
) -> None:
    """概率未命中时，直接消息不应唤醒 Stop。"""
    manager = StreamLoopManager()
    stream_id = "stream-stop-private-probability"

    manager._wait_states[stream_id] = (
        Stop(
            time=3600.0,
            direct_message_wake_enabled=True,
            direct_message_wake_probability=0.5,
        ),
        time.time(),
        0,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.random.random",
        lambda: 0.99,
    )

    context = StreamContext(stream_id=stream_id, chat_type="private")
    context.unread_messages = [Message(content="hello", chat_type="private")]

    assert manager._wait_state_check(stream_id, context) is False


def test_wait_state_check_wait_for_messages_only() -> None:
    """Wait(None) 语义：仅有新未读时恢复。"""
    manager = StreamLoopManager()
    stream_id = "stream-wait-msg"

    manager._wait_states[stream_id] = (Wait(time=None), time.time(), 0)

    context_empty = StreamContext(stream_id=stream_id)
    assert manager._wait_state_check(stream_id, context_empty) is False

    context_non_empty = StreamContext(stream_id=stream_id)
    context_non_empty.unread_messages = cast(list, ["m1"])
    assert manager._wait_state_check(stream_id, context_non_empty) is True


def test_wait_state_check_wait_for_time_or_new_message() -> None:
    """Wait(seconds) 语义：仅时间到达即可恢复。"""
    manager = StreamLoopManager()
    stream_id = "stream-wait-time"

    manager._wait_states[stream_id] = (Wait(time=4102444800.0), 0.0, 0)  # 2100-01-01

    context_with_unread = StreamContext(stream_id=stream_id)
    context_with_unread.unread_messages = cast(list, ["m1"])
    assert manager._wait_state_check(stream_id, context_with_unread) is True
    assert manager.take_wait_resume_event(stream_id) == WaitResumeEvent(
        source="message",
        wait_time=4102444800.0,
        unread_count=1,
    )

    manager._wait_states[stream_id] = (Wait(time=0.0), time.time(), 0)
    context_any = StreamContext(stream_id=stream_id)
    assert manager._wait_state_check(stream_id, context_any) is True
    assert manager.take_wait_resume_event(stream_id) == WaitResumeEvent(
        source="timer",
        wait_time=0.0,
        unread_count=0,
    )
