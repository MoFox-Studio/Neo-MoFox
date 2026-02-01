"""ChromaDB 向量数据库实现

基于 ChromaDB 的向量数据库具体实现。
采用单例模式，确保全局只有一个 ChromaDB 客户端实例。
"""

import asyncio
from threading import Lock
from typing import Any

import chromadb
from chromadb.config import Settings
from chromadb.api import ClientAPI

from src.kernel.logger import get_logger

from .base import VectorDBBase

logger = get_logger("vector_db.chromadb", display="ChromaDB")


class ChromaDBImpl(VectorDBBase):
    """ChromaDB 的具体实现类

    遵循 VectorDBBase 接口规范，提供基于 ChromaDB 的向量存储能力。
    采用单例模式，确保全局只有一个 ChromaDB 客户端实例。
    """

    _instance: "ChromaDBImpl | None" = None
    _lock = Lock()

    def __new__(cls, *args: Any, **kwargs: Any):
        """实现单例模式"""
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, path: str = "data/chroma_db", **kwargs: Any):
        """初始化 ChromaDB 客户端

        由于是单例，这个初始化只会执行一次。

        Args:
            path: 数据库存储路径
            **kwargs: 其他配置参数
        """
        if not hasattr(self, "_initialized"):
            with self._lock:
                if not hasattr(self, "_initialized"):
                    self._path: str = path
                    self._client: ClientAPI | None = None
                    self._collections: dict[str, Any] = {}
                    self._initialized: bool = False
                    self._init_lock = asyncio.Lock()

    async def initialize(self, path: str, **kwargs: Any) -> None:
        """异步初始化 ChromaDB 客户端

        Args:
            path: 数据库存储路径
            **kwargs: 其他配置参数

        Raises:
            ConnectionError: 当 ChromaDB 初始化失败时
        """
        async with self._init_lock:
            if self._initialized:
                return

            try:
                # ChromaDB 的初始化是同步的，需要在单独的线程中执行
                loop = asyncio.get_event_loop()
                self._client = await loop.run_in_executor(
                    None,
                    lambda: chromadb.PersistentClient(
                        path=path, settings=Settings(anonymized_telemetry=False)
                    ),
                )
                self._path = path
                self._initialized = True
                logger.info(f"ChromaDB 客户端已初始化，数据库路径: {path}")
            except Exception as e:
                logger.error(f"ChromaDB 初始化失败: {e}")
                self._initialized = False
                raise ConnectionError(f"ChromaDB 初始化失败: {e}") from e

    async def get_or_create_collection(
        self, name: str, **kwargs: Any
    ) -> Any:
        """获取或创建一个集合

        Args:
            name: 集合的名称
            **kwargs: 其他集合配置参数

        Returns:
            ChromaDB 集合对象
        """
        if not self._initialized or not self._client:
            await self.initialize(self._path)

        if name in self._collections:
            return self._collections[name]

        try:
            loop = asyncio.get_event_loop()
            collection = await loop.run_in_executor(
                None, lambda: self._client.get_or_create_collection(name, **kwargs)
            )
            self._collections[name] = collection
            logger.info(f"成功获取或创建集合: '{name}'")
            return collection
        except Exception as e:
            logger.error(f"获取或创建集合 '{name}' 失败: {e}")
            return None

    async def add(
        self,
        collection_name: str,
        embeddings: list[list[float]],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        """向指定集合中添加数据

        Args:
            collection_name: 目标集合的名称
            embeddings: 向量列表
            documents: 文档列表，可选
            metadatas: 元数据列表，可选
            ids: ID 列表，可选
        """
        collection = await self.get_or_create_collection(collection_name)
        if collection:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: collection.add(
                        embeddings=embeddings,
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids,
                    ),
                )
            except Exception as e:
                logger.error(f"向集合 '{collection_name}' 添加数据失败: {e}")

    async def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]],
        n_results: int = 1,
        where: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, list[Any]]:
        """在指定集合中查询相似向量

        Args:
            collection_name: 目标集合的名称
            query_embeddings: 用于查询的向量列表
            n_results: 返回结果的数量
            where: 元数据过滤条件，可选
            **kwargs: 其他查询参数

        Returns:
            查询结果字典，包含 ids, distances, metadatas, documents
        """
        collection = await self.get_or_create_collection(collection_name)
        if not collection:
            return {}

        try:
            query_params = {
                "query_embeddings": query_embeddings,
                "n_results": n_results,
                **kwargs,
            }

            # 处理 where 条件
            if where:
                processed_where = self._process_where_condition(where)
                if processed_where:
                    query_params["where"] = processed_where

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, lambda: collection.query(**query_params)
            )
        except Exception as e:
            logger.error(f"查询集合 '{collection_name}' 失败: {e}")
        return {}

    def _process_where_condition(self, where: dict[str, Any]) -> dict[str, Any] | None:
        """处理 where 条件，转换为 ChromaDB 支持的格式

        ChromaDB 支持的格式：
        - 简单条件: {"field": "value"}
        - 操作符条件: {"field": {"$op": "value"}}
        - AND 条件: {"$and": [condition1, condition2]}
        - OR 条件: {"$or": [condition1, condition2]}

        Args:
            where: 原始 where 条件

        Returns:
            处理后的 where 条件
        """
        if not where:
            return None

        try:
            # 如果只有一个字段，直接返回
            if len(where) == 1:
                key, value = next(iter(where.items()))

                # 处理列表值（如 memory_types）
                if isinstance(value, list):
                    if len(value) == 1:
                        return {key: value[0]}
                    else:
                        # 多个值使用 $in 操作符
                        return {key: {"$in": value}}
                else:
                    return {key: value}

            # 多个字段使用 $and 操作符
            conditions = []
            for key, value in where.items():
                if isinstance(value, list):
                    if len(value) == 1:
                        conditions.append({key: value[0]})
                    else:
                        conditions.append({key: {"$in": value}})
                else:
                    conditions.append({key: value})

            return {"$and": conditions}

        except Exception as e:
            logger.warning(f"处理where条件失败: {e}, 使用简化条件")
            return None

    async def get(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        where_document: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """根据条件从集合中获取数据

        Args:
            collection_name: 目标集合的名称
            ids: 要获取的条目的 ID 列表，可选
            where: 基于元数据的过滤条件，可选
            limit: 返回结果的数量限制，可选
            offset: 返回结果的偏移量，可选
            where_document: 基于文档内容的过滤条件，可选
            include: 指定返回的数据字段，可选

        Returns:
            获取到的数据字典
        """
        collection = await self.get_or_create_collection(collection_name)
        if not collection:
            return {}

        try:
            # 处理 where 条件
            processed_where = None
            if where:
                processed_where = self._process_where_condition(where)

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: collection.get(
                    ids=ids,
                    where=processed_where,
                    limit=limit,
                    offset=offset,
                    where_document=where_document,
                    include=include or ["documents", "metadatas", "embeddings"],
                ),
            )
        except Exception as e:
            logger.error(f"从集合 '{collection_name}' 获取数据失败: {e}")
        return {}

    async def delete(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        """从指定集合中删除数据

        Args:
            collection_name: 目标集合的名称
            ids: 要删除的条目的 ID 列表，可选
            where: 基于元数据的过滤条件，可选
        """
        collection = await self.get_or_create_collection(collection_name)
        if collection:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: collection.delete(ids=ids, where=where)
                )
            except Exception as e:
                logger.error(f"从集合 '{collection_name}' 删除数据失败: {e}")

    async def count(self, collection_name: str) -> int:
        """获取指定集合中的条目总数

        Args:
            collection_name: 目标集合的名称

        Returns:
            条目总数
        """
        collection = await self.get_or_create_collection(collection_name)
        if collection:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: collection.count())
            except Exception as e:
                logger.error(f"获取集合 '{collection_name}' 计数失败: {e}")
        return 0

    async def delete_collection(self, name: str) -> None:
        """删除一个集合

        Args:
            name: 要删除的集合的名称
        """
        if not self._initialized or not self._client:
            await self.initialize(self._path)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: self._client.delete_collection(name=name)
            )
            if name in self._collections:
                del self._collections[name]
            logger.info(f"集合 '{name}' 已被删除")
        except Exception as e:
            logger.error(f"删除集合 '{name}' 失败: {e}")

    async def close(self) -> None:
        """关闭数据库连接并清理资源"""
        if self._initialized:
            self._collections.clear()
            self._client = None
            self._initialized = False
            logger.info("ChromaDB 连接已关闭")
