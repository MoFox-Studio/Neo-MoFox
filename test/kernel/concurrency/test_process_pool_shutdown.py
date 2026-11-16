"""PR181 进程池代际隔离与共享回收预算的受控回归。"""

from __future__ import annotations

from contextlib import nullcontext
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


@pytest.mark.parametrize("scope", ["unscoped", "borrowed", "owned"])
def test_getter_explicit_configuration_respects_active_ownership(clock: Clock, scope: str) -> None:
    """无作用域可显式重配；活跃作用域只保护借用池，不限制自有池。"""
    manager = None if scope == "owned" else concurrency.get_task_manager(process_workers=2)
    context = nullcontext() if scope == "unscoped" else concurrency.process_pool_lifespan()
    with context:
        if manager is None:
            manager = concurrency.get_task_manager(process_workers=2)
        original = manager._process_pool
        assert concurrency.get_task_manager() is manager
        assert manager._process_pool is original
        original.shutdown.assert_not_called()

        assert concurrency.get_task_manager(process_workers=7) is manager
        current = manager._process_pool
        if scope == "borrowed":
            assert current is original
            assert manager._process_workers == 2
            original.shutdown.assert_not_called()
        else:
            assert current is not original
            assert manager._process_workers == 7
            original.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        current.shutdown.assert_not_called()

    if scope == "owned":
        assert manager._process_pool is None
        current.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    else:
        assert manager._process_pool is current
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
        """模拟旧代关闭时新代已接管管理器。"""
        manager._process_pool = replacement

    old.shutdown.side_effect = replace
    manager.shutdown_process_pool(wait=False)
    assert manager._process_pool is replacement
    replacement.shutdown.assert_not_called()


def test_stale_owner_cannot_close_replacement_generation(clock: Clock) -> None:
    """关闭凭据必须校验具体 executor 实例，而非仅管理器。"""
    manager = concurrency.TaskManager()
    old = manager._process_pool
    replacement = Mock(_processes={}, _executor_manager_thread=None)
    manager._process_pool = replacement
    manager.shutdown_process_pool(wait=False, expected_pool=old)
    assert manager._process_pool is replacement
    replacement.shutdown.assert_not_called()


def test_lifespan_skips_pool_created_by_another_context(clock: Clock) -> None:
    """原 owner 退出不得回收其它上下文新建的池。"""
    with concurrency.process_pool_lifespan():
        manager = concurrency.get_task_manager()
        old = manager._process_pool
        Context().run(manager.configure_process_pool, 7)
        replacement = manager._process_pool
        assert replacement is not old
    assert manager._process_pool is replacement
    old.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    replacement.shutdown.assert_not_called()


def test_owned_reconfiguration_registers_the_new_generation(clock: Clock) -> None:
    """自己配置替换的每一代都在各自生命周期结束时回收。"""
    with concurrency.process_pool_lifespan():
        manager = concurrency.get_task_manager()
        old = manager._process_pool
        manager.configure_process_pool(7)
        current = manager._process_pool
    assert manager._process_pool is None
    old.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    current.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_all_owned_managers_share_the_same_exit_budget(clock: Clock) -> None:
    """多个管理器也不能分别获得完整等待预算。"""
    workers: list[Worker] = []
    with concurrency.process_pool_lifespan(timeout=0.4):
        for index in range(5):
            manager = concurrency.TaskManager()
            worker = Worker(clock, [], str(index))
            workers.append(worker)
            manager._process_pool._processes = {index: worker}
    assert clock.now <= 0.4 + 1e-9
    assert all(worker.killed for worker in workers)


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


def test_replacing_global_manager_does_not_transfer_ownership(clock: Clock) -> None:
    """所有者保存创建凭据，退出不通过 getter 误关新的全局实例。"""
    with concurrency.process_pool_lifespan():
        original = concurrency.get_task_manager()
        pool = original._process_pool

        def replace() -> concurrency.TaskManager:
            """模拟独立宿主上下文替换全局管理器。"""
            concurrency._task_manager = None
            return concurrency.get_task_manager()

        host = Context().run(replace)
    assert concurrency._task_manager is host
    host._process_pool.shutdown.assert_not_called()
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_explicit_graceful_shutdown_still_waits(clock: Clock) -> None:
    """保留宿主显式请求的 wait=True 行为。"""
    manager = concurrency.TaskManager()
    pool = manager._process_pool
    worker = Worker(clock, [], "one")
    pool._processes = {1: worker}
    manager.shutdown_process_pool()
    pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert worker.alive
    assert not worker.killed



def test_cleanup_errors_preserve_primary_error_and_close_other_pools(clock: Clock) -> None:
    """一个池回收失败不阻止其它池，并保留最初的取消异常。"""
    error = RuntimeError("initialization")
    with pytest.raises(RuntimeError) as caught:
        with concurrency.process_pool_lifespan():
            first = concurrency.TaskManager()
            first._process_pool.shutdown.side_effect = OSError("close failed")
            second = concurrency.TaskManager()
            second_pool = second._process_pool
            raise error
    assert caught.value is error
    assert "close failed" in error.__notes__[0]
    second_pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


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


def test_nested_owner_borrows_outer_pool(clock: Clock) -> None:
    """内层作用域不获得外层已有池的关闭权。"""
    with concurrency.process_pool_lifespan():
        manager = concurrency.get_task_manager()
        pool = manager._process_pool
        with concurrency.process_pool_lifespan():
            assert concurrency.get_task_manager() is manager
        pool.shutdown.assert_not_called()
    pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
