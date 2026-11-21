"""用 Event 补齐 P1-A 关闭锁等待、新取消和重试交接的确定性分支。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.kernel.concurrency import TaskManager
from src.kernel.llm.exceptions import LLMTimeoutError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.model_client.gemini_client import _ClientLease, _OwnedStream, _close_after_error
from test.kernel.llm.test_p1_exception_arbitration import Source, consume, managed_response


class WaitingLock(asyncio.Lock):
    """只标记真实进入锁等待的窗口，锁行为仍由 asyncio.Lock 实现。"""

    def __init__(self) -> None:
        """创建正常互斥锁和第二个调用者等待事件。"""
        super().__init__()
        self.waiting = asyncio.Event()

    async def acquire(self) -> bool:
        """在已占用时标记等待窗口，不修改等待或取消语义。"""
        if self.locked():
            self.waiting.set()
        return await super().acquire()


@pytest.fixture
def manager() -> TaskManager:
    """提供项目托管任务，不创建进程池工作。"""
    manager = TaskManager()
    manager.shutdown_process_pool(wait=False)
    return manager


async def test_source_lock_wait_new_cancel_preserves_control(manager: TaskManager) -> None:
    """读错后等待关闭锁收到新取消，必须终止原读错并保留真实任务取消。"""
    source = Source(text=False, error=ValueError("read-primary"))
    response, stream, terminal, resume = managed_response(source)
    lock = WaitingLock()
    await lock.acquire()
    stream._source_lock = lock

    async def finish(error: BaseException | None, usage: dict[str, Any] | None,
                     latency: float, phase: str) -> None:
        """释放测试持有的锁，让消费包装器完成后续资源清理。"""
        assert isinstance(error, asyncio.CancelledError) and phase == "cancelled"
        assert source.closed == 0
        lock.release()

    terminal.side_effect = finish
    task = manager.create_task(consume(response, "await"), name="p1-source-lock-cancel", daemon=True).task
    assert task is not None
    await lock.waiting.wait()
    task.cancel("source-lock-new-cancel")
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert raised.value.args == ("source-lock-new-cancel",)
    assert task.cancelled() and source.closed == 1
    assert stream.phase == "cancelled" and terminal.await_count == 1
    resume.assert_not_awaited()


async def test_close_lock_wait_new_cancel_does_not_interrupt_owner(manager: TaskManager) -> None:
    """第二个关闭者等待锁时的新取消只终止自己，原关闭者仍能完成释放。"""
    source = Source(text=False, block_close=True)
    response, stream, terminal, _ = managed_response(source)
    lock = WaitingLock()
    stream._close_lock = lock
    first = manager.create_task(response.aclose(), name="p1-lock-close-owner", daemon=True).task
    assert first is not None
    await source.close_entered.wait()
    second = manager.create_task(response.aclose(), name="p1-lock-close-waiter", daemon=True).task
    assert second is not None
    await lock.waiting.wait()
    second.cancel("close-lock-new-cancel")
    with pytest.raises(asyncio.CancelledError) as raised:
        await second
    assert raised.value.args == ("close-lock-new-cancel",)
    assert second.cancelled() and not first.done()
    source.close_release.set()
    await first
    await response.aclose()
    assert source.closed == 1 and terminal.await_count == 1
    assert stream.phase == "abandoned"


async def test_explicit_close_terminal_new_cancel_is_cached(manager: TaskManager) -> None:
    """未启动流显式关闭时观察器收到新取消，对外传播且重复关闭报告同一结果。"""
    source = Source(text=False)
    response, stream, terminal, _ = managed_response(source)
    entered = asyncio.Event()

    async def finish(error: BaseException | None, usage: dict[str, Any] | None,
                     latency: float, phase: str) -> None:
        """关闭源之后固定等待观察器中的真实取消窗口。"""
        assert source.closed == 1 and phase == "abandoned"
        entered.set()
        await asyncio.Event().wait()

    terminal.side_effect = finish
    task = manager.create_task(response.aclose(), name="p1-close-observer-cancel", daemon=True).task
    assert task is not None
    await entered.wait()
    task.cancel("terminal-new-cancel")
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert raised.value.args == ("terminal-new-cancel",) and task.cancelled()
    with pytest.raises(asyncio.CancelledError) as repeated:
        await stream.aclose()
    assert repeated.value is raised.value
    assert terminal.await_count == 1 and source.closed == 1


async def test_managed_explicit_close_replays_ordinary_failure() -> None:
    """直接 ManagedStream.aclose 保留同一普通关闭错误，不重复关闭源。"""
    failure = RuntimeError("close-only")
    source = Source(text=False, close_error=failure)
    _, stream, terminal, _ = managed_response(source)
    for _ in range(2):
        with pytest.raises(RuntimeError) as raised:
            await stream.aclose()
        assert raised.value is failure
    assert terminal.await_count == 1 and source.closed == 1
    assert stream.phase == "failed"


async def test_retry_resume_without_stream_is_independent_failure() -> None:
    """源成功释放后 replacement 缺流直接报错，不重记原尝试终态或二次重试。"""
    primary = LLMTimeoutError("read-primary")
    source = Source(text=False, error=primary)
    response, stream, terminal, resume = managed_response(source)
    resume.side_effect = None
    resume.return_value = SimpleNamespace(_stream=None)
    with pytest.raises(RuntimeError, match="Streaming retry returned no stream"):
        await response
    assert source.closed == 1 and stream.phase == "failed"
    assert terminal.await_count == 1 and terminal.await_args.args[0] is primary
    assert stream.context.retry_count == 1 and stream.context.session.calls == 1
    resume.assert_awaited_once()


async def test_usage_only_event_and_duplicate_finish_do_not_publish_again() -> None:
    """仅 usage 事件不算外部内容暴露；终态完成后重复 finish 不再通知观察器。"""
    source = Source(text=False)
    source.events = iter([StreamEvent(usage={"total_tokens": 3})])
    response, stream, terminal, _ = managed_response(source)
    await consume(response, "events")
    assert not stream.exposed and stream.usage == {"total_tokens": 3}
    assert stream.phase == "completed" and source.closed == 1
    await stream._finish(ValueError("late-observer"), "failed")
    assert terminal.await_count == 1 and stream.phase == "completed"
    assert terminal.await_args.args[0] is None


async def test_adapter_read_cleanup_new_cancel_remains_fresh(manager: TaskManager) -> None:
    """adapter 读取主错后的真实清理等待收到新取消，不可恢复为普通读取错误。"""
    events = Source(text=False, error=ValueError("read-primary"))
    resource = Source(text=False, block_close=True)
    lease = _ClientLease(sdk=None, http=None, active=1)
    adapter = _OwnedStream(events, resource, lease)
    task = manager.create_task(anext(adapter), name="p1-adapter-cleanup-cancel", daemon=True).task
    assert task is not None
    await resource.close_entered.wait()
    task.cancel("adapter-new-cancel")
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert raised.value.args == ("adapter-new-cancel",) and task.cancelled()
    resource.close_release.set()
    assert events.closed == resource.closed == 1 and lease.active == 0
    with pytest.raises(asyncio.CancelledError) as repeated:
        await adapter.aclose()
    assert repeated.value is raised.value


async def test_adapter_close_lock_wait_cancel_does_not_release_owner(manager: TaskManager) -> None:
    """adapter 并发关闭等待者取消仅作用于等待者，owner 仍完成一次真实关闭。"""
    events, resource = Source(text=False), Source(text=False, block_close=True)
    lease = _ClientLease(sdk=None, http=None, active=1)
    adapter = _OwnedStream(events, resource, lease)
    lock = WaitingLock()
    adapter._close_lock = lock
    first = manager.create_task(adapter.aclose(), name="p1-adapter-close-owner", daemon=True).task
    assert first is not None
    await resource.close_entered.wait()
    second = manager.create_task(adapter.aclose(), name="p1-adapter-close-waiter", daemon=True).task
    assert second is not None
    await lock.waiting.wait()
    second.cancel("adapter-lock-cancel")
    with pytest.raises(asyncio.CancelledError) as raised:
        await second
    assert raised.value.args == ("adapter-lock-cancel",) and second.cancelled()
    assert not first.done() and lease.active == 1
    resource.close_release.set()
    await first
    await adapter.aclose()
    assert events.closed == resource.closed == 1 and lease.active == 0


async def test_unmanaged_explicit_context_reports_unique_close_error() -> None:
    """非托管旧流不自动扩大关闭所有权，但显式上下文的唯一关闭故障仍可见。"""
    failure = RuntimeError("legacy-close-only")
    source = Source(text=False, close_error=failure)
    response, _, terminal, _ = managed_response(source, managed=False)
    with pytest.raises(RuntimeError) as raised:
        async with response:
            pass
    assert raised.value is failure and source.closed == 1 and response._consumed
    terminal.assert_not_awaited()


async def test_adapter_close_after_error_new_cancel_overrides_primary(manager: TaskManager) -> None:
    """adapter 内部清理普通资源 await 实际收到的新取消必须替代普通主错。"""
    source = Source(text=False, block_close=True)
    task = manager.create_task(_close_after_error(source, ValueError("read-primary")),
                               name="p1-adapter-error-cleanup-cancel", daemon=True).task
    assert task is not None
    await source.close_entered.wait()
    task.cancel("close-after-error-cancel")
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert task.cancelled() and raised.value.args == ("close-after-error-cancel",)
    source.close_release.set()
    assert source.closed == 1


async def test_adapter_abandon_does_not_hide_unique_close_failure() -> None:
    """正常放弃信号不能掩盖唯一资源关闭故障，明确覆盖仲裁后的抛出出口。"""
    failure = RuntimeError("offline-close-only")
    source = Source(text=False, close_error=failure)
    with pytest.raises(RuntimeError) as caught:
        await _close_after_error(source, GeneratorExit())
    assert caught.value is failure
    assert source.closed == 1
