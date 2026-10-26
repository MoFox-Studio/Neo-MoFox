"""Mnemosyne 异步任务与数据库清理辅助函数。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from src.kernel.concurrency import get_task_manager

_T = TypeVar("_T")
_cleanup_tasks: dict[asyncio.Task[Any], asyncio.Task[Any] | None] = {}


def cleanup_ancestors() -> set[asyncio.Task[Any]]:
    """取得当前资源收尾的调用链，避免关闭数据库时等待自己的调用者。"""
    ancestors: set[asyncio.Task[Any]] = set()
    task = asyncio.current_task()
    while task in _cleanup_tasks:
        task = _cleanup_tasks[task]
        if task is None or task in ancestors:
            break
        ancestors.add(task)
    return ancestors


async def await_cancellation_safe(
    operation: Coroutine[Any, Any, _T], *, name: str = "mnemosyne_cleanup",
) -> _T:
    """等待受管资源操作完成后再传播取消；嵌套操作复用同一保护任务。"""
    current = asyncio.current_task()
    if current in _cleanup_tasks:
        return await operation
    cleanup = get_task_manager().create_task(
        operation, name=name, daemon=True,
        metadata={"plugin": "mnemosyne_memory", "resource_cleanup": True},
    ).task
    assert cleanup is not None
    _cleanup_tasks[cleanup] = current
    cancellation: asyncio.CancelledError | None = None
    try:
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                if cleanup.cancelled():
                    raise
                cancellation = error
        result = cleanup.result()
        if cancellation is not None:
            raise cancellation
        return result
    finally:
        _cleanup_tasks.pop(cleanup, None)

__all__ = ["await_cancellation_safe"]
