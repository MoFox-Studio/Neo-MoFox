"""数据库任务取消时，事务收尾必须完成并归还连接。"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.util.concurrency import await_only

from src.app.plugin_system.api.storage_api import PluginDatabase
from src.kernel.db.api.crud import _get_session_ctx
from src.kernel.db.core import session as session_module
from src.kernel.db.core.exceptions import DatabaseTransactionError
from plugins.Mnemosyne_memory.vnext.schema import _MnemosynePluginDatabase

DatabaseContext = tuple[
    PluginDatabase, Callable[[], AbstractAsyncContextManager[AsyncSession]]
]


class _Base(DeclarativeBase):
    """隔离回归测试的 ORM 元数据。"""


class _Entry(_Base):
    """在自动提交时触发唯一约束错误的模型。"""

    __tablename__ = "entries"
    value: Mapped[int] = mapped_column(primary_key=True)


@pytest.fixture(params=["main", "custom", "plugin", "mnemosyne"])
async def database_context(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[DatabaseContext]:
    """用临时 SQLite 覆盖主库、独立 CRUD 和插件原始会话入口。"""
    db_class = _MnemosynePluginDatabase if request.param == "mnemosyne" else PluginDatabase
    db = db_class(str(tmp_path / "cancellation.db"), [])
    await db.initialize()
    factory = db._require_initialized()
    monkeypatch.setattr(session_module, "_session_factory", factory)
    monkeypatch.setattr(session_module, "get_configured_db_type", lambda: "sqlite")
    monkeypatch.setattr(session_module, "record_db_lifecycle_event", AsyncMock())
    contexts = {
        "main": session_module.get_db_session,
        "custom": lambda: _get_session_ctx(factory),
        "plugin": db.session,
        "mnemosyne": db.session,
    }
    async with db._engine.begin() as connection:
        await connection.execute(text("CREATE TABLE entries (value INTEGER UNIQUE)"))
    try:
        yield db, contexts[request.param]
    finally:
        await db.close()


@pytest.mark.parametrize("mode", ["commit-reset", "manual-commit", "manual-rollback", "rollback"])
async def test_repeated_cancellation_finishes_transaction(
    database_context: DatabaseContext,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    """在真实驱动回滚等待期间重复取消，不得打断连接池重置或泄漏连接。"""
    db, context = database_context
    entered = asyncio.Event()
    release = asyncio.Event()
    body_ready = asyncio.Event()
    armed = False
    original_rollback = db._engine.sync_engine.dialect.do_rollback

    def delayed_rollback(connection) -> None:
        """暂停第一次事务收尾回滚，稳定命中原报错中的 await 点。"""
        nonlocal armed
        if armed:
            armed = False
            entered.set()
            await_only(release.wait())
        original_rollback(connection)

    monkeypatch.setattr(db._engine.sync_engine.dialect, "do_rollback", delayed_rollback)

    async def write() -> None:
        """写入后进入自动提交，或在业务执行期间接收取消。"""
        nonlocal armed
        async with context() as session:
            await session.execute(text("INSERT INTO entries VALUES (1)"))
            armed = True
            body_ready.set()
            if mode == "rollback":
                await asyncio.Event().wait()
            elif mode == "manual-commit":
                await session.commit()
            elif mode == "manual-rollback":
                await session.rollback()

    caplog.set_level(logging.ERROR, logger="sqlalchemy.pool")
    worker = asyncio.create_task(write())
    try:
        await asyncio.wait_for(body_ready.wait(), timeout=5)
        if mode == "rollback":
            worker.cancel()
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(3):
            worker.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=5)

        assert db._engine.pool.checkedout() == 0
        async with context() as session:
            count = (await session.execute(text("SELECT count(*) FROM entries"))).scalar_one()
        assert count == (0 if mode in {"rollback", "manual-rollback"} else 1)
        assert not [record for record in caplog.records if record.name.startswith("sqlalchemy.pool")]
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_cancellation_waits_for_session_close(
    database_context: DatabaseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话只关闭一次，关闭期间的重复取消不能把清理遗留到后台。"""
    db, context = database_context
    entered = asyncio.Event()
    release = asyncio.Event()
    original_close = AsyncSession.close
    close_calls = 0

    async def delayed_close(session: AsyncSession) -> None:
        """暂停会话关闭，以模拟慢驱动的资源释放。"""
        nonlocal close_calls
        close_calls += 1
        entered.set()
        await release.wait()
        await original_close(session)

    monkeypatch.setattr(AsyncSession, "close", delayed_close)

    async def write() -> None:
        """执行会触发正常自动提交的写入。"""
        async with context() as session:
            await session.execute(text("INSERT INTO entries VALUES (1)"))

    worker = asyncio.create_task(write())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(3):
            worker.cancel()
            await asyncio.sleep(0)
        assert not worker.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=5)
        assert close_calls == 1
        assert db._engine.pool.checkedout() == 0
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_commit_failure_rolls_back_and_preserves_error(
    database_context: DatabaseContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动 flush 失败后的非活动事务仍须回滚，并保留真实错误。"""
    db, context = database_context
    original_rollback = session_module.CancellationSafeAsyncSession.rollback
    rollback = AsyncMock(autospec=True, side_effect=original_rollback)
    monkeypatch.setattr(
        session_module.CancellationSafeAsyncSession, "rollback", lambda session: rollback(session),
    )
    with pytest.raises((IntegrityError, DatabaseTransactionError)):
        async with context() as session:
            session.add_all([_Entry(value=1), _Entry(value=1)])

    assert rollback.await_count == 1
    assert db._engine.pool.checkedout() == 0
    async with context() as session:
        count = (await session.execute(text("SELECT count(*) FROM entries"))).scalar_one()
        assert count == 0
        await session.execute(text("INSERT INTO entries VALUES (2)"))


async def test_business_error_rolls_back(
    database_context: DatabaseContext,
) -> None:
    """普通业务异常应原样传播，且未提交的写入必须撤销。"""
    db, context = database_context
    error = ValueError("business failure")
    with pytest.raises(ValueError) as caught:
        async with context() as session:
            await session.execute(text("INSERT INTO entries VALUES (1)"))
            raise error
    assert caught.value is error
    assert db._engine.pool.checkedout() == 0
    async with context() as session:
        assert (await session.execute(text("SELECT count(*) FROM entries"))).scalar_one() == 0


@pytest.mark.parametrize("method", ["execute", "scalar", "scalars", "get", "flush"])
async def test_repeated_cancellation_during_driver_execution(
    database_context: DatabaseContext,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    method: str,
) -> None:
    """取消查询不能让驱动失效/终止连接的路径再次被取消并泄漏连接。"""
    db, context = database_context
    entered = asyncio.Event()
    release = asyncio.Event()
    original_execute = db._engine.sync_engine.dialect.do_execute

    def delayed_execute(cursor, statement, parameters, execution_context=None) -> None:
        """在 SQLAlchemy 正在等待驱动时挂起写入。"""
        if "entries" in statement:
            entered.set()
            await_only(release.wait())
        original_execute(cursor, statement, parameters, execution_context)

    monkeypatch.setattr(db._engine.sync_engine.dialect, "do_execute", delayed_execute)

    async def write() -> None:
        """写入在业务上下文退出前必须保持可回滚。"""
        async with context() as session:
            if method == "flush":
                session.add(_Entry(value=1))
                await session.flush()
            elif method == "get":
                await session.get(_Entry, 1)
            else:
                await getattr(session, method)(text("SELECT value FROM entries"))

    caplog.set_level(logging.ERROR, logger="sqlalchemy.pool")
    worker = asyncio.create_task(write())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(3):
            worker.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=5)
        assert db._engine.pool.checkedout() == 0
        async with context() as session:
            assert (await session.scalar(text("SELECT count(*) FROM entries"))) == 0
        assert not [record for record in caplog.records if record.name.startswith("sqlalchemy.pool")]
    finally:
        release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_database_close_waits_for_active_driver_operation(
    database_context: DatabaseContext,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """关闭数据库应先取消并等待查询使用者，不可提前 dispose 或重开旧工厂。"""
    db, context = database_context
    entered = asyncio.Event()
    release = asyncio.Event()
    original_execute = db._engine.sync_engine.dialect.do_execute
    factory = db._require_initialized()

    def delayed_execute(cursor, statement, parameters, execution_context=None) -> None:
        """暂停 SQL 执行以与数据库关闭形成真实竞态。"""
        if statement.startswith("INSERT INTO entries"):
            entered.set()
            await_only(release.wait())
        original_execute(cursor, statement, parameters, execution_context)

    monkeypatch.setattr(db._engine.sync_engine.dialect, "do_execute", delayed_execute)

    async def write() -> None:
        """持有待回滚写入的数据库使用者。"""
        async with context() as session:
            await session.execute(text("INSERT INTO entries VALUES (1)"))

    caplog.set_level(logging.ERROR, logger="sqlalchemy.pool")
    worker = asyncio.create_task(write())
    closer = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        closer = asyncio.create_task(db.close())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not closer.done()
        assert not worker.done()
        with pytest.raises(RuntimeError, match="正在关闭"):
            factory()
        closer.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closer, timeout=5)
        assert worker.cancelled()
        assert db._engine is None
        await db.initialize()
        async with db.session() as session:
            assert await session.scalar(text("SELECT count(*) FROM entries")) == 0
        assert not [record for record in caplog.records if record.name.startswith("sqlalchemy.pool")]
    finally:
        release.set()
        await asyncio.gather(worker, *([closer] if closer is not None else []), return_exceptions=True)


async def test_real_aiosqlite_query_finishes_before_cancelled_rollback(
    database_context: DatabaseContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """真实 SQLite 工作线程正在运行 SQL 时取消，不能漏出连接终止异常。"""
    db, context = database_context
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def hold_query() -> int:
        """在 SQLite 线程内暂停实际 SQL，而非模拟 Session 方法。"""
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(timeout=5):
            raise RuntimeError("test query was not released")
        return 7

    async with db._engine.connect() as connection:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.create_function("hold_query", 0, hold_query)

    async def query() -> None:
        """运行会进入 SQLite 后台线程的真实查询。"""
        async with context() as session:
            await session.scalar(text("SELECT hold_query()"))

    caplog.set_level(logging.ERROR, logger="sqlalchemy.pool")
    worker = asyncio.create_task(query())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(3):
            worker.cancel()
            await asyncio.sleep(0)
        assert not worker.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=5)
        assert db._engine.pool.checkedout() == 0
        async with context() as session:
            assert await session.scalar(text("SELECT 1")) == 1
        assert not [record for record in caplog.records if record.name.startswith("sqlalchemy.pool")]
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
