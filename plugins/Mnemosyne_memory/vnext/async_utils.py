"""Mnemosyne 异步任务与数据库清理辅助函数。"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


async def await_cancellation_safe(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """完成数据库清理操作后再传播等待期间收到的任务取消。"""
    cleanup_task = asyncio.create_task(coroutine)
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(cleanup_task)
            break
        except asyncio.CancelledError:
            if cleanup_task.cancelled():
                raise
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError
    return result
