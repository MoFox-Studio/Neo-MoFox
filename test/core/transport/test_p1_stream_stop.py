"""正常停止的取消隔离、任务身份和停止栅栏回归。"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.transport.distribution.stream_loop_manager import StreamLoopManager


@dataclass
class Owner:
    """以事件控制任务进入和退出取消清理。"""

    ready: asyncio.Event = field(default_factory=asyncio.Event)
    cleaning: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    fail: bool = False
    resist_force_cancel: bool = False


class Rig:
    """安装内存中的配置、流上下文和统一任务管理器。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """隔离配置与所有后台任务。"""
        self.manager = StreamLoopManager()
        self.manager.is_running = True
        self.context = SimpleNamespace(stream_loop_task=None, is_chatter_processing=False)
        self.owners: list[Owner] = []
        self.tasks: list[asyncio.Task[Any]] = []
        self.logger = MagicMock()
        monkeypatch.setattr("src.core.transport.distribution.stream_loop_manager.logger", self.logger)
        monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: SimpleNamespace(
            _streams={"stream": SimpleNamespace(context=self.context)},
        ))
        monkeypatch.setattr("src.core.config.get_core_config", lambda: SimpleNamespace(bot=SimpleNamespace(
            tick_interval=1.0, stream_warning_threshold=2.0, stream_restart_threshold=3.0,
        )))
        monkeypatch.setattr("src.kernel.concurrency.get_task_manager", lambda: self)
        monkeypatch.setattr("src.kernel.concurrency.get_watchdog", lambda: SimpleNamespace(
            register_stream=lambda **kwargs: None,
        ))
        monkeypatch.setattr("src.core.transport.distribution.loop.run_chat_stream", self.run_owner)

    def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str = "test", daemon: bool = True,
    ) -> SimpleNamespace:
        """模拟 TaskManager 创建并保存任务句柄。"""
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.tasks.append(task)
        return SimpleNamespace(task=task)

    async def run_owner(self, stream_id: str, manager: StreamLoopManager) -> None:
        """真实取消后停在可观测清理阶段，不靠 sleep 制造竞态。"""
        owner = self.owners[-1]
        task = asyncio.current_task()
        owner.ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            owner.cleaning.set()
            while not owner.release.is_set():
                try:
                    await owner.release.wait()
                except asyncio.CancelledError:
                    if not owner.resist_force_cancel:
                        raise
            if owner.fail:
                raise RuntimeError("secret-url-and-token-must-not-be-logged")
            owner.closed.set()
            if self.context.stream_loop_task is task:
                self.context.stream_loop_task = None

    async def launch(self, force: bool = False) -> tuple[Owner, asyncio.Task[Any]]:
        """通过真实 start 方法启动一代 owner。"""
        owner = Owner()
        self.owners.append(owner)
        assert await self.manager.start_stream_loop("stream", force=force)
        task = self.context.stream_loop_task
        await owner.ready.wait()
        return owner, task

    async def stop(self, single: bool) -> bool | None:
        """选择真实全局或单流停止入口。"""
        if single:
            return await self.manager.stop_stream_loop("stream")
        return await self.manager.stop()

    async def finish(self) -> None:
        """释放测试任务，读取所有结局，避免跨测试残留。"""
        for owner in self.owners:
            owner.release.set()
        for task in self.tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        for task in self.tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("already_cancelling", [False, True])
async def test_p1_normal_stop_cancels_owner_once(
    monkeypatch: pytest.MonkeyPatch, single: bool, already_cancelling: bool,
) -> None:
    """全局及单流停止不得中断已经开始的清理。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    try:
        if already_cancelling:
            task.cancel()
            await owner.cleaning.wait()
        stopper = rig.create_task(rig.stop(single)).task
        await owner.cleaning.wait()
        # cleaning 的 set 可能来自外部取消；让停止入口先执行到首个 await。
        entered = asyncio.Event()

        async def mark_after_stop_entry() -> None:
            """在已排队的停止入口之后标记本轮调度。"""
            entered.set()

        await rig.create_task(mark_after_stop_entry()).task
        await entered.wait()
        assert task.cancelling() == 1
        assert not task.done()
        owner.release.set()
        result = await stopper
        assert result is (True if single else None)
        assert owner.closed.is_set()
    finally:
        await rig.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("single", [False, True])
async def test_p1_cancel_waiter_does_not_cancel_owner(
    monkeypatch: pytest.MonkeyPatch, single: bool,
) -> None:
    """取消协调者须原样传播，但不能给 owner 追加取消。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    try:
        stopper = rig.create_task(rig.stop(single)).task
        await owner.cleaning.wait()
        stopper.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopper
        assert task.cancelling() == 1
        assert not task.done()
        owner.release.set()
        await asyncio.wait({task})
        assert owner.closed.is_set()
    finally:
        await rig.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("single", [False, True])
