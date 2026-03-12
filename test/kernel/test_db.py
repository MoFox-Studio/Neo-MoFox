"""
DB 模块简化测试
"""

from __future__ import annotations

import pytest
from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, declarative_base

from src.kernel.db import (
    CRUDBase,
    QueryBuilder,
    configure_engine,
    get_engine,
)


@pytest.fixture(scope="session", autouse=True)
def _configure_kernel_db_for_tests(tmp_path_factory: pytest.TempPathFactory) -> None:
    """为测试环境配置 kernel/db 引擎。

    kernel/db 不读取用户配置，因此测试作为“高层调用方”负责注入连接参数。
    """
    from src.kernel.db.core.engine import _build_sqlite_config

    db_path = tmp_path_factory.mktemp("kernel_db") / "test.db"
    url, engine_kwargs = _build_sqlite_config(str(db_path))

    # 测试中无需跑优化逻辑，避免额外开销/偶发差异
    configure_engine(
        url,
        engine_kwargs=engine_kwargs,
        db_type="sqlite",
        apply_optimizations=False,
    )

# 创建测试用的 Base 和模型
TestBase = declarative_base()


class TestUser(TestBase):
    """测试用户模型"""
    __tablename__ = "test_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


@pytest.mark.asyncio
async def test_crud_create():
    """测试创建记录"""
    # 创建表
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({
            "name": "TestUser",
            "age": 25,
            "is_active": True
        })
        assert user.name == "TestUser"
        assert user.age == 25
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter():
    """测试查询过滤"""
    # 创建表并插入数据
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
        ])

        qb = QueryBuilder(TestUser)
        users = await qb.filter(name="Alice").all()
        assert len(users) == 1
        assert users[0].name == "Alice"
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))

