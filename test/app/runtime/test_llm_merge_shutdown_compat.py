"""LLM 合并收口的共享池归属、退出观察和分发登记回归。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterable
from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.core.models.message import Message
from src.core.transport.distribution import distributor
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager
from src.kernel.concurrency import TaskManager
from src.kernel.concurrency.task_info import TaskInfo
from src.kernel.event import EventDecision


WATCHDOG_SECONDS = 3.0
PROTECTED_STEPS = (
    "_close_default_model_clients",
    "_close_database",
    "_close_vector_database",
    "_close_llm_statistics",
    "_close_telemetry",
)


async def wait_event(event: asyncio.Event) -> None:
    """等待明确屏障；超时仅用于发现挂起，不用于制造时序。"""
    async with asyncio.timeout(WATCHDOG_SECONDS):
        await event.wait()


async def checkpoint() -> None:
    """观察本轮已排队回调，不用 sleep 猜测任务进度。"""
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await wait_event(reached)


async def completed(task: asyncio.Future[Any]) -> Any:
    """有界观察退出并读取结果，不把等待者取消传给被观察者。"""
    _, pending = await asyncio.wait({task}, timeout=WATCHDOG_SECONDS)
    assert not pending, "测试任务未在 watchdog 内退出"
    return task.result()


async def settle(tasks: Iterable[asyncio.Future[Any]]) -> None:
    """回收测试持有的结果，包括预期取消和断言失败后的异常。"""
    pending_tasks = set(tasks)
    if not pending_tasks:
        return
    done, pending = await asyncio.wait(pending_tasks, timeout=WATCHDOG_SECONDS)
    for task in done:
        if not task.cancelled():
            task.exception()
    assert not pending, "测试清理后仍有未退出任务"


@pytest_asyncio.fixture
async def bot(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Bot]:
    """保留真实 TaskManager/TaskInfo，仅替换进程池工厂和外部资源关闭。"""
    pool = Mock(name="controlled_process_pool", spec=["shutdown", "submit"])
    monkeypatch.setattr(
        "src.kernel.concurrency.task_manager.ProcessPoolExecutor",
        Mock(name="controlled_process_pool_factory", return_value=pool),
    )
    manager = TaskManager()
    # 只观察调用，绝不执行共享池的强制关闭实现。
    monkeypatch.setattr(manager, "shutdown_process_pool", Mock(name="shared_pool_shutdown"))
    monkeypatch.setattr("src.kernel.concurrency.get_task_manager", lambda: manager)
    monkeypatch.setattr("src.kernel.concurrency.task_manager.get_task_manager", lambda: manager)
    owned_tasks: list[asyncio.Task[Any]] = []
    original_create = manager.create_task

    def create_task(
        coro: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any,
    ) -> TaskInfo:
        """同时保留已从 TaskManager 清理的真实任务，供 fixture 最终回收。"""
        info = original_create(coro, *args, **kwargs)
        assert info.task is not None
        owned_tasks.append(info.task)
        return info

    monkeypatch.setattr(manager, "create_task", create_task)
    instance = Bot()
    instance.task_manager = manager
    instance.ui = Mock()
    instance.logger = Mock()
    for name in PROTECTED_STEPS:
        monkeypatch.setattr(instance, name, AsyncMock(name=name))
    try:
        yield instance
    finally:
        for task in owned_tasks:
            if not task.done():
                task.cancel()
        await settle(owned_tasks)
        manager.cleanup_tasks()


def start(
    bot: Bot, coro: Coroutine[Any, Any, Any], name: str, *, daemon: bool = True,
) -> asyncio.Task[Any]:
    """通过被测实例的现有 TaskManager 创建真实任务。"""
    assert bot.task_manager is not None
    task = bot.task_manager.create_task(coro, name=name, daemon=daemon).task
    assert task is not None
    return task


@pytest_asyncio.fixture
async def network_runtime(
    bot: Bot, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[SimpleNamespace]:
    """安装自有线程池与 DNS 替身，不读取配置或发起 DNS/网络请求。"""
    loop = asyncio.get_running_loop()
    previous = getattr(loop, "_default_executor", None)
    original_addr, original_name = loop.getaddrinfo, loop.getnameinfo
    default = ThreadPoolExecutor(max_workers=1)
    dns = ThreadPoolExecutor(max_workers=1)
    default_shutdown = Mock(wraps=default.shutdown)
    dns_shutdown = Mock(wraps=dns.shutdown)
    monkeypatch.setattr(default, "shutdown", default_shutdown)
    monkeypatch.setattr(dns, "shutdown", dns_shutdown)
    bot._previous_default_executor = previous
    bot._default_executor, bot._dns_executor = default, dns
    bot._original_getaddrinfo, bot._original_getnameinfo = original_addr, original_name
    loop.set_default_executor(default)
    monkeypatch.setattr(loop, "getaddrinfo", AsyncMock())
    monkeypatch.setattr(loop, "getnameinfo", AsyncMock())
    runtime = SimpleNamespace(
        loop=loop, previous=previous, default=default, dns=dns,
        original_addr=original_addr, original_name=original_name,
        shutdowns=(default_shutdown, dns_shutdown),
    )
    try:
        yield runtime
    finally:
        # 测试先释放所有线程屏障，再在此回收；不依赖被测恢复逻辑兜底。
        loop.getaddrinfo, loop.getnameinfo = original_addr, original_name
        if previous is None:
            setattr(loop, "_default_executor", None)
        else:
            loop.set_default_executor(previous)
        ThreadPoolExecutor.shutdown(default, wait=True, cancel_futures=False)
        ThreadPoolExecutor.shutdown(dns, wait=True, cancel_futures=False)
        bot._default_executor = bot._dns_executor = None
        bot._original_getaddrinfo = bot._original_getnameinfo = None


def assert_loop_restored(runtime: SimpleNamespace) -> None:
    """检查宿主 loop 的可见配置已恢复，而不是等待 owner 完成后才恢复。"""
    assert runtime.loop.getaddrinfo == runtime.original_addr
    assert runtime.loop.getnameinfo == runtime.original_name
    assert getattr(runtime.loop, "_default_executor", None) is runtime.previous


def stream_source(
    bot: Bot, monkeypatch: pytest.MonkeyPatch, owner: asyncio.Task[Any], *, tracked: bool = True,
) -> StreamLoopManager:
    """使用真实 SLM owner 字典和退出回调，流管理器边界保持纯内存。"""
    source = StreamLoopManager()
    source.is_running = True
    context = SimpleNamespace(stream_loop_task=owner, is_chatter_processing=False)
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: SimpleNamespace(_streams={"stream": SimpleNamespace(context=context)}),
    )
    monkeypatch.setattr("src.core.transport.distribution.stream_loop_manager.logger", Mock())
    if tracked:
        source._track_stream_owner("stream", context, owner)
    bot._stream_loop_manager = source
    return source


async def test_managed_tasks_stop_without_shared_process_pool_shutdown(bot: Bot) -> None:
    """保留 dev 的守护/普通受管任务清理，但不关闭共享进程池或无关任务。"""
    entered = [asyncio.Event(), asyncio.Event()]

    async def worker(ready: asyncio.Event) -> None:
        """等待管理器发出正常取消请求。"""
        ready.set()
        await asyncio.Event().wait()

    managed = [
        start(bot, worker(entered[0]), "managed-daemon"),
        start(bot, worker(entered[1]), "managed-foreground", daemon=False),
    ]
    unrelated = asyncio.get_running_loop().create_task(asyncio.Event().wait())
    try:
        for ready in entered:
            await wait_event(ready)
        await bot._stop_task_manager()
        assert all(task.cancelled() and task.cancelling() == 1 for task in managed)
        assert not unrelated.done()
        assert bot.task_manager is not None
        bot.task_manager.shutdown_process_pool.assert_not_called()
        bot.task_manager._process_pool.shutdown.assert_not_called()
    finally:
        unrelated.cancel()
        for task in managed:
            if not task.done():
                task.cancel()
        await settle([*managed, unrelated])


@pytest.mark.parametrize("pending_kind", ["owner", "step"])
async def test_network_restore_keeps_queued_cleanup_alive(
    bot: Bot, network_runtime: SimpleNamespace, pending_kind: str,
) -> None:
    """已知 owner/步骤仍在等待队列时先恢复 loop，退出回调再温和回收一次。"""
    runtime = network_runtime
    release = ThreadEvent()
    occupied = [asyncio.Event(), asyncio.Event()]
    queued = asyncio.Event()
    blockers: list[asyncio.Future[Any]] = []
    owner: asyncio.Task[Any] | None = None

    def occupy(ready: asyncio.Event) -> None:
        """用有界线程屏障占满单线程池，确保清理工作仍真实排队。"""
        runtime.loop.call_soon_threadsafe(ready.set)
        assert release.wait(WATCHDOG_SECONDS * 2), "线程屏障未被测试释放"

    async def cleanup() -> list[str]:
        """保留两个真实排队 Future；提前 cancel_futures 会破坏此清理。"""
        default_job = runtime.loop.run_in_executor(runtime.default, lambda: "default-cleaned")
        dns_job = runtime.loop.run_in_executor(runtime.dns, lambda: "dns-cleaned")
        queued.set()
        return await asyncio.gather(default_job, dns_job)

    try:
        for pool, ready in zip((runtime.default, runtime.dns), occupied):
            blockers.append(runtime.loop.run_in_executor(pool, occupy, ready))
        for ready in occupied:
            await wait_event(ready)
        owner = start(bot, cleanup(), "queued-cleanup")
        await wait_event(queued)
        if pending_kind == "owner":
            bot._track_shutdown_owners([owner])
        else:
            bot._shutdown_step_tasks.add(owner)
            owner.add_done_callback(bot._shutdown_task_done)

        bot._restore_network_runtime()
        assert_loop_restored(runtime)
        assert not owner.done() and owner.cancelling() == 0
        bot._restore_network_runtime()
        for shutdown in runtime.shutdowns:
            shutdown.assert_not_called()

        release.set()
        assert await completed(owner) == ["default-cleaned", "dns-cleaned"]
        await settle(blockers)
        for shutdown in runtime.shutdowns:
            shutdown.assert_called_once_with(wait=False, cancel_futures=False)
        bot._restore_network_runtime()
        for shutdown in runtime.shutdowns:
            shutdown.assert_called_once_with(wait=False, cancel_futures=False)
    finally:
        release.set()
        await settle([*blockers, *([owner] if owner is not None else [])])


async def test_cancelled_shutdown_defers_pools_until_late_step_exit(
    bot: Bot, network_runtime: SimpleNamespace,
) -> None:
    """取消协调者也先恢复 loop；迟到失败由既有 done 回调触发池回收。"""
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    step_tasks: list[asyncio.Task[Any]] = []

    async def stop_http() -> None:
        """吞掉步骤取消后停在明确清理窗口，最后报告普通失败。"""
        task = asyncio.current_task()
        assert task is not None
        step_tasks.append(task)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert task.uncancel() == 0
            cleaning.set()
            await release.wait()
            raise RuntimeError("late-step-failure")

    bot.http_server = SimpleNamespace(stop=stop_http)
    coordinator = start(bot, bot.shutdown(timeout=WATCHDOG_SECONDS), "shutdown-coordinator")
    try:
        await wait_event(entered)
        coordinator.cancel()
        with pytest.raises(asyncio.CancelledError):
            await completed(coordinator)
        await wait_event(cleaning)
        assert_loop_restored(network_runtime)
        assert not step_tasks[0].done() and step_tasks[0].cancelling() == 0
        bot._restore_network_runtime()
        for shutdown in network_runtime.shutdowns:
            shutdown.assert_not_called()

        # 不要求新增日志机制；恢复不应依赖 logger 仍存在。
        bot.logger = None
        release.set()
        with pytest.raises(RuntimeError, match="late-step-failure"):
            await completed(step_tasks[0])
        for shutdown in network_runtime.shutdowns:
            shutdown.assert_called_once_with(wait=False, cancel_futures=False)
        bot._restore_network_runtime()
        for shutdown in network_runtime.shutdowns:
            shutdown.assert_called_once_with(wait=False, cancel_futures=False)
    finally:
        release.set()
        if not coordinator.done():
            coordinator.cancel()
        await settle([coordinator, *step_tasks])


async def test_early_coordinator_cancel_keeps_existing_slm_owner_pool_usable(
    bot: Bot, network_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 步骤前已有的精确 SLM owner，也不能因协调者提前取消而丢失池保护。"""
    entered, http_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    http_tasks: list[asyncio.Task[Any]] = []

    async def owner() -> str:
        """协调者退出后仍可向自己保存的池提交一次必要清理。"""
        entered.set()
        await release.wait()
        return await network_runtime.loop.run_in_executor(network_runtime.default, lambda: "late-cleaned")

    async def stop_http() -> None:
        """固定关闭流程在聊天流步骤之前，收到取消后立即退出。"""
        task = asyncio.current_task()
        assert task is not None
        http_tasks.append(task)
        http_entered.set()
        await asyncio.Event().wait()

    task = start(bot, owner(), "preexisting-slm-owner")
    stream_source(bot, monkeypatch, task)
    bot.http_server = SimpleNamespace(stop=stop_http)
    coordinator = start(bot, bot.shutdown(timeout=WATCHDOG_SECONDS), "early-shutdown")
    try:
        await wait_event(entered)
        await wait_event(http_entered)
        coordinator.cancel()
        with pytest.raises(asyncio.CancelledError):
            await completed(coordinator)
        await settle(http_tasks)
        assert_loop_restored(network_runtime)
        assert task.cancelling() == 0 and not task.done()
        for shutdown in network_runtime.shutdowns:
            shutdown.assert_not_called()
        release.set()
        assert await completed(task) == "late-cleaned"
        await checkpoint()
        for shutdown in network_runtime.shutdowns:
            shutdown.assert_called_once_with(wait=False, cancel_futures=False)
    finally:
        release.set()
        if not coordinator.done():
            coordinator.cancel()
        await settle([coordinator, task, *http_tasks])


