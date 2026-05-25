from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.core.components.base.chatter import Success, Wait, WaitResumeEvent
from src.core.components.types import EventType
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

    async def _publish_event(event_name: object, params: dict[str, object]) -> dict[str, object]:
        if event_name == EventType.ON_CHATTER_STEP:
            tick = cast(SimpleNamespace, params["tick"])
            if tick.tick_count == 1:
                return {
                    "decision": "SUCCESS",
                    "params": {
                        "stream_id": stream_id,
                        "context": context,
                        "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                        "chatter_gene": None,
                        "continue": False,
                    },
                }
            return {
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=2),
                    "chatter_gene": None,
                    "continue": True,
                },
            }

        return {
            "decision": "SUCCESS",
            "params": params,
        }

    publish_event_mock = AsyncMock(side_effect=_publish_event)
    event_manager = SimpleNamespace(publish_event=publish_event_mock)

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _two_ticks)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=60.0)),
    )
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
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
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

    assert publish_event_mock.await_count == 3
    assert step_call_count == 1
    assert manager._stats["total_process_cycles"] == 1
    assert manager._stats["total_failures"] == 0


@pytest.mark.asyncio
async def test_run_chat_stream_times_out_stuck_chatter_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chatter 单步卡住时，应由步骤级超时打断并清理生成器状态。"""
    stream_id = "stream-timeout"

    async def _one_tick(*_args, **_kwargs):
        yield SimpleNamespace(stream_id=stream_id, tick_count=1)

    message = SimpleNamespace(sender_id="u1")
    context = SimpleNamespace(
        unread_messages=[message],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    async def chatter_generator():
        await asyncio.Future()
        yield Success(message="never")

    chatter = SimpleNamespace(execute=lambda: chatter_generator())
    chatter_manager = SimpleNamespace(
        get_chatter_by_stream=lambda _sid: chatter,
        get_or_create_chatter_for_stream=lambda *_args, **_kwargs: chatter,
    )
    event_manager = SimpleNamespace(
        publish_event=AsyncMock(
            return_value={
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                    "chatter_gene": None,
                    "continue": True,
                },
            }
        )
    )

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _one_tick)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=0.01)),
    )
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
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
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

    await asyncio.wait_for(run_chat_stream(stream_id=stream_id, manager=manager), timeout=0.2)

    assert manager._stats["total_failures"] == 1
    assert manager._stats["total_process_cycles"] == 0
    assert manager._chatter_genes == {}
    assert context.is_chatter_processing is False


@pytest.mark.asyncio
async def test_run_chat_stream_sends_timer_resume_event_to_waiting_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wait(seconds) 到期后，驱动器应通过 asend 将 timer 恢复事件送回生成器。"""
    stream_id = "stream-wait-resume"
    received_events: list[WaitResumeEvent | None] = []

    async def _one_tick(*_args, **_kwargs):
        yield SimpleNamespace(stream_id=stream_id, tick_count=1)

    async def chatter_generator():
        resume_event = yield Wait(time=0.0)
        received_events.append(resume_event)
        yield Success(message="ok")

    chatter_gene = chatter_generator()
    first_wait = await anext(chatter_gene)
    assert isinstance(first_wait, Wait)

    context = SimpleNamespace(
        unread_messages=[],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    event_manager = SimpleNamespace(
        publish_event=AsyncMock(
            return_value={
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                    "chatter_gene": chatter_gene,
                    "continue": True,
                },
            }
        )
    )

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _one_tick)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=60.0)),
    )
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "src.core.managers.get_event_manager",
        lambda: event_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(
            feed_dog=lambda stream_id=None, **_kwargs: None,
            unregister_stream=lambda stream_id=None, **_kwargs: None,
        ),
    )

    manager = StreamLoopManager()
    manager.is_running = True
    manager._chatter_genes[stream_id] = chatter_gene
    manager._wait_states[stream_id] = (first_wait, 0.0, 0)

    async def _get_context(_stream_id: str):
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
        return context

    manager._get_stream_context = _get_context  # type: ignore[method-assign]
    manager._flush_cached_messages_to_unread = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._message_buffer_check = lambda _stream_id, _context: True  # type: ignore[method-assign]

    await run_chat_stream(stream_id=stream_id, manager=manager)

    assert len(received_events) == 1
    assert received_events[0] is not None
    assert received_events[0].source == "timer"
    assert manager._stats["total_process_cycles"] == 1


