"""用文件 SQLite 和独立连接复现媒体写入竞争，校验记录及计数没有丢失。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.managers.media_manager import cache, repository
from src.core.models.sql_alchemy import (
    Base, ImageDescriptions, Images, VideoDescriptions, Videos, VoiceDescriptions, Voices,
)
from src.kernel.db.api.write import run_write_transaction
from src.kernel.db.core.session import CancellationSafeAsyncSession

KINDS = [
    ("image", Images, "image_id", "vlm_processed"),
    ("emoji", Images, "image_id", "vlm_processed"),
    ("voice", Voices, "voice_id", "asr_processed"),
    ("video", Videos, "video_id", "video_processed"),
]


@pytest.fixture
async def media_db(tmp_path, monkeypatch) -> AsyncIterator[async_sessionmaker]:
    """使用真实连接池，避免内存 SQLite 的共享单连接掩盖竞争。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'media.db').as_posix()}",
        connect_args={"timeout": 0.1},
    )
    models = [Images, Voices, Videos, ImageDescriptions, VoiceDescriptions, VideoDescriptions]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[model.__table__ for model in models])
    factory = async_sessionmaker(engine, class_=CancellationSafeAsyncSession, expire_on_commit=False)

    async def write(operation):
        """将产品的短写事务接到当前隔离数据库。"""
        return await run_write_transaction(operation, session_factory=factory)

    monkeypatch.setattr(repository, "run_write_transaction", write)
    monkeypatch.setattr(cache, "run_write_transaction", write)
    logger = Mock()
    monkeypatch.setattr(repository, "logger", logger)
    monkeypatch.setattr(cache, "logger", logger)
    try:
        yield factory
        logger.error.assert_not_called()
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,model,hash_field,processed_field", KINDS)
async def test_parallel_first_insert_and_updates_keep_every_count(
    media_db, kind, model, hash_field, processed_field,
) -> None:
    """多个仓储实例同时保存同一媒体：唯一记录、精确计数和识别状态。"""
    repos = [repository.MediaRepository() for _ in range(3)]
    await asyncio.gather(*(
        repos[i % 3].save_recognized_media(
            "shared", kind, "/media/shared", "recognized" if i == 0 else None, i == 0,
        ) for i in range(24)
    ))
    async with media_db() as session:
        rows = (await session.scalars(select(model))).all()
        assert len(rows) == 1
        assert rows[0].count == 24
        assert getattr(rows[0], hash_field) == "shared"
        assert rows[0].description == "recognized"
        assert getattr(rows[0], processed_field) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,model,hash_field,processed_field", KINDS)
async def test_hash_and_path_matching_different_rows_keeps_both(
    media_db, kind, model, hash_field, processed_field,
) -> None:
    """目标路径行优先，不因另一条更新的 hash 记录而撞唯一键或删除历史行。"""
    async with media_db() as session:
        session.add_all([
            model(**{hash_field: "old", "path": "/target", "type": kind,
                     "timestamp": 1, "count": 3, "description": "kept", processed_field: True}),
            model(**{hash_field: "incoming", "path": "/other", "type": kind,
                     "timestamp": 2, "count": 5, processed_field: False}),
        ])
        await session.commit()
    await repository.MediaRepository().save_recognized_media(
        "incoming", kind, "/target", None, False,
    )
    async with media_db() as session:
        rows = (await session.scalars(select(model).order_by(model.path))).all()
        assert [(row.path, row.count) for row in rows] == [("/other", 5), ("/target", 4)]
        assert rows[1].description == "kept"
        assert getattr(rows[1], processed_field) is True
        canonical = (await session.scalars(
            select(model).where(getattr(model, hash_field) == "incoming")
            .order_by(model.timestamp.desc()).limit(1)
        )).one()
        assert canonical.path == "/target"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,model,hash_field,processed_field", KINDS)
async def test_same_hash_new_path_retains_identity(media_db, kind, model, hash_field, processed_field) -> None:
    """移动媒体路径时复用原主键并保留计数，不创建重复记录。"""
    repo = repository.MediaRepository()
    await repo.save_recognized_media("hash", kind, "/old", "kept", True)
    async with media_db() as session:
        original_id = (await session.scalars(select(model.id))).one()
    await repo.save_recognized_media("hash", kind, "/new", None, False)
    async with media_db() as session:
        row = (await session.scalars(select(model))).one()
        assert (row.id, row.path, row.count, row.description) == (original_id, "/new", 2, "kept")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,model,hash_field", [
    ("image", ImageDescriptions, "image_description_hash"),
    ("emoji", ImageDescriptions, "image_description_hash"),
    ("voice", VoiceDescriptions, "voice_description_hash"),
    ("video", VideoDescriptions, "video_description_hash"),
])
async def test_parallel_description_cache_keeps_first_result(media_db, kind, model, hash_field) -> None:
    """并发缓存写入只有一行，已缓存的首个结果不被晚到任务覆盖。"""
    media_cache = cache.MediaCache()
    await media_cache.save_description_cache("hash", kind, "first")
    await asyncio.gather(*(
        media_cache.save_description_cache("hash", kind, f"late-{i}") for i in range(16)
    ))
    async with media_db() as session:
        row = (await session.scalars(select(model))).one()
        assert getattr(row, hash_field) == "hash"
        assert row.description == "first"
