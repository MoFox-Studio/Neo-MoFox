"""跟踪不可中断的资源收尾，供正常取消和全局停机共同使用。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterable
from typing import Any, TypeVar

_T = TypeVar("_T")
_cleanup_tasks: dict[asyncio.Task[Any], asyncio.Task[Any] | None] = {}


def is_cleanup_task(task: asyncio.Task[Any]) -> bool:
    """识别清理任务本身，避免全局清扫直接取消 shield 内的操作。"""
    return task in _cleanup_tasks


def cleanup_ancestors() -> set[asyncio.Task[Any]]:
    """返回当前清理任务的等待方，防止停机反过来等待自己的调用者。"""
    ancestors: set[asyncio.Task[Any]] = set()
    task = asyncio.current_task()
    while task in _cleanup_tasks:
        task = _cleanup_tasks[task]
        if task is None or task in ancestors:
            break
        ancestors.add(task)
    return ancestors


async def await_cancellation_safe(
    operation: Coroutine[Any, Any, _T], *, name: str = "resource_cleanup",
) -> _T:
    """操作完成后再传播取消；嵌套清理直接复用已受保护的任务。"""
    current = asyncio.current_task()
    if current in _cleanup_tasks:
        return await operation
    cleanup = asyncio.create_task(operation, name=name)
    _cleanup_tasks[cleanup] = current
    cancellation: asyncio.CancelledError | None = None
    try:
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                if cleanup.cancelled():
                    raise
                cancellation = exc
        result = cleanup.result()
        if cancellation is not None:
            raise cancellation
        return result
    finally:
        _cleanup_tasks.pop(cleanup, None)


async def cancel_and_wait_tasks(tasks: Iterable[asyncio.Task[Any]]) -> None:
    """取消业务任务并等待其 finally；排除清理任务和当前停机调用链。"""
    excluded = cleanup_ancestors() | {asyncio.current_task()}
    pending = {
        task for task in tasks
        if not task.done() and task not in excluded and not is_cleanup_task(task)
    }
    for task in pending:
        if not task.cancelling():
            task.cancel()

    async def wait() -> None:
        """完整等待取消清理，不因调用者再次取消而遗留后台任务。"""
        await asyncio.gather(*pending, return_exceptions=True)

    if pending:
        await await_cancellation_safe(wait(), name="wait_cancelled_tasks")


async def drain_cleanup_tasks() -> None:
    """停机时等待剩余资源收尾，包括未被任务管理器收录的清理操作。"""
    excluded = cleanup_ancestors() | {asyncio.current_task()}
    loop = asyncio.get_running_loop()
    while pending := {
        task for task in _cleanup_tasks
        if task not in excluded and task.get_loop() is loop and not task.done()
    }:
        await asyncio.gather(*pending, return_exceptions=True)
