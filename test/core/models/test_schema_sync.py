"""数据库结构同步与浮点精度回归测试。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, Float, Index, Integer, MetaData, REAL, Table, Text, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from src.core.utils import schema_sync
from src.core.utils.schema_sync import (
    _normalize_type,
    enforce_database_schema_consistency,
)
from src.kernel.db import configure_engine, get_engine
from src.kernel.db.core.exceptions import DatabaseInitializationError
from src.kernel.db.core.engine import _build_sqlite_config


TestBase = declarative_base()


@pytest.fixture(scope="module", autouse=True)
def _configure_kernel_db_for_schema_sync_tests(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """为当前测试模块配置 kernel/db 引擎（支持单文件运行）。"""
    db_path = tmp_path_factory.mktemp("schema_sync") / "schema_sync.db"
    url, engine_kwargs = _build_sqlite_config(str(db_path))

    try:
        configure_engine(
            url,
            engine_kwargs=engine_kwargs,
            db_type="sqlite",
            apply_optimizations=False,
        )
    except RuntimeError:
        # 其他测试模块可能已完成配置，复用即可
        pass


class SyncTarget(TestBase):
    """用于验证 schema 同步行为的测试表。"""

    __tablename__ = "schema_sync_target"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class TypeMismatchTarget(TestBase):
    """用于验证类型不一致时的硬失败行为。"""

    __tablename__ = "schema_sync_type_mismatch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)


@pytest.mark.parametrize("single_type", ["REAL", "float4", "FLOAT(24)"])
@pytest.mark.parametrize("double_type", ["FLOAT", "float8", "DOUBLE PRECISION", "FLOAT(53)"])
def test_postgresql_float_precision_is_not_equivalent(
    single_type: str, double_type: str,
) -> None:
    """PostgreSQL 的单精度不能与双精度归一化为同一种类型。"""
    assert _normalize_type(single_type, "postgresql") != _normalize_type(
        double_type, "postgresql",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("FLOAT(1)", "real"), ("FLOAT(24)", "real"),
     ("FLOAT(25)", "double precision"), ("FLOAT(53)", "double precision"),
     ("float8", "double precision"), ("FLOAT", "double precision")],
)
def test_postgresql_float_precision_aliases(raw: str, expected: str) -> None:
    """精度边界及 PostgreSQL 默认 FLOAT 应与反射类型一致。"""
    assert _normalize_type(raw, "postgresql") == expected


@pytest.mark.parametrize("raw", ["REAL", "FLOAT", "DOUBLE PRECISION", "float4", "float8"])
def test_sqlite_float_affinity_is_equivalent(raw: str) -> None:
    """SQLite 浮点亲和性不能触发无意义的类型迁移。"""
    assert _normalize_type(raw, "sqlite") == "float"


def test_core_unix_timestamps_use_double_precision() -> None:
    """核心浮点时间字段显式声明 53 位二进制精度。"""
    from src.core.models.sql_alchemy import Base

    columns = [
        column for table in Base.metadata.tables.values()
        for column in table.columns if isinstance(column.type, Float)
    ]
    assert columns
    assert all(column.type.precision == 53 for column in columns)


@pytest.mark.asyncio
async def test_sqlite_time_precision_needs_no_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite 既有 REAL 时间列不需要 ALTER TYPE，秒数完整保留。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'time.db'}")
    monkeypatch.setattr(schema_sync, "get_engine", AsyncMock(return_value=engine))
    monkeypatch.setattr(schema_sync, "get_configured_db_type", lambda: "sqlite")
    model = MetaData()
    target = Table(
        "time_precision", model, Column("id", Integer, primary_key=True),
        Column("time", Float(precision=53), nullable=False),
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE time_precision (id INTEGER PRIMARY KEY, time REAL NOT NULL)"))
            await conn.execute(target.insert().values(id=1, time=1700000000.123456))
        stats = await enforce_database_schema_consistency(model)
        assert stats.columns_type_altered == 0
        async with engine.connect() as conn:
            assert (await conn.execute(select(target.c.time))).scalar_one() == 1700000000.123456
    finally:
        await engine.dispose()


@pytest.fixture
async def postgres_engine(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """使用显式测试 URL 的隔离 PostgreSQL，不读取应用配置。"""
    url = os.environ.get("NEO_MOFOX_TEST_POSTGRESQL_URL")
    if not url:
        pytest.skip("设置 NEO_MOFOX_TEST_POSTGRESQL_URL 可运行隔离 PostgreSQL 迁移测试")
    engine = create_async_engine(url)
    monkeypatch.setattr(schema_sync, "get_engine", AsyncMock(return_value=engine))
    monkeypatch.setattr(schema_sync, "get_configured_db_type", lambda: "postgresql")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_time_migration_preserves_data_and_indexes(
    postgres_engine: AsyncEngine,
) -> None:
    """启动同步扩宽 REAL，保留旧记录和索引，并且重复同步幂等。"""
    table_name = f"time_precision_{uuid4().hex}"
    legacy_metadata = MetaData()
    legacy = Table(
        table_name, legacy_metadata, Column("id", Integer, primary_key=True),
        Column("time", REAL, nullable=False), Column("content", Text, nullable=False),
    )
    index_name = f"idx_{table_name}"
    Index(index_name, legacy.c.time)
    model = MetaData()
    target = Table(
        table_name, model, Column("id", Integer, primary_key=True),
        Column("time", Float(precision=53), nullable=False), Column("content", Text, nullable=False),
    )
    try:
        async with postgres_engine.begin() as conn:
            await conn.run_sync(legacy_metadata.create_all)
            await conn.execute(legacy.insert().values(id=1, time=1700000000.123456, content="example"))
            original = (await conn.execute(select(legacy))).one()
        stats = await enforce_database_schema_consistency(model)
        assert stats.columns_type_altered == 1
        assert stats.columns_added == stats.columns_removed == 0
        assert (await enforce_database_schema_consistency(model)).columns_type_altered == 0
        async with postgres_engine.begin() as conn:
            assert (await conn.execute(select(target))).one() == original
            columns = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns(table_name))
            assert str(next(column["type"] for column in columns if column["name"] == "time")) == "DOUBLE PRECISION"
            indexes = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_indexes(table_name))
            assert index_name in {index["name"] for index in indexes}
            await conn.execute(target.insert().values(id=2, time=1700000123.123456, content="new"))
            actual = (await conn.execute(select(target.c.time).where(target.c.id == 2))).scalar_one()
            assert actual == 1700000123.123456
    finally:
        async with postgres_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: legacy_metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_postgresql_time_migration_rolls_back_on_failure(
    postgres_engine: AsyncEngine,
) -> None:
    """后续字段转换失败时，先前时间类型变更及数据一同回滚。"""
    table_name = f"time_rollback_{uuid4().hex}"
    legacy_metadata = MetaData()
    legacy = Table(
        table_name, legacy_metadata, Column("id", Integer, primary_key=True),
        Column("time", REAL, nullable=False), Column("score", Text, nullable=False),
    )
    model = MetaData()
    Table(
        table_name, model, Column("id", Integer, primary_key=True),
        Column("time", Float(precision=53), nullable=False), Column("score", Integer, nullable=False),
    )
    try:
        async with postgres_engine.begin() as conn:
            await conn.run_sync(legacy_metadata.create_all)
            await conn.execute(legacy.insert().values(id=1, time=1700000000.123456, score="invalid"))
            original = (await conn.execute(select(legacy))).one()
        with pytest.raises(DBAPIError):
            await enforce_database_schema_consistency(model)
        async with postgres_engine.connect() as conn:
            columns = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns(table_name))
            assert str(next(column["type"] for column in columns if column["name"] == "time")) == "REAL"
            assert (await conn.execute(select(legacy))).one() == original
    finally:
        async with postgres_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: legacy_metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_schema_sync_adds_missing_and_removes_undefined_columns() -> None:
    """应删除未定义字段并补齐缺失字段。"""
    engine = await get_engine()

    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS schema_sync_target"))
        await conn.execute(
            text(
                """
                CREATE TABLE schema_sync_target (
                    id INTEGER PRIMARY KEY,
                    legacy_col TEXT
                )
                """
            )
        )

    try:
        stats = await enforce_database_schema_consistency(TestBase.metadata)
        assert stats.tables_checked >= 1
        assert stats.columns_added >= 1
        assert stats.columns_removed >= 1

        async with engine.begin() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns("schema_sync_target")
            )

        names = {column["name"] for column in columns}
        assert "id" in names
        assert "name" in names
        assert "legacy_col" not in names
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS schema_sync_target"))


@pytest.mark.asyncio
async def test_schema_sync_raises_on_sqlite_type_mismatch() -> None:
    """SQLite 遇到类型漂移时应硬失败，避免带病启动。"""
    engine = await get_engine()

    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS schema_sync_type_mismatch"))
        await conn.execute(
            text(
                """
                CREATE TABLE schema_sync_type_mismatch (
                    id INTEGER PRIMARY KEY,
                    score TEXT NOT NULL
                )
                """
            )
        )

    try:
        with pytest.raises(DatabaseInitializationError):
            await enforce_database_schema_consistency(TestBase.metadata)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS schema_sync_type_mismatch"))