@pytest.mark.asyncio
async def test_run_chat_stream_keeps_timer_resume_event_across_message_buffer_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timer 恢复事件不应在消息缓冲跳过当前 tick 时丢失。"""
    stream_id = "stream-wait-buffered-resume"
    received_events: list[WaitResumeEvent | None] = []

    async def _two_ticks(*_args, **_kwargs):
        yield SimpleNamespace(stream_id=stream_id, tick_count=1)
        yield SimpleNamespace(stream_id=stream_id, tick_count=2)

    async def chatter_generator():
        resume_event = yield Wait(time=0.0)
        received_events.append(resume_event)
        yield Success(message="ok")

    chatter_gene = chatter_generator()
    first_wait = await anext(chatter_gene)
    assert isinstance(first_wait, Wait)

    context = SimpleNamespace(
        unread_messages=[],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    event_manager = SimpleNamespace(
        publish_event=AsyncMock(
            return_value={
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=2),
                    "chatter_gene": chatter_gene,
                    "continue": True,
                },
            }
        )
    )

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _two_ticks)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=60.0)),
    )
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "src.core.managers.get_event_manager",
        lambda: event_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(
            feed_dog=lambda stream_id=None, **_kwargs: None,
            unregister_stream=lambda stream_id=None, **_kwargs: None,
        ),
    )

    manager = StreamLoopManager()
    manager.is_running = True
    manager._chatter_genes[stream_id] = chatter_gene
    manager._wait_states[stream_id] = (first_wait, 0.0, 0)

    async def _get_context(_stream_id: str):
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
        return context

    buffer_results = iter([False, True])

    manager._get_stream_context = _get_context  # type: ignore[method-assign]
    manager._flush_cached_messages_to_unread = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._message_buffer_check = lambda _stream_id, _context: next(buffer_results)  # type: ignore[method-assign]

    await run_chat_stream(stream_id=stream_id, manager=manager)

    assert len(received_events) == 1
    assert received_events[0] is not None
    assert received_events[0].source == "timer"
    assert manager._stats["total_process_cycles"] == 1


@pytest.mark.asyncio
async def test_run_chat_stream_prioritizes_pending_sub_agent_resume_over_wait_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pending 的子代理恢复事件不应被随后写入的 Wait 状态挡住。"""
    stream_id = "stream-sub-agent-race"
    received_events: list[WaitResumeEvent | None] = []

    async def _one_tick(*_args, **_kwargs):
        yield SimpleNamespace(stream_id=stream_id, tick_count=1)

    async def chatter_generator():
        resume_event = yield Wait(time=None)
        received_events.append(resume_event)
        yield Success(message="ok")

    chatter_gene = chatter_generator()
    first_wait = await anext(chatter_gene)
    assert isinstance(first_wait, Wait)

    context = SimpleNamespace(
        unread_messages=[],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    event_manager = SimpleNamespace(
        publish_event=AsyncMock(
            return_value={
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                    "chatter_gene": chatter_gene,
                    "continue": True,
                },
            }
        )
    )

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _one_tick)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=60.0)),
    )
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "src.core.managers.get_event_manager",
        lambda: event_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(
            feed_dog=lambda stream_id=None, **_kwargs: None,
            unregister_stream=lambda stream_id=None, **_kwargs: None,
        ),
    )

    manager = StreamLoopManager()
    manager.is_running = True
    manager._chatter_genes[stream_id] = chatter_gene
    manager._wait_states[stream_id] = (first_wait, time.time(), 0)
    manager._pending_wait_resume_events[stream_id] = WaitResumeEvent(source="sub_agent")

    async def _get_context(_stream_id: str):
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
        return context

    manager._get_stream_context = _get_context  # type: ignore[method-assign]
    manager._flush_cached_messages_to_unread = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._message_buffer_check = lambda _stream_id, _context: True  # type: ignore[method-assign]

    await run_chat_stream(stream_id=stream_id, manager=manager)

    assert received_events == [WaitResumeEvent(source="sub_agent")]
    assert manager._stats["total_process_cycles"] == 1


