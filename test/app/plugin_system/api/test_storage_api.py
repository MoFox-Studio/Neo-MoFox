"""storage_api 模块测试。

覆盖 PluginDatabase 生命周期与 JSON 存储扁平化函数。
"""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

from src.app.plugin_system.api.storage_api import (
    PluginDatabase,
    delete_json,
    exists_json,
    list_json,
    load_json,
    save_json,
)

# ---------------------------------------------------------------------------
# 测试用 SQLAlchemy 模型
# ---------------------------------------------------------------------------

TestBase = declarative_base()


class _Note(TestBase):
    """测试用记录模型。"""

    __tablename__ = "storage_api_test_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# PluginDatabase 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_database_initialize_creates_table(tmp_path: pytest.TempdirFactory) -> None:
    """initialize() 应建表，crud().create() 应成功写入。"""
    db = PluginDatabase(str(tmp_path / "test.db"), [_Note])
    await db.initialize()
    try:
        record = await db.crud(_Note).create({"title": "hello", "body": "world"})
        assert record.title == "hello"
        assert record.body == "world"
        assert record.id is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_plugin_database_initialize_idempotent(tmp_path: pytest.TempdirFactory) -> None:
    """多次调用 initialize() 不应抛出异常。"""
    db = PluginDatabase(str(tmp_path / "idempotent.db"), [_Note])
    await db.initialize()
    await db.initialize()  # 第二次调用
    await db.close()


@pytest.mark.asyncio
async def test_plugin_database_crud_read_write(tmp_path: pytest.TempdirFactory) -> None:
    """crud() 接口的 get / get_by / get_multi / count / exists / delete 方法。"""
    db = PluginDatabase(str(tmp_path / "crud.db"), [_Note])
    await db.initialize()
    try:
        crud = db.crud(_Note)
        a = await crud.create({"title": "A"})
        b = await crud.create({"title": "B"})
        c = await crud.create({"title": "C"})

        assert await crud.count() == 3
        assert await crud.exists(title="A")
        assert not await crud.exists(title="Z")

        fetched = await crud.get(a.id)
        assert fetched is not None and fetched.title == "A"

        multi = await crud.get_multi()
        assert len(multi) == 3

        await crud.delete(b.id)
        assert await crud.count() == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_plugin_database_query_filter(tmp_path: pytest.TempdirFactory) -> None:
    """query() 接口的链式 filter / order_by / limit。"""
    db = PluginDatabase(str(tmp_path / "query.db"), [_Note])
    await db.initialize()
    try:
        crud = db.crud(_Note)
        await crud.bulk_create([{"title": "alpha"}, {"title": "beta"}, {"title": "gamma"}])

        results = await db.query(_Note).filter(title="beta").all()
        assert len(results) == 1
        assert results[0].title == "beta"

        first = await db.query(_Note).order_by("title").first()
        assert first is not None
        assert first.title == "alpha"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_plugin_database_aggregate(tmp_path: pytest.TempdirFactory) -> None:
    """aggregate() 接口的 group_by_count。"""
    db = PluginDatabase(str(tmp_path / "agg.db"), [_Note])
    await db.initialize()
    try:
        crud = db.crud(_Note)
        await crud.bulk_create([
            {"title": "x"},
            {"title": "x"},
            {"title": "y"},
        ])
        counts = await db.aggregate(_Note).group_by_count("title")
        # 转换为字典方便断言
        count_map = {row[0]: row[1] for row in counts}
        assert count_map["x"] == 2
        assert count_map["y"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_plugin_database_session_raw(tmp_path: pytest.TempdirFactory) -> None:
    """session() 上下文管理器应暴露 AsyncSession，支持直接 execute。"""
    from sqlalchemy import select

    db = PluginDatabase(str(tmp_path / "session.db"), [_Note])
    await db.initialize()
    try:
        async with db.session() as s:
            s.add(_Note(title="raw"))

        async with db.session() as s:
            result = await s.execute(select(_Note).where(_Note.title == "raw"))
            row = result.scalars().first()
        assert row is not None and row.title == "raw"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_plugin_database_requires_initialize(tmp_path: pytest.TempdirFactory) -> None:
    """未调用 initialize() 时调用 crud() 应抛出 RuntimeError。"""
    db = PluginDatabase(str(tmp_path / "uninit.db"), [_Note])
    with pytest.raises(RuntimeError, match="尚未初始化"):
        db.crud(_Note)


@pytest.mark.asyncio
async def test_plugin_database_independent_from_main_db(
    tmp_path: pytest.TempdirFactory,
) -> None:
    """两个 PluginDatabase 实例使用独立文件，互不影响。"""
    db1 = PluginDatabase(str(tmp_path / "db1.db"), [_Note])
    db2 = PluginDatabase(str(tmp_path / "db2.db"), [_Note])
    await db1.initialize()
    await db2.initialize()
    try:
        await db1.crud(_Note).create({"title": "only_in_db1"})
        assert await db2.crud(_Note).count() == 0
        assert await db1.crud(_Note).count() == 1
    finally:
        await db1.close()
        await db2.close()


# ---------------------------------------------------------------------------
# JSON 存储扁平化函数测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_load_json(tmp_path: pytest.TempdirFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_json / load_json 基本读写。"""
    import src.app.plugin_system.api.storage_api as sa

    # 使用临时目录隔离，避免污染 data/
    from src.kernel.storage import JSONStore
    store = JSONStore(str(tmp_path / "json_ns"))
    monkeypatch.setattr(sa, "_get_plugin_json_store", lambda _name: store)

    await save_json("test_ns", "cfg", {"v": 42})
    data = await load_json("test_ns", "cfg")
    assert data is not None and data["v"] == 42


@pytest.mark.asyncio
async def test_exists_and_delete_json(tmp_path: pytest.TempdirFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """exists_json / delete_json 行为。"""
    import src.app.plugin_system.api.storage_api as sa

    from src.kernel.storage import JSONStore
    store = JSONStore(str(tmp_path / "json_ns2"))
    monkeypatch.setattr(sa, "_get_plugin_json_store", lambda _name: store)

    assert not await exists_json("ns", "key1")
    await save_json("ns", "key1", {"x": 1})
    assert await exists_json("ns", "key1")
    deleted = await delete_json("ns", "key1")
    assert deleted
    assert not await exists_json("ns", "key1")


@pytest.mark.asyncio
async def test_list_json(tmp_path: pytest.TempdirFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_json 应返回所有已保存的键名。"""
    import src.app.plugin_system.api.storage_api as sa

    from src.kernel.storage import JSONStore
    store = JSONStore(str(tmp_path / "json_ns3"))
    monkeypatch.setattr(sa, "_get_plugin_json_store", lambda _name: store)

    await save_json("ns", "a", {})
    await save_json("ns", "b", {})
    keys = await list_json("ns")
    assert set(keys) == {"a", "b"}
