"""验证 Bot 持有的任务管理器在失败、取消和初始化退出时仍回收进程池。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.kernel.concurrency import TaskManager
from src.kernel.concurrency import task_manager as concurrency


@pytest_asyncio.fixture
async def rig(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    """保留真实受管任务和关闭方法，仅替换不分配 OS 资源的进程池。"""
    pools: list[Mock] = []

    def factory(**kwargs: Any) -> Mock:
        """为每个管理器提供独立的无资源池。"""
        pool = Mock(_processes={}, _executor_manager_thread=None)
        pools.append(pool)
        return pool

    monkeypatch.setattr(concurrency, "ProcessPoolExecutor", factory)
    manager, supervisor = TaskManager(), TaskManager()
    bot = Bot()
    bot.task_manager, bot.logger, bot.ui = manager, Mock(), Mock()
    bot._record_telemetry_event = AsyncMock()
    tasks: list[asyncio.Task[Any]] = []

    def create(coro: Coroutine[Any, Any, Any], *, supervised: bool = False) -> asyncio.Task[Any]:
        """保留全部测试任务强引用，断言失败也能回收。"""
        info = (supervisor if supervised else manager).create_task(coro)
        assert info.task is not None
        tasks.append(info.task)
        return info.task

    try:
        yield SimpleNamespace(bot=bot, manager=manager, pool=pools[0], create=create)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2)
            for task in done:
                if not task.cancelled():
                    task.exception()
            assert not pending, "测试自身仍有未退出任务"
        # 所有池都是 Mock；恢复故障注入后仍执行真实幂等回收方法。
        for pool in pools:
            pool.shutdown.side_effect = None
        TaskManager.cleanup_tasks(manager)
        TaskManager.cleanup_tasks(supervisor)
        TaskManager.shutdown_process_pool(manager, wait=False)
        TaskManager.shutdown_process_pool(supervisor, wait=False)


async def test_stop_closes_held_pool_without_getter(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bot 只回收持有的实例，不借助 getter 创建或替换管理器。"""
    getter = Mock(side_effect=AssertionError("关闭不得调用 getter"))
    monkeypatch.setattr(concurrency, "get_task_manager", getter)
    monkeypatch.setattr("src.kernel.concurrency.get_task_manager", getter)
    await rig.bot._stop_task_manager()
    rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    assert rig.manager._process_pool is None
    getter.assert_not_called()


