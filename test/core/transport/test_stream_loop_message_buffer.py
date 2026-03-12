"""Stream loop message buffer regression tests.

这些测试覆盖“消息缓冲机制”在高压消息输入下的正确行为：
- `_message_buffer_check` 必须能在连续跳过达到上限后强制放行
- 新消息到达只应更新时间戳，不应把 skip_count 重置为 0（否则会无限跳过）
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.core.transport.distribution.distributor import _on_message_received
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager
from src.core.models.stream import StreamContext


def _fake_core_config(*, window: float, max_skip: int) -> SimpleNamespace:
    """构造最小 core_config 替身。"""

    return SimpleNamespace(bot=SimpleNamespace(message_buffer_window=window, message_buffer_max_skip=max_skip))


def test_message_buffer_check_forces_release_under_high_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续消息输入下，缓冲最多跳过 max_skip 次后必须强制放行。"""

    # buffer_window 足够大，确保 elapsed 一直 < window
    monkeypatch.setattr(
        "src.core.config.get_core_config",
        lambda: _fake_core_config(window=60.0, max_skip=3),
    )

    manager = StreamLoopManager()
    context = StreamContext(stream_id="stream")
    context.last_message_time = time.time()
    context.message_buffer_skip_count = 0

    # Tick 1..3：仍处于缓冲窗口内，应跳过并累积 skip_count
    assert manager._message_buffer_check("stream", context) is False
    assert context.message_buffer_skip_count == 1

    # 模拟“新消息到达”：更新时间戳，但不重置 skip_count
    context.last_message_time = time.time()
    assert manager._message_buffer_check("stream", context) is False
    assert context.message_buffer_skip_count == 2

    context.last_message_time = time.time()
    assert manager._message_buffer_check("stream", context) is False
    assert context.message_buffer_skip_count == 3

    # 再来一次 Tick：达到上限后必须强制放行并重置
    context.last_message_time = time.time()
    assert manager._message_buffer_check("stream", context) is True
    assert context.message_buffer_skip_count == 0


@pytest.mark.asyncio
async def test_distributor_does_not_reset_message_buffer_skip_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """收到新消息时不应清零 skip_count，否则高压群聊会无限缓冲。"""

    # 准备 chat_stream/context 替身
    context = SimpleNamespace(last_message_time=None, message_buffer_skip_count=2, stream_loop_task=None)
    chat_stream = SimpleNamespace(stream_id="stream-001", context=context)

    # 伪造 StreamManager
    fake_sm = SimpleNamespace(
        get_or_create_stream=lambda **_kwargs: chat_stream,  # type: ignore[assignment]
        add_message=lambda _msg: None,  # type: ignore[assignment]
    )

    async def _async_get_or_create_stream(**_kwargs):
        return chat_stream

    async def _async_add_message(_msg):
        return None

    fake_sm.get_or_create_stream = _async_get_or_create_stream
    fake_sm.add_message = _async_add_message

    # 伪造 StreamLoopManager
    fake_slm = SimpleNamespace(is_running=True, start_stream_loop=lambda _sid: None)

    async def _async_start_stream_loop(_sid: str) -> None:
        return None

    fake_slm.start_stream_loop = _async_start_stream_loop

    monkeypatch.setattr("src.core.managers.stream_manager.get_stream_manager", lambda: fake_sm)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: fake_slm,
    )

    # 构造最小 Message
    message = SimpleNamespace(
        platform="test",
        stream_id="stream-001",
        chat_type="group",
        sender_id="u1",
        sender_name="U",
        sender_cardname="",
        extra={"group_id": "g1", "group_name": "G"},
    )

    # 执行分发
    await _on_message_received("ON_MESSAGE_RECEIVED", {"message": message})

    # 验证：skip_count 未被清零，但时间戳被更新
    assert context.message_buffer_skip_count == 2
    assert isinstance(context.last_message_time, float)
