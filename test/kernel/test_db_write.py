"""短写事务的真实 SQLite 锁竞争、重试边界及取消回收回归。"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock

from src.kernel.db.api.write import run_write_transaction
from src.kernel.db.api import write
from src.kernel.db.core.session import CancellationSafeAsyncSession, dispose_engine_safely


@pytest.fixture
async def write_db(tmp_path) -> AsyncIterator[async_sessionmaker]:
    """每个会话使用独立文件连接，并将锁等待缩短以验证真实失败。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'write.db').as_posix()}",
        connect_args={"timeout": 0.03},
    )
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("CREATE TABLE records (value INTEGER NOT NULL)"))
    factory = async_sessionmaker(engine, class_=CancellationSafeAsyncSession, expire_on_commit=False)
    try:
        yield factory
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_busy_retries_fresh_transaction_after_external_writer_releases(write_db) -> None:
    """首轮真实 SQLITE_BUSY 后重新开事务，外部写者释放后正确提交。"""
    engine = write_db.kw["bind"]
    second_attempt = asyncio.Event()
    attempts = 0

    def count_attempts(conn, cursor, statement, parameters, context, executemany) -> None:
        """在第二次真实写锁尝试开始时放行外部持有者。"""
        nonlocal attempts
        if statement == "BEGIN IMMEDIATE":
            attempts += 1
            if attempts == 2:
                second_attempt.set()

    async with engine.connect() as blocker:
        await blocker.execute(text("BEGIN IMMEDIATE"))
        event.listen(engine.sync_engine, "before_cursor_execute", count_attempts)

        async def insert(session) -> int:
            """短事务只写一次已提交的记录。"""
            await session.execute(text("INSERT INTO records VALUES (42)"))
            return 42

        task = asyncio.create_task(run_write_transaction(insert, session_factory=write_db))
        try:
            await asyncio.wait_for(second_attempt.wait(), 3)
            await blocker.commit()
            assert await asyncio.wait_for(task, 3) == 42
        finally:
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            event.remove(engine.sync_engine, "before_cursor_execute", count_attempts)
    async with write_db() as session:
        assert (await session.scalars(text("SELECT value FROM records"))).all() == [42]
    assert attempts == 2


@pytest.mark.asyncio
async def test_persistent_lock_is_bounded_and_propagates(write_db) -> None:
    """持续被占用的锁不会无限重试，也不伪装成写入成功。"""
    called = False

    async def operation(session) -> None:
        """没有取得写权限前不能运行回调。"""
        nonlocal called
        called = True

    async with write_db.kw["bind"].connect() as blocker:
        await blocker.execute(text("BEGIN IMMEDIATE"))
        with pytest.raises(OperationalError) as error:
            await asyncio.wait_for(run_write_transaction(operation, session_factory=write_db), 3)
        assert error.value.orig.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert not called
        await blocker.rollback()


@pytest.mark.asyncio
async def test_callback_error_rolls_back_and_is_not_retried(write_db) -> None:
    """非锁 SQL 错误只执行一次，回调先前的写入完整回滚。"""
    calls = 0

    async def operation(session) -> None:
        """写入后触发真实 SQL 错误。"""
        nonlocal calls
        calls += 1
        await session.execute(text("INSERT INTO records VALUES (1)"))
        await session.execute(text("INSERT INTO missing_table VALUES (1)"))

    with pytest.raises(OperationalError, match="no such table"):
        await run_write_transaction(operation, session_factory=write_db)
    assert calls == 1
    async with write_db() as session:
        assert (await session.scalar(text("SELECT count(*) FROM records"))) == 0


