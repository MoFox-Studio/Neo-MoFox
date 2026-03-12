"""扁平化数据库 API

为插件提供简洁的数据库操作接口，将 kernel.db 的类方法扁平化为独立函数。
"""
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from src.kernel.db import CRUDBase, QueryBuilder, AggregateQuery

T = TypeVar("T", bound=Any)


# =============================================================================
# CRUD 基础操作
# =============================================================================

async def get_by_id(model: type[T], id: int) -> T | None:
    """根据 ID 获取单个记录

    Args:
        model: SQLAlchemy 模型类
        id: 记录 ID

    Returns:
        模型实例或 None
    """
    return await CRUDBase(model).get(id=id)


async def get_by(model: type[T], **filters: Any) -> T | None:
    """根据条件获取单条记录

    Args:
        model: SQLAlchemy 模型类
        **filters: 过滤条件

    Returns:
        模型实例或 None
    """
    return await CRUDBase(model).get_by(**filters)


async def get_multi(
    model: type[T],
    skip: int = 0,
    limit: int = 100,
    **filters: Any,
) -> list[T]:
    """获取多条记录

    Args:
        model: SQLAlchemy 模型类
        skip: 跳过的记录数
        limit: 返回的最大记录数
        **filters: 过滤条件

    Returns:
        模型实例列表
    """
    return await CRUDBase(model).get_multi(skip=skip, limit=limit, **filters)


async def create(model: type[T], obj_in: dict[str, Any]) -> T:
    """创建新记录

    Args:
        model: SQLAlchemy 模型类
        obj_in: 要创建的数据

    Returns:
        已创建的模型实例
    """
    return await CRUDBase(model).create(obj_in)


async def update(model: type[T], id: int, obj_in: dict[str, Any]) -> T | None:
    """更新记录

    Args:
        model: SQLAlchemy 模型类
        id: 记录 ID
        obj_in: 更新数据

    Returns:
        更新后的模型实例或 None
    """
    return await CRUDBase(model).update(id=id, obj_in=obj_in)


async def delete(model: type[T], id: int) -> bool:
    """删除记录

    Args:
        model: SQLAlchemy 模型类
        id: 记录 ID

    Returns:
        是否删除成功
    """
    return await CRUDBase(model).delete(id=id)


async def count(model: type[T], **filters: Any) -> int:
    """统计记录数

    Args:
        model: SQLAlchemy 模型类
        **filters: 过滤条件

    Returns:
        记录数量
    """
    return await CRUDBase(model).count(**filters)


async def exists(model: type[T], **filters: Any) -> bool:
    """检查记录是否存在

    Args:
        model: SQLAlchemy 模型类
        **filters: 过滤条件

    Returns:
        是否存在记录
    """
    return await CRUDBase(model).exists(**filters)


async def get_or_create(
    model: type[T],
    defaults: dict[str, Any] | None = None,
    **filters: Any,
) -> tuple[T, bool]:
    """获取或创建记录

    Args:
        model: SQLAlchemy 模型类
        defaults: 创建时的默认值
        **filters: 搜索条件

    Returns:
        (实例, 是否为新创建)
    """
    return await CRUDBase(model).get_or_create(defaults=defaults, **filters)


async def bulk_create(model: type[T], objs_in: list[dict[str, Any]]) -> list[T]:
    """批量创建记录

    Args:
        model: SQLAlchemy 模型类
        objs_in: 要创建的数据列表

    Returns:
        已创建的模型实例列表
    """
    return await CRUDBase(model).bulk_create(objs_in)


async def bulk_update(
    model: type[T],
    updates: list[tuple[int, dict[str, Any]]],
) -> int:
    """批量更新记录

    Args:
        model: SQLAlchemy 模型类
        updates: (id, update_data) 元组列表

    Returns:
        更新的记录数量
    """
    return await CRUDBase(model).bulk_update(updates=updates)


# =============================================================================
# 查询构建器操作
# =============================================================================


def query(model: type[T]) -> QueryBuilder[T]:
    """创建查询构建器

    Args:
        model: SQLAlchemy 模型类

    Returns:
        QueryBuilder 实例

    Example:
        result = await query(MyModel).filter(field="value").first()
    """
    return QueryBuilder(model)


async def filter_query(model: type[T], **conditions: Any) -> list[T]:
    """快速过滤查询

    Args:
        model: SQLAlchemy 模型类
        **conditions: 过滤条件，支持操作符 (field__gt=5, field__in=[1,2])

    Returns:
        模型实例列表
    """
    return await QueryBuilder(model).filter(**conditions).all(as_dict=False)  # type: ignore[return-value]


