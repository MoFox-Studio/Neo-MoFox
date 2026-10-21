"""数据库会话管理

职责:
- 提供数据库会话工厂
- 提供数据库会话的上下文管理器
- 应用数据库特定的会话设置
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from weakref import WeakKeyDictionary, WeakSet

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from src.kernel.db.telemetry import record_db_lifecycle_event
from src.kernel.logger import get_logger
from src.kernel.concurrency.cleanup import (
    await_cancellation_safe, cancel_and_wait_tasks, cleanup_ancestors,
)

from .exceptions import DatabaseConnectionError, DatabaseTransactionError
from .engine import get_engine, get_configured_db_type

logger = get_logger("database.session", display="DB 会话")

# 全局会话工厂
_session_factory: async_sessionmaker | None = None
_factory_lock: asyncio.Lock | None = None
_P = ParamSpec("_P")
_R = TypeVar("_R")
_sessions: WeakSet[CancellationSafeAsyncSession] = WeakSet()
_closing_engines: WeakSet[Any] = WeakSet()
_pending_writers: WeakKeyDictionary[Any, WeakSet[asyncio.Task[Any]]] = WeakKeyDictionary()


@contextmanager
def track_engine_writer(factory: async_sessionmaker) -> Iterator[None]:
    """登记尚未取得连接的写入等待者，让关闭引擎能取消并等待整条写队列。"""
    bind = factory.kw.get("bind")
    engine = getattr(bind, "sync_engine", None)
    task = asyncio.current_task()
    if engine is None or task is None:
        yield
        return
    if engine in _closing_engines:
        raise RuntimeError("数据库正在关闭，不能开始新写入")
    pending = _pending_writers.setdefault(engine, WeakSet())
    pending.add(task)
    try:
        yield
    finally:
        pending.discard(task)


async def _await_transaction_operation(operation: Coroutine[Any, Any, None]) -> None:
    """保护事务收尾，并在重复取消后仍等待操作完成再传播取消。"""
    await await_cancellation_safe(operation, name="database_transaction_cleanup")


def _session_operation(
    method: Callable[_P, Coroutine[Any, Any, _R]],
) -> Callable[_P, Coroutine[Any, Any, _R]]:
    """统一保护进入驱动的会话方法，同时保留原方法的参数签名和结果。"""
    @wraps(method)
    async def protected(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        session = args[0]
        if session not in _sessions or session._owner_task is None or session._owner_task.done():
            session._owner_task = asyncio.current_task()
            _sessions.add(session)
        return await await_cancellation_safe(
            getattr(AsyncSession, method.__name__)(*args, **kwargs),
            name=f"database_{method.__name__}",
        )
    return protected


class CancellationSafeAsyncSession(AsyncSession):
    """让驱动操作完成后再传播取消，防止失效连接的清理被二次打断。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """记录会话拥有者，使引擎关闭能等待所有实际使用者。"""
        super().__init__(*args, **kwargs)
        bind = self.sync_session.bind
        if getattr(bind, "engine", bind) in _closing_engines:
            raise RuntimeError("数据库正在关闭，不能创建新会话")
        self._owner_task: asyncio.Task[Any] | None = None

    # 这些方法直接进入 greenlet/驱动；scalars、aclose 等会复用受保护入口。
    execute = _session_operation(AsyncSession.execute)
    scalar = _session_operation(AsyncSession.scalar)
    get = _session_operation(AsyncSession.get)
    get_one = _session_operation(AsyncSession.get_one)
    flush = _session_operation(AsyncSession.flush)
    refresh = _session_operation(AsyncSession.refresh)
    delete = _session_operation(AsyncSession.delete)
    merge = _session_operation(AsyncSession.merge)
    connection = _session_operation(AsyncSession.connection)
    run_sync = _session_operation(AsyncSession.run_sync)
    stream = _session_operation(AsyncSession.stream)
    reset = _session_operation(AsyncSession.reset)
    invalidate = _session_operation(AsyncSession.invalidate)
    commit = _session_operation(AsyncSession.commit)
    rollback = _session_operation(AsyncSession.rollback)

    async def close(self) -> None:
        """等待连接归还后再传播取消。"""
        async def close_session() -> None:
            """只有驱动关闭完成后才从活动会话集合移除。"""
            await super(CancellationSafeAsyncSession, self).close()
            _sessions.discard(self)

        await _await_transaction_operation(close_session())


async def dispose_engine_safely(engine: Any) -> None:
    """拒绝新会话，取消并等待该引擎的使用者，最后释放连接池。"""
    async def dispose() -> None:
        """引擎释放与使用者退出属于同一次不可中断的资源收尾。"""
        bind = engine.sync_engine
        _closing_engines.add(bind)
        try:
            sessions = [
                session for session in _sessions
                if getattr(session.sync_session.bind, "engine", session.sync_session.bind) is bind
            ]
            excluded = cleanup_ancestors() | {asyncio.current_task()}
            owners = {
                session._owner_task for session in sessions
                if session._owner_task is not None and session._owner_task not in excluded
            }
            owners.update(task for task in _pending_writers.get(bind, ()) if task not in excluded)
            await cancel_and_wait_tasks(owners)
            # 使用者也可能是受保护的提交任务；等待它结束后才能关闭同一会话。
            await asyncio.gather(*owners, return_exceptions=True)
            for session in sessions:
                await session.close()
            await engine.dispose()
        finally:
            _closing_engines.discard(bind)

    await await_cancellation_safe(dispose(), name="database_engine_dispose")


