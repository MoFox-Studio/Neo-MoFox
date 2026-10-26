"""
数据库模块使用示例

演示如何使用 kernel.db 模块进行数据库操作。
"""
import sys
import asyncio
from pathlib import Path

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, declarative_base

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.kernel.db import (
    AggregateQuery,
    CRUDBase,
    QueryBuilder,
    close_engine,
    configure_engine,
    get_engine,
)
from src.kernel.logger import get_logger, COLOR

# 创建全局 logger
logger = get_logger("db_example", display="DB", color=COLOR.GREEN)


def configure_db() -> None:
    """示例：由高层调用方配置数据库连接参数。

    kernel/db 不读取用户配置；这里直接注入 SQLite 参数。
    """
    db_file = Path("data") / "mofox_example.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Windows 绝对路径需要转成 posix 风格以适配 SQLAlchemy URL
    url = f"sqlite+aiosqlite:///{db_file.absolute().as_posix()}"
    engine_kwargs = {
        "echo": False,
        "future": True,
        "connect_args": {"check_same_thread": False, "timeout": 60},
    }
    configure_engine(url, engine_kwargs=engine_kwargs, db_type="sqlite")

# 定义基类和模型
Base = declarative_base()


class User(Base):
    """用户模型"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


async def init_database():
    """初始化数据库，创建表"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn))
    logger.info("[OK] 数据库初始化完成")


