"""执行可重试的短数据库写事务，不在事务内执行网络或其他外部副作用。"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import TypeVar
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.kernel.db.core.session import get_session_factory, track_engine_writer, transaction_session

_T = TypeVar("_T")
_sqlite_writer_locks: WeakKeyDictionary[
    Engine, tuple[asyncio.AbstractEventLoop, asyncio.Lock]
] = WeakKeyDictionary()


def _writer_queue(factory: async_sessionmaker) -> AbstractAsyncContextManager:
    """同一事件循环及 SQLite 引擎先排队，等待时不占用池连接或驱动线程。"""
    bind = factory.kw.get("bind")
    if bind is None or bind.dialect.name != "sqlite":
        return nullcontext()
    engine = bind.sync_engine
    loop = asyncio.get_running_loop()
    entry = _sqlite_writer_locks.get(engine)
    if entry is None or entry[0] is not loop:
        entry = (loop, asyncio.Lock())
        _sqlite_writer_locks[engine] = entry
    return entry[1]


async def run_write_transaction(
    operation: Callable[[AsyncSession], Awaitable[_T]],
    *,
    session_factory: async_sessionmaker | None = None,
) -> _T:
    """运行仅含数据库操作的短事务，SQLite 在读取前取得写权限。

    同一 SQLite 引擎先按异步队列串行写入；跨引擎/进程的 BUSY/LOCKED
    会完整回滚后以新会话重试，最多三次；其他
    异常和取消直接传播。回调可能重复执行，不能包含网络、文件等副作用。
    单次锁等待沿用引擎 timeout，不修改池内连接的 PRAGMA 设置。
    """
    factory = session_factory or await get_session_factory()
    with track_engine_writer(factory):
        async with _writer_queue(factory):
            return await _run_write_attempts(operation, factory)


async def _run_write_attempts(
    operation: Callable[[AsyncSession], Awaitable[_T]],
    factory: async_sessionmaker,
) -> _T:
    """在已取得应用层写入名额后，处理数据库自身的外部锁竞争。"""
    for attempt in range(3):
        try:
            async with transaction_session(factory) as session:
                if session.get_bind().dialect.name == "sqlite":
                    await session.execute(text("PRAGMA foreign_keys = ON"))
                    # 在 SELECT 前排队，避免读快照升级和并发的查询后插入。
                    await session.execute(text("BEGIN IMMEDIATE"))
                return await operation(session)
        except OperationalError as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                # 驱动返回 BUSY 的同时调用方已取消时，不得重新启动写入。
                raise asyncio.CancelledError from exc
            code = getattr(exc.orig, "sqlite_errorcode", 0)
            if code & 0xFF not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 2:
                raise
            await asyncio.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")
