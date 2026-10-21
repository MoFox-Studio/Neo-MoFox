"""通过独立会话复现 get_or_create 查询和插入之间的竞争。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.models.sql_alchemy import Messages
from src.kernel.db import CRUDBase
from src.kernel.db.api import crud as crud_module
from src.kernel.db.core.exceptions import DatabaseTransactionError
from src.kernel.db.core.session import CancellationSafeAsyncSession, transaction_session


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped_errors", [False, True])
async def test_get_or_create_concurrent_misses_return_one_record(tmp_path, monkeypatch, wrapped_errors) -> None:
    """强制八个调用先查到不存在，再并发插入；全部成功且只创建一次。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'race.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Messages.__table__.create)
    factory = async_sessionmaker(engine, class_=CancellationSafeAsyncSession, expire_on_commit=False)
    @asynccontextmanager
    async def main_session():
        """与主库相同地在事务关闭后将原始完整性错误转换为业务异常。"""
        try:
            async with transaction_session(factory) as session:
                yield session
        except IntegrityError as exc:
            raise DatabaseTransactionError("database transaction failed") from exc

    monkeypatch.setattr(crud_module, "get_db_session", main_session)
    crud = CRUDBase(Messages, session_factory=None if wrapped_errors else factory)
    original_get_by = crud.get_by
    all_missed = asyncio.Event()
    misses = 0

    async def get_by(**filters):
        """只将第一轮真实查询聚集到屏障，不模拟数据库查询结果。"""
        nonlocal misses
        row = await original_get_by(**filters)
        if row is None:
            misses += 1
            if misses == 8:
                all_missed.set()
            await all_missed.wait()
        return row

    monkeypatch.setattr(crud, "get_by", get_by)
    try:
        results = await asyncio.wait_for(asyncio.gather(*(
            crud.get_or_create(
                message_id="duplicate", stream_id="stream", platform="qq",
                defaults={"message_type": "text", "content": "kept", "time": 1.25},
            ) for _ in range(8)
        )), 10)
        assert sum(created for _, created in results) == 1
        assert len({row.id for row, _ in results}) == 1
        async with factory() as session:
            assert len((await session.scalars(select(Messages))).all()) == 1
        # 其他字段上的完整性错误不能被当成竞争吞掉。
        with pytest.raises(IntegrityError):
            await CRUDBase(Messages, session_factory=factory).get_or_create(
                message_id="invalid", stream_id="stream", defaults={"time": 1.0},
            )
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_preserves_unrelated_transaction_errors() -> None:
    """回读不到匹配项或原始错误不是完整性冲突时，保留真实异常。"""
    crud = CRUDBase(Messages)
    crud.get_by = AsyncMock(return_value=None)
    error = DatabaseTransactionError("actual failure")
    crud.create = AsyncMock(side_effect=error)
    with pytest.raises(DatabaseTransactionError, match="actual failure"):
        await crud.get_or_create(message_id="id", stream_id="stream")
    crud.get_by.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_message_replay_accepts_legacy_null_platform(tmp_path) -> None:
    """按数据库真实身份去重，旧记录 platform=NULL 不应被误认成新消息。"""
    from src.core.managers.stream_manager import StreamManager
    from src.core.models.message import Message

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Messages.__table__.create)
    factory = async_sessionmaker(engine, class_=CancellationSafeAsyncSession, expire_on_commit=False)
    manager = StreamManager()
    manager._messages_crud = CRUDBase(Messages, session_factory=factory)
    manager._update_stream_active_time = AsyncMock()
    try:
        original = await manager._messages_crud.create({
            "message_id": "shared", "stream_id": "stream-a", "platform": None,
            "message_type": "text", "time": 1.0, "content": "kept",
        })
        replay = Message(message_id="shared", stream_id="stream-a", platform="qq", content="replay")
        returned = await manager.add_message_to_history(replay, silent=True)
        assert returned.id == original.id
        other = Message(message_id="shared", stream_id="stream-b", platform="qq", content="other")
        assert (await manager.add_message_to_history(other, silent=True)).id != original.id
        async with factory() as session:
            assert len((await session.scalars(select(Messages))).all()) == 2
    finally:
        await engine.dispose()