async def test_p1_timeout_retains_owner_and_repeat_stop_observes(
    monkeypatch: pytest.MonkeyPatch, single: bool,
) -> None:
    """观察预算耗尽保留引用，重复停止不重取消，pending 不允许 start 解栅栏。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    calls: list[set[asyncio.Task[Any]]] = []

    async def timed_out_wait(tasks: Any, **kwargs: Any) -> tuple[set[Any], set[Any]]:
        """确定性模拟观察预算耗尽，不等待真实五秒。"""
        calls.append(set(tasks))
        return set(), set(tasks)

    async def timed_out_wait_for(task: Any, **kwargs: Any) -> Any:
        """旧实现同样立即返回超时，以暴露引用丢失而非睡五秒。"""
        raise asyncio.TimeoutError

    try:
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "wait", timed_out_wait)
            patch.setattr(asyncio, "wait_for", timed_out_wait_for)
            assert await rig.stop(single) is (True if single else None)
            assert rig.context.stream_loop_task is task
            assert await rig.stop(single) is (True if single else None)
            assert task.cancelling() == 1
            assert len(calls) == 2
            assert calls == [{task}, {task}]
            if not single:
                await rig.manager.start()
                assert rig.manager.is_running is False
                assert not await rig.manager.start_stream_loop("stream", force=True)
        await owner.cleaning.wait()
        owner.release.set()
        await asyncio.wait({task})
        assert owner.closed.is_set()
        if not single:
            await rig.manager.start()
            assert rig.manager.is_running is True
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_concurrent_global_and_single_stop_share_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """并发全局/单流停止共享取消请求，且 False 运行标志不跳过观察。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    try:
        first = rig.create_task(rig.stop(False)).task
        await owner.cleaning.wait()
        entered = asyncio.Event()

        async def second_stop() -> None:
            """先标记入口，再观察同一个 owner。"""
            entered.set()
            await rig.stop(False)

        second = rig.create_task(second_stop()).task
        third = rig.create_task(rig.stop(True)).task
        await entered.wait()
        assert not second.done()
        assert task.cancelling() == 1
        owner.release.set()
        await asyncio.wait({first, second, third})
        for waiter in (first, second, third):
            waiter.result()
        assert owner.closed.is_set()
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_old_generation_completion_does_not_clear_new_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧停止等待者迟到完成不得清空强制重启的新任务和状态。"""
    rig = Rig(monkeypatch)
    old, old_task = await rig.launch()
    try:
        stopper = rig.create_task(rig.stop(True)).task
        await old.cleaning.wait()
        new, new_task = await rig.launch(force=True)
        marker = object()
        rig.manager._pending_wait_resume_events["stream"] = marker  # type: ignore[assignment]
        old.release.set()
        await asyncio.wait({old_task, stopper})
        assert rig.context.stream_loop_task is new_task
        assert rig.manager._pending_wait_resume_events["stream"] is marker
        assert not new_task.done()
        new.release.set()
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_global_stop_observes_replaced_old_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制替换丢失 context 句柄的旧代仍属于全局停止观察集合。"""
    rig = Rig(monkeypatch)
    old, old_task = await rig.launch()
    try:
        old.resist_force_cancel = True
        old_task.cancel()
        await old.cleaning.wait()
        # force 可以追加取消；旧任务仍未退出时必须和新代一起被观察。
        new, new_task = await rig.launch(force=True)
        observed: list[set[Any]] = []

        async def observe(tasks: Any, **kwargs: Any) -> tuple[set[Any], set[Any]]:
            """仅记录管理器捕获的代际集合。"""
            observed.append(set(tasks))
            return set(), set(tasks)

        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "wait", observe)
            await rig.manager.stop()
        assert observed == [{old_task, new_task}]
        assert not old_task.done()
        assert rig.manager._stream_owners.keys() == {old_task, new_task}
        assert old_task.cancelling() == 2  # 外部取消和 force；正常 stop 不追加。
        old.release.set()
        new.release.set()
    finally:
        await rig.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("watchdog", [False, True])
