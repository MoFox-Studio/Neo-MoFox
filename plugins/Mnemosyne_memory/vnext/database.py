"""基于公开 storage_api 1.0 的插件独立数据库与取消安全事务。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from weakref import WeakSet

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.plugin_system.api.storage_api import PluginDatabase

from .async_utils import await_cancellation_safe, cleanup_ancestors

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _protected(
    method: Callable[_P, Coroutine[Any, Any, _R]],
) -> Callable[_P, Coroutine[Any, Any, _R]]:
    """保护进入数据库驱动的操作，避免取消使连接仍在后台使用。"""
    @wraps(method)
    async def execute(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        """操作完成并归还执行权后才向调用方传播取消。"""
        return await await_cancellation_safe(
            method(*args, **kwargs), name=f"mnemosyne_db_{method.__name__}",
        )
    return execute


class _SafeSession(AsyncSession):
    """记录会话使用者，保护查询、写入及事务收尾。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """在驱动操作之前登记会话，包括通过公开 CRUD 工厂创建的会话。"""
        super().__init__(*args, **kwargs)
        database = self.info["mnemosyne_database"]
        if database._closing:
            raise RuntimeError("Mnemosyne 数据库正在关闭")
        self.owner = asyncio.current_task()
        database._sessions.add(self)

    execute = _protected(AsyncSession.execute)
    scalar = _protected(AsyncSession.scalar)
    get = _protected(AsyncSession.get)
    get_one = _protected(AsyncSession.get_one)
    flush = _protected(AsyncSession.flush)
    refresh = _protected(AsyncSession.refresh)
    delete = _protected(AsyncSession.delete)
    merge = _protected(AsyncSession.merge)
    connection = _protected(AsyncSession.connection)
    run_sync = _protected(AsyncSession.run_sync)
    reset = _protected(AsyncSession.reset)
    invalidate = _protected(AsyncSession.invalidate)
    commit = _protected(AsyncSession.commit)
    rollback = _protected(AsyncSession.rollback)

    async def close(self) -> None:
        """连接归还完成后再从活动会话集合移除。"""
        async def finish() -> None:
            """关闭和移除登记属于同一次受保护操作。"""
            await super(_SafeSession, self).close()
            self.info["mnemosyne_database"]._sessions.discard(self)

        await await_cancellation_safe(finish(), name="mnemosyne_db_session_close")


class MnemosyneDatabase(PluginDatabase):
    """隔离于主程序数据库，保留取消、失败 flush 和卸载期间的连接清理。"""

    def __init__(self, db_path: str, models: list[type]) -> None:
        """沿用公开存储接口，仅在本插件内管理会话生命周期。"""
        super().__init__(db_path, models)
        self._sessions: WeakSet[_SafeSession] = WeakSet()
        self._closing = False

    async def initialize(self) -> None:
        """幂等建表；初始化失败或取消时完整释放尚未发布的引擎。"""
        async def initialize_database() -> None:
            """初始化与关闭共用互斥锁，不暴露半初始化的连接池。"""
            async with self._init_lock:
                if self._engine is not None:
                    return
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                engine = create_async_engine(
                    f"sqlite+aiosqlite:///{self._db_path.absolute().as_posix()}",
                    connect_args={"check_same_thread": False, "timeout": 60},
                )

                @event.listens_for(engine.sync_engine, "connect")
                def configure_connection(connection: Any, record: Any) -> None:
                    """每个池连接均启用外键和锁等待，而非仅配置初始化连接。"""
                    cursor = connection.cursor()
                    try:
                        cursor.execute("PRAGMA foreign_keys=ON")
                        cursor.execute("PRAGMA busy_timeout=10000")
                    finally:
                        cursor.close()

                try:
                    async with engine.begin() as connection:
                        await connection.execute(text("PRAGMA journal_mode=WAL"))
                        await connection.execute(text("PRAGMA synchronous=NORMAL"))
                        seen: set[int] = set()
                        for model in self._models:
                            metadata = model.__table__.metadata
                            if id(metadata) not in seen:
                                seen.add(id(metadata))
                                await connection.run_sync(metadata.create_all)
                except BaseException:
                    await engine.dispose()
                    raise
                self._session_factory = async_sessionmaker(
                    engine, class_=_SafeSession, expire_on_commit=False,
                    info={"mnemosyne_database": self},
                )
                self._engine = engine

        await await_cancellation_safe(initialize_database(), name="mnemosyne_db_initialize")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """正常提交，异常或取消时回滚；失败 flush 也执行回滚和关闭。"""
        session = self._require_initialized()()
        commit = False
        try:
            yield session
            commit = True
        finally:
            async def finish() -> None:
                """提交失败也须回滚，连接归还前不传播取消。"""
                try:
                    if commit:
                        try:
                            await session.commit()
                        except BaseException:
                            await session.rollback()
                            raise
                    else:
                        await session.rollback()
                finally:
                    await session.close()

            await await_cancellation_safe(finish(), name="mnemosyne_db_transaction")

    async def close(self) -> None:
        """拒绝新会话，取消并等待现有使用者，然后释放独立连接池。"""
        async def close_database() -> None:
            """插件卸载不会关闭仍有驱动操作或事务清理使用的连接。"""
            async with self._init_lock:
                if self._engine is None:
                    return
                self._closing = True
                try:
                    excluded = cleanup_ancestors() | {asyncio.current_task()}
                    owners = {
                        session.owner for session in self._sessions
                        if session.owner is not None and session.owner not in excluded
                        and not session.owner.done()
                    }
                    for owner in owners:
                        if not owner.cancelling():
                            owner.cancel()
                    if owners:
                        await asyncio.gather(*owners, return_exceptions=True)
                    for session in list(self._sessions):
                        await session.close()
                    await self._engine.dispose()
                    self._engine = None
                    self._session_factory = None
                finally:
                    self._closing = False

        await await_cancellation_safe(close_database(), name="mnemosyne_db_close")
