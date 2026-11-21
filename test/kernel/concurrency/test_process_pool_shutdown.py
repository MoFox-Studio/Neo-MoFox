"""进程池显式重配、代际隔离与共享回收预算的受控回归。"""

from __future__ import annotations

from contextvars import Context
from typing import Any
from unittest.mock import Mock

import pytest

from src.kernel.concurrency import task_manager as concurrency


class Clock:
    """不用真实等待的单调时钟。"""

    def __init__(self) -> None:
        """从零累计模拟等待时间。"""
        self.now = 0.0

    def __call__(self) -> float:
        """返回当前时间。"""
        return self.now


class Worker:
    """忽略 terminate、必须 kill 才停止的受控 worker。"""

    def __init__(self, clock: Clock, events: list[str], name: str) -> None:
        """共享时钟和事件记录。"""
        self.clock = clock
        self.events = events
        self.name = name
        self.alive = True
        self.killed = False

    def is_alive(self) -> bool:
        """报告受控进程状态。"""
        return self.alive

    def terminate(self) -> None:
        """模拟忽略温和终止。"""
        self.events.append(f"terminate:{self.name}")

    def kill(self) -> None:
        """模拟强制终止立即生效。"""
        self.events.append(f"kill:{self.name}")
        self.killed = True
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        """仅存活时消耗传入的等待预算。"""
        assert timeout is not None and timeout >= 0
        self.events.append(f"join:{self.name}")
        if self.alive:
            self.clock.now += timeout


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """替换时钟及 executor，测试中不启动后台线程。"""
    value = Clock()
    monkeypatch.setattr(concurrency, "monotonic", value, raising=False)
    monkeypatch.setattr(concurrency, "_task_manager", None)
    monkeypatch.setattr(
        concurrency, "ProcessPoolExecutor",
        lambda **kwargs: Mock(_processes={}, _executor_manager_thread=None),
    )
    return value


@pytest.mark.parametrize("new_context", [False, True])
def test_getter_explicit_configuration_is_not_restricted_by_context(
    clock: Clock, new_context: bool,
) -> None:
    """无参 getter 不重配，显式配置在当前或新上下文均直接生效。"""
    manager = concurrency.get_task_manager(process_workers=2)
    original = manager._process_pool
    assert concurrency.get_task_manager() is manager
    assert manager._process_pool is original
    original.shutdown.assert_not_called()

    if new_context:
        configured = Context().run(concurrency.get_task_manager, process_workers=7)
    else:
        configured = concurrency.get_task_manager(process_workers=7)
    assert configured is manager
    current = manager._process_pool
    assert current is not original
    assert manager._process_workers == 7
    original.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    current.shutdown.assert_not_called()


@pytest.mark.parametrize("count", [1, 4, 32])
def test_worker_shutdown_has_one_budget_and_signals_every_worker_first(clock: Clock, count: int) -> None:
    """worker 数量不能乘上退出等待；先通知全部再等待。"""
    events: list[str] = []
    workers = [Worker(clock, events, str(index)) for index in range(count)]
    manager = concurrency.TaskManager()
    manager._terminate_worker_processes(workers)
    assert clock.now <= 0.4 + 1e-9
    assert events[:count] == [f"terminate:{index}" for index in range(count)]
    assert all(worker.killed for worker in workers)


def test_shutdown_does_not_clear_a_replacement_pool(clock: Clock) -> None:
    """旧池 shutdown 期间出现的新代不能被末尾赋空。"""
    manager = concurrency.TaskManager()
    old = manager._process_pool
    replacement = Mock(_processes={}, _executor_manager_thread=None)

    def replace(**kwargs: Any) -> None:
        """模拟旧代已摘除后，新代在关闭过程中接管管理器。"""
        assert manager._process_pool is None
        manager._process_pool = replacement

    old.shutdown.side_effect = replace
    manager.shutdown_process_pool(wait=False)
    assert manager._process_pool is replacement
    replacement.shutdown.assert_not_called()


