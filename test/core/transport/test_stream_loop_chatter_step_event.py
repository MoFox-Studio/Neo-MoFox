from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.core.components.base.chatter import Success
from src.core.transport.distribution.loop import run_chat_stream
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager


async def _two_ticks(*_args, **_kwargs):
    """产出两个 Tick 供 run_chat_stream 消费。"""
    yield SimpleNamespace(stream_id="stream-001", tick_count=1)
    yield SimpleNamespace(stream_id="stream-001", tick_count=2)


@pytest.mark.asyncio
async def test_on_chatter_step_continue_false_skips_current_tick_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """continue=False 仅跳过当前 Tick，下一 Tick 允许继续执行。"""
    stream_id = "stream-001"

    message = SimpleNamespace(sender_id="u1")
    context = SimpleNamespace(
        unread_messages=[message],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    step_call_count = 0

    async def chatter_generator():
        nonlocal step_call_count
        while True:
            step_call_count += 1
            yield Success(message="ok")

    chatter = SimpleNamespace(execute=lambda: chatter_generator())
    chatter_manager = SimpleNamespace(
        get_chatter_by_stream=lambda _sid: chatter,
        get_or_create_chatter_for_stream=lambda *_args, **_kwargs: chatter,
    )

    publish_event_mock = AsyncMock(
        side_effect=[
            {
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                    "chatter_gene": None,
                    "continue": False,
                },
            },
            {
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=2),
                    "chatter_gene": None,
                    "continue": True,
                },
            },
        ]
    )
    event_manager = SimpleNamespace(publish_event=publish_event_mock)

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _two_ticks)
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: chatter_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.get_event_manager",
        lambda: event_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(
            feed_dog=lambda stream_id: None,
            unregister_stream=lambda stream_id: None,
        ),
    )

    async def _get_context(_stream_id: str):
        return context

    manager = cast(
        StreamLoopManager,
        SimpleNamespace(
            is_running=True,
            _chatter_genes={},
            _wait_states={},
            _stats={"total_failures": 0, "total_process_cycles": 0},
            _get_stream_context=_get_context,
            _flush_cached_messages_to_unread=AsyncMock(return_value=[]),
            _wait_state_check=lambda _stream_id, _context: True,
            _message_buffer_check=lambda _stream_id, _context: True,
        ),
    )

    await run_chat_stream(stream_id=stream_id, manager=manager)

    assert publish_event_mock.await_count == 2
    assert step_call_count == 1
    assert manager._stats["total_process_cycles"] == 1
    assert manager._stats["total_failures"] == 0