@pytest.mark.parametrize("explicit_restart", [False, True])
async def test_p1_queued_start_cannot_revive_after_stop(
    monkeypatch: pytest.MonkeyPatch, watchdog: bool, explicit_restart: bool,
) -> None:
    """启动锁外已排队的普通/WatchDog 启动必须在锁内复查停止栅栏。"""
    rig = Rig(monkeypatch)
    lock = asyncio.Lock()
    rig.manager._stream_start_locks["stream"] = lock
    await lock.acquire()
    entered = asyncio.Event()

    async def queued_start() -> bool:
        """将真实启动入口排在持有的启动锁之后。"""
        entered.set()
        if watchdog:
            return await rig.manager._restart_stream_loop_from_watchdog("stream", 1.0)
        return await rig.manager.start_stream_loop("stream")

    try:
        queued = rig.create_task(queued_start()).task
        await entered.wait()
        await rig.manager.stop()
        if explicit_restart:
            await rig.manager.start()
            assert rig.manager.is_running is True
        lock.release()
        assert await queued is False
        assert rig.context.stream_loop_task is None
    finally:
        if lock.locked():
            lock.release()
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_watchdog_context_lookup_cannot_cross_stop_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """WatchDog 在上下文查找 await 期间也不能跨停止/启动批次复活。"""
    rig = Rig(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_context(stream_id: str) -> Any:
        """阻塞来源查找，确定性重排 stop/start。"""
        entered.set()
        await release.wait()
        return rig.context

    monkeypatch.setattr(rig.manager, "_get_stream_context", blocked_context)
    try:
        queued = rig.create_task(rig.manager._restart_stream_loop_from_watchdog("stream", 1.0)).task
        await entered.wait()
        await rig.manager.stop()
        await rig.manager.start()
        release.set()
        assert await queued is False
        assert rig.context.stream_loop_task is None
    finally:
        release.set()
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_completed_owner_is_not_cancelled_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """已退出 owner 重复 stop 不追加取消，也不重复记录退出诊断。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    try:
        task.cancel()
        await owner.cleaning.wait()
        owner.release.set()
        await asyncio.wait({task})
        await rig.manager.stop()
        await rig.manager.stop()
        assert task.cancelling() == 1
        assert not rig.manager._stream_owners
        assert not rig.manager._stop_requested
        assert rig.context.stream_loop_task is None
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_exit_error_history_is_bounded_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """自然退出和迟到清理异常均只读取一次，安全摘要不无限增长。"""
    rig = Rig(monkeypatch)

    async def fail_owner() -> None:
        """模拟清理失败后已退出的 owner。"""
        raise ValueError("secret-url-and-token-must-not-be-logged")

    try:
        for _ in range(40):
            task = rig.create_task(fail_owner()).task
            rig.context.stream_loop_task = task
            rig.manager._track_stream_owner("stream", rig.context, task)
            await asyncio.wait({task})
            rig.manager._track_stream_owner("stream", rig.context, task)
        assert len(rig.manager._stream_exit_errors) == 32
        assert rig.manager._owner_sequence == 40
        assert rig.logger.error.call_count == 40
        assert not rig.manager._stream_owners
        assert "secret-url-and-token" not in str(rig.logger.mock_calls)
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_late_failure_is_retrieved_and_safely_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时后迟到的清理失败必须被读取；done 不等价于资源清理成功。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    owner.fail = True

    async def observe(tasks: Any, **kwargs: Any) -> tuple[set[Any], set[Any]]:
        """模拟未排空返回。"""
        return set(), set(tasks)

    async def old_timeout(task: Any, **kwargs: Any) -> Any:
        """让旧入口同样无需睡眠。"""
        raise asyncio.TimeoutError

    try:
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "wait", observe)
            patch.setattr(asyncio, "wait_for", old_timeout)
            await rig.manager.stop()
        await owner.cleaning.wait()
        owner.release.set()
        completed = asyncio.Event()
        task.add_done_callback(lambda done: completed.set())
        await completed.wait()
        assert task.done()
        assert not owner.closed.is_set()
        assert not task._log_traceback  # asyncio 在 exception() 被读取后复位。
        logs = str(rig.logger.mock_calls)
        assert "RuntimeError" in logs
        assert "secret-url-and-token" not in logs
    finally:
        await rig.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_restart", [False, True])
async def test_p1_start_context_lookup_cannot_cross_stop_generation(
    monkeypatch: pytest.MonkeyPatch, explicit_restart: bool,
) -> None:
    """普通启动查找跨停止批次，即使显式重新启动也不得创建旧请求任务。"""
    rig = Rig(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_context(stream_id: str) -> Any:
        """在真实启动的首次查找处挂起。"""
        entered.set()
        await release.wait()
        return rig.context

    monkeypatch.setattr(rig.manager, "_get_stream_context", blocked_context)
    try:
        queued = rig.create_task(rig.manager.start_stream_loop("stream")).task
        await entered.wait()
        await rig.manager.stop()
        if explicit_restart:
            await rig.manager.start()
        release.set()
        assert await queued is False
        assert rig.context.stream_loop_task is None
        assert len(rig.tasks) == 1
    finally:
        release.set()
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_concurrent_start_rechecks_current_owner_inside_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个启动都通过锁外检查时，后到者在锁内复用前者创建的 owner。"""
    rig = Rig(monkeypatch)
    lock = asyncio.Lock()
    rig.manager._stream_start_locks["stream"] = lock
    await lock.acquire()
    entered = [asyncio.Event(), asyncio.Event()]
    rig.owners.append(Owner())

    async def start(index: int) -> bool:
        """确认启动调用已经排队等待同一把锁。"""
        entered[index].set()
        return await rig.manager.start_stream_loop("stream")

    try:
        first = rig.create_task(start(0)).task
        second = rig.create_task(start(1)).task
        await entered[0].wait()
        await entered[1].wait()
        lock.release()
        assert await first is True
        assert await second is True
        await rig.owners[0].ready.wait()
        assert len(rig.tasks) == 3  # 两个启动调用者加唯一一个 owner。
        assert rig.manager._stats["total_loops"] == 1
        assert rig.context.stream_loop_task is rig.tasks[-1]
    finally:
        if lock.locked():
            lock.release()
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_watchdog_after_stop_rejects_before_context_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """来源已封闭时 WatchDog 不执行上下文查找或创建任务。"""
    rig = Rig(monkeypatch)
    await rig.manager.stop()

    async def forbidden_context(stream_id: str) -> Any:
        """停止后不应再访问上下文。"""
        pytest.fail("停止后的 WatchDog 不应查找上下文")

    monkeypatch.setattr(rig.manager, "_get_stream_context", forbidden_context)
    assert await rig.manager._restart_stream_loop_from_watchdog("stream", 1.0) is False
    assert not rig.tasks


@pytest.mark.asyncio
async def test_p1_stop_clears_observed_done_owner_without_duplicate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """已读取异常但运行器未清空的 done 引用，stop 按身份清空且不重记错误。"""
    rig = Rig(monkeypatch)

    async def fail_owner() -> None:
        """模拟普通退出清理失败，保留上下文句柄供停止方发现。"""
        raise ValueError("secret-url-and-token-must-not-be-logged")

    try:
        task = rig.create_task(fail_owner()).task
        rig.context.stream_loop_task = task
        rig.manager._track_stream_owner("stream", rig.context, task)
        await asyncio.wait({task})
        assert rig.context.stream_loop_task is task
        assert task in rig.manager._observed_stream_owners
        await rig.manager.stop()
        assert rig.context.stream_loop_task is None
        assert len(rig.manager._stream_exit_errors) == 1
        assert rig.logger.error.call_count == 1
        assert not rig.manager._stream_owners
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_stop_discovers_previously_untracked_done_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """停止首次发现已经 done 的 owner 时不 cancel，仍读取并记录失败。"""
    rig = Rig(monkeypatch)

    async def fail_owner() -> None:
        """模拟已经完成但尚未被 manager 登记的任务。"""
        raise RuntimeError("secret-url-and-token-must-not-be-logged")

    try:
        task = rig.create_task(fail_owner()).task
        await asyncio.wait({task})
        rig.context.stream_loop_task = task
        assert await rig.manager.stop_stream_loop("stream") is True
        assert task.cancelling() == 0
        assert not task._log_traceback
        assert rig.context.stream_loop_task is None
        assert list(rig.manager._stream_exit_errors) == [(1, "RuntimeError")]
        assert not rig.manager._stop_requested
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_compatibility_waiter_only_observes_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """兼容内部等待入口不主动取消 owner，取消此等待者也不传播。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    entered = asyncio.Event()

    async def wait_owner() -> None:
        """标记兼容等待入口已经运行到观察阶段。"""
        entered.set()
        await rig.manager._wait_for_task_cancel("stream", task)

    try:
        waiter = rig.create_task(wait_owner()).task
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert task.cancelling() == 0
        assert not owner.cleaning.is_set()
        task.cancel()
        await owner.cleaning.wait()
        owner.release.set()
        await rig.manager._wait_for_task_cancel("stream", task)
        assert task.cancelling() == 1
        assert owner.closed.is_set()
    finally:
        await rig.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("invoke_after_restart", [False, True])
async def test_p1_registered_watchdog_callback_cannot_cross_stop_before_coroutine_starts(
    monkeypatch: pytest.MonkeyPatch, invoke_after_restart: bool,
) -> None:
    """真实注册的旧回调及其未启动协程均不得跨 stop/start 批次复活。"""
    import concurrent.futures

    rig = Rig(monkeypatch)
    callbacks: list[Any] = []
    scheduled: list[Coroutine[Any, Any, bool]] = []
    futures: list[concurrent.futures.Future[bool]] = []

    def register(**kwargs: Any) -> None:
        """保存生产代码真正交给 WatchDog 的注册回调。"""
        callbacks.append(kwargs["restart_callback"])

    def queue(coro: Coroutine[Any, Any, bool], loop: asyncio.AbstractEventLoop) -> Any:
        """模拟线程安全队列已受理，但事件循环尚未执行协程。"""
        scheduled.append(coro)
        future: concurrent.futures.Future[bool] = concurrent.futures.Future()
        futures.append(future)
        return future

    monkeypatch.setattr("src.kernel.concurrency.get_watchdog", lambda: SimpleNamespace(register_stream=register))
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", queue)
    owner, task = await rig.launch()
    try:
        if not invoke_after_restart:
            callbacks[0]()
        owner.release.set()
        await rig.manager.stop()
        assert task.done()
        await rig.manager.start()
        if invoke_after_restart:
            callbacks[0]()
        assert len(scheduled) == 1
        result = await scheduled.pop()
        futures[0].set_result(result)
        assert result is False
        assert rig.context.stream_loop_task is None
        assert rig.manager._stats["total_loops"] == 1
    finally:
        for coro in scheduled:
            coro.close()
        await rig.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("new_input", ["none", "different", "same"])
async def test_p1_owner_clearing_context_before_done_only_removes_old_resume(
    monkeypatch: pytest.MonkeyPatch, single: bool, new_input: str,
) -> None:
    """等价真实运行器先清 None：旧恢复事件应清，新外部输入必须保留。"""
    rig = Rig(monkeypatch)
    owner, task = await rig.launch()
    old_event = object()
    new_event = old_event if new_input == "same" else object()
    rig.manager._pending_wait_resume_events["stream"] = old_event  # type: ignore[assignment]
    try:
        stopper = rig.create_task(rig.stop(single)).task
        await owner.cleaning.wait()
        if new_input != "none":
            await rig.manager.trigger_external_resume("stream", new_event)  # type: ignore[arg-type]
        owner.release.set()
        await stopper
        assert task.done()
        assert rig.context.stream_loop_task is None
        if new_input != "none":
            assert rig.manager._pending_wait_resume_events["stream"] is new_event
        else:
            assert "stream" not in rig.manager._pending_wait_resume_events
        assert not rig.manager._stop_resume_events
    finally:
        await rig.finish()


@pytest.mark.asyncio
async def test_p1_old_owner_cannot_clear_resume_after_new_generation_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新代先退出清 None 后，旧代仍无权清新代复用的同一事件对象。"""
    rig = Rig(monkeypatch)
    old, old_task = await rig.launch()
    old.resist_force_cancel = True
    event = object()
    rig.manager._pending_wait_resume_events["stream"] = event  # type: ignore[assignment]
    try:
        stopper = rig.create_task(rig.stop(True)).task
        await old.cleaning.wait()
        new, new_task = await rig.launch(force=True)
        new_task.cancel()
        await new.cleaning.wait()
        new.release.set()
        await asyncio.wait({new_task})
        assert rig.context.stream_loop_task is None
        rig.manager._pending_wait_resume_events["stream"] = event  # type: ignore[assignment]
        old.release.set()
        await stopper
        assert old_task.done()
        assert rig.manager._pending_wait_resume_events["stream"] is event
        assert not rig.manager._stop_resume_events
    finally:
        await rig.finish()