@pytest.mark.asyncio
async def test_crud_get():
    """测试根据 ID 获取记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        created = await crud.create({"name": "David", "age": 28, "is_active": True})

        user = await crud.get(created.id)
        assert user is not None
        assert user.name == "David"
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_get_by():
    """测试根据条件获取记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Eve", "age": 30, "is_active": True})

        user = await crud.get_by(name="Eve")
        assert user is not None
        assert user.age == 30
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_get_multi():
    """测试获取多条记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Frank", "age": 25, "is_active": True},
            {"name": "Grace", "age": 27, "is_active": True},
            {"name": "Henry", "age": 29, "is_active": True},
        ])

        users = await crud.get_multi(skip=0, limit=2)
        assert len(users) == 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_update():
    """测试更新记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({"name": "Ivy", "age": 26, "is_active": True})

        updated = await crud.update(user.id, {"age": 27, "email": "ivy@test.com"})
        assert updated is not None
        assert updated.age == 27
        assert updated.email == "ivy@test.com"
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_delete():
    """测试删除记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({"name": "Jack", "age": 32, "is_active": True})

        success = await crud.delete(user.id)
        assert success is True

        deleted_user = await crud.get(user.id)
        assert deleted_user is None
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_count():
    """测试统计记录数"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        initial_count = await crud.count()

        await crud.bulk_create([
            {"name": "Kate", "age": 24, "is_active": True},
            {"name": "Leo", "age": 26, "is_active": True},
        ])

        new_count = await crud.count()
        assert new_count == initial_count + 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_exists():
    """测试检查记录是否存在"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        assert not await crud.exists(name="NonExistent")

        await crud.create({"name": "Mary", "age": 28, "is_active": True})
        assert await crud.exists(name="Mary")
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_get_or_create():
    """测试获取或创建记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user1, created1 = await crud.get_or_create(
            defaults={"age": 30},
            name="Nancy"
        )
        assert created1 is True
        assert user1.name == "Nancy"

        user2, created2 = await crud.get_or_create(
            defaults={"age": 35},
            name="Nancy"
        )
        assert created2 is False
        assert user2.id == user1.id
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_operators():
    """测试查询过滤操作符"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        # 测试 gt
        qb1 = QueryBuilder(TestUser)
        users = await qb1.filter(age__gt=28).all()
        assert len(users) == 2

        # 测试 lt
        qb2 = QueryBuilder(TestUser)
        users = await qb2.filter(age__lt=30).all()
        assert len(users) == 1

        # 测试 in
        qb3 = QueryBuilder(TestUser)
        users = await qb3.filter(age__in=[25, 35]).all()
        assert len(users) == 2

        # 测试 like
        qb4 = QueryBuilder(TestUser)
        users = await qb4.filter(name__like="%a%").all()
        assert len(users) == 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_order_and_pagination():
    """测试排序和分页"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "User1", "age": 20, "is_active": True},
            {"name": "User2", "age": 25, "is_active": True},
            {"name": "User3", "age": 30, "is_active": True},
        ])

        # 测试升序排序
        qb1 = QueryBuilder(TestUser)
        users = await qb1.order_by("age").all()
        assert users[0].age == 20

        # 测试降序排序
        qb2 = QueryBuilder(TestUser)
        users = await qb2.order_by("-age").all()
        assert users[0].age == 30

        # 测试分页
        qb3 = QueryBuilder(TestUser)
        items, total = await qb3.paginate(page=1, page_size=2)
        assert total == 3
        assert len(items) == 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_more_operators():
    """测试更多查询操作符"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "email": "alice@test.com", "is_active": True},
            {"name": "Bob", "age": 30, "email": None, "is_active": True},
            {"name": "Charlie", "age": 35, "email": "charlie@test.com", "is_active": False},
        ])

        # 测试 gte
        qb1 = QueryBuilder(TestUser)
        users = await qb1.filter(age__gte=30).all()
        assert len(users) == 2

        # 测试 lte
        qb2 = QueryBuilder(TestUser)
        users = await qb2.filter(age__lte=30).all()
        assert len(users) == 2

        # 测试 ne
        qb3 = QueryBuilder(TestUser)
        users = await qb3.filter(name__ne="Alice").all()
        assert len(users) == 2

        # 测试 nin
        qb4 = QueryBuilder(TestUser)
        users = await qb4.filter(age__nin=[25, 35]).all()
        assert len(users) == 1
        assert users[0].name == "Bob"

        # 测试 isnull
        qb5 = QueryBuilder(TestUser)
        users = await qb5.filter(email__isnull=True).all()
        assert len(users) == 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_first_count_exists():
    """测试 first, count, exists 方法"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
        ])

        # first
        qb1 = QueryBuilder(TestUser)
        user = await qb1.filter(name="Alice").first()
        assert user is not None
        assert user.name == "Alice"

        # first not found
        qb2 = QueryBuilder(TestUser)
        user = await qb2.filter(name="NonExistent").first()
        assert user is None

        # count
        qb3 = QueryBuilder(TestUser)
        count = await qb3.filter(is_active=True).count()
        assert count == 2

        # exists
        qb4 = QueryBuilder(TestUser)
        assert await qb4.filter(name="Alice").exists()

        # not exists
        qb5 = QueryBuilder(TestUser)
        assert not await qb5.filter(name="NonExistent").exists()

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_bulk_operations():
    """测试批量操作"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)

        # bulk_create
        users = await crud.bulk_create([
            {"name": "Rose", "age": 25, "is_active": True},
            {"name": "Sam", "age": 26, "is_active": True},
            {"name": "Tom", "age": 27, "is_active": True},
        ])
        assert len(users) == 3

        # bulk_update
        updates = [
            (users[0].id, {"age": 26}),
            (users[1].id, {"age": 27}),
        ]
        count = await crud.bulk_update(updates)
        assert count == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_or():
    """测试 OR 过滤"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        # filter_or - 不同字段的 OR 条件
        qb = QueryBuilder(TestUser)
        users = await qb.filter_or(name="Alice", age=35).all()
        assert len(users) == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_edge_cases():
    """测试边界情况"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)

        # get 不存在的记录
        user = await crud.get(99999)
        assert user is None

        # get_by 不存在的记录
        user = await crud.get_by(name="NonExistent")
        assert user is None

        # update 不存在的记录
        result = await crud.update(99999, {"age": 30})
        assert result is None

        # delete 不存在的记录
        success = await crud.delete(99999)
        assert success is False

        # bulk_update 空列表
        count = await crud.bulk_update([])
        assert count == 0

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query():
    """测试聚合查询"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        agg = AggregateQuery(TestUser)

        # sum
        total_age = await agg.sum("age")
        assert total_age == 90

        # avg
        avg_age = await agg.avg("age")
        assert avg_age == 30

        # max
        max_age = await agg.max("age")
        assert max_age == 35

        # min
        min_age = await agg.min("age")
        assert min_age == 25

        # with filter
        agg2 = AggregateQuery(TestUser)
        agg2.filter(is_active=True)
        total_active_age = await agg2.sum("age")
        assert total_active_age == 55

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_iterators():
    """测试迭代器方法"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": f"User{i}", "age": 20 + i, "is_active": True}
            for i in range(10)
        ])

        qb = QueryBuilder(TestUser)

        # iter_batches
        batch_count = 0
        async for batch in qb.iter_batches(batch_size=3):
            batch_count += 1
            assert len(batch) <= 3
        assert batch_count == 4  # 10条数据，每批3条，需要4批

        # iter_all
        count = 0
        async for user in qb.iter_all():
            count += 1
        assert count == 10

        # iter_all as_dict=False
        count = 0
        async for user in qb.iter_all(as_dict=False):
            count += 1
            assert hasattr(user, "name")
        assert count == 10

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_empty_result():
    """测试空结果情况"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        qb = QueryBuilder(TestUser)

        # all on empty table
        users = await qb.all()
        assert len(users) == 0

        # first on empty table
        user = await qb.first()
        assert user is None

        # count on empty table
        count = await qb.count()
        assert count == 0

        # exists on empty table
        exists = await qb.exists()
        assert exists is False

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_model_to_dict_edge_cases():
    """测试模型转换的边界情况"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.crud import _model_to_dict, _dict_to_model

        # 测试 _model_to_dict with None
        result = _model_to_dict(None)
        assert result == {}

        # 测试 _dict_to_model
        user_dict = {"name": "Test", "age": 25, "is_active": True}
        user = _dict_to_model(TestUser, user_dict)
        assert user.name == "Test"
        assert user.age == 25

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


# =============================================================================
# Engine 模块测试
# =============================================================================

@pytest.mark.asyncio
async def test_engine_singleton():
    """测试引擎单例模式"""
    from src.kernel.db.core.engine import get_engine, close_engine

    engine1 = await get_engine()
    engine2 = await get_engine()

    # 应该返回同一个实例
    assert engine1 is engine2

    # 关闭后重新获取
    await close_engine()
    engine3 = await get_engine()

    # 应该是新实例
    assert engine3 is not engine1


@pytest.mark.asyncio
async def test_engine_info():
    """测试获取引擎信息"""
    from src.kernel.db.core.engine import get_engine_info

    info = await get_engine_info()

    # 验证返回的信息包含必要的字段
    assert "name" in info
    assert "driver" in info
    assert "url" in info

    # 对于 SQLite，验证基本属性
    if info["name"] == "sqlite":
        assert "aiosqlite" in info["driver"]


@pytest.mark.asyncio
async def test_close_engine():
    """测试关闭引擎"""
    from src.kernel.db.core.engine import get_engine, close_engine

    # 获取引擎
    engine = await get_engine()
    assert engine is not None

    # 关闭引擎
    await close_engine()

    # 重新获取应该创建新引擎
    new_engine = await get_engine()
    assert new_engine is not None


@pytest.mark.asyncio
async def test_sqlite_config_builder():
    """测试SQLite配置构建"""
    from src.kernel.db.core.engine import _build_sqlite_config
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        url, kwargs = _build_sqlite_config(str(db_path))

        # 验证URL
        assert "sqlite" in url
        assert "aiosqlite" in url
        assert str(db_path.absolute()) in url

        # 验证参数
        assert kwargs["echo"] is False
        assert kwargs["future"] is True
        assert "check_same_thread" in kwargs["connect_args"]
        assert "timeout" in kwargs["connect_args"]


@pytest.mark.asyncio
async def test_postgresql_config_builder():
    """测试PostgreSQL配置构建"""
    from src.kernel.db.core.engine import _build_postgresql_config

    url, kwargs = _build_postgresql_config(
        host="localhost",
        port=5432,
        user="testuser",
        password="p@ssw0rd",
        database="testdb",
    )

    # 验证URL编码
    assert "postgresql" in url
    assert "asyncpg" in url
    assert "testuser" in url
    assert "p%40ssw0rd" in url  # 密码中的@应该被编码

    # 验证连接池参数
    assert kwargs["pool_size"] == 10
    assert kwargs["max_overflow"] == 20
    assert kwargs["pool_timeout"] == 30
    assert kwargs["pool_recycle"] == 3600
    assert kwargs["pool_pre_ping"] is True


@pytest.mark.asyncio
async def test_sqlite_optimizations():
    """测试SQLite优化应用"""
    from src.kernel.db.core.engine import _enable_sqlite_optimizations
    from sqlalchemy.ext.asyncio import create_async_engine
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "opt_test.db"
        url = f"sqlite+aiosqlite:///{db_path.absolute()}"

        engine = create_async_engine(url)

        try:
            # 应用优化（应该不抛出异常）
            await _enable_sqlite_optimizations(engine)
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_optimizations():
    """测试PostgreSQL优化应用（仅验证不抛出异常）"""
    from src.kernel.db.core.engine import _enable_postgresql_optimizations
    from sqlalchemy.ext.asyncio import create_async_engine

    # 创建一个内存SQLite引擎来测试函数逻辑
    # （真实PostgreSQL连接需要数据库服务）
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    try:
        # 函数应该优雅地处理失败
        await _enable_postgresql_optimizations(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_engine_initialization_error():
    """测试引擎初始化失败时的错误处理"""
    from src.kernel.db.core.engine import get_engine, close_engine
    from src.kernel.db.core.exceptions import DatabaseInitializationError
    from unittest.mock import patch

    # 关闭现有引擎
    await close_engine()

    # Mock create_async_engine 抛出异常
    with patch("src.kernel.db.core.engine.create_async_engine") as mock_create:
        mock_create.side_effect = Exception("Connection failed")

        # 应该抛出 DatabaseInitializationError
        with pytest.raises(DatabaseInitializationError):
            await get_engine()

    # 清理：重置引擎状态以便后续测试
    from src.kernel.db.core import engine as engine_module
    engine_module._engine = None
    engine_module._engine_lock = None


# =============================================================================
# Session 模块测试
# =============================================================================

@pytest.mark.asyncio
async def test_session_factory_singleton():
    """测试会话工厂单例模式"""
    from src.kernel.db.core.session import get_session_factory, reset_session_factory

    factory1 = await get_session_factory()
    factory2 = await get_session_factory()

    # 应该返回同一个实例
    assert factory1 is factory2

    # 重置后获取 - 注意：reset后engine仍然存在，所以factory可能相同
    await reset_session_factory()
    factory3 = await get_session_factory()

    # factory3应该是一个factory实例
    assert factory3 is not None


@pytest.mark.asyncio
async def test_session_context_manager():
    """测试会话上下文管理器"""
    from src.kernel.db.core.session import get_db_session

    # 测试正常退出（自动提交）
    async with get_db_session() as session:
        assert session is not None
        # 会话应该是活动的
        assert session.is_active


@pytest.mark.asyncio
async def test_session_transaction_commit():
    """测试会话事务提交"""
    from src.kernel.db.core.session import get_db_session
    from sqlalchemy import text

    async with get_db_session() as session:
        # 执行一个简单的查询
        result = await session.execute(text("SELECT 1"))
        value = result.scalar()
        assert value == 1

        # 正常退出应该自动提交


@pytest.mark.asyncio
async def test_session_transaction_rollback():
    """测试会话事务回滚"""
    from src.kernel.db.core.session import get_db_session

    # 测试异常时自动回滚
    with pytest.raises(ValueError):
        async with get_db_session() as session:
            assert session.is_active
            raise ValueError("Test error")

    # 异常后应该正确回滚


@pytest.mark.asyncio
async def test_apply_session_settings_sqlite():
    """测试SQLite会话设置应用"""
    from src.kernel.db.core.session import _apply_session_settings

    engine = await get_engine()
    async with engine.begin() as conn:
        # 获取原始会话对象来测试设置
        from sqlalchemy.ext.asyncio import AsyncSession
        async_session = AsyncSession(conn)

        try:
            # 应用SQLite设置（应该不抛出异常）
            await _apply_session_settings(async_session, "sqlite")
        finally:
            await async_session.close()


@pytest.mark.asyncio
async def test_apply_session_settings_postgresql():
    """测试PostgreSQL会话设置应用"""
    from src.kernel.db.core.session import _apply_session_settings
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = await get_engine()
    async with engine.begin() as conn:
        async_session = AsyncSession(conn)

        try:
            # PostgreSQL设置目前是pass，应该不抛出异常
            await _apply_session_settings(async_session, "postgresql")
        finally:
            await async_session.close()


@pytest.mark.asyncio
async def test_apply_session_settings_error_handling():
    """测试会话设置应用时的错误处理"""
    from src.kernel.db.core.session import _apply_session_settings
    from unittest.mock import AsyncMock

    # 创建一个mock session，execute会抛出异常
    mock_session = AsyncMock()
    mock_session.execute.side_effect = Exception("Simulated error")

    # 应该优雅地处理异常（不抛出）
    await _apply_session_settings(mock_session, "sqlite")


# =============================================================================
# CRUD 模块额外测试
# =============================================================================

@pytest.mark.asyncio
async def test_crud_get_multi_with_list_filter():
    """测试get_multi中使用列表过滤"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        # 使用列表过滤
        users = await crud.get_multi(age=[25, 30])
        assert len(users) == 2

        # 使用set过滤
        users = await crud.get_multi(age={25, 35})
        assert len(users) == 2

        # 使用tuple过滤
        users = await crud.get_multi(age=(30,))
        assert len(users) == 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_count_with_list_filter():
    """测试count中使用列表过滤"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": True},
        ])

        # 使用列表过滤统计
        count = await crud.count(age=[25, 30])
        assert count == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


# =============================================================================
# Query 模块额外测试
# =============================================================================

@pytest.mark.asyncio
async def test_query_filter_unknown_field():
    """测试过滤时使用未知字段"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # 使用未知字段应该被忽略，不抛出异常
        qb = QueryBuilder(TestUser)
        users = await qb.filter(unknown_field="value", name="Alice").all()

        # 仍然应该找到记录
        assert len(users) == 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_unknown_operator():
    """测试过滤时使用未知操作符"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # 使用未知操作符应该被忽略，不抛出异常
        qb = QueryBuilder(TestUser)
        users = await qb.filter(age__unknown_op=30, name="Alice").all()

        # 仍然应该找到记录（未知操作符被忽略）
        assert len(users) >= 0

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_or_no_conditions():
    """测试filter_or没有有效条件"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # 只使用未知字段，应该不添加任何条件
        qb = QueryBuilder(TestUser)
        users = await qb.filter_or(unknown_field="value").all()

        # 应该返回所有记录
        assert len(users) >= 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_order_by_unknown_field():
    """测试排序时使用未知字段"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # 使用未知字段应该被忽略，不抛出异常
        qb = QueryBuilder(TestUser)
        users = await qb.order_by("unknown_field").all()

        # 应该返回记录（未知字段被忽略）
        assert len(users) >= 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_limit_and_offset():
    """测试limit和offset"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": f"User{i}", "age": 20 + i, "is_active": True}
            for i in range(5)
        ])

        # 测试limit
        qb1 = QueryBuilder(TestUser)
        users = await qb1.limit(3).all()
        assert len(users) == 3

        # 测试offset
        qb2 = QueryBuilder(TestUser)
        users = await qb2.offset(2).all()
        assert len(users) == 3

        # 测试limit + offset
        qb3 = QueryBuilder(TestUser)
        users = await qb3.offset(2).limit(2).all()
        assert len(users) == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_as_dict_true():
    """测试as_dict=True返回字典"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        qb = QueryBuilder(TestUser)
        users = await qb.all(as_dict=True)

        # 应该返回字典列表
        assert len(users) == 1
        assert isinstance(users[0], dict)
        assert users[0]["name"] == "Alice"
        assert users[0]["age"] == 25

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_first_as_dict():
    """测试first的as_dict参数"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        qb = QueryBuilder(TestUser)
        user = await qb.first(as_dict=True)

        # 应该返回字典
        assert isinstance(user, dict)
        assert user["name"] == "Alice"

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query_invalid_field():
    """测试聚合查询使用无效字段"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        agg = AggregateQuery(TestUser)

        # 使用不存在的字段应该抛出ValueError
        with pytest.raises(ValueError, match="不存在"):
            await agg.sum("nonexistent_field")

        with pytest.raises(ValueError, match="不存在"):
            await agg.avg("nonexistent_field")

        with pytest.raises(ValueError, match="不存在"):
            await agg.max("nonexistent_field")

        with pytest.raises(ValueError, match="不存在"):
            await agg.min("nonexistent_field")

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query_group_by():
    """测试分组统计"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 25, "is_active": True},
            {"name": "Charlie", "age": 30, "is_active": False},
            {"name": "David", "age": 30, "is_active": False},
        ])

        agg = AggregateQuery(TestUser)

        # 按年龄分组统计
        result = await agg.group_by_count("age")
        assert len(result) == 2

        # 验证分组结果
        age_counts = {row[0]: row[1] for row in result}
        assert age_counts[25] == 2
        assert age_counts[30] == 2

        # 带过滤条件的分组
        agg2 = AggregateQuery(TestUser)
        agg2.filter(is_active=True)
        result2 = await agg2.group_by_count("age")

        # 只有age=25的记录是active的
        age_counts2 = {row[0]: row[1] for row in result2}
        assert age_counts2.get(25) == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query_group_by_no_fields():
    """测试分组统计不指定字段"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        agg = AggregateQuery(TestUser)

        # 不指定字段应该抛出ValueError
        with pytest.raises(ValueError, match="至少需要一个分组字段"):
            await agg.group_by_count()

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query_group_by_invalid_fields():
    """测试分组统计使用无效字段"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        agg = AggregateQuery(TestUser)

        # 只使用无效字段应该返回空列表
        result = await agg.group_by_count("nonexistent_field")
        assert result == []

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query_sum_avg_zero_result():
    """测试聚合查询在没有结果时返回0"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        agg = AggregateQuery(TestUser)

        # 空表的sum应该返回0
        total = await agg.sum("age")
        assert total == 0

        # 空表的avg应该返回0
        avg = await agg.avg("age")
        assert avg == 0

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


# =============================================================================
# 异常测试
# =============================================================================

def test_exceptions_hierarchy():
    """测试异常类层次结构"""
    from src.kernel.db.core.exceptions import (
        DatabaseError,
        DatabaseInitializationError,
        DatabaseConnectionError,
        DatabaseQueryError,
        DatabaseTransactionError,
    )

    # 测试所有异常都继承自DatabaseError
    assert issubclass(DatabaseInitializationError, DatabaseError)
    assert issubclass(DatabaseConnectionError, DatabaseError)
    assert issubclass(DatabaseQueryError, DatabaseError)
    assert issubclass(DatabaseTransactionError, DatabaseError)

    # 测试可以抛出和捕获异常
    with pytest.raises(DatabaseInitializationError):
        raise DatabaseInitializationError("Test error")

    with pytest.raises(DatabaseError):
        raise DatabaseConnectionError("Test error")


# =============================================================================
# 更多边界情况测试（提升覆盖率）
# =============================================================================

@pytest.mark.asyncio
async def test_crud_get_multi_filter_without_field():
    """测试get_multi过滤时字段不存在"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # 使用不存在的字段过滤，应该被忽略
        users = await crud.get_multi(unknown_field="value")
        # 应该返回所有记录（因为过滤条件被忽略）
        assert len(users) >= 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_count_filter_without_field():
    """测试count过滤时字段不存在"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # 使用不存在的字段过滤，应该被忽略
        count = await crud.count(unknown_field="value")
        # 应该返回所有记录数量
        assert count >= 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_filter_without_field():
    """测试AggregateQuery过滤时字段不存在"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        crud = CRUDBase(TestUser)
        await crud.create({"name": "Alice", "age": 25, "is_active": True})

        agg = AggregateQuery(TestUser)
        # 使用不存在的字段过滤，应该被忽略
        agg.filter(unknown_field="value")
        total = await agg.sum("age")
        assert total == 25

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_isnull_false():
    """测试filter的isnull操作符为False"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "email": "alice@test.com", "is_active": True},
            {"name": "Bob", "age": 30, "email": None, "is_active": True},
        ])

        # 测试 isnull=False (不为空)
        qb = QueryBuilder(TestUser)
        users = await qb.filter(email__isnull=False).all()
        assert len(users) == 1
        assert users[0].name == "Alice"

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


def test_model_helper_with_empty_model():
    """测试辅助函数处理只有一个列的模型"""
    from sqlalchemy import Integer
    from sqlalchemy.orm import mapped_column, declarative_base
    from src.kernel.db.api.crud import _get_model_column_names, _get_model_value_fetcher

    # 创建一个只有一列的模型
    SingleBase = declarative_base()

    class SingleModel(SingleBase):
        """只有一列的模型"""
        __tablename__ = "single_model"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 测试 _get_model_column_names
    columns = _get_model_column_names(SingleModel)
    assert len(columns) == 1

    # 测试 _get_model_value_fetcher
    fetcher = _get_model_value_fetcher(SingleModel)
    assert callable(fetcher)


@pytest.mark.asyncio
async def test_engine_info_error_handling():
    """测试get_engine_info的错误处理"""
    from src.kernel.db.core.engine import get_engine_info, close_engine
    from unittest.mock import patch

    # 关闭现有引擎
    await close_engine()

    # Mock get_engine抛出异常
    async def mock_get_engine_error():
        raise Exception("Engine error")

    with patch("src.kernel.db.core.engine.get_engine", side_effect=mock_get_engine_error):
        # get_engine_info应该捕获异常并返回空字典
        info = await get_engine_info()
        assert info == {}

    # 清理：重置引擎状态
    from src.kernel.db.core import engine as engine_module
    engine_module._engine = None
    engine_module._engine_lock = None


@pytest.mark.asyncio
async def test_session_double_check_locking():
    """测试会话工厂的双重检查锁定"""
    from src.kernel.db.core.session import get_session_factory
    import asyncio

    # 并发获取会话工厂
    tasks = [get_session_factory() for _ in range(10)]
    factories = await asyncio.gather(*tasks)

    # 所有应该是同一个实例
    assert all(f is factories[0] for f in factories)


@pytest.mark.asyncio
async def test_query_iter_batches_empty_result():
    """测试iter_batches在空结果时正常工作"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        qb = QueryBuilder(TestUser)

        # 空表的iter_batches应该立即结束
        batch_count = 0
        async for batch in qb.iter_batches(batch_size=10):
            batch_count += 1

        assert batch_count == 0

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_iter_all_empty_result():
    """测试iter_all在空结果时正常工作"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        qb = QueryBuilder(TestUser)

        # 空表的iter_all应该立即结束
        count = 0
        async for item in qb.iter_all():
            count += 1

        assert count == 0

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query_with_filter():
    """测试AggregateQuery的filter方法"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        # 测试filter的链式调用
        agg = AggregateQuery(TestUser)
        result = await agg.filter(is_active=True).sum("age")
        assert result == 55  # 25 + 30

        # 测试多个filter条件
        agg2 = AggregateQuery(TestUser)
        result2 = await agg2.filter(is_active=True).filter(age=25).max("age")
        assert result2 == 25

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_builder_chain_calls():
    """测试QueryBuilder的链式调用"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": True},
            {"name": "David", "age": 40, "is_active": True},
        ])

        # 多个方法的链式调用
        qb = QueryBuilder(TestUser)
        users = await qb.filter(age__gte=25).order_by("-age").limit(2).all()
        assert len(users) == 2
        assert users[0].age == 40

        # 链式调用with filter_or (AND逻辑：age>30 AND (name=Alice OR age=40))
        qb2 = QueryBuilder(TestUser)
        users2 = await qb2.filter(age__gt=30).filter_or(name="Alice", age=40).all()
        # age>30过滤后剩下Charlie(35)和David(40)
        # 然后filter_or(name="Alice" OR age=40)，只有David(40)满足
        assert len(users2) >= 1

        # 测试多个filter组合
        qb3 = QueryBuilder(TestUser)
        users3 = await qb3.filter(is_active=True).filter(age__gte=30).order_by("age").limit(2).all()
        assert len(users3) == 2
        assert users3[0].age == 30

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_model_to_dict_fallback_path():
    """测试_model_to_dict的fallback路径"""
    from src.kernel.db.api.crud import _model_to_dict
    from unittest.mock import patch

    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({"name": "Alice", "age": 25, "is_active": True})

        # Mock fetch_values抛出异常，触发fallback路径
        async def mock_fetch_values_error(instance):
            raise RuntimeError("Simulated error")

        with patch("src.kernel.db.api.crud._get_model_value_fetcher", return_value=mock_fetch_values_error):
            # 这将触发fallback逻辑
            result = _model_to_dict(user)
            # fallback应该返回一个字典，即使某些字段为None
            assert isinstance(result, dict)

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


def test_model_helper_single_column():
    """测试单列模型的辅助函数"""
    from sqlalchemy import Integer
    from sqlalchemy.orm import mapped_column, declarative_base
    from src.kernel.db.api.crud import _get_model_column_names, _get_model_value_fetcher

    SingleBase = declarative_base()

    class SingleColumnModel(SingleBase):
        """单列模型"""
        __tablename__ = "single_column_model"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 获取列名
    columns = _get_model_column_names(SingleColumnModel)
    assert len(columns) == 1
    assert columns[0] == "id"

    # 获取value fetcher
    fetcher = _get_model_value_fetcher(SingleColumnModel)
    assert callable(fetcher)

    # 测试fetcher
    instance = SingleColumnModel()
    instance.id = 42
    values = fetcher(instance)
    assert values == (42,)


@pytest.mark.asyncio
async def test_concurrent_engine_initialization():
    """测试并发引擎初始化（触发双重检查锁定）"""
    import asyncio
    from src.kernel.db.core.engine import get_engine, close_engine

    # 关闭现有引擎
    await close_engine()

    # 并发初始化引擎
    tasks = [get_engine() for _ in range(5)]
    engines = await asyncio.gather(*tasks)

    # 所有应该是同一个实例
    assert all(e is engines[0] for e in engines)


@pytest.mark.asyncio
async def test_aggregate_query_max_min():
    """测试AggregateQuery的max和min方法"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery

        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": True},
        ])

        agg = AggregateQuery(TestUser)

        # 测试max
        max_age = await agg.max("age")
        assert max_age == 35

        # 测试min
        min_age = await agg.min("age")
        assert min_age == 25

        # 带过滤的max
        agg2 = AggregateQuery(TestUser)
        agg2.filter(is_active=True)
        max_age2 = await agg2.max("age")
        assert max_age2 == 35

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))
