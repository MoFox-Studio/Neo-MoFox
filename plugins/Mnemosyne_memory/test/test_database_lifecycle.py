"""在最新 dev 存储接口上验证真实 SQLite 事务、取消及插件卸载。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..diary.store import DiaryStore, StreamProgress
from ..vnext.async_utils import await_cancellation_safe
from ..vnext.database import MnemosyneDatabase
from ..vnext import database as database_module
from ..vnext.schema import VNextSchema


@pytest.fixture(params=["memory", "diary"])
async def database(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[MnemosyneDatabase]:
    """通过两种实际存储入口构建隔离数据库，结束后检查连接清理。"""
    if request.param == "memory":
        db = VNextSchema(str(tmp_path / "memory.db")).database
    else:
        db = DiaryStore(str(tmp_path / "diary.db")).database
    await db.initialize()
    async with db.session() as session:
        await session.execute(text("CREATE TABLE lifecycle_test (id INTEGER PRIMARY KEY)"))
    try:
        yield db
    finally:
        await db.close()
        assert not db._sessions


async def test_nested_cleanup_defers_repeated_cancellation() -> None:
    """重复取消不会中断嵌套收尾，也不会吞掉最外层取消。"""
    entered, release = asyncio.Event(), asyncio.Event()
    finished: list[bool] = []

    async def operation() -> int:
        """模拟已经进入驱动的资源操作。"""
        entered.set()
        await release.wait()
        finished.append(True)
        return 7

    async def nested() -> int:
        """嵌套保护不创建第二条独立的清理链。"""
        return await await_cancellation_safe(operation())

    task = asyncio.create_task(await_cancellation_safe(nested()))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [True]
    assert await await_cancellation_safe(asyncio.sleep(0, result=9)) == 9


async def test_failed_initialization_disposes_engine_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实连接已打开但建表失败时释放引擎，修正模型后可重新初始化。"""
    create_engine = database_module.create_async_engine
    disposed: list[bool] = []

    def tracked_engine(*args: object, **kwargs: object) -> object:
        """观察真实引擎的释放事件，不替换数据库行为。"""
        engine = create_engine(*args, **kwargs)
        event.listen(engine.sync_engine, "engine_disposed", lambda _: disposed.append(True))
        return engine

    monkeypatch.setattr(database_module, "create_async_engine", tracked_engine)
    db = MnemosyneDatabase(str(tmp_path / "initialize.db"), [object])
    with pytest.raises(AttributeError):
        await db.initialize()
    assert disposed == [True]
    assert db._engine is None and db._session_factory is None
    db._models = [StreamProgress]
    try:
        await db.initialize()
        async with db.session() as session:
            assert await session.scalar(text("SELECT count(*) FROM chat_diary_progress")) == 0
    finally:
        await db.close()


async def test_failed_flush_rolls_back_and_allows_reuse(database: MnemosyneDatabase) -> None:
    """真实约束错误后会回滚并归还连接，后续事务仍能提交。"""
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await session.execute(text("INSERT INTO lifecycle_test VALUES (1)"))
            await session.execute(text("INSERT INTO lifecycle_test VALUES (1)"))
    async with database.session() as session:
        assert await session.scalar(text("SELECT count(*) FROM lifecycle_test")) == 0
        await session.execute(text("INSERT INTO lifecycle_test VALUES (2)"))
    async with database.session() as session:
        assert await session.scalar(text("SELECT id FROM lifecycle_test")) == 2
    assert database._engine.pool.checkedout() == 0


async def test_failed_orm_flush_does_not_skip_rollback(tmp_path: Path) -> None:
    """SQLAlchemy 将失败 flush 标为 inactive 时仍然回滚并关闭。"""
    store = DiaryStore(str(tmp_path / "failed-flush.db"))
    await store.initialize()
    try:
        with pytest.raises(IntegrityError):
            async with store.database.session() as session:
                for _ in range(2):
                    session.add(StreamProgress(
                        stream_id="same", chat_type="group", start_time=0,
                        bootstrap_through=0, initialized_at=0,
                    ))
                await session.flush()
        async with store.database.session() as session:
            assert (await session.scalars(select(StreamProgress))).all() == []
        assert not store.database._sessions
    finally:
        await store.close()


async def test_cancellation_during_commit_finishes_transaction(
    database: MnemosyneDatabase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交已开始时延迟传播取消，保留已提交结果并归还连接。"""
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_commit(session: AsyncSession) -> None:
        """在提交边界暂停，再调用真实 SQLAlchemy 提交。"""
        entered.set()
        await release.wait()
        await AsyncSession.commit(session)

    monkeypatch.setattr(database_module._SafeSession, "commit", delayed_commit)

    async def write() -> None:
        """正常退出事务时触发受保护提交。"""
        async with database.session() as session:
            await session.execute(text("INSERT INTO lifecycle_test VALUES (3)"))

    task = asyncio.create_task(write())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with database.session() as session:
        assert await session.scalar(text("SELECT id FROM lifecycle_test")) == 3
    assert not database._sessions


async def test_cancelled_driver_query_finishes_before_connection_return(database: MnemosyneDatabase) -> None:
    """用真实 SQLite 用户函数阻塞驱动，重复取消后连接仍有序收尾。"""
    entered, release = threading.Event(), threading.Event()

    def block_driver() -> int:
        """在 aiosqlite 工作线程中阻塞，超时确保失败测试也能退出。"""
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test driver was not released")
        return 1

    async with database._engine.connect() as connection:
        await connection.run_sync(
            lambda sync: sync.connection.dbapi_connection.create_function("test_wait", 0, block_driver)
        )

    async def query() -> None:
        """写入与阻塞查询必须在同一取消事务中回滚。"""
        async with database.session() as session:
            await session.execute(text("INSERT INTO lifecycle_test VALUES (1)"))
            await session.execute(text("SELECT test_wait()"))

    task = asyncio.create_task(query())
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not database._sessions
    assert database._engine.pool.checkedout() == 0
    async with database.session() as session:
        assert await session.scalar(text("SELECT count(*) FROM lifecycle_test")) == 0


async def test_close_waits_for_user_and_rejects_new_sessions(database: MnemosyneDatabase) -> None:
    """关闭主动取消业务并等待其 finally，不允许新会话插入关闭流程。"""
    entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def user() -> None:
        """持有一个未提交事务，并在取消后模拟业务收尾。"""
        async with database.session() as session:
            await session.execute(text("INSERT INTO lifecycle_test VALUES (1)"))
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()

    task = asyncio.create_task(user())
    await entered.wait()
    closing = asyncio.create_task(database.close())
    try:
        await asyncio.wait_for(cancelling.wait(), 5)
        assert not closing.done()
        with pytest.raises(RuntimeError, match="正在关闭"):
            async with database.session():
                pass
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, 5)
    assert task.cancelled()
    assert database._engine is None
    await database.initialize()
    async with database.session() as session:
        assert await session.scalar(text("SELECT count(*) FROM lifecycle_test")) == 0