def test_expected_pool_cannot_close_replacement_generation(clock: Clock) -> None:
    """旧代快照只能关闭对应 executor，不能误关新代。"""
    manager = concurrency.TaskManager()
    old = manager._process_pool
    replacement = Mock(_processes={}, _executor_manager_thread=None)
    manager._process_pool = replacement
    manager.shutdown_process_pool(wait=False, expected_pool=old)
    assert manager._process_pool is replacement
    old.shutdown.assert_not_called()
    replacement.shutdown.assert_not_called()


def test_same_configuration_keeps_existing_pool(clock: Clock) -> None:
    """显式提交相同配置不创建新代，也不触发无谓回收。"""
    manager = concurrency.get_task_manager(process_workers=2)
    pool = manager._process_pool
    assert concurrency.get_task_manager(process_workers=2) is manager
    assert manager._process_pool is pool
    pool.shutdown.assert_not_called()


def test_explicit_reconfiguration_reclaims_each_generation(clock: Clock) -> None:
    """显式重配先回收旧代，最后的关闭只处理当前代。"""
    manager = concurrency.TaskManager()
    old = manager._process_pool
    manager.configure_process_pool(7)
    current = manager._process_pool
    assert current is not old
    assert manager._process_workers == 7
    old.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    current.shutdown.assert_not_called()

    manager.shutdown_process_pool(wait=False)
    assert manager._process_pool is None
    old.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    current.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_multiple_managers_share_an_explicit_deadline(clock: Clock) -> None:
    """调用方传入同一截止时间时，多个管理器不能分别重置预算。"""
    managers: list[concurrency.TaskManager] = []
    workers: list[Worker] = []
    for index in range(5):
        manager = concurrency.TaskManager()
        worker = Worker(clock, [], str(index))
        managers.append(manager)
        workers.append(worker)
        manager._process_pool._processes = {index: worker}

    deadline = clock() + 0.4
    for manager in managers:
        manager.shutdown_process_pool(wait=False, timeout=10, deadline=deadline)
    assert clock.now <= 0.4 + 1e-9
    assert clock.now == pytest.approx(0.4)
    assert all(worker.killed for worker in workers)
    assert all(manager._process_pool is None for manager in managers)


def test_executor_manager_thread_uses_remaining_budget(clock: Clock) -> None:
    """executor 管理线程等待计入同一总预算。"""
    manager = concurrency.TaskManager()
    pool = manager._process_pool
    pool._processes = {1: Worker(clock, [], "one")}
    thread = Mock()
    thread.is_alive.return_value = False

    def join(timeout: float) -> None:
        """消耗剩余预算，禁止无界等待。"""
        assert timeout >= 0
        clock.now += timeout

    thread.join.side_effect = join
    pool._executor_manager_thread = thread
    manager.shutdown_process_pool(wait=False, timeout=0.4)
    assert clock.now <= 0.4 + 1e-9
    thread.join.assert_called_once()


def test_empty_shutdown_does_not_recreate_pool(clock: Clock) -> None:
    """重复回收不产生新的 executor。"""
    manager = concurrency.TaskManager()
    pool = manager._process_pool
    manager.shutdown_process_pool(wait=False)
    manager.shutdown_process_pool(wait=False)
    assert manager._process_pool is None
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_shutdown_overhead_consumes_the_shared_budget(clock: Clock) -> None:
    """executor.shutdown 自身耗时也从等待预算中扣除。"""
    manager = concurrency.TaskManager()
    worker = Worker(clock, [], "one")
    manager._process_pool._processes = {1: worker}

    def shutdown(**kwargs: Any) -> None:
        """模拟同步关闭用去大部分预算。"""
        clock.now += 0.35

    manager._process_pool.shutdown.side_effect = shutdown
    manager.shutdown_process_pool(wait=False, timeout=0.4)
    assert clock.now <= 0.4 + 1e-9
    assert worker.killed


def test_closing_original_manager_does_not_close_replacement_singleton(clock: Clock) -> None:
    """实例关闭只处理自身的池，不通过 getter 误关新的全局实例。"""
    original = concurrency.get_task_manager()
    pool = original._process_pool
    concurrency._task_manager = None
    replacement = concurrency.get_task_manager()

    original.shutdown_process_pool(wait=False)
    assert original._process_pool is None
    assert concurrency._task_manager is replacement
    replacement._process_pool.shutdown.assert_not_called()
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_explicit_graceful_shutdown_still_waits(clock: Clock) -> None:
    """保留调用方显式请求的 wait=True 行为。"""
    manager = concurrency.TaskManager()
    pool = manager._process_pool
    worker = Worker(clock, [], "one")
    pool._processes = {1: worker}
    manager.shutdown_process_pool()
    pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert worker.alive
    assert not worker.killed


