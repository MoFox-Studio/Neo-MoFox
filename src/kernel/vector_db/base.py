"""向量数据库抽象基类

定义了所有向量数据库实现必须遵循的接口规范。
提供平台无关的向量存储与检索能力。
"""

from abc import ABC, abstractmethod
from typing import Any


class VectorDBBase(ABC):
    """向量数据库的抽象基类

    定义了所有向量数据库实现必须遵循的标准接口。
    实现类需要提供向量存储、检索、更新、删除等核心功能。
    """

    @abstractmethod
    async def initialize(self, path: str, **kwargs: Any) -> None:
        """初始化向量数据库客户端

        Args:
            path: 数据库文件的存储路径
            **kwargs: 其他特定于实现的参数
        """
        pass

    @abstractmethod
    async def get_or_create_collection(
        self, name: str, **kwargs: Any
    ) -> Any:
        """获取或创建一个集合 (Collection)

        不同的业务模块应使用不同的 collection 名称以实现物理隔离。

        Args:
            name: 集合的名称
            **kwargs: 其他特定于实现的参数 (例如 metadata)

        Returns:
            代表集合的对象
        """
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
            **kwargs: 其他特定于实现的参数

        Returns:
            查询结果字典，通常包含 ids, distances, metadatas, documents
        """
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
            include: 指定返回的数据字段 (例如 ["metadatas", "documents"])，可选

        Returns:
            获取到的数据字典
        """
        pass

    @abstractmethod
    async def count(self, collection_name: str) -> int:
        """获取指定集合中的条目总数

        Args:
            collection_name: 目标集合的名称

        Returns:
            条目总数
        """
        pass

    @abstractmethod
    async def delete_collection(self, name: str) -> None:
        """删除一个集合

        Args:
            name: 要删除的集合的名称
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """关闭数据库连接并清理资源"""
        pass
