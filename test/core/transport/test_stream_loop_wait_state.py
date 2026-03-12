from __future__ import annotations

import time
from typing import cast

from src.core.models.stream import StreamContext
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager
from src.core.components.base.chatter import Wait, Stop


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


def test_wait_state_check_wait_for_time_only() -> None:
    """Wait(seconds) 语义：仅时间到达即可恢复。"""
    manager = StreamLoopManager()
    stream_id = "stream-wait-time"

    manager._wait_states[stream_id] = (Wait(time=4102444800.0), 0.0, 0)  # 2100-01-01

    context_any = StreamContext(stream_id=stream_id)
    assert manager._wait_state_check(stream_id, context_any) is False

    manager._wait_states[stream_id] = (Wait(time=0.0), time.time(), 0)
    assert manager._wait_state_check(stream_id, context_any) is True