def test_executor_shutdown_failure_still_reaps_workers_and_reports_error(clock: Clock) -> None:
    """executor 关闭失败仍清理已捕获的 worker 和线程，并保留原错误对象。"""
    manager = concurrency.TaskManager()
    pool = manager._process_pool
    failure = OSError("close failed")
    workers = [Worker(clock, [], str(index)) for index in range(2)]
    pool._processes = dict(enumerate(workers))
    pool.shutdown.side_effect = failure
    thread = Mock()
    thread.is_alive.return_value = False
    pool._executor_manager_thread = thread

    with pytest.raises(ExceptionGroup, match="进程池关闭失败") as caught:
        manager.shutdown_process_pool(wait=False)
    assert caught.value.exceptions == (failure,)
    assert manager._process_pool is None
    assert all(worker.killed for worker in workers)
    thread.join.assert_called_once_with(timeout=0)
    assert clock.now <= 0.4 + 1e-9


def test_lingering_manager_thread_is_reported_without_unbounded_join(clock: Clock) -> None:
    """预算内无法退出时明确报错，不伪装已经终止。"""
    manager = concurrency.TaskManager()
    thread = Mock()
    thread.is_alive.return_value = True
    manager._process_pool._executor_manager_thread = thread
    with pytest.raises(ExceptionGroup, match="进程池关闭失败"):
        manager.shutdown_process_pool(wait=False, timeout=0)
    thread.join.assert_called_once_with(timeout=0)


def test_closed_process_handles_do_not_prevent_other_workers_cleanup(clock: Clock) -> None:
    """管理线程抢先关闭句柄属于已回收竞态，继续处理剩余进程。"""
    closed = Mock()
    closed.is_alive.side_effect = ValueError("process object is closed")
    worker = Worker(clock, [], "live")
    concurrency.TaskManager()._terminate_worker_processes([closed, worker])
    assert worker.killed


def test_signal_failure_still_attempts_other_workers_and_kill(clock: Clock) -> None:
    """一个 terminate 报错仍尝试其它 worker 和强制终止。"""
    failed = Worker(clock, [], "failed")
    failed.terminate = Mock(side_effect=OSError("terminate denied"))
    other = Worker(clock, [], "other")
    with pytest.raises(ExceptionGroup, match="worker 回收失败"):
        concurrency.TaskManager()._terminate_worker_processes([failed, other])
    assert failed.killed and other.killed
    assert clock.now <= 0.4 + 1e-9


def test_pool_shutdown_aggregates_executor_worker_and_thread_failures(clock: Clock) -> None:
    """多处关闭失败全部保留，不能漏掉其它 worker 或跳过线程回收。"""
    manager = concurrency.TaskManager()
    pool = manager._process_pool
    executor_error = OSError("executor failed")
    signal_error = OSError("terminate denied")
    failed = Worker(clock, [], "failed")
    failed.terminate = Mock(side_effect=signal_error)
    other = Worker(clock, [], "other")
    pool._processes = {1: failed, 2: other}
    pool.shutdown.side_effect = executor_error
    thread = Mock()
    thread.is_alive.return_value = True
    pool._executor_manager_thread = thread

    with pytest.raises(ExceptionGroup, match="进程池关闭失败") as caught:
        manager.shutdown_process_pool(wait=False)
    errors = caught.value.exceptions
    assert len(errors) == 3
    assert errors[0] is executor_error
    assert isinstance(errors[1], ExceptionGroup)
    assert errors[1].exceptions == (signal_error,)
    assert isinstance(errors[2], TimeoutError)
    assert failed.killed and other.killed
    assert manager._process_pool is None
    thread.join.assert_called_once_with(timeout=0)
    assert clock.now <= 0.4 + 1e-9
