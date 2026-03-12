"""高级查询 API

提供复杂的查询操作：
- MongoDB 风格的查询操作符
- 聚合查询
- 排序和分页
- 流式迭代（内存优化）
"""

from collections.abc import AsyncIterator
from typing import Any, Generic, TypeVar, Self

from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

# 导入 CRUD 辅助函数以避免重复定义
from src.kernel.db.api.crud import _dict_to_model, _get_session_ctx, _model_to_dict
from src.kernel.logger import get_logger

logger = get_logger("database.query", display="DB Query")

T = TypeVar("T", bound=Any)


class QueryBuilder(Generic[T]):
    """查询构建器

    支持链式调用，构建复杂查询
    """

    def __init__(self, model: type[T], *, session_factory: async_sessionmaker | None = None):
        """初始化查询构建器

        Args:
            model: SQLAlchemy 模型类
            session_factory: 可选的自定义异步会话工厂。为 None 时使用全局主数据库会话；
                传入自定义工厂时使用该工厂（适用于插件独立数据库等场景）。
        """
        self.model = model
        self.model_name = model.__tablename__
        self._stmt = select(model)
        self._session_factory = session_factory

    def filter(self, **conditions: Any) -> Self:
        """添加过滤条件

        支持的操作符：
        - 直接相等: field=value
        - 大于: field__gt=value
        - 小于: field__lt=value
        - 大于等于: field__gte=value
        - 小于等于: field__lte=value
        - 不等于: field__ne=value
        - 包含: field__in=[values]
        - 不包含: field__nin=[values]
        - 模糊匹配: field__like='%pattern%'
        - 为空: field__isnull=True

        Args:
            **conditions: 过滤条件

        Returns:
            self，支持链式调用
        """
        for key, value in conditions.items():
            # 解析字段和操作符
            if "__" in key:
                field_name, operator = key.rsplit("__", 1)
            else:
                field_name, operator = key, "eq"

            if not hasattr(self.model, field_name):
                logger.warning(f"模型 {self.model_name} 没有字段 {field_name}")
                continue

            field = getattr(self.model, field_name)

            # 应用操作符
            if operator == "eq":
                self._stmt = self._stmt.where(field == value)
            elif operator == "gt":
                self._stmt = self._stmt.where(field > value)
            elif operator == "lt":
                self._stmt = self._stmt.where(field < value)
            elif operator == "gte":
                self._stmt = self._stmt.where(field >= value)
            elif operator == "lte":
                self._stmt = self._stmt.where(field <= value)
            elif operator == "ne":
                self._stmt = self._stmt.where(field != value)
            elif operator == "in":
                self._stmt = self._stmt.where(field.in_(value))
            elif operator == "nin":
                self._stmt = self._stmt.where(~field.in_(value))
            elif operator == "like":
                self._stmt = self._stmt.where(field.like(value))
            elif operator == "isnull":
                if value:
                    self._stmt = self._stmt.where(field.is_(None))
                else:
                    self._stmt = self._stmt.where(field.isnot(None))
            else:
                logger.warning(f"未知操作符: {operator}")

        return self

    def filter_or(self, **conditions: Any) -> Self:
        """添加 OR 过滤条件

        Args:
            **conditions: OR 条件

        Returns:
            self，支持链式调用
        """
        or_conditions = []
        for key, value in conditions.items():
            if hasattr(self.model, key):
                field = getattr(self.model, key)
                or_conditions.append(field == value)

        if or_conditions:
            self._stmt = self._stmt.where(or_(*or_conditions))

        return self

    def order_by(self, *fields: str) -> Self:
        """添加排序

        Args:
            *fields: 排序字段，'-' 前缀表示降序

        Returns:
            self，支持链式调用
        """
        for field_name in fields:
            if field_name.startswith("-"):
                field_name = field_name[1:]
                if hasattr(self.model, field_name):
                    self._stmt = self._stmt.order_by(desc(getattr(self.model, field_name)))
            else:
                if hasattr(self.model, field_name):
                    self._stmt = self._stmt.order_by(asc(getattr(self.model, field_name)))

        return self

    def limit(self, limit: int) -> Self:
        """限制结果数量

        Args:
            limit: 最大数量

        Returns:
            self，支持链式调用
        """
        self._stmt = self._stmt.limit(limit)
        return self

    def offset(self, offset: int) -> Self:
        """跳过指定数量

        Args:
            offset: 跳过数量

        Returns:
            self，支持链式调用
        """
        self._stmt = self._stmt.offset(offset)
        return self

    async def iter_batches(
        self,
        batch_size: int = 1000,
        *,
        as_dict: bool = True,
    ) -> AsyncIterator[list[T] | list[dict[str, Any]]]:
        """分批迭代获取结果（内存优化）

        使用 LIMIT/OFFSET 分页策略，避免一次性加载全部数据到内存。
        适用于大数据量的统计、导出等场景。

        Args:
            batch_size: 每批获取的记录数，默认 1000
            as_dict: 为 True 时返回字典格式

        Yields:
            每批的模型实例列表或字典列表

        Example:
            async for batch in query_builder.iter_batches(batch_size=500):
                for record in batch:
                    process(record)
        """
        offset = 0

        while True:
            # 构建带分页的查询
            paginated_stmt = self._stmt.offset(offset).limit(batch_size)

            async with _get_session_ctx(self._session_factory) as session:
                result = await session.execute(paginated_stmt)
                instances = result.scalars().all()

                if not instances:
                    # 没有更多数据
                    break

                # 在 session 内部转换为字典列表，保证字段可用再释放连接
                instances_dicts = [_model_to_dict(inst) for inst in instances]

            if as_dict:
                yield instances_dicts
            else:
                yield [_dict_to_model(self.model, row) for row in instances_dicts]

            # 如果返回的记录数小于 batch_size，说明已经是最后一批
            if len(instances) < batch_size:
                break

            offset += batch_size

    async def iter_all(
        self,
        batch_size: int = 1000,
        *,
        as_dict: bool = True,
    ) -> AsyncIterator[T | dict[str, Any]]:
        """逐条迭代所有结果（内存优化）

        内部使用分批获取，但对外提供逐条迭代的接口。
        适用于需要逐条处理但数据量很大的场景。

        Args:
            batch_size: 内部分批大小，默认 1000
            as_dict: 为 True 时返回字典格式

        Yields:
            单个模型实例或字典

        Example:
            async for record in query_builder.iter_all():
                process(record)
        """
        async for batch in self.iter_batches(batch_size=batch_size, as_dict=as_dict):
            for item in batch:
                yield item

    async def all(self, *, as_dict: bool = False) -> list[T] | list[dict[str, Any]]:
        """获取所有结果

        Args:
            as_dict: 为 True 时返回字典格式

        Returns:
            模型实例列表或字典列表
        """
        async with _get_session_ctx(self._session_factory) as session:
            result = await session.execute(self._stmt)
            instances = list(result.scalars().all())

            # 在 session 内部转换为字典列表，此时所有字段都可安全访问
            instances_dicts = [_model_to_dict(inst) for inst in instances]

            if as_dict:
                return instances_dicts
            return [_dict_to_model(self.model, row) for row in instances_dicts]

    async def first(self, *, as_dict: bool = False) -> T | dict[str, Any] | None:
        """获取第一条结果

        Args:
            as_dict: 为 True 时返回字典格式

        Returns:
            模型实例、字典或 None
        """
        async with _get_session_ctx(self._session_factory) as session:
            result = await session.execute(self._stmt)
            instance = result.scalars().first()

            if instance is not None:
                # 在 session 内部转换为字典，此时所有字段都可安全访问
                instance_dict = _model_to_dict(instance)

                if as_dict:
                    return instance_dict
                return _dict_to_model(self.model, instance_dict)

            return None

    async def count(self) -> int:
        """统计数量

        Returns:
            记录数量
        """
        count_stmt = select(func.count()).select_from(self._stmt.subquery())

        async with _get_session_ctx(self._session_factory) as session:
            result = await session.execute(count_stmt)
            return result.scalar() or 0

    async def exists(self) -> bool:
        """检查是否存在

        Returns:
            是否存在记录
        """
        count = await self.count()
        return count > 0

    async def paginate(
        self,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[T], int]:
        """分页查询

        Args:
            page: 页码（从 1 开始）
            page_size: 每页数量

        Returns:
            (结果列表, 总数量)
        """
        # 计算偏移量
        offset = (page - 1) * page_size

        # 获取总数
        total = await self.count()

        # 获取当前页数据
        paginated_stmt = self._stmt.offset(offset).limit(page_size)

        async with _get_session_ctx(self._session_factory) as session:
            result = await session.execute(paginated_stmt)
            instances = list(result.scalars().all())

            # 在 session 内部转换为字典列表
            instances_dicts = [_model_to_dict(inst) for inst in instances]
            items = [_dict_to_model(self.model, row) for row in instances_dicts]

        return items, total  # type: ignore