@pytest.mark.asyncio
async def test_run_chat_stream_primes_new_generator_before_resume_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新建生成器遇到 pending resume_event 时应先预激，再发送恢复事件。"""
    stream_id = "stream-new-generator-resume"
    received_events: list[WaitResumeEvent | None] = []

    async def _one_tick(*_args, **_kwargs):
        yield SimpleNamespace(stream_id=stream_id, tick_count=1)

    async def chatter_generator():
        resume_event = yield Wait(time=0.0)
        received_events.append(resume_event)
        yield Success(message="ok")

    chatter = SimpleNamespace(
        execute=lambda: chatter_generator(),
    )

    context = SimpleNamespace(
        unread_messages=[SimpleNamespace(sender_id="user-1")],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    event_manager = SimpleNamespace(
        publish_event=AsyncMock(
            return_value={
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                    "chatter_gene": None,
                    "continue": True,
                },
            }
        )
    )

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _one_tick)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=60.0)),
    )
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: SimpleNamespace(
            get_chatter_by_stream=lambda _stream_id: chatter,
            get_or_create_chatter_for_stream=lambda *_args, **_kwargs: chatter,
        ),
    )
    monkeypatch.setattr(
        "src.core.managers.get_event_manager",
        lambda: event_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(
            feed_dog=lambda stream_id=None, **_kwargs: None,
            unregister_stream=lambda stream_id=None, **_kwargs: None,
        ),
    )

    manager = StreamLoopManager()
    manager.is_running = True
    manager._pending_wait_resume_events[stream_id] = WaitResumeEvent(source="timer", wait_time=0.0)

    async def _get_context(_stream_id: str):
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
        return context

    manager._get_stream_context = _get_context  # type: ignore[method-assign]
    manager._flush_cached_messages_to_unread = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._message_buffer_check = lambda _stream_id, _context: True  # type: ignore[method-assign]

    await run_chat_stream(stream_id=stream_id, manager=manager)

    assert received_events == [WaitResumeEvent(source="timer", wait_time=0.0)]
    assert manager._stats["total_failures"] == 0
    assert manager._stats["total_process_cycles"] == 1


@pytest.mark.asyncio
async def test_after_chatter_step_is_published_after_result_without_affecting_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AFTER_CHATTER_STEP 应为执行后通报，且订阅者返回不得影响步进结果。"""
    stream_id = "stream-after-step"

    async def _one_tick(*_args, **_kwargs):
        yield SimpleNamespace(stream_id=stream_id, tick_count=1)

    message = SimpleNamespace(sender_id="u1")
    context = SimpleNamespace(
        unread_messages=[message],
        is_chatter_processing=False,
        triggering_user_id=None,
        stream_loop_task=None,
    )

    async def chatter_generator():
        yield Wait(
            time=6.0,
            step_data={
                "step_scope": "actor_round",
                "used_tools": ["memory_command"],
            },
        )

    chatter = SimpleNamespace(
        chatter_name="default_chatter",
        execute=lambda: chatter_generator(),
    )
    chatter_manager = SimpleNamespace(
        get_chatter_by_stream=lambda _sid: chatter,
        get_or_create_chatter_for_stream=lambda *_args, **_kwargs: chatter,
    )

    publish_calls: list[tuple[object, dict[str, object]]] = []

    async def _publish_event(event_name: object, params: dict[str, object]) -> dict[str, object]:
        publish_calls.append((event_name, params))
        if event_name == EventType.ON_CHATTER_STEP:
            return {
                "decision": "SUCCESS",
                "params": {
                    "stream_id": stream_id,
                    "context": context,
                    "tick": SimpleNamespace(stream_id=stream_id, tick_count=1),
                    "chatter_gene": None,
                    "continue": True,
                },
            }
        return {
            "decision": "STOP",
            "params": {
                **params,
                "continue": False,
            },
        }

    event_manager = SimpleNamespace(publish_event=AsyncMock(side_effect=_publish_event))

    monkeypatch.setattr("src.core.transport.distribution.loop.conversation_loop", _one_tick)
    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_core_config",
        lambda: SimpleNamespace(bot=SimpleNamespace(stream_step_timeout=60.0)),
    )
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
        if context.stream_loop_task is None:
            context.stream_loop_task = asyncio.current_task()
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

    assert len(publish_calls) == 2
    after_event_name, after_params = publish_calls[1]
    assert after_event_name == EventType.AFTER_CHATTER_STEP
    assert after_params["result_type"] == "wait"
    assert after_params["step_scope"] == "actor_round"
    assert after_params["used_tools"] == ["memory_command"]
    assert stream_id in manager._wait_states
    assert manager._stats["total_process_cycles"] == 1
