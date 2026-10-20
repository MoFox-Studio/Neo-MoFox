"""全局任务清扫必须保留不可中断的资源操作及其等待调用链。"""

from __future__ import annotations

import asyncio

import pytest

from src.kernel.concurrency.cleanup import (
    await_cancellation_safe, cancel_and_wait_tasks, cleanup_ancestors,
    drain_cleanup_tasks, is_cleanup_task,
)


async def test_global_sweep_waits_for_protected_operation() -> None:
    """模拟 all_tasks 全局清扫，不能直接取消 shield 里的资源操作。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = False
    caller = asyncio.current_task()

    async def operation() -> None:
        """资源操作必须完成，不能收到全局清扫的取消。"""
        nonlocal finished
        assert is_cleanup_task(asyncio.current_task())
        entered.set()
        await release.wait()
        finished = True

    async def work() -> None:
        """业务等待受保护操作。"""
        await await_cancellation_safe(operation())

    async def sweep() -> None:
        """以真实全局快照取消所有业务任务，同时排除调用者。"""
        assert caller in cleanup_ancestors()
        await cancel_and_wait_tasks(asyncio.all_tasks())
        await drain_cleanup_tasks()

    worker = asyncio.create_task(work())
    try:
        await entered.wait()
        timer = asyncio.get_running_loop().call_later(0.02, release.set)
        try:
            await await_cancellation_safe(sweep())
        finally:
            timer.cancel()
        assert worker.cancelled()
        assert finished
        assert not [task for task in asyncio.all_tasks() if is_cleanup_task(task)]
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


async def test_nested_operations_share_cleanup_task() -> None:
    """嵌套清理不派生多层 task，返回值和真实异常都保持原样。"""
    async def nested() -> int:
        """验证内部操作复用外部清理任务。"""
        owner = asyncio.current_task()

        async def inner() -> int:
            """返回可验证结果。"""
            assert asyncio.current_task() is owner
            return 42

        return await await_cancellation_safe(inner())

    assert await await_cancellation_safe(nested()) == 42
    failure = ValueError("driver failure")

    async def fail() -> None:
        """真实驱动错误不能被清理辅助函数吞掉。"""
        raise failure

    with pytest.raises(ValueError) as caught:
        await await_cancellation_safe(fail())
    assert caught.value is failure


async def test_operation_cancellation_is_not_retried_forever() -> None:
    """操作自身取消时应立即传播，避免等待循环卡死。"""
    async def cancelled() -> None:
        """模拟资源操作自身取消。"""
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await await_cancellation_safe(cancelled())


async def test_repeated_caller_cancellation_waits_for_completion() -> None:
    """重复取消调用者时，操作结果先完成，随后向调用者传递取消。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def operation() -> None:
        """模拟正在回滚的资源。"""
        nonlocal finished
        entered.set()
        await release.wait()
        finished = True

    worker = asyncio.create_task(await_cancellation_safe(operation()))
    try:
        await entered.wait()
        for _ in range(3):
            worker.cancel()
            await asyncio.sleep(0)
        assert not worker.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert finished
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