async def test_slm_nonprefix_owner_is_captured_before_stop_callback_removes_it(
    bot: Bot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非聊天前缀 owner 在 stop 内完成并移出真实字典，Bot 仍已安装退出观察。"""
    entered = asyncio.Event()

    async def owner() -> None:
        """让 SLM 的唯一取消请求正常结束任务。"""
        entered.set()
        await asyncio.Event().wait()

    task = start(bot, owner(), "renamed-stream-owner")
    source = stream_source(bot, monkeypatch, task)
    observed_before_stop: set[asyncio.Task[Any]] = set()
    original_stop = source.stop
    done_callback = Mock(wraps=bot._shutdown_task_done)
    monkeypatch.setattr(bot, "_shutdown_task_done", done_callback)

    async def stop() -> None:
        """在 SLM 删除字典键之前记录 Bot 是否已捕获真实任务身份。"""
        observed_before_stop.update(bot._shutdown_owner_tasks)
        await original_stop()

    monkeypatch.setattr(source, "stop", stop)
    try:
        await wait_event(entered)
        await bot._stop_stream_loops()
        assert task.cancelled() and task not in source._stream_owners
        assert task in observed_before_stop
        done_callback.assert_any_call(task)
    finally:
        if not task.done():
            task.cancel()
        await settle([task])


async def test_slm_owner_discovered_during_cancelled_stop_is_observed(
    bot: Bot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop 内发现 owner 后取消真实停止任务，仍须经 finally 捕获并继续观察。"""
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observing = asyncio.Event()

    async def owner() -> None:
        """将取消计数清零后继续清理，使按 cancelling 猜测归属的实现暴露。"""
        task = asyncio.current_task()
        assert task is not None
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert task.uncancel() == 0
            cleaning.set()
            await release.wait()

    task = start(bot, owner(), "renamed-late-stream-owner")
    source = stream_source(bot, monkeypatch, task, tracked=False)
    stopper: asyncio.Task[Any] | None = None
    observer: asyncio.Task[Any] | None = None

    async def pending_observation(tasks: set[asyncio.Task[Any]]) -> None:
        """固定停止入口被取消的位置，不依赖 SLM 的实际五秒预算。"""
        assert tasks == {task}
        await wait_event(cleaning)
        observing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(source, "_observe_stream_owners", pending_observation)
    try:
        await wait_event(entered)
        assert not source._stream_owners
        stopper = start(bot, bot._stop_stream_loops(), "stop-stream-source")
        await wait_event(observing)
        stopper.cancel()
        with pytest.raises(asyncio.CancelledError):
            await completed(stopper)
        assert task in source._stream_owners and task.cancelling() == 0
        observer = start(bot, bot._stop_chat_tasks(), "observe-late-owner")
        await checkpoint()
        assert not observer.done(), "Bot 未观察 stop 内发现的非前缀 owner"
        assert not task.done() and task.cancelling() == 0
        release.set()
        await completed(observer)
        assert await completed(task) is None
    finally:
        release.set()
        observers = [item for item in (stopper, observer) if item is not None]
        for item in observers:
            if not item.done():
                item.cancel()
        if not task.done() and not cleaning.is_set():
            task.cancel()
        await settle([task, *observers])


@pytest.mark.parametrize("phase", ["chat", "task-manager"])
async def test_slm_uncancelled_owner_is_not_cancelled_again(
    bot: Bot, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    """同名不是同一 owner；SLM 已受理且 uncancel 清零的 owner 只能被观察。"""
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        """第一次取消后等待清理；任何第二次取消都会破坏正常返回。"""
        task = asyncio.current_task()
        assert task is not None
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert task.uncancel() == 0
            cleaning.set()
            await release.wait()

    task = start(bot, owner(), "chat_stream_same-name")
    unrelated = start(bot, asyncio.Event().wait(), "chat_stream_same-name")
    source = stream_source(bot, monkeypatch, task)
    observer: asyncio.Task[Any] | None = None

    async def pending_observation(tasks: set[asyncio.Task[Any]]) -> None:
        """只截断 SLM 的等待预算，保留真实的登记和唯一取消入口。"""
        assert tasks == {task}
        await wait_event(cleaning)

    monkeypatch.setattr(source, "_observe_stream_owners", pending_observation)
    try:
        await wait_event(entered)
        await bot._stop_stream_loops()
        assert task.get_name() == unrelated.get_name() and task is not unrelated
        callback = bot._stop_chat_tasks if phase == "chat" else bot._stop_task_manager
        observer = start(bot, callback(), "observe-owned-tasks")
        await checkpoint()
        assert unrelated.cancelled() and unrelated.cancelling() == 1
        assert not task.done() and task.cancelling() == 0
        if phase == "chat":
            assert not observer.done()
        else:
            await completed(observer)
        release.set()
        await completed(observer)
        assert await completed(task) is None
    finally:
        release.set()
        if observer is not None and not observer.done():
            observer.cancel()
        if not task.done() and not cleaning.is_set():
            task.cancel()
        if not unrelated.done():
            unrelated.cancel()
        await settle([task, unrelated, *([observer] if observer is not None else [])])


async def test_slm_exit_error_survives_task_manager_cleanup(
    bot: Bot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TaskInfo 已清理且 owner 字典已空时，SLM 的安全退出摘要仍阻止资源关闭。"""
    failure_detail = "private-owner-failure-detail"

    async def fail() -> None:
        """提供普通退出异常，不访问模型或数据库。"""
        raise RuntimeError(failure_detail)

    task = start(bot, fail(), "renamed-failed-owner")
    source = stream_source(bot, monkeypatch, task)
    with pytest.raises(RuntimeError, match=failure_detail):
        await completed(task)
    assert not source._stream_owners
    assert list(source._stream_exit_errors) == [(1, "RuntimeError")]
    assert bot.task_manager is not None
    assert bot.task_manager.cleanup_tasks() == 1
    assert not bot.task_manager.get_all_tasks()

    with pytest.raises(BotShutdownError) as raised:
        await bot.shutdown(timeout=WATCHDOG_SECONDS)
    assert failure_detail not in str(raised.value)
    for name in PROTECTED_STEPS:
        getattr(bot, name).assert_not_awaited()
    bot.ui.display_success.assert_not_called()


async def test_http_stop_failure_keeps_model_and_storage_dependencies(bot: Bot) -> None:
    """HTTP 普通关闭异常必须留下 sticky 失败，不能被后续空任务集合抹掉。"""
    bot.http_server = SimpleNamespace(
        stop=AsyncMock(side_effect=RuntimeError("private-http-stop-detail")),
    )
    with pytest.raises(BotShutdownError, match="HTTP: RuntimeError") as raised:
        await bot.shutdown(timeout=WATCHDOG_SECONDS)
    bot.http_server.stop.assert_awaited_once()
    assert "private-http-stop-detail" not in str(raised.value)
    for name in PROTECTED_STEPS:
        getattr(bot, name).assert_not_awaited()
    bot.ui.display_success.assert_not_called()


async def test_distributor_registers_dispatch_before_event_callback_returns(
    bot: Bot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行真实消息回调；返回时实际后台 Task 已由现有 TaskManager 登记。"""
    entered, release = asyncio.Event(), asyncio.Event()
    context = SimpleNamespace(allow_message_buffer=True, stream_loop_task=None)
    chat_stream = SimpleNamespace(stream_id="offline-stream", context=context)

    async def get_or_create_stream(**kwargs: Any) -> SimpleNamespace:
        """把真实 dispatch 固定在数据库边界之前。"""
        entered.set()
        await release.wait()
        return chat_stream

    streams = SimpleNamespace(
        get_or_create_stream=AsyncMock(side_effect=get_or_create_stream),
        add_message=AsyncMock(),
    )
    source = SimpleNamespace(is_running=True, start_stream_loop=AsyncMock())
    monkeypatch.setattr("src.core.managers.stream_manager.get_stream_manager", lambda: streams)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager", lambda: source,
    )
    monkeypatch.setattr(distributor, "logger", Mock())
    message = Message(
        message_id="offline-message", content="", sender_id="offline-user",
        platform="offline", chat_type="private", stream_id="offline-stream",
    )
    params: dict[str, object] = {"message": message}
    created: list[asyncio.Task[Any]] = []
    original_create = asyncio.create_task

    def capture_task(
        coro: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any,
    ) -> asyncio.Task[Any]:
        """也捕获旧实现的裸任务，确保预期红灯路径不会泄漏后台任务。"""
        task = original_create(coro, *args, **kwargs)
        created.append(task)
        return task

    try:
        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "create_task", capture_task)
            decision, returned = await distributor._on_message_received("offline-event", params)
        # 此前没有 await 屏障：登记必须在事件回调返回这一时点已经成立。
        assert decision is EventDecision.SUCCESS and returned is params
        assert len(created) == 1
        assert bot.task_manager is not None
        assert created[0] in {info.task for info in bot.task_manager.get_all_tasks()}
        await wait_event(entered)
        assert not created[0].done()
        release.set()
        await completed(created[0])
        streams.add_message.assert_awaited_once_with(message)
        source.start_stream_loop.assert_awaited_once_with("offline-stream")
    finally:
        release.set()
        await settle(created)
