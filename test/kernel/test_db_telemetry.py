"""DB telemetry 集成测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json

import pytest
from sqlalchemy import Integer, String, text
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from src.kernel.db import CRUDBase, configure_engine, get_engine, get_db_session
from src.kernel.db.core.engine import _build_sqlite_config
from src.kernel.db.core.session import reset_session_factory
from src.kernel.db.core.exceptions import DatabaseTransactionError
from src.kernel.db.core.engine import reset_engine_state
from src.kernel.telemetry import (
    TelemetryConfig,
    close_telemetry_db,
    get_telemetry_collector,
    init_telemetry,
)

TestBase = declarative_base()


class TelemetryUser(TestBase):
    """DB telemetry 测试用模型。"""

    __tablename__ = "telemetry_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)


@pytest.fixture(autouse=True)
async def _reset_global_state() -> AsyncGenerator[None, None]:
    """确保测试之间的全局状态隔离。"""
    await close_telemetry_db()
    await reset_session_factory()
    await reset_engine_state()
    yield
    await close_telemetry_db()
    await reset_session_factory()
    await reset_engine_state()


def _decode_attributes(row: dict[str, object]) -> dict[str, object]:
    """解析 telemetry 行中的 attributes_json。"""
    raw = row.get("attributes_json")
    if not isinstance(raw, str) or not raw:
        return {}
    return json.loads(raw)


@pytest.mark.asyncio
async def test_engine_initialization_records_db_event(tmp_path) -> None:
    """数据库引擎初始化应记录 telemetry 事件。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_db_metrics=True,
        )
    )

    url, engine_kwargs = _build_sqlite_config(str(tmp_path / "db.sqlite"))
    configure_engine(
        url,
        engine_kwargs=engine_kwargs,
        db_type="sqlite",
        apply_optimizations=False,
    )
    await get_engine()

    rows = await get_telemetry_collector().get_recent(domain="db", limit=10)
    assert any(row["event_name"] == "engine_initialized" for row in rows)


@pytest.mark.asyncio
async def test_crud_count_records_slow_query_event(tmp_path) -> None:
    """低阈值下 CRUD 查询应产生日志化的慢查询事件。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_db_metrics=True,
            slow_query_threshold_ms=0.0,
        )
    )

    url, engine_kwargs = _build_sqlite_config(str(tmp_path / "db.sqlite"))
    configure_engine(
        url,
        engine_kwargs=engine_kwargs,
        db_type="sqlite",
        apply_optimizations=False,
    )
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TelemetryUser)
        await crud.create({"name": "alice"})
        await crud.count(name="alice")

        rows = await get_telemetry_collector().get_recent(domain="db", limit=20)
        slow_rows = [row for row in rows if row["event_name"] == "slow_query"]
        assert slow_rows
        assert any(
            _decode_attributes(row).get("operation") == "count"
            and _decode_attributes(row).get("model_name") == "telemetry_users"
            for row in slow_rows
        )
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_transaction_error_records_db_event(tmp_path) -> None:
    """事务异常应记录 transaction_error 事件。"""
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_db_metrics=True,
        )
    )

    url, engine_kwargs = _build_sqlite_config(str(tmp_path / "db.sqlite"))
    configure_engine(
        url,
        engine_kwargs=engine_kwargs,
        db_type="sqlite",
        apply_optimizations=False,
    )
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        with pytest.raises(DatabaseTransactionError):
            async with get_db_session() as session:
                await session.execute(text("SELECT * FROM not_exists_table"))

        rows = await get_telemetry_collector().get_recent(domain="db", limit=20)
        error_rows = [row for row in rows if row["event_name"] == "transaction_error"]
        assert error_rows
        assert any(
            _decode_attributes(row).get("error_type") == "OperationalError"
            for row in error_rows
        )
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))