from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.transport.distribution.loop import run_chat_stream
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager


@pytest.mark.asyncio
async def test_start_stream_loop_registers_watchdog_with_exact_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动流循环时应使用精确秒级阈值，并注入可调用的重启回调。"""
    manager = StreamLoopManager()
    stream_id = "stream_watchdog_threshold"

    async def dummy_runner(_stream_id: str, _manager: StreamLoopManager) -> None:
        await asyncio.sleep(0.01)

    context = SimpleNamespace(stream_loop_task=None)
    manager._get_stream_context = AsyncMock(return_value=context)

    monkeypatch.setattr(
        "src.core.transport.distribution.loop.run_chat_stream",
        dummy_runner,
    )
    monkeypatch.setattr(
        "src.core.config.get_core_config",
        lambda: SimpleNamespace(
            bot=SimpleNamespace(
                tick_interval=30.0,
                stream_warning_threshold=150.0,
                stream_restart_threshold=300.0,
            )
        ),
    )

    registered_kwargs: dict[str, object] = {}

    fake_watchdog = SimpleNamespace(
        register_stream=lambda **kwargs: registered_kwargs.update(kwargs)
    )

    class FakeTaskInfo:
        def __init__(self, task: asyncio.Task[None]) -> None:
            self.task = task

    class FakeTaskManager:
        def create_task(self, coro, name: str, daemon: bool):
            assert isinstance(name, str)
            assert daemon is True
            return FakeTaskInfo(asyncio.create_task(coro))

    monkeypatch.setattr("src.kernel.concurrency.get_watchdog", lambda: fake_watchdog)
    monkeypatch.setattr("src.kernel.concurrency.get_task_manager", lambda: FakeTaskManager())

    started = await manager.start_stream_loop(stream_id)

    assert started is True
    assert registered_kwargs["stream_id"] == stream_id
    assert registered_kwargs["tick_interval"] == 30.0
    assert registered_kwargs["warning_threshold"] == 150.0
    assert registered_kwargs["restart_threshold"] == 300.0
    assert registered_kwargs["restart_cooldown"] == 30.0
    assert callable(registered_kwargs["restart_callback"])

    task = context.stream_loop_task
    if task and not task.done():
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_run_chat_stream_unregisters_watchdog_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """流驱动器退出时应注销 WatchDog 心跳注册。"""
    stream_id = "stream_unregister_on_exit"
    unregister_mock = MagicMock()

    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(unregister_stream=unregister_mock, feed_dog=lambda stream_id: None),
    )
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: SimpleNamespace(),
    )

    context = SimpleNamespace(stream_loop_task=None)

    async def get_context(_stream_id: str):
        context.stream_loop_task = asyncio.current_task()
        return context

    manager = cast(
        StreamLoopManager,
        SimpleNamespace(
        is_running=False,
        _chatter_genes={},
        _stats={"total_failures": 0, "total_process_cycles": 0},
        _get_stream_context=get_context,
        _flush_cached_messages_to_unread=AsyncMock(return_value=[]),
        _wait_state_check=lambda _stream_id, _context: True,
        ),
    )

    await run_chat_stream(stream_id=stream_id, manager=manager)

    unregister_mock.assert_called_once_with(stream_id=stream_id)


@pytest.mark.asyncio
async def test_watchdog_restart_callback_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """WatchDog 高频触发重启回调时，应被管理器冷却窗口抑制。"""
    manager = StreamLoopManager()
    stream_id = "stream_watchdog_throttle"

    async def dummy_runner(_stream_id: str, _manager: StreamLoopManager) -> None:
        await asyncio.sleep(0.05)

    context = SimpleNamespace(stream_loop_task=None)
    manager._get_stream_context = AsyncMock(return_value=context)

    monkeypatch.setattr(
        "src.core.transport.distribution.loop.run_chat_stream",
        dummy_runner,
    )
    monkeypatch.setattr(
        "src.core.config.get_core_config",
        lambda: SimpleNamespace(
            bot=SimpleNamespace(
                tick_interval=1.0,
                stream_warning_threshold=2.0,
                stream_restart_threshold=5.0,
            )
        ),
    )

    registered_kwargs: dict[str, object] = {}
    fake_watchdog = SimpleNamespace(
        register_stream=lambda **kwargs: registered_kwargs.update(kwargs)
    )

    class FakeTaskInfo:
        def __init__(self, task: asyncio.Task[None]) -> None:
            self.task = task

    class FakeTaskManager:
        def create_task(self, coro, name: str, daemon: bool):
            assert isinstance(name, str)
            assert daemon is True
            return FakeTaskInfo(asyncio.create_task(coro))

    monkeypatch.setattr("src.kernel.concurrency.get_watchdog", lambda: fake_watchdog)
    monkeypatch.setattr("src.kernel.concurrency.get_task_manager", lambda: FakeTaskManager())

    manager.restart_stream_loop = AsyncMock(return_value=True)

    started = await manager.start_stream_loop(stream_id)
    assert started is True

    restart_cb = registered_kwargs["restart_callback"]
    assert callable(restart_cb)
    restart_cb()
    restart_cb()
    restart_cb()

    await asyncio.sleep(0.05)

    assert manager.restart_stream_loop.await_count == 1

    task = context.stream_loop_task
    if task and not task.done():
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_force_restart_does_not_wait_for_stuck_old_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制重启应立即切换到新任务，而不是卡在已取消但不退出的旧任务上。"""
    manager = StreamLoopManager()
    stream_id = "stream_force_restart"

    release_old_task = asyncio.Event()

    async def stuck_runner() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release_old_task.wait()
            raise

    started_new_runner = asyncio.Event()

    async def dummy_runner(_stream_id: str, _manager: StreamLoopManager) -> None:
        started_new_runner.set()
        await asyncio.sleep(0.01)

    async def stale_generator() -> AsyncGenerator[object, None]:
        yield object()

    old_task = asyncio.create_task(stuck_runner())
    context = SimpleNamespace(
        stream_loop_task=old_task,
        is_chatter_processing=True,
    )
    manager._get_stream_context = AsyncMock(return_value=context)
    manager._chatter_genes[stream_id] = stale_generator()
    manager._wait_states[stream_id] = (object(), 0.0, 0)

    monkeypatch.setattr(
        "src.core.transport.distribution.loop.run_chat_stream",
        dummy_runner,
    )
    monkeypatch.setattr(
        "src.core.config.get_core_config",
        lambda: SimpleNamespace(
            bot=SimpleNamespace(
                tick_interval=1.0,
                stream_warning_threshold=2.0,
                stream_restart_threshold=5.0,
            )
        ),
    )

    fake_watchdog = SimpleNamespace(register_stream=lambda **kwargs: None)

    class FakeTaskInfo:
        def __init__(self, task: asyncio.Task[None]) -> None:
            self.task = task

    class FakeTaskManager:
        def create_task(self, coro, name: str, daemon: bool):
            assert isinstance(name, str)
            assert daemon is True
            return FakeTaskInfo(asyncio.create_task(coro))

    monkeypatch.setattr("src.kernel.concurrency.get_watchdog", lambda: fake_watchdog)
    monkeypatch.setattr("src.kernel.concurrency.get_task_manager", lambda: FakeTaskManager())

    started = await asyncio.wait_for(manager.start_stream_loop(stream_id, force=True), timeout=0.2)

    assert started is True
    assert context.stream_loop_task is not old_task
    assert stream_id not in manager._chatter_genes
    assert stream_id not in manager._wait_states
    assert context.is_chatter_processing is False

    await asyncio.wait_for(started_new_runner.wait(), timeout=0.2)

    release_old_task.set()
    with pytest.raises(asyncio.CancelledError):
        await old_task

    new_task = context.stream_loop_task
    assert new_task is not None
    await new_task


