"""验证逐步骤关闭预算、受管任务所有权与 pending 依赖保护的合并契约。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.kernel.concurrency import TaskManager


@pytest_asyncio.fixture
async def bot() -> AsyncIterator[Bot]:
    """每项测试使用独立 TaskManager，退出时等待全部测试任务。"""
    instance = Bot()
    instance.task_manager = TaskManager()
    instance.task_manager.shutdown_process_pool(wait=False)
    instance.ui = MagicMock()
    instance.logger = MagicMock()
    instance.watchdog = MagicMock()
    yield instance
    tasks = [info.task for info in instance.task_manager.get_all_tasks() if info.task is not None]
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    instance.task_manager.shutdown_process_pool(wait=False)


def start(bot: Bot, operation: Any, name: str) -> asyncio.Task[Any]:
    """通过实例 TaskManager 启动一个可观察的测试任务。"""
    assert bot.task_manager is not None
    task = bot.task_manager.create_task(operation, name=name, daemon=True).task
    assert task is not None
    return task


async def test_already_cancelling_owner_is_not_cancelled_again(bot: Bot) -> None:
    """关闭观察者不能二次取消正在资源清理中的聊天 owner。"""
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        """在第一次取消后停在确定的资源清理窗口。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    task = start(bot, owner(), "chat_stream_pending")
    await entered.wait()
    task.cancel()
    await cleaning.wait()
    observation = start(bot, bot._stop_chat_tasks(), "observe-owner")
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await reached.wait()
    assert task.cancelling() == 1 and not task.done()
    release.set()
    await observation
    assert task.cancelled() and task.cancelling() == 1


async def test_uncancelled_owner_is_requested_once(bot: Bot) -> None:
    """尚未收到取消的聊天任务由停止入口请求退出。"""
    entered = asyncio.Event()

    async def owner() -> None:
        """等待正常停止请求。"""
        entered.set()
        await asyncio.Event().wait()

    task = start(bot, owner(), "chat_stream_active")
    await entered.wait()
    await bot._stop_chat_tasks()
    assert task.cancelled() and task.cancelling() == 1


async def test_watchdog_stops_before_stream_source(bot: Bot) -> None:
    """实际 shutdown 编排先封闭 WatchDog，再停止聊天流来源且只停止一次。"""
    order: list[str] = []
    bot.watchdog.stop.side_effect = lambda: order.append("watchdog")

    async def stop_source() -> None:
        """检查恢复来源已经封闭。"""
        assert "watchdog" in order
        order.append("stream")

    source = SimpleNamespace(stop=AsyncMock(side_effect=stop_source))
    bot._stream_loop_manager = source
    await bot.shutdown(timeout=0.2)
    source.stop.assert_awaited_once()
    assert order.index("watchdog") < order.index("stream")


