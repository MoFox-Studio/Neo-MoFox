"""基础 CRUD API

提供通用的数据库 CRUD 操作。
遵循最简原则，不包含缓存和批处理等复杂功能。
"""

import operator
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.kernel.db.core.cache import (
    LIST_RESULT_CACHE_LIMIT,
    get_cache_namespace,
    get_db_read_cache,
    invalidate_model_cache,
)
from src.kernel.logger import get_logger

from ..telemetry import record_db_operation_event
from ..core.session import get_db_session

logger = get_logger("database.crud", display="CRUD")

T = TypeVar("T", bound=Any)


@asynccontextmanager
async def _get_session_ctx(
    session_factory: async_sessionmaker | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """通用会话上下文管理器。

    当 session_factory 为 None 时使用全局默认会话（主数据库），
    否则使用传入的 session_factory（用于独立数据库）。

    Args:
        session_factory: 指定的异步会话工厂；为 None 时使用全局默认会话。

    Yields:
        AsyncSession 实例（自动处理提交/回滚）。
    """
    if session_factory is not None:
        async with session_factory() as session:
            try:
                yield session
                if session.is_active:
                    await session.commit()
            except Exception:
                if session.is_active:
                    await session.rollback()
                raise
            finally:
                await session.close()
    else:
        async with get_db_session() as session:
            yield session


def _normalize_cache_value(value: Any) -> Any:
    """将动态参数归一化为稳定可哈希结构。"""
    if isinstance(value, dict):
        return tuple(sorted((str(k), _normalize_cache_value(v)) for k, v in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_normalize_cache_value(item) for item in value)
    if isinstance(value, set | frozenset):
        normalized = [_normalize_cache_value(item) for item in value]
        return tuple(sorted(normalized, key=repr))
    return value


def _build_crud_cache_key(operation: str, **parts: Any) -> tuple[Any, ...]:
    return (operation,) + tuple(
        sorted((key, _normalize_cache_value(value)) for key, value in parts.items())
    )


@lru_cache(maxsize=256)
def _get_model_column_names(model: type[Any]) -> tuple[str, ...]:
    """获取模型的列名"""
    return tuple(column.name for column in model.__table__.columns)


@lru_cache(maxsize=256)
def _get_model_field_set(model: type[Any]) -> frozenset[str]:
    """获取模型的有效字段集合"""
    return frozenset(_get_model_column_names(model))


@lru_cache(maxsize=256)
def _get_model_value_fetcher(model: type[Any]) -> Callable[[Any], tuple[Any, ...]]:
    """为模型准备 attrgetter 以批量获取属性值"""
    column_names = _get_model_column_names(model)

    if not column_names:
        return lambda _: ()

    if len(column_names) == 1:
        attr_name = column_names[0]

        def _single(instance: Any) -> tuple[Any, ...]:
            return (getattr(instance, attr_name),)

        return _single

    getter = operator.attrgetter(*column_names)

    def _multi(instance: Any) -> tuple[Any, ...]:
        values = getter(instance)
        return values if isinstance(values, tuple) else (values,)

    return _multi


def _model_to_dict(instance: Any) -> dict[str, Any]:
    """将 SQLAlchemy 模型实例转换为字典

    Args:
        instance: SQLAlchemy 模型实例

    Returns:
        模型实例字段值的字典表示
    """
    if instance is None:
        return {}

    model = type(instance)
    column_names = _get_model_column_names(model)
    fetch_values = _get_model_value_fetcher(model)

    try:
        values = fetch_values(instance)
        return dict(zip(column_names, values))
    except Exception as exc:
        logger.warning(f"无法转换模型 {model.__name__}: {exc}")
        fallback = {}
        for column in column_names:
            try:
                fallback[column] = getattr(instance, column)
            except Exception:
                fallback[column] = None
        return fallback


def _dict_to_model(model_class: type[T], data: dict[str, Any]) -> T:
    """从字典创建 SQLAlchemy 模型实例（分离状态）

    Args:
        model_class: SQLAlchemy 模型类
        data: 字典数据

    Returns:
        模型实例（分离状态，所有字段已加载）
    """
    instance = model_class()
    valid_fields = _get_model_field_set(model_class)
    for key, value in data.items():
        if key in valid_fields:
            setattr(instance, key, value)
    return instance


async def _record_crud_operation(
    *,
    operation: str,
    model_name: str,
    started_at: float,
    session_factory: async_sessionmaker | None,
    success: bool,
    result_count: int | None = None,
    error: Exception | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """记录 CRUD 操作遥测。"""
    await record_db_operation_event(
        operation=operation,
        model_name=model_name,
        duration_ms=(time.perf_counter() - started_at) * 1000,
        custom_session_factory=session_factory is not None,
        success=success,
        result_count=result_count,
        error=error,
        attributes=attributes,
    )


class CRUDBase(Generic[T]):
    """基础 CRUD 操作类

    提供通用的创建、读取、更新、删除操作
    """

    def __init__(self, model: type[T], *, session_factory: async_sessionmaker | None = None):
        """初始化 CRUD 操作

        Args:
            model: SQLAlchemy 模型类
            session_factory: 可选的自定义异步会话工厂。为 None 时使用全局主数据库会话；
                传入自定义工厂时使用该工厂（适用于插件独立数据库等场景）。
        """
        self.model = model
        self.model_name = model.__tablename__
        self._session_factory = session_factory
        self._cache = get_db_read_cache()
        self._cache_namespace = get_cache_namespace(
            model,
            session_factory=session_factory,
        )

    async def get(self, id: int) -> T | None:
        """根据 ID 获取单条记录

        Args:
            id: 记录 ID

        Returns:
            模型实例或 None
        """
        started_at = time.perf_counter()
        cache_key = _build_crud_cache_key("get", id=id)
        hit, cached = self._cache.get(self._cache_namespace, cache_key)
        if hit:
            return _dict_to_model(self.model, cached) if cached is not None else None
        try:
            async with _get_session_ctx(self._session_factory) as session:
                stmt = select(self.model).where(self.model.id == id)
                result = await session.execute(stmt)
                instance = result.scalar_one_or_none()

                if instance is not None:
                    instance_dict = _model_to_dict(instance)
                    typed_instance = _dict_to_model(self.model, instance_dict)
                    await _record_crud_operation(
                        operation="get",
                        model_name=self.model_name,
                        started_at=started_at,
                        session_factory=self._session_factory,
                        success=True,
                        result_count=1,
                    )
                    self._cache.set(self._cache_namespace, cache_key, instance_dict)
                    return typed_instance

                await _record_crud_operation(
                    operation="get",
                    model_name=self.model_name,
                    started_at=started_at,
                    session_factory=self._session_factory,
                    success=True,
                    result_count=0,
                )
                return None
        except Exception as exc:
            await _record_crud_operation(
                operation="get",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
            )
            raise

    async def get_by(self, **filters: Any) -> T | None:
        """根据条件获取单条记录

        Args:
            **filters: 过滤条件

        Returns:
            模型实例或 None
        """
        started_at = time.perf_counter()
        cache_key = _build_crud_cache_key("get_by", filters=filters)
        hit, cached = self._cache.get(self._cache_namespace, cache_key)
        if hit:
            return _dict_to_model(self.model, cached) if cached is not None else None
        try:
            async with _get_session_ctx(self._session_factory) as session:
                stmt = select(self.model)
                for key, value in filters.items():
                    if hasattr(self.model, key):
                        stmt = stmt.where(getattr(self.model, key) == value)

                result = await session.execute(stmt)
                instance = result.scalars().first()

                if instance is not None:
                    instance_dict = _model_to_dict(instance)
                    typed_instance = _dict_to_model(self.model, instance_dict)
                    await _record_crud_operation(
                        operation="get_by",
                        model_name=self.model_name,
                        started_at=started_at,
                        session_factory=self._session_factory,
                        success=True,
                        result_count=1,
                        attributes={"filter_count": len(filters)},
                    )
                    self._cache.set(self._cache_namespace, cache_key, instance_dict)
                    return typed_instance

                await _record_crud_operation(
                    operation="get_by",
                    model_name=self.model_name,
                    started_at=started_at,
                    session_factory=self._session_factory,
                    success=True,
                    result_count=0,
                    attributes={"filter_count": len(filters)},
                )
                return None
        except Exception as exc:
            await _record_crud_operation(
                operation="get_by",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
                attributes={"filter_count": len(filters)},
            )
            raise

    async def get_multi(
        self,
        skip: int = 0,
        limit: int = 100,
        **filters: Any,
    ) -> list[T]:
        """获取多条记录

        Args:
            skip: 跳过的记录数
            limit: 返回的最大记录数
            **filters: 过滤条件

        Returns:
            模型实例列表
        """
        started_at = time.perf_counter()
        cache_key = _build_crud_cache_key(
            "get_multi",
            skip=skip,
            limit=limit,
            filters=filters,
        )
        hit, cached = self._cache.get(self._cache_namespace, cache_key)
        if hit:
            return [_dict_to_model(self.model, row) for row in (cached or [])]  # type: ignore[return-value]
        try:
            async with _get_session_ctx(self._session_factory) as session:
                stmt = select(self.model)

                for key, value in filters.items():
                    if hasattr(self.model, key):
                        if isinstance(value, list | tuple | set):
                            stmt = stmt.where(getattr(self.model, key).in_(value))
                        else:
                            stmt = stmt.where(getattr(self.model, key) == value)

                stmt = stmt.offset(skip).limit(limit)

                result = await session.execute(stmt)
                instances = list(result.scalars().all())

                instances_dicts = [_model_to_dict(inst) for inst in instances]
                typed_instances = [_dict_to_model(self.model, d) for d in instances_dicts]
                await _record_crud_operation(
                    operation="get_multi",
                    model_name=self.model_name,
                    started_at=started_at,
                    session_factory=self._session_factory,
                    success=True,
                    result_count=len(typed_instances),
                    attributes={"filter_count": len(filters), "limit": limit, "skip": skip},
                )
                if len(instances_dicts) <= LIST_RESULT_CACHE_LIMIT:
                    self._cache.set(self._cache_namespace, cache_key, instances_dicts)
                return typed_instances  # type: ignore
        except Exception as exc:
            await _record_crud_operation(
                operation="get_multi",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
                attributes={"filter_count": len(filters), "limit": limit, "skip": skip},
            )
            raise

    async def create(self, obj_in: dict[str, Any]) -> T:
        """创建新记录

        Args:
            obj_in: 要创建的数据

        Returns:
            已创建的模型实例
        """
        started_at = time.perf_counter()
        try:
            typed_instance: T | None = None
            async with _get_session_ctx(self._session_factory) as session:
                instance = self.model(**obj_in)
                session.add(instance)
                await session.flush()
                await session.refresh(instance)

                instance_dict = _model_to_dict(instance)
                typed_instance = _dict_to_model(self.model, instance_dict)
                await _record_crud_operation(
                    operation="create",
                    model_name=self.model_name,
                    started_at=started_at,
                    session_factory=self._session_factory,
                    success=True,
                    result_count=1,
                    attributes={"field_count": len(obj_in)},
                )
            invalidate_model_cache(self.model, session_factory=self._session_factory)
            assert typed_instance is not None
            return typed_instance
        except Exception as exc:
            await _record_crud_operation(
                operation="create",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
                attributes={"field_count": len(obj_in)},
            )
            raise

    async def update(self, id: int, obj_in: dict[str, Any]) -> T | None:
        """更新记录

        Args:
            id: 记录 ID
            obj_in: 更新数据

        Returns:
            更新后的模型实例或 None
        """
        started_at = time.perf_counter()
        try:
            typed_instance: T | None = None
            async with _get_session_ctx(self._session_factory) as session:
                stmt = select(self.model).where(self.model.id == id)
                result = await session.execute(stmt)
                db_instance = result.scalar_one_or_none()

                if db_instance:
                    for key, value in obj_in.items():
                        if hasattr(db_instance, key):
                            setattr(db_instance, key, value)
                    await session.flush()
                    await session.refresh(db_instance)

                    instance_dict = _model_to_dict(db_instance)
                    typed_instance = _dict_to_model(self.model, instance_dict)
                    await _record_crud_operation(
                        operation="update",
                        model_name=self.model_name,
                        started_at=started_at,
                        session_factory=self._session_factory,
                        success=True,
                        result_count=1,
                        attributes={"field_count": len(obj_in)},
                    )
                else:
                    await _record_crud_operation(
                        operation="update",
                        model_name=self.model_name,
                        started_at=started_at,
                        session_factory=self._session_factory,
                        success=True,
                        result_count=0,
                        attributes={"field_count": len(obj_in)},
                    )
            invalidate_model_cache(self.model, session_factory=self._session_factory)
            return typed_instance
        except Exception as exc:
            await _record_crud_operation(
                operation="update",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
                attributes={"field_count": len(obj_in)},
            )
            raise

    async def delete(self, id: int) -> bool:
        """删除记录

        Args:
            id: 记录 ID

        Returns:
            是否删除成功
        """
        started_at = time.perf_counter()
        try:
            deleted = False
            async with _get_session_ctx(self._session_factory) as session:
                stmt = delete(self.model).where(self.model.id == id)
                result = await session.execute(stmt)
                deleted = result.rowcount > 0  # type: ignore
                await _record_crud_operation(
                    operation="delete",
                    model_name=self.model_name,
                    started_at=started_at,
                    session_factory=self._session_factory,
                    success=True,
                    result_count=int(bool(deleted)),
                )
            invalidate_model_cache(self.model, session_factory=self._session_factory)
            return deleted
        except Exception as exc:
            await _record_crud_operation(
                operation="delete",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
            )
            raise

    async def count(self, **filters: Any) -> int:
        """统计记录数

        Args:
            **filters: 过滤条件

        Returns:
            记录数量
        """
        started_at = time.perf_counter()
        cache_key = _build_crud_cache_key("count", filters=filters)
        hit, cached = self._cache.get(self._cache_namespace, cache_key)
        if hit:
            return int(cached or 0)
        try:
            async with _get_session_ctx(self._session_factory) as session:
                stmt = select(func.count(self.model.id))

                for key, value in filters.items():
                    if hasattr(self.model, key):
                        if isinstance(value, list | tuple | set):
                            stmt = stmt.where(getattr(self.model, key).in_(value))
                        else:
                            stmt = stmt.where(getattr(self.model, key) == value)

                result = await session.execute(stmt)
                count_value = int(result.scalar() or 0)
                await _record_crud_operation(
                    operation="count",
                    model_name=self.model_name,
                    started_at=started_at,
                    session_factory=self._session_factory,
                    success=True,
                    result_count=count_value,
                    attributes={"filter_count": len(filters)},
                )
                self._cache.set(self._cache_namespace, cache_key, count_value)
                return count_value
        except Exception as exc:
            await _record_crud_operation(
                operation="count",
                model_name=self.model_name,
                started_at=started_at,
                session_factory=self._session_factory,
                success=False,
                error=exc,
                attributes={"filter_count": len(filters)},
            )
            raise

    async def exists(self, **filters: Any) -> bool:
        """检查记录是否存在

        Args:
            **filters: 过滤条件

        Returns:
            是否存在记录
        """
        count = await self.count(**filters)
        return count > 0

    async def get_or_create(
        self,
        defaults: dict[str, Any] | None = None,
        **filters: Any,
    ) -> tuple[T, bool]:
        """获取或创建记录

        Args:
            defaults: 创建时的默认值
            **filters: 搜索条件

        Returns:
            (实例, 是否为新创建)
        """
        # 先尝试获取
        instance = await self.get_by(**filters)
        if instance is not None:
            return instance, False

        # 创建新记录
        create_data = {**filters}
        if defaults:
            create_data.update(defaults)

        instance = await self.create(create_data)
        return instance, True

    async def bulk_create(self, objs_in: list[dict[str, Any]]) -> list[T]:
        """批量创建记录

        Args:
            objs_in: 要创建的数据列表

        Returns:
            已创建的模型实例列表
        """
        async with _get_session_ctx(self._session_factory) as session:
            instances = [self.model(**obj_data) for obj_data in objs_in]
            session.add_all(instances)
            await session.flush()

            for instance in instances:
                await session.refresh(instance)

            # 在会话关闭前转换为字典
            instances_dicts = [_model_to_dict(inst) for inst in instances]
        invalidate_model_cache(self.model, session_factory=self._session_factory)
        return [_dict_to_model(self.model, d) for d in instances_dicts]

    async def bulk_update(
        self,
        updates: list[tuple[int, dict[str, Any]]],
    ) -> int:
        """批量更新记录

        Args:
            updates: (id, update_data) 元组列表

        Returns:
            更新的记录数量
        """
        async with _get_session_ctx(self._session_factory) as session:
            count = 0
            for id, obj_in in updates:
                stmt = (
                    update(self.model)
                    .where(self.model.id == id)
                    .values(**obj_in)
                )
                result = await session.execute(stmt)
                count += result.rowcount  # type: ignore

        invalidate_model_cache(self.model, session_factory=self._session_factory)
        return count