@pytest.mark.asyncio
async def test_run_chat_stream_does_not_cleanup_new_task_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧任务退出时，不应清理已被新任务接管的 context.stream_loop_task/WatchDog 注册。"""
    stream_id = "stream_cleanup_race"
    unregister_mock = MagicMock()

    monkeypatch.setattr(
        "src.core.transport.distribution.loop.get_watchdog",
        lambda: SimpleNamespace(unregister_stream=unregister_mock, feed_dog=lambda stream_id: None),
    )
    monkeypatch.setattr(
        "src.core.managers.get_chatter_manager",
        lambda: SimpleNamespace(),
    )

    new_task = asyncio.create_task(asyncio.sleep(0.2))
    context = SimpleNamespace(stream_loop_task=new_task)

    async def get_context(_stream_id: str):
        return context

    manager = cast(
        StreamLoopManager,
        SimpleNamespace(
            is_running=False,
            _chatter_genes={},
            _stats={"total_failures": 0, "total_process_cycles": 0},
            _get_stream_context=get_context,
            _flush_cached_messages_to_unread=AsyncMock(return_value=[]),
            _wait_state_check=lambda _stream_id, _context: True,
        ),
    )

    await run_chat_stream(stream_id=stream_id, manager=manager)

    assert context.stream_loop_task is new_task
    unregister_mock.assert_not_called()

    new_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await new_task