@pytest.mark.asyncio
async def test_cancel_during_write_rolls_back_and_returns_connection(write_db) -> None:
    """回调取消依然回滚，不遗留写锁或池中连接。"""
    written = asyncio.Event()

    async def operation(session) -> None:
        """写入后暂停，在业务等待点接收取消。"""
        await session.execute(text("INSERT INTO records VALUES (1)"))
        written.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run_write_transaction(operation, session_factory=write_db))
    await asyncio.wait_for(written.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with write_db() as session:
        assert (await session.scalar(text("SELECT count(*) FROM records"))) == 0


@pytest.mark.asyncio
async def test_cancel_while_driver_returns_busy_does_not_retry(write_db) -> None:
    """取消和驱动 BUSY 同时到达时，不能在取消后重新开始回调。"""
    engine = write_db.kw["bind"]
    entered = asyncio.Event()
    attempts = 0

    def before_execute(conn, cursor, statement, parameters, context, executemany) -> None:
        """定位实际驱动开始等待写锁的时刻。"""
        nonlocal attempts
        if statement == "BEGIN IMMEDIATE":
            attempts += 1
            entered.set()

    async with engine.connect() as blocker:
        await blocker.execute(text("BEGIN IMMEDIATE"))
        event.listen(engine.sync_engine, "before_cursor_execute", before_execute)
        operation = AsyncMock()
        task = asyncio.create_task(run_write_transaction(operation, session_factory=write_db))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            operation.assert_not_awaited()
            assert attempts == 1
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before_execute)
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_default_factory_preserves_busy_timeout_and_enforces_foreign_keys(write_db, monkeypatch) -> None:
    """默认入口使用宿主工厂，保持 timeout，并启用 SQLite 外键约束。"""
    monkeypatch.setattr(write, "get_session_factory", AsyncMock(return_value=write_db))

    async def inspect_settings(session) -> tuple[int, int]:
        """读取与当前写事务相同连接的配置。"""
        return (
            await session.scalar(text("PRAGMA busy_timeout")),
            await session.scalar(text("PRAGMA foreign_keys")),
        )

    assert await run_write_transaction(inspect_settings) == (30, 1)


@pytest.mark.asyncio
async def test_waiting_writer_is_cancellable_without_checkout(write_db) -> None:
    """同引擎写队列等待不占连接，取消等待者后不执行其写入。"""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first_operation(session) -> None:
        """持有一个实际写事务以稳定观察队列等待状态。"""
        await session.execute(text("INSERT INTO records VALUES (1)"))
        entered.set()
        await release.wait()

    first = asyncio.create_task(run_write_transaction(first_operation, session_factory=write_db))
    await asyncio.wait_for(entered.wait(), 3)
    operation = AsyncMock()
    waiting = asyncio.create_task(run_write_transaction(operation, session_factory=write_db))
    try:
        await asyncio.sleep(0)
        assert write_db.kw["bind"].pool.checkedout() == 1
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        operation.assert_not_awaited()
        release.set()
        await asyncio.wait_for(first, 3)
    finally:
        release.set()
        await asyncio.gather(first, waiting, return_exceptions=True)
    async with write_db() as session:
        assert (await session.scalars(text("SELECT value FROM records"))).all() == [1]


@pytest.mark.asyncio
async def test_engine_close_cancels_active_and_queued_writers(write_db) -> None:
    """引擎关闭同时等待写入者与无连接的排队者，不能在 dispose 后补写。"""
    entered = asyncio.Event()

    async def first_operation(session) -> None:
        """保持一个尚未提交的真实写入。"""
        await session.execute(text("INSERT INTO records VALUES (1)"))
        entered.set()
        await asyncio.Event().wait()

    first = asyncio.create_task(run_write_transaction(first_operation, session_factory=write_db))
    await asyncio.wait_for(entered.wait(), 3)
    operation = AsyncMock()
    waiting = asyncio.create_task(run_write_transaction(operation, session_factory=write_db))
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(dispose_engine_safely(write_db.kw["bind"]), 3)
        results = await asyncio.gather(first, waiting, return_exceptions=True)
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        operation.assert_not_awaited()
        assert write_db.kw["bind"].pool.checkedout() == 0
        async with write_db() as session:
            assert (await session.scalar(text("SELECT count(*) FROM records"))) == 0
    finally:
        for task in (first, waiting):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, waiting, return_exceptions=True)
