"""验证存储关闭前逐步重查 pending 的保守契约，不推定真实跨存储依赖。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterable
from functools import partial
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.kernel.concurrency import TaskManager
from src.kernel.concurrency.task_info import TaskInfo


WATCHDOG_SECONDS = 3.0
STEP_TIMEOUT = 0.05
CLOSE_LABELS = {
    "_close_default_model_clients": "默认模型资源",
    "_close_database": "主数据库",
    "_close_vector_database": "向量数据库",
    "_close_llm_statistics": "LLM统计",
    "_close_telemetry": "本地遥测",
}
STORAGE_STEPS = (
    "_close_database",
    "_close_vector_database",
    "_close_llm_statistics",
)


async def wait_event(event: asyncio.Event) -> None:
    """等待明确屏障，watchdog 只用于报告测试挂起，不用于制造时序。"""
    async with asyncio.timeout(WATCHDOG_SECONDS):
        await event.wait()


async def settle(tasks: Iterable[asyncio.Task[Any]]) -> None:
    """回收测试持有的全部任务结果，不向清理中的步骤传播二次取消。"""
    tracked = set(tasks)
    if not tracked:
        return
    done, pending = await asyncio.wait(tracked, timeout=WATCHDOG_SECONDS)
    for task in done:
        if not task.cancelled():
            task.exception()
    assert not pending, "释放测试屏障后仍有未退出任务"


@pytest.fixture
def close_calls() -> dict[str, AsyncMock]:
    """为受保护资源提供纯内存关闭入口，不打开模型或任何存储。"""
    return {name: AsyncMock(name=name) for name in CLOSE_LABELS}


@pytest_asyncio.fixture
async def bot(
    monkeypatch: pytest.MonkeyPatch, close_calls: dict[str, AsyncMock],
) -> AsyncIterator[Bot]:
    """保留真实关闭编排与 TaskManager，仅替换进程池工厂和资源边界。"""
    pool = Mock(name="offline_process_pool", spec=["shutdown", "submit"])
    monkeypatch.setattr(
        "src.kernel.concurrency.task_manager.ProcessPoolExecutor",
        Mock(return_value=pool),
    )
    manager = TaskManager()
    owned_tasks: list[asyncio.Task[Any]] = []
    original_create = manager.create_task

    def create_task(
        coro: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any,
    ) -> TaskInfo:
        """保留真实任务身份，覆盖已被管理器清除记录的任务。"""
        info = original_create(coro, *args, **kwargs)
        assert info.task is not None
        owned_tasks.append(info.task)
        return info

    monkeypatch.setattr(manager, "create_task", create_task)
    instance = Bot()
    instance.task_manager = manager
    instance.ui = Mock()
    instance.logger = Mock()
    for name, close in close_calls.items():
        monkeypatch.setattr(instance, name, close)
    try:
        yield instance
    finally:
        for task in owned_tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await settle(owned_tasks)
        manager.cleanup_tasks()


@pytest.mark.parametrize(
    "pending_step", ["_close_database", "_close_vector_database"],
    ids=["database-pending", "vector-pending"],
)
async def test_pending_storage_preserves_later_dependencies(
    bot: Bot,
    close_calls: dict[str, AsyncMock],
    pending_step: str,
) -> None:
    """主库或向量库取消后仍在清理时，不再调用后续受保护资源关闭入口。"""
    cleaning, release = asyncio.Event(), asyncio.Event()
    step_tasks: list[asyncio.Task[Any]] = []

    async def close_pending() -> None:
        """通过 Event 固定取消后的 finally，直到断言结束才允许退出。"""
        task = asyncio.current_task()
        assert task is not None
        step_tasks.append(task)
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    close_calls[pending_step].side_effect = close_pending
    names = list(CLOSE_LABELS)
    pending_index = names.index(pending_step)
    try:
        async with asyncio.timeout(WATCHDOG_SECONDS):
            with pytest.raises(
                BotShutdownError, match=f"{CLOSE_LABELS[pending_step]}: TimeoutError",
            ) as raised:
                await bot.shutdown(timeout=STEP_TIMEOUT)
        await wait_event(cleaning)
        assert len(step_tasks) == 1
        task = step_tasks[0]
        assert not task.done() and task.cancelling() == 1
        assert task in bot._shutdown_step_tasks
        # pending 出现前已经完成的关闭不回滚；只保护尚未开始的入口。
        for name in names[:pending_index + 1]:
            close_calls[name].assert_awaited_once()
        for name in names[pending_index + 1:]:
            close_calls[name].assert_not_called()
            assert f"{CLOSE_LABELS[name]}: 跳过" in str(raised.value)
        bot.ui.display_success.assert_not_called()
    finally:
        release.set()
        await settle(step_tasks)
    assert step_tasks[0].cancelled() and step_tasks[0].cancelling() == 1


@pytest.mark.parametrize(
    "failed_step", [None, *STORAGE_STEPS],
    ids=["success", "database-error", "vector-error", "statistics-error"],
)
async def test_finished_storage_allows_later_cleanup(
    bot: Bot,
    close_calls: dict[str, AsyncMock],
    failed_step: str | None,
) -> None:
    """已退出的成功或普通失败不等于永久 pending，后续资源仍独立清理。"""
    order: list[str] = []
    finished_tasks: list[asyncio.Task[Any]] = []

    async def close_finished(name: str) -> None:
        """记录真实步骤的完成状态，按需报告已退出的普通关闭故障。"""
        assert all(task.done() for task in finished_tasks)
        task = asyncio.current_task()
        assert task is not None
        finished_tasks.append(task)
        order.append(name)
        if name == failed_step:
            raise RuntimeError("受控存储关闭失败")

    for name in CLOSE_LABELS:
        close_calls[name].side_effect = partial(close_finished, name)
    async with asyncio.timeout(WATCHDOG_SECONDS):
        if failed_step is None:
            await bot.shutdown(timeout=STEP_TIMEOUT)
        else:
            with pytest.raises(
                BotShutdownError, match=f"{CLOSE_LABELS[failed_step]}: RuntimeError",
            ) as raised:
                await bot.shutdown(timeout=STEP_TIMEOUT)
            assert "跳过" not in str(raised.value)
    assert order == list(CLOSE_LABELS)
    assert all(task.done() for task in finished_tasks)
    for close in close_calls.values():
        close.assert_awaited_once()
    if failed_step is None:
        bot.ui.display_success.assert_called_once()
    else:
        bot.ui.display_success.assert_not_called()


async def test_storage_steps_keep_independent_budgets_after_cleanup_timeout(
    bot: Bot,
    close_calls: dict[str, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前置清理耗尽预算但已退出，存储各获完整预算并可真正等待后成功。"""
    original_wait = asyncio.wait
    budgets: dict[str, list[float | None]] = {}
    timed_out_tasks: list[asyncio.Task[Any]] = []
    completed_storage: list[str] = []

    async def observe_wait(
        tasks: Iterable[asyncio.Future[Any]],
        *,
        timeout: float | None = None,
        return_when: str = asyncio.ALL_COMPLETED,
    ) -> tuple[set[asyncio.Future[Any]], set[asyncio.Future[Any]]]:
        """旁观实际 asyncio.wait 的预算，仍由真实等待决定完成或超时。"""
        tracked = set(tasks)
        for task in tracked:
            if isinstance(task, asyncio.Task):
                budgets.setdefault(task.get_name(), []).append(timeout)
        return await original_wait(tracked, timeout=timeout, return_when=return_when)

    async def exhaust_budget() -> None:
        """耗尽一个独立清理步骤的预算，收到一次取消即退出。"""
        task = asyncio.current_task()
        assert task is not None
        timed_out_tasks.append(task)
        await asyncio.Event().wait()

    async def close_after_checkpoint(name: str) -> None:
        """明确让出一次调度后完成，不以 sleep 或共享剩余预算猜测顺序。"""
        assert len(timed_out_tasks) == 1 and timed_out_tasks[0].cancelled()
        ready = asyncio.Event()
        handle = asyncio.get_running_loop().call_soon(ready.set)
        try:
            await ready.wait()
            completed_storage.append(name)
        finally:
            handle.cancel()

    monkeypatch.setattr(asyncio, "wait", observe_wait)
    bot._stop_logger_cleanup = exhaust_budget
    for name in STORAGE_STEPS:
        close_calls[name].side_effect = partial(close_after_checkpoint, name)
    async with asyncio.timeout(WATCHDOG_SECONDS):
        with pytest.raises(BotShutdownError, match="日志清理: TimeoutError") as raised:
            await bot.shutdown(timeout=STEP_TIMEOUT)
    assert timed_out_tasks[0].cancelling() == 1
    assert completed_storage == list(STORAGE_STEPS)
    assert budgets["shutdown_step_日志清理"] == [STEP_TIMEOUT]
    for name, label in CLOSE_LABELS.items():
        assert budgets[f"shutdown_step_{label}"] == [STEP_TIMEOUT]
        close_calls[name].assert_awaited_once()
        assert f"{label}: TimeoutError" not in str(raised.value)
    assert "跳过" not in str(raised.value)
    bot.ui.display_success.assert_not_called()