async def filter_query_first(model: type[T], **conditions: Any) -> T | None:
    """快速过滤查询获取第一条

    Args:
        model: SQLAlchemy 模型类
        **conditions: 过滤条件

    Returns:
        模型实例或 None
    """
    return await QueryBuilder(model).filter(**conditions).first(as_dict=False)  # type: ignore[return-value]


async def filter_query_count(model: type[T], **conditions: Any) -> int:
    """快速过滤查询统计数量

    Args:
        model: SQLAlchemy 模型类
        **conditions: 过滤条件

    Returns:
        记录数量
    """
    return await QueryBuilder(model).filter(**conditions).count()


# =============================================================================
# 聚合查询操作
# =============================================================================


def aggregate(model: type[T]) -> AggregateQuery:
    """创建聚合查询

    Args:
        model: SQLAlchemy 模型类

    Returns:
        AggregateQuery 实例

    Example:
        total = await aggregate(MyModel).filter(status="active").sum("amount")
    """
    return AggregateQuery(model)


async def sum_field(model: type[T], field: str, **filters: Any) -> float:
    """对字段求和

    Args:
        model: SQLAlchemy 模型类
        field: 字段名
        **filters: 过滤条件

    Returns:
        总和
    """
    return await AggregateQuery(model).filter(**filters).sum(field)


async def avg_field(model: type[T], field: str, **filters: Any) -> float:
    """对字段求平均值

    Args:
        model: SQLAlchemy 模型类
        field: 字段名
        **filters: 过滤条件

    Returns:
        平均值
    """
    return await AggregateQuery(model).filter(**filters).avg(field)


async def max_field(model: type[T], field: str, **filters: Any) -> Any:
    """获取字段最大值

    Args:
        model: SQLAlchemy 模型类
        field: 字段名
        **filters: 过滤条件

    Returns:
        最大值
    """
    return await AggregateQuery(model).filter(**filters).max(field)


async def min_field(model: type[T], field: str, **filters: Any) -> Any:
    """获取字段最小值

    Args:
        model: SQLAlchemy 模型类
        field: 字段名
        **filters: 过滤条件

    Returns:
        最小值
    """
    return await AggregateQuery(model).filter(**filters).min(field)


async def group_by_count(model: type[T], *fields: str, **filters: Any) -> list[tuple[Any, ...]]:
    """分组统计

    Args:
        model: SQLAlchemy 模型类
        *fields: 分组字段
        **filters: 过滤条件

    Returns:
        [(分组值1, 分组值2, ..., 数量), ...]
    """
    agg = AggregateQuery(model)
    if filters:
        agg = agg.filter(**filters)
    return await agg.group_by_count(*fields)


# =============================================================================
# 迭代器操作
# =============================================================================


async def iter_batches(
    model: type[T],
    batch_size: int = 1000,
    **conditions: Any,
) -> AsyncIterator[list[T]]:
    """分批迭代获取结果（内存优化）

    Args:
        model: SQLAlchemy 模型类
        batch_size: 每批获取的记录数
        **conditions: 过滤条件

    Yields:
        每批的模型实例列表

    Example:
        async for batch in iter_batches(MyModel, batch_size=500, status="active"):
            for record in batch:
                process(record)
    """
    builder = QueryBuilder(model)
    if conditions:
        builder = builder.filter(**conditions)

    async for batch in builder.iter_batches(batch_size=batch_size, as_dict=False):  # type: ignore[arg-type]
        yield batch  # type: ignore[misc]


async def iter_all(
    model: type[T],
    batch_size: int = 1000,
    **conditions: Any,
) -> AsyncIterator[T]:
    """逐条迭代所有结果（内存优化）

    Args:
        model: SQLAlchemy 模型类
        batch_size: 内部分批大小
        **conditions: 过滤条件

    Yields:
        单个模型实例

    Example:
        async for record in iter_all(MyModel, status="active"):
            process(record)
    """
    builder = QueryBuilder(model)
    if conditions:
        builder = builder.filter(**conditions)

    async for record in builder.iter_all(batch_size=batch_size, as_dict=False):  # type: ignore[arg-type]
        yield record  # type: ignore[misc]


# =============================================================================
# 分页操作
# =============================================================================


async def paginate(
    model: type[T],
    page: int = 1,
    page_size: int = 20,
    **conditions: Any,
) -> tuple[list[T], int]:
    """分页查询

    Args:
        model: SQLAlchemy 模型类
        page: 页码（从 1 开始）
        page_size: 每页数量
        **conditions: 过滤条件

    Returns:
        (结果列表, 总数量)
    """
    builder = QueryBuilder(model)
    if conditions:
        builder = builder.filter(**conditions)

    return await builder.paginate(page=page, page_size=page_size)