class AggregateQuery:
    """聚合查询

    提供聚合操作如 sum、avg、max、min 等
    """

    def __init__(self, model: type[T], *, session_factory: async_sessionmaker | None = None):
        """初始化聚合查询

        Args:
            model: SQLAlchemy 模型类
            session_factory: 可选的自定义异步会话工厂。为 None 时使用全局主数据库会话；
                传入自定义工厂时使用该工厂（适用于插件独立数据库等场景）。
        """
        self.model = model
        self.model_name = model.__tablename__
        self._conditions = []
        self._session_factory = session_factory

    def filter(self, **conditions: Any) -> Self:
        """添加过滤条件

        Args:
            **conditions: 过滤条件

        Returns:
            self，支持链式调用
        """
        for key, value in conditions.items():
            if hasattr(self.model, key):
                field = getattr(self.model, key)
                self._conditions.append(field == value)
        return self

    async def sum(self, field: str) -> float:
        """求和

        Args:
            field: 字段名

        Returns:
            总和
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with _get_session_ctx(self._session_factory) as session:
            stmt = select(func.sum(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar() or 0

    async def avg(self, field: str) -> float:
        """求平均值

        Args:
            field: 字段名

        Returns:
            平均值
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with _get_session_ctx(self._session_factory) as session:
            stmt = select(func.avg(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar() or 0

    async def max(self, field: str) -> Any:
        """求最大值

        Args:
            field: 字段名

        Returns:
            最大值
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with _get_session_ctx(self._session_factory) as session:
            stmt = select(func.max(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar()

    async def min(self, field: str) -> Any:
        """求最小值

        Args:
            field: 字段名

        Returns:
            最小值
        """
        if not hasattr(self.model, field):
            raise ValueError(f"字段 {field} 不存在")

        async with _get_session_ctx(self._session_factory) as session:
            stmt = select(func.min(getattr(self.model, field)))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            result = await session.execute(stmt)
            return result.scalar()

    async def group_by_count(
        self,
        *fields: str,
    ) -> list[tuple[Any, ...]]:
        """分组统计

        Args:
            *fields: 分组字段

        Returns:
            [(分组值1, 分组值2, ..., 数量), ...] 
        """
        if not fields:
            raise ValueError("至少需要一个分组字段")

        group_columns = [
            getattr(self.model, field_name)
            for field_name in fields
            if hasattr(self.model, field_name)
        ]

        if not group_columns:
            return []

        async with _get_session_ctx(self._session_factory) as session:
            stmt = select(*group_columns, func.count(self.model.id))

            if self._conditions:
                stmt = stmt.where(and_(*self._conditions))

            stmt = stmt.group_by(*group_columns)

            result = await session.execute(stmt)
            return [tuple(row) for row in result.all()]
