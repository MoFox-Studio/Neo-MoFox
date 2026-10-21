"""消息 ID 的聊天流作用域、旧索引迁移和失败回滚回归。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import MetaData, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.models.sql_alchemy import Images, Messages, Videos, Voices
from src.core.utils import schema_sync


def _message(stream_id: str, content: str = "original") -> dict:
    """构造有相同平台 ID 的不同会话消息。"""
    return dict(message_id="851747885", stream_id=stream_id, platform="qq",
                time=1792520520.7191777, message_type="text", content=content)


@pytest.fixture
async def identity_db(tmp_path, monkeypatch):
    """重建生产旧版的具名 message_id 唯一索引，隔离于生产数据库。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'messages.db').as_posix()}")
    current = MetaData()
    target = Messages.__table__.to_metadata(current)
    legacy = MetaData()
    old = target.to_metadata(legacy)
    for index in list(old.indexes):
        if index.name == "idx_messages_stream_message_id":
            old.indexes.remove(index)
        elif index.name == "ix_messages_message_id":
            index.unique = True
    async with engine.begin() as conn:
        await conn.run_sync(legacy.create_all)
        await conn.execute(old.insert().values(**_message("stream-original")))
    monkeypatch.setattr(schema_sync, "get_engine", AsyncMock(return_value=engine))
    monkeypatch.setattr(schema_sync, "get_configured_db_type", lambda: "sqlite")
    try:
        yield engine, current, target
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_preserves_rows_and_allows_other_stream_same_id(identity_db) -> None:
    """同 ID 可出现在另一会话，原记录/主键/时间完整保留，迁移可重复执行。"""
    engine, metadata, target = identity_db
    async with engine.connect() as conn:
        original = (await conn.execute(select(target))).one()
    await schema_sync.enforce_database_schema_consistency(metadata)
    await schema_sync.enforce_database_schema_consistency(metadata)
    async with engine.begin() as conn:
        assert (await conn.execute(select(target))).one() == original
        await conn.execute(target.insert().values(**_message("stream-other", "other")))
        indexes = await conn.run_sync(lambda sync: inspect(sync).get_indexes("messages"))
        by_name = {index["name"]: index for index in indexes}
        assert by_name["idx_messages_stream_message_id"]["unique"]
        assert not by_name["ix_messages_message_id"]["unique"]
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            await conn.execute(target.insert().values(**_message("stream-other", "duplicate")))
    async with engine.connect() as conn:
        assert len((await conn.execute(select(target))).all()) == 2


@pytest.mark.asyncio
async def test_failed_index_migration_restores_old_index_and_data(identity_db, monkeypatch) -> None:
    """模拟删除旧索引后失败：DDL 与数据一起回滚，不能留下无约束状态。"""
    engine, metadata, target = identity_db

    def fail_after_drop(message: str) -> None:
        """在移除旧索引的操作后触发测试故障。"""
        if "已迁移消息 ID" in message:
            raise RuntimeError("injected failure")

    monkeypatch.setattr(schema_sync.logger, "info", fail_after_drop)
    with pytest.raises(RuntimeError, match="injected failure"):
        await schema_sync.enforce_database_schema_consistency(metadata)
    async with engine.connect() as conn:
        indexes = await conn.run_sync(lambda sync: inspect(sync).get_indexes("messages"))
        by_name = {index["name"]: index for index in indexes}
        assert by_name["ix_messages_message_id"]["unique"]
        assert "idx_messages_stream_message_id" not in by_name
        row = (await conn.execute(select(target))).one()
        assert row.content == "original"
        assert row.message_id == "851747885"


@pytest.mark.asyncio
async def test_fresh_schema_uses_stream_scoped_identity(tmp_path, monkeypatch) -> None:
    """新数据库直接创建正确约束，无需先经历旧版迁移。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    metadata = MetaData()
    target = Messages.__table__.to_metadata(metadata)
    monkeypatch.setattr(schema_sync, "get_engine", AsyncMock(return_value=engine))
    monkeypatch.setattr(schema_sync, "get_configured_db_type", lambda: "sqlite")
    try:
        await schema_sync.enforce_database_schema_consistency(metadata)
        async with engine.begin() as conn:
            await conn.execute(target.insert(), [_message("stream-a"), _message("stream-b")])
            assert len((await conn.execute(select(target))).all()) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("model,hash_field", [(Images, "image_id"), (Voices, "voice_id"), (Videos, "video_id")])
async def test_existing_media_tables_get_hash_indexes_without_rebuilding(tmp_path, monkeypatch, model, hash_field) -> None:
    """老库补齐哈希索引后 OR 查询走索引，已有主键和识别内容不变。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'indexes.db').as_posix()}")
    metadata = MetaData()
    target = model.__table__.to_metadata(metadata)
    legacy = MetaData()
    old = target.to_metadata(legacy)
    for index in list(old.indexes):
        if index.name == f"idx_{old.name}_{hash_field}":
            old.indexes.remove(index)
    monkeypatch.setattr(schema_sync, "get_engine", AsyncMock(return_value=engine))
    monkeypatch.setattr(schema_sync, "get_configured_db_type", lambda: "sqlite")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(legacy.create_all)
            await conn.execute(old.insert().values(**{
                "id": 7, hash_field: "kept", "path": "/kept", "type": "image",
                "description": "recognized", "timestamp": 1.25,
            }))
            original = (await conn.execute(select(old))).one()
        await schema_sync.enforce_database_schema_consistency(metadata)
        async with engine.connect() as conn:
            assert (await conn.execute(select(target))).one() == original
            from sqlalchemy import text
            plan = (await conn.execute(text(
                f"EXPLAIN QUERY PLAN SELECT id FROM {target.name} "
                f"WHERE {hash_field}=:hash OR path=:path"
            ), {"hash": "kept", "path": "/kept"})).all()
            assert any("MULTI-INDEX OR" in row[3] for row in plan)
            assert not any(row[3].startswith("SCAN ") for row in plan)
    finally:
        await engine.dispose()