async def get_session_factory() -> async_sessionmaker:
    """获取会话工厂（单例模式）

    Returns:
        async_sessionmaker: SQLAlchemy 异步会话工厂
    """
    global _session_factory, _factory_lock

    # 快速路径
    if _session_factory is not None:
        return _session_factory

    # 延迟创建锁
    if _factory_lock is None:
        _factory_lock = asyncio.Lock()

    async with _factory_lock:
        # 双重检查
        if _session_factory is not None:
            return _session_factory

        engine = await get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=CancellationSafeAsyncSession,
            expire_on_commit=False,  # 避免提交后访问属性时重新查询
        )

        logger.debug("会话工厂已创建")
        return _session_factory


async def _apply_session_settings(session: AsyncSession, db_type: str) -> None:
    """应用数据库特定的会话设置

    Args:
        session: 数据库会话
        db_type: 数据库类型（sqlite 或 postgresql）
    """
    try:
        if db_type == "sqlite":
            # SQLite 特定的 PRAGMA 设置
            await session.execute(text("PRAGMA busy_timeout = 60000"))
            await session.execute(text("PRAGMA foreign_keys = ON"))
        elif db_type == "postgresql":
            # PostgreSQL 特定设置（如果需要）
            # 可以设置 schema 搜索路径等
            pass
    except Exception:
        # 连接复用时设置可能已存在，忽略错误
        pass


async def _finish_transaction(session: AsyncSession, *, commit: bool) -> None:
    """完成事务并关闭会话；失败的 flush 也必须回滚，不能以 is_active 跳过。"""
    try:
        if commit:
            try:
                if session.is_active:
                    await session.commit()
            except BaseException:
                await session.rollback()
                raise
        else:
            await session.rollback()
    finally:
        await session.close()


@asynccontextmanager
async def transaction_session(
    session_factory: async_sessionmaker,
) -> AsyncGenerator[AsyncSession, None]:
    """共享事务上下文：取消可中断业务，但必须等待提交/回滚及连接归还完成。

    提交也包含连接池的 rollback-on-return，必须和关闭一起保护。
    已开始提交时收到的取消会在收尾后重新抛出，提交可能已经生效。
    """
    session = session_factory()
    if isinstance(session, CancellationSafeAsyncSession):
        session._owner_task = asyncio.current_task()
        _sessions.add(session)
    commit = False
    try:
        yield session
        commit = True
    finally:
        await _await_transaction_operation(_finish_transaction(session, commit=commit))


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话上下文管理器

    这是数据库操作的主要入口点。

    事务管理:
    - 正常退出时自动提交事务
    - 发生异常时自动回滚事务
    - 安全地手动调用 commit/rollback
    - 取消时等待事务收尾与连接归还，再传播 CancelledError

    使用示例:
        async with get_db_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

    Yields:
        AsyncSession: SQLAlchemy 异步会话对象
    """
    session_factory = await get_session_factory()

    try:
        try:
            async with transaction_session(session_factory) as session:
                # 使用公共函数获取 db_type
                db_type = get_configured_db_type()

                if db_type:
                    await _apply_session_settings(session, db_type)

                yield session

        except Exception as e:
            await record_db_lifecycle_event(
                event_name="transaction_error",
                severity="error",
                summary="database transaction failed",
                attributes={
                    "db_type": get_configured_db_type() or "unknown",
                    "error_type": type(e).__name__,
                },
                detail={"message": str(e)},
            )

            # 转换 SQLAlchemy 异常为内部异常
            if isinstance(e, SQLAlchemyError):
                raise DatabaseTransactionError(f"数据库事务执行失败: {e}") from e
            raise
    except (asyncio.TimeoutError, DatabaseConnectionError) as e:
        await record_db_lifecycle_event(
            event_name="connection_error",
            severity="error",
            summary="database connection failed",
            attributes={
                "db_type": get_configured_db_type() or "unknown",
                "error_type": type(e).__name__,
            },
            detail={"message": str(e)},
        )
        logger.error(f"数据库连接超时或失败: {e}")
        raise DatabaseConnectionError(f"无法连接到数据库: {e}") from e
    except Exception as e:
        if "timeout" in str(e).lower():
             await record_db_lifecycle_event(
                 event_name="connection_error",
                 severity="error",
                 summary="database operation timeout",
                 attributes={
                     "db_type": get_configured_db_type() or "unknown",
                     "error_type": type(e).__name__,
                 },
                 detail={"message": str(e)},
             )
             raise DatabaseConnectionError(f"数据库操作超时: {e}") from e
        raise


async def reset_session_factory() -> None:
    """重置会话工厂（用于测试）"""
    global _session_factory
    _session_factory = None