async def cleanup_database():
    """清理数据库，删除表"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn))
    logger.info("[OK] 数据库清理完成")


async def demo_crud_operations():
    """演示 CRUD 基础操作"""
    logger.info("\n=== CRUD 基础操作 ===")
    crud = CRUDBase(User)

    # 创建记录
    logger.info("\n1. 创建用户")
    user = await crud.create({
        "name": "张三",
        "age": 25,
        "email": "zhangsan@example.com",
        "is_active": True
    })
    logger.info(f"   创建用户: {user.name} (ID: {user.id})")

    # 根据 ID 获取
    logger.info("\n2. 根据 ID 获取用户")
    found_user = await crud.get(user.id)
    logger.info(f"   找到用户: {found_user.name}")

    # 根据条件获取
    logger.info("\n3. 根据条件获取用户")
    user_by_name = await crud.get_by(name="张三")
    logger.info(f"   找到用户: {user_by_name.name}, 年龄: {user_by_name.age}")

    # 更新记录
    logger.info("\n4. 更新用户信息")
    updated_user = await crud.update(user.id, {"age": 26, "email": "newemail@example.com"})
    logger.info(f"   更新后年龄: {updated_user.age}")

    # 统计记录数
    logger.info("\n5. 统计记录数")
    count = await crud.count()
    logger.info(f"   当前用户数: {count}")

    # 检查是否存在
    logger.info("\n6. 检查用户是否存在")
    exists = await crud.exists(name="张三")
    logger.info(f"   张三存在: {exists}")

    # get_or_create
    logger.info("\n7. 获取或创建用户")
    user1, created1 = await crud.get_or_create(
        defaults={"age": 30, "email": "lisi@example.com"},
        name="李四"
    )
    logger.info(f"   {'创建新用户' if created1 else '获取已存在用户'}: {user1.name}")

    user2, created2 = await crud.get_or_create(
        defaults={"age": 35, "email": "lisi2@example.com"},
        name="李四"
    )
    logger.info(f"   {'创建新用户' if created2 else '获取已存在用户'}: {user2.name} (ID: {user2.id})")


async def demo_query_builder():
    """演示 QueryBuilder 高级查询"""
    logger.info("\n=== QueryBuilder 高级查询 ===")

    # 先插入一些测试数据
    crud = CRUDBase(User)
    await crud.bulk_create([
        {"name": "Alice", "age": 25, "email": "alice@example.com", "is_active": True},
        {"name": "Bob", "age": 30, "email": "bob@example.com", "is_active": True},
        {"name": "Charlie", "age": 35, "email": "charlie@example.com", "is_active": False},
        {"name": "David", "age": 28, "email": None, "is_active": True},
    ])

    # 基础过滤
    logger.info("\n1. 基础过滤 - 查找活跃用户")
    qb = QueryBuilder(User)
    active_users = await qb.filter(is_active=True).all()
    logger.info(f"   活跃用户: {[u.name for u in active_users]}")

    # 比较操作符
    logger.info("\n2. 比较操作符 - 年龄大于 25 的用户")
    qb = QueryBuilder(User)
    users = await qb.filter(age__gt=25).all()
    logger.info(f"   用户: {[u.name for u in users]}")

    logger.info("\n3. IN 操作 - 年龄为 25 或 35 的用户")
    qb = QueryBuilder(User)
    users = await qb.filter(age__in=[25, 35]).all()
    logger.info(f"   用户: {[u.name for u in users]}")

    logger.info("\n4. LIKE 操作 - 名字包含 'a' 的用户")
    qb = QueryBuilder(User)
    users = await qb.filter(name__like="%a%").all()
    logger.info(f"   用户: {[u.name for u in users]}")

    logger.info("\n5. ISNULL 操作 - 邮箱为空的用户")
    qb = QueryBuilder(User)
    users = await qb.filter(email__isnull=True).all()
    logger.info(f"   用户: {[u.name for u in users]}")

    # OR 条件
    logger.info("\n6. OR 条件 - 名字是 Alice 或年龄 35 的用户")
    qb = QueryBuilder(User)
    users = await qb.filter_or(name="Alice", age=35).all()
    logger.info(f"   用户: {[u.name for u in users]}")

    # 排序
    logger.info("\n7. 排序 - 按年龄降序")
    qb = QueryBuilder(User)
    users = await qb.order_by("-age").all()
    logger.info(f"   用户顺序: {[u.name for u in users]}")

    # 分页
    logger.info("\n8. 分页 - 第 1 页，每页 2 条")
    qb = QueryBuilder(User)
    items, total = await qb.paginate(page=1, page_size=2)
    logger.info(f"   总数: {total}, 当前页: {[u.name for u in items]}")

    # first
    logger.info("\n9. 获取第一条记录")
    qb = QueryBuilder(User)
    user = await qb.filter(name="Alice").first()
    logger.info(f"   找到用户: {user.name if user else 'None'}")


async def demo_aggregate_query():
    """演示聚合查询"""
    logger.info("\n=== 聚合查询 ===")

    agg = AggregateQuery(User)

    # 求和
    logger.info("\n1. 年龄总和")
    total_age = await agg.sum("age")
    logger.info(f"   总年龄: {total_age}")

    # 平均值
    logger.info("\n2. 平均年龄")
    avg_age = await agg.avg("age")
    logger.info(f"   平均年龄: {avg_age:.1f}")

    # 最大值
    logger.info("\n3. 最大年龄")
    max_age = await agg.max("age")
    logger.info(f"   最大年龄: {max_age}")

    # 最小值
    logger.info("\n4. 最小年龄")
    min_age = await agg.min("age")
    logger.info(f"   最小年龄: {min_age}")

    # 带过滤的聚合
    logger.info("\n5. 活跃用户的平均年龄")
    agg2 = AggregateQuery(User)
    agg2.filter(is_active=True)
    avg_active_age = await agg2.avg("age")
    logger.info(f"   活跃用户平均年龄: {avg_active_age:.1f}")


async def demo_bulk_operations():
    """演示批量操作"""
    logger.info("\n=== 批量操作 ===")
    crud = CRUDBase(User)

    # 批量创建
    logger.info("\n1. 批量创建用户")
    new_users = await crud.bulk_create([
        {"name": "用户1", "age": 20, "is_active": True},
        {"name": "用户2", "age": 22, "is_active": True},
        {"name": "用户3", "age": 24, "is_active": True},
    ])
    logger.info(f"   创建了 {len(new_users)} 个用户")

    # 批量更新
    logger.info("\n2. 批量更新用户年龄")
    updates = [
        (new_users[0].id, {"age": 21}),
        (new_users[1].id, {"age": 23}),
    ]
    count = await crud.bulk_update(updates)
    logger.info(f"   更新了 {count} 个用户")

    # 获取多条记录（带过滤和分页）
    logger.info("\n3. 获取多条记录")
    users = await crud.get_multi(skip=0, limit=5, is_active=True)
    logger.info(f"   获取到 {len(users)} 个活跃用户")


async def demo_stream_iteration():
    """演示流式迭代（大数据量场景）"""
    logger.info("\n=== 流式迭代（大数据量优化） ===")

    # 先插入更多数据
    crud = CRUDBase(User)
    test_users = [
        {"name": f"测试用户{i}", "age": 20 + (i % 10), "is_active": True}
        for i in range(20)
    ]
    await crud.bulk_create(test_users)
    logger.info(f"   插入了 {len(test_users)} 个测试用户")

    # 分批迭代
    logger.info("\n1. 分批迭代 - 每批 5 条")
    batch_num = 0
    qb = QueryBuilder(User)
    async for batch in qb.iter_batches(batch_size=5):
        batch_num += 1
        logger.info(f"   批次 {batch_num}: {len(batch)} 条记录")

    # 逐条迭代
    logger.info("\n2. 逐条迭代 - 统计年龄大于 25 的用户")
    count = 0
    qb = QueryBuilder(User)
    async for user in qb.iter_all(as_dict=False):
        if user.age > 25:
            count += 1
    logger.info(f"   找到 {count} 个年龄大于 25 的用户")


async def main():
    """主函数"""
    logger.info("=" * 50)
    logger.info("Neo-MoFox 数据库模块使用示例")
    logger.info("=" * 50)

    try:
        # 由高层注入数据库配置
        configure_db()

        # 初始化数据库
        await init_database()

        # 运行各种示例
        await demo_crud_operations()
        await demo_query_builder()
        await demo_aggregate_query()
        await demo_bulk_operations()
        await demo_stream_iteration()

        logger.info("\n" + "=" * 50)
        logger.info("示例运行完成！")
        logger.info("=" * 50)

    finally:
        # 清理数据库
        await cleanup_database()
        # 关闭引擎
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
