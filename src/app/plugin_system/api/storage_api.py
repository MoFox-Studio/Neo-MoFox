"""插件存储 API

为插件提供两种独立存储能力，统一从此模块导入：

- **JSON 存储**：基于文件的简单键值存储，每个 store_name 对应 data/json_storage/<store_name>/ 目录，
  不同插件互不干扰。
- **数据库存储**：基于 SQLite 的结构化存储（:class:`PluginDatabase`），使用与主数据库完全相同的
  :class:`~src.kernel.db.CRUDBase` / :class:`~src.kernel.db.QueryBuilder` /
  :class:`~src.kernel.db.AggregateQuery` 接口，在指定路径独立存储，与主程序数据库不共享连接。

使用示例（JSON 存储）::

    from src.app.plugin_system.api import storage_api

    await storage_api.save_json("my_plugin", "settings", {"key": "value"})
    data = await storage_api.load_json("my_plugin", "settings")

使用示例（数据库存储）::

    from src.app.plugin_system.api.storage_api import PluginDatabase
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import Mapped, mapped_column
    from sqlalchemy import Integer, Text

    Base = declarative_base()

    class MyRecord(Base):
        __tablename__ = "my_records"
        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        name: Mapped[str] = mapped_column(Text, nullable=False)

    db = PluginDatabase("data/my_plugin/data.db", [MyRecord])
    await db.initialize()

    # 使用标准 CRUD 接口
    record = await db.crud(MyRecord).create({"name": "hello"})

    # 使用标准查询构建器
    results = await db.query(MyRecord).filter(name="hello").all()

    # 使用标准聚合接口
    counts = await db.aggregate(MyRecord).group_by_count("name")

    # 复杂操作使用原始 session
    async with db.session() as s:
        await s.execute(...)

    await db.close()
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.kernel.db import AggregateQuery, CRUDBase, QueryBuilder
from src.kernel.logger import get_logger
from src.kernel.storage import JSONStore

logger = get_logger("plugin.storage", display="插件存储")

T = TypeVar("T", bound=Any)


# =============================================================================
# JSON 存储分区
# =============================================================================


@lru_cache(maxsize=64)
def _get_plugin_json_store(store_name: str) -> JSONStore:
    """按 store_name 获取或创建隔离的 JSONStore 实例（按路径缓存）。

    Args:
        store_name: 存储命名空间，通常使用插件名称。

    Returns:
        JSONStore 实例。
    """
    return JSONStore(f"data/json_storage/{store_name}")


async def save_json(store_name: str, name: str, data: dict[str, Any]) -> None:
    """保存 JSON 数据到指定命名空间。

    Args:
        store_name: 存储命名空间（对应目录 ``data/json_storage/<store_name>/``）。
        name: 数据键名（对应文件名，不含 ``.json`` 后缀）。
        data: 要保存的数据字典。
    """
    await _get_plugin_json_store(store_name).save(name, data)


async def load_json(store_name: str, name: str) -> dict[str, Any] | None:
    """从指定命名空间加载 JSON 数据。

    Args:
        store_name: 存储命名空间。
        name: 数据键名。

    Returns:
        数据字典；键不存在时返回 ``None``。
    """
    return await _get_plugin_json_store(store_name).load(name)


async def delete_json(store_name: str, name: str) -> bool:
    """删除指定命名空间中的 JSON 数据。

    Args:
        store_name: 存储命名空间。
        name: 数据键名。

    Returns:
        是否成功删除（键不存在时返回 ``False``）。
    """
    return await _get_plugin_json_store(store_name).delete(name)


async def exists_json(store_name: str, name: str) -> bool:
    """检查指定命名空间中的 JSON 键是否存在。

    Args:
        store_name: 存储命名空间。
        name: 数据键名。

    Returns:
        是否存在。
    """
    return await _get_plugin_json_store(store_name).exists(name)


async def list_json(store_name: str) -> list[str]:
    """列出指定命名空间下所有键名。

    Args:
        store_name: 存储命名空间。

    Returns:
        键名列表（不含 ``.json`` 后缀）。
    """
    return await _get_plugin_json_store(store_name).list_all()


# =============================================================================
# 数据库存储分区
# =============================================================================


class PluginDatabase:
    """插件独立 SQLite 数据库。

    为插件提供完全隔离的结构化持久化存储。永远使用 SQLite，在指定路径独立存储，
    与主程序数据库不共享任何引擎或连接。

    通过 :meth:`crud`、:meth:`query`、:meth:`aggregate` 方法返回绑定到本数据库的
    标准接口实例，与主数据库的用法完全一致。复杂的原子操作（upsert、批量更新等）
    可通过 :meth:`session` 上下文管理器直接操作 :class:`~sqlalchemy.ext.asyncio.AsyncSession`。

    Args:
        db_path: SQLite 数据库文件路径，例如 ``"data/my_plugin/data.db"``。
        models: SQLAlchemy 模型类列表；调用 :meth:`initialize` 时自动建表。

    示例::

        db = PluginDatabase("data/my_plugin/data.db", [MyModel])
        await db.initialize()
        record = await db.crud(MyModel).create({"field": "value"})
        await db.close()
    """

    def __init__(self, db_path: str, models: list[type]) -> None:
        """初始化插件数据库（不立即创建引擎，需调用 :meth:`initialize`）。

        Args:
            db_path: SQLite 数据库文件路径。
            models: SQLAlchemy 模型类列表。
        """
        self._db_path = Path(db_path)
        self._models = models
        self._engine: Any = None
        self._session_factory: async_sessionmaker | None = None
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """初始化数据库引擎并建表（幂等，可多次调用）。

        - 创建 SQLite 异步引擎，启用 WAL / NORMAL 等性能优化 pragma。
        - 根据传入的模型列表自动创建尚不存在的表。
        """
        async with self._init_lock:
            if self._engine is not None:
                return

            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+aiosqlite:///{self._db_path.absolute().as_posix()}"

            engine = create_async_engine(
                url,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 60},
            )

            # 应用 SQLite 优化
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA journal_mode = WAL"))
                await conn.execute(text("PRAGMA synchronous = NORMAL"))
                await conn.execute(text("PRAGMA foreign_keys = ON"))
                await conn.execute(text("PRAGMA busy_timeout = 10000"))

            # 合并所有模型的 Metadata（去重），逐一建表
            seen_metadata: set[int] = set()
            for model in self._models:
                meta = model.__table__.metadata
                if id(meta) not in seen_metadata:
                    seen_metadata.add(id(meta))
                    async with engine.begin() as conn:
                        await conn.run_sync(meta.create_all)

            self._session_factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            self._engine = engine
            logger.debug(f"插件数据库已初始化: {self._db_path}")

    def _require_initialized(self) -> async_sessionmaker:
        """断言数据库已初始化并返回 session_factory。

        Returns:
            async_sessionmaker 实例。

        Raises:
            RuntimeError: 若 :meth:`initialize` 尚未调用。
        """
        if self._session_factory is None:
            raise RuntimeError(
                f"PluginDatabase({self._db_path}) 尚未初始化，请先调用 await db.initialize()"
            )
        return self._session_factory

    def crud(self, model: type[T]) -> CRUDBase[T]:
        """返回绑定到本数据库的 :class:`~src.kernel.db.CRUDBase` 实例。

        Args:
            model: SQLAlchemy 模型类。

        Returns:
            ``CRUDBase`` 实例，使用本数据库的连接。
        """
        return CRUDBase(model, session_factory=self._require_initialized())

    def query(self, model: type[T]) -> QueryBuilder[T]:
        """返回绑定到本数据库的 :class:`~src.kernel.db.QueryBuilder` 实例。

        Args:
            model: SQLAlchemy 模型类。

        Returns:
            ``QueryBuilder`` 实例，支持链式调用。
        """
        return QueryBuilder(model, session_factory=self._require_initialized())

    def aggregate(self, model: type[T]) -> AggregateQuery:
        """返回绑定到本数据库的 :class:`~src.kernel.db.AggregateQuery` 实例。

        Args:
            model: SQLAlchemy 模型类。

        Returns:
            ``AggregateQuery`` 实例。
        """
        return AggregateQuery(model, session_factory=self._require_initialized())

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """获取原始 :class:`~sqlalchemy.ext.asyncio.AsyncSession`，用于复杂操作。

        适用于 upsert、原子计数更新、复杂 JOIN 等无法通过标准接口表达的场景。
        会话在退出时自动提交；发生异常时自动回滚。

        Yields:
            :class:`~sqlalchemy.ext.asyncio.AsyncSession` 实例。

        Raises:
            RuntimeError: 若数据库尚未初始化。

        示例::

            async with db.session() as s:
                await s.execute(update(MyModel).where(...).values(...))
        """
        sf = self._require_initialized()
        async with sf() as s:
            try:
                yield s
                if s.is_active:
                    await s.commit()
            except SQLAlchemyError:
                if s.is_active:
                    await s.rollback()
                raise
            except Exception:
                if s.is_active:
                    await s.rollback()
                raise
            finally:
                await s.close()

    async def close(self) -> None:
        """关闭数据库引擎，释放所有连接资源。

        调用后如需再次使用须重新调用 :meth:`initialize`。
        """
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.debug(f"插件数据库已关闭: {self._db_path}")


__all__ = [
    # JSON 存储
    "JSONStore",
    "save_json",
    "load_json",
    "delete_json",
    "exists_json",
    "list_json",
    # 数据库存储
    "PluginDatabase",
]
