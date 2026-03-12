from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.transport.distribution.loop import run_chat_stream
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager


@pytest.mark.asyncio
async def test_start_stream_loop_registers_watchdog_with_multiplier_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动流循环时应使用倍率阈值，并注入可调用的重启回调。"""
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
        lambda: SimpleNamespace(bot=SimpleNamespace(tick_interval=30.0)),
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
    assert registered_kwargs["warning_threshold"] == 2.0
    assert registered_kwargs["restart_threshold"] == 5.0
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