async def test_missing_manager_does_not_create_pool(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """未持有管理器时直接返回，不为了池清理分配资源。"""
    rig.bot.task_manager = None
    getter = Mock(side_effect=AssertionError("不得新建管理器"))
    monkeypatch.setattr(concurrency, "get_task_manager", getter)
    monkeypatch.setattr("src.kernel.concurrency.get_task_manager", getter)
    await rig.bot._stop_task_manager()
    getter.assert_not_called()
    rig.pool.shutdown.assert_not_called()


@pytest.mark.parametrize("pool_fails", [False, True])
@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_cleanup_failure_still_attempts_pool(
    rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    pool_fails: bool, failure_type: type[BaseException],
) -> None:
    """清理记录失败或取消不跳过池，两个失败均可被调用方观察。"""
    failure = failure_type("记录清理失败")
    monkeypatch.setattr(rig.manager, "cleanup_tasks", Mock(side_effect=failure))
    if pool_fails:
        rig.pool.shutdown.side_effect = OSError("池关闭失败")
    with pytest.raises((failure_type, BaseExceptionGroup)) as caught:
        await rig.bot._stop_task_manager()
    rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    if pool_fails:
        assert isinstance(caught.value, BaseExceptionGroup)
        assert failure in caught.value.exceptions
        assert any(isinstance(error, ExceptionGroup) for error in caught.value.exceptions)
    else:
        assert caught.value is failure


@pytest.mark.parametrize("pool_fails", [False, True])
async def test_task_failure_remains_primary_and_pool_is_attempted(rig: SimpleNamespace, pool_fails: bool) -> None:
    """受管任务失败不跳过回收，池失败附加到原错误而非覆盖它。"""
    entered = asyncio.Event()

    async def work() -> None:
        """取消后报告真实任务清理失败。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("受管任务清理失败")

    task = rig.create(work())
    await entered.wait()
    if pool_fails:
        rig.pool.shutdown.side_effect = OSError("池关闭失败")
    with pytest.raises(BotShutdownError, match="受管任务异常") as caught:
        await rig.bot._stop_task_manager()
    assert task.done() and isinstance(task.exception(), RuntimeError)
    rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    if pool_fails:
        assert any("ExceptionGroup" in note for note in caught.value.__notes__)
        rig.bot.logger.error.assert_called()


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancelled_stop_still_attempts_pool_without_second_cancel(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool) -> None:
    """关闭步骤等待期间被取消也回收池，受管任务不承受第二次取消。"""
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work() -> None:
        """保留真实取消中的清理任务，待测试 finally 释放。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    task = rig.create(work())
    await entered.wait()
    observer = rig.create(rig.bot._stop_task_manager(), supervised=True)
    try:
        await asyncio.wait_for(cleaning.wait(), timeout=2)
        if cleanup_fails:
            monkeypatch.setattr(rig.manager, "cleanup_tasks", Mock(side_effect=RuntimeError("清理失败")))
            rig.pool.shutdown.side_effect = OSError("池关闭失败")
        observer.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await observer
        rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        assert task.cancelling() == 1 and not task.done()
        if cleanup_fails:
            assert len(caught.value.__notes__) == 2
    finally:
        release.set()
        await asyncio.gather(task, observer, return_exceptions=True)


@pytest.mark.parametrize("failure_stage", ["initialize", "run"])
async def test_start_failure_closes_existing_manager_and_keeps_original(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure_stage: str) -> None:
    """初始化或运行失败后走真实 shutdown，池失败不能覆盖最初错误。"""
    primary = RuntimeError("原始启动或运行错误")
    rig.bot.initialize = AsyncMock(side_effect=primary if failure_stage == "initialize" else None)
    rig.bot.run = AsyncMock(side_effect=primary if failure_stage == "run" else None)
    rig.pool.shutdown.side_effect = OSError("池关闭失败")
    close_model = AsyncMock()
    monkeypatch.setattr(rig.bot, "_close_default_model_clients", close_model)
    with pytest.raises(RuntimeError) as caught:
        await rig.bot.start()
    assert caught.value is primary
    assert any("任务管理器" in note for note in primary.__notes__)
    rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    close_model.assert_not_awaited()
    rig.bot.ui.display_success.assert_not_called()


@pytest.mark.parametrize("pool_fails", [False, True])
async def test_early_shutdown_cancel_still_closes_held_pool(rig: SimpleNamespace, pool_fails: bool) -> None:
    """尚未进入任务管理器步骤就取消时，Bot 外层仍负责同步回收池。"""
    entered = asyncio.Event()

    async def stop_http() -> None:
        """阻塞在任务管理器步骤之前，取消后直接退出。"""
        entered.set()
        await asyncio.Event().wait()

    rig.bot.http_server = SimpleNamespace(stop=stop_http)
    if pool_fails:
        rig.pool.shutdown.side_effect = OSError("池关闭失败")
    observer = rig.create(rig.bot.shutdown(), supervised=True)
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await observer
        rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        assert rig.manager._process_pool is None
        if pool_fails:
            assert any("进程池" in note for note in caught.value.__notes__)
            rig.bot.logger.error.assert_called()
    finally:
        await asyncio.gather(observer, return_exceptions=True)


async def test_unstarted_manager_step_timeout_reports_pool_error_without_primary(
    rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理器步骤尚未开始就超时，外层回收错误仍汇总且不输出关闭完成。"""
    original_create = rig.manager.create_task
    entered, exited = asyncio.Event(), asyncio.Event()
    delayed: list[asyncio.Task[Any]] = []

    def create_task(
        coro: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any,
    ) -> Any:
        """只延迟管理器步骤执行，仍用真实受管任务接受超时取消。"""
        if kwargs.get("name") != "shutdown_step_任务管理器":
            return original_create(coro, *args, **kwargs)

        async def wait_before_callback() -> None:
            """未取得回调执行权时也显式关闭原协程，避免测试制造泄漏。"""
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                coro.close()
                exited.set()

        info = original_create(wait_before_callback(), *args, **kwargs)
        assert info.task is not None
        delayed.append(info.task)
        return info

    monkeypatch.setattr(rig.manager, "create_task", create_task)
    rig.pool.shutdown.side_effect = OSError("池关闭失败")
    try:
        with pytest.raises(BotShutdownError) as caught:
            await rig.bot.shutdown(timeout=0.05)
        assert entered.is_set()
        assert "任务管理器: TimeoutError" in str(caught.value)
        assert "进程池: ExceptionGroup" in str(caught.value)
        assert not getattr(caught.value, "__notes__", [])
        rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        assert rig.manager._process_pool is None
        rig.bot.ui.display_error.assert_called_once()
        rig.bot.ui.display_success.assert_not_called()
        rig.bot.logger.error.assert_not_called()
    finally:
        for task in delayed:
            if not task.done() and not task.cancelling():
                task.cancel()
        if delayed:
            await asyncio.wait_for(asyncio.gather(*delayed, return_exceptions=True), timeout=2)
        assert exited.is_set()


async def test_early_shutdown_pool_failure_without_logger_preserves_cancel(
    rig: SimpleNamespace,
) -> None:
    """没有 logger 的前置步骤取消仍保留原取消对象及池错误附注。"""
    rig.bot.logger = None
    entered, exited = asyncio.Event(), asyncio.Event()
    primary = asyncio.CancelledError("原始关闭取消")

    async def stop_http() -> None:
        """让真实关闭步骤返回指定取消对象，并记录协程实际退出。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise primary
        finally:
            exited.set()

    rig.bot.http_server = SimpleNamespace(stop=stop_http)
    rig.pool.shutdown.side_effect = OSError("池关闭失败")
    observer = rig.create(rig.bot.shutdown(), supervised=True)
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        http_step = next(task for task in rig.bot._shutdown_step_tasks if task.get_name() == "shutdown_step_HTTP")
        http_step.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(observer, timeout=2)
        assert caught.value is primary
        assert exited.is_set() and http_step.done()
        assert caught.value.__notes__ == ["进程池清理另有异常: ExceptionGroup"]
        rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        assert rig.manager._process_pool is None
        rig.bot.ui.display_success.assert_not_called()
    finally:
        if not observer.done() and not observer.cancelling():
            observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)


async def test_task_cleanup_without_logger_preserves_primary_and_both_errors(
    rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 logger 时仍逐条保留清理错误，原受管任务错误不能被覆盖。"""
    rig.bot.logger = None
    primary = RuntimeError("任务枚举失败")
    cleanup = ValueError("记录清理失败")
    monkeypatch.setattr(rig.manager, "get_active_tasks", Mock(side_effect=primary))
    monkeypatch.setattr(rig.manager, "cleanup_tasks", Mock(side_effect=cleanup))
    rig.pool.shutdown.side_effect = OSError("池关闭失败")
    with pytest.raises(RuntimeError) as caught:
        await rig.bot._stop_task_manager()
    assert caught.value is primary
    assert caught.value.__notes__ == [
        "任务管理器清理另有异常: ValueError",
        "任务管理器清理另有异常: ExceptionGroup",
    ]
    rig.manager.cleanup_tasks.assert_called_once_with()
    rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    assert rig.manager._process_pool is None


async def test_pool_failure_uses_shutdown_error_aggregation(rig: SimpleNamespace) -> None:
    """池失败沿现有步骤错误汇总对外报告，不能输出关闭完成。"""
    rig.pool.shutdown.side_effect = OSError("池关闭失败")
    with pytest.raises(BotShutdownError, match="任务管理器: ExceptionGroup"):
        await rig.bot.shutdown()
    rig.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    rig.bot.ui.display_error.assert_called_once()
    rig.bot.ui.display_success.assert_not_called()