async def test_pending_owner_preserves_dependencies(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 owner 超时仍在清理时，不关闭其模型池或存储。"""
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        """固定 owner 不合作退出的窗口。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    task = start(bot, owner(), "chat_stream_lease")
    await entered.wait()
    protected = {}
    for name in ["_close_default_model_clients", "_close_database", "_close_vector_database", "_close_llm_statistics", "_close_telemetry"]:
        protected[name] = AsyncMock()
        monkeypatch.setattr(bot, name, protected[name])
    try:
        with pytest.raises(BotShutdownError, match="跳过"):
            await bot.shutdown(timeout=0.01)
        assert cleaning.is_set() and not task.done() and task.cancelling() == 1
        assert task in bot._shutdown_owner_tasks
        for close in protected.values():
            close.assert_not_awaited()
        bot.ui.display_success.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_source_cleanup_timeout_is_bounded(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    """来源 stop 吞掉取消后仍不会拖死协调者，依赖资源继续保留。"""
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def stop_source() -> None:
        """模拟进入不合作清理的来源。"""
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    bot._stream_loop_manager = SimpleNamespace(stop=stop_source)
    close_db = AsyncMock()
    monkeypatch.setattr(bot, "_close_database", close_db)
    try:
        with pytest.raises(BotShutdownError, match="聊天流: TimeoutError"):
            await bot.shutdown(timeout=0.01)
        assert cleaning.is_set()
        assert any(not task.done() for task in bot._shutdown_step_tasks)
        close_db.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(*list(bot._shutdown_step_tasks), return_exceptions=True)


async def test_each_independent_step_gets_new_budget(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    """首步超时，次步获完整预算并成功；不能只证明两步都能超时。"""
    order: list[str] = []
    budgets: dict[str, float | None] = {}
    cancellations: list[int] = []
    original_wait = asyncio.wait

    async def observe(tasks: Any, *, timeout: float | None = None, **kwargs: Any) -> Any:
        """旁观真实等待入口，精确记录各步骤获得的预算。"""
        for task in tasks:
            if isinstance(task, asyncio.Task):
                budgets[task.get_name()] = timeout
        return await original_wait(tasks, timeout=timeout, **kwargs)

    async def first() -> None:
        """耗尽首步预算并记录唯一取消请求。"""
        order.append("first")
        try:
            await asyncio.Event().wait()
        finally:
            task = asyncio.current_task()
            assert task is not None
            cancellations.append(task.cancelling())

    async def second() -> None:
        """等待短于本步预算的定时信号，首步耗尽不能影响本步成功。"""
        order.append("second")
        ready = asyncio.Event()
        handle = asyncio.get_running_loop().call_later(0.01, ready.set)
        try:
            await ready.wait()
            order.append("second-complete")
        finally:
            handle.cancel()
            task = asyncio.current_task()
            assert task is not None
            cancellations.append(task.cancelling())

    monkeypatch.setattr(asyncio, "wait", observe)
    monkeypatch.setattr(bot, "_stop_log_cleanup", first)
    monkeypatch.setattr(bot, "_stop_media_manager", second)
    with pytest.raises(BotShutdownError) as raised:
        await bot.shutdown(timeout=0.03)
    assert order == ["first", "second", "second-complete"]
    assert budgets["shutdown_step_日志清理"] == 0.03
    assert budgets["shutdown_step_媒体管理器"] == 0.03
    assert cancellations == [1, 0]
    assert "日志清理: TimeoutError" in str(raised.value)
    assert "媒体管理器: TimeoutError" not in str(raised.value)


async def test_cancelled_coordinator_restores_synchronous_resources(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    """取消外层协调者仍逐项恢复网络、日志、终端和信号。"""
    entered = asyncio.Event()
    restored: list[str] = []

    async def stop_http() -> None:
        """固定取消发生在步骤观察窗口。"""
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(bot, "_stop_http_server", stop_http)
    for name in ["_restore_network_runtime", "_close_logger", "_close_command_parser", "_restore_signal_handler"]:
        monkeypatch.setattr(bot, name, lambda name=name: restored.append(name))
    task = start(bot, bot.shutdown(timeout=0.5), "shutdown-coordinator")
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert restored == ["_restore_network_runtime", "_close_logger", "_close_command_parser", "_restore_signal_handler"]


async def test_no_lazy_manager_created(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    """从未取得聊天来源时，关闭不调用其全局 getter。"""
    getter = MagicMock(side_effect=AssertionError("不应创建管理器"))
    monkeypatch.setattr("src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager", getter)
    await bot.shutdown(timeout=0.2)
    getter.assert_not_called()


async def test_completed_chat_exception_is_reported(bot: Bot) -> None:
    """即使没有 pending，仍登记的已退出聊天异常也不能遗漏。"""
    failure = RuntimeError("offline owner exit")

    async def fail() -> None:
        """返回确定的普通退出故障。"""
        raise failure

    task = start(bot, fail(), "chat_stream_failed")
    with pytest.raises(RuntimeError) as raised:
        await task
    assert raised.value is failure
    with pytest.raises(BotShutdownError, match="异常退出"):
        await bot._stop_chat_tasks()


async def test_managed_shutdown_never_waits_or_cancels_itself(bot: Bot) -> None:
    """同一 TaskManager 托管 shutdown 时，任务清理排除其协调者。"""
    task = start(bot, bot.shutdown(timeout=0.2), "shutdown-managed")
    await task
    assert not task.cancelled() and task.cancelling() == 0
    bot.ui.display_success.assert_called_once()
