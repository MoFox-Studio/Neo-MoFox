"""Vector DB 模块单元测试

测试 VectorDBBase 抽象基类和 ChromaDBImpl 实现类的功能。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.kernel.vector_db.base import VectorDBBase
from src.kernel.vector_db.chromadb_impl import ChromaDBImpl
from src.kernel.vector_db import get_vector_db_service, vector_db_service


class MockVectorDB(VectorDBBase):
    """用于测试抽象基类的 Mock 实现"""

    def __init__(self):
        self.initialized = False
        self.collections: dict[str, Any] = {}
        self.data: dict[str, list[dict[str, Any]]] = {}

    async def initialize(self, path: str, **kwargs: Any) -> None:
        """Mock 初始化"""
        self.initialized = True
        self.path = path

    async def get_or_create_collection(
        self, name: str, **kwargs: Any
    ) -> Any:
        """Mock 获取或创建集合"""
        if name not in self.collections:
            self.collections[name] = {"name": name, **kwargs}
            self.data[name] = []
        return self.collections[name]

    async def add(
        self,
        collection_name: str,
        embeddings: list[list[float]],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        """Mock 添加数据"""
        if collection_name not in self.data:
            await self.get_or_create_collection(collection_name)

        for i, embedding in enumerate(embeddings):
            item = {"embedding": embedding}
            if documents and i < len(documents):
                item["document"] = documents[i]
            if metadatas and i < len(metadatas):
                item["metadata"] = metadatas[i]
            if ids and i < len(ids):
                item["id"] = ids[i]
            self.data[collection_name].append(item)

    async def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]],
        n_results: int = 1,
        where: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, list[Any]]:
        """Mock 查询"""
        if collection_name not in self.data:
            return {}
        return {
            "ids": [["1"]],
            "distances": [[0.1]],
            "metadatas": [[{}]],
            "documents": [["test"]],
        }

    async def delete(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        """Mock 删除"""
        if collection_name in self.data:
            self.data[collection_name].clear()

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
        """Mock 获取"""
        if collection_name not in self.data:
            return {}
        return {
            "ids": ["1"],
            "documents": ["test"],
            "metadatas": [{}],
            "embeddings": [[0.1, 0.2]],
        }

    async def count(self, collection_name: str) -> int:
        """Mock 计数"""
        if collection_name not in self.data:
            return 0
        return len(self.data[collection_name])

    async def delete_collection(self, name: str) -> None:
        """Mock 删除集合"""
        if name in self.collections:
            del self.collections[name]
        if name in self.data:
            del self.data[name]

    async def close(self) -> None:
        """Mock 关闭"""
        self.initialized = False
        self.collections.clear()
        self.data.clear()


class TestVectorDBBase:
    """测试 VectorDBBase 抽象基类"""

    def test_abstract_methods_defined(self) -> None:
        """测试抽象方法是否正确定义"""
        abstract_methods = VectorDBBase.__abstractmethods__
        expected_methods = {
            "initialize",
            "get_or_create_collection",
            "add",
            "query",
            "delete",
            "get",
            "count",
            "delete_collection",
            "close",
        }
        assert abstract_methods == expected_methods

    @pytest.mark.asyncio
    async def test_mock_implementation(self) -> None:
        """测试 Mock 实现是否可以正常工作"""
        mock_db = MockVectorDB()
        await mock_db.initialize("test_path")
        assert mock_db.initialized

        await mock_db.get_or_create_collection("test_collection")
        assert "test_collection" in mock_db.collections

        await mock_db.add(
            "test_collection",
            embeddings=[[0.1, 0.2, 0.3]],
            documents=["test document"],
            ids=["1"],
        )
        assert await mock_db.count("test_collection") == 1

        results = await mock_db.query(
            "test_collection", query_embeddings=[[0.1, 0.2, 0.3]]
        )
        assert "ids" in results

        await mock_db.delete("test_collection", ids=["1"])
        assert await mock_db.count("test_collection") == 0


class TestChromaDBImpl:
    """测试 ChromaDBImpl 实现类"""

    @pytest.fixture
    def temp_db_path(self, tmp_path: Path) -> str:
        """创建临时数据库路径"""
        return str(tmp_path / "chroma_test")

    @pytest.fixture
    async def chroma_db(self, temp_db_path: str) -> ChromaDBImpl:
        """创建 ChromaDB 实例"""
        db = ChromaDBImpl(path=temp_db_path)
        await db.initialize(temp_db_path)
        yield db
        await db.close()

    def test_cached_by_db_path(self) -> None:
        """测试按 db_path 缓存实例（推荐用法）"""
        with tempfile.TemporaryDirectory() as tmpdir1:
            db1 = get_vector_db_service(tmpdir1)
            db2 = get_vector_db_service(tmpdir1)
            assert db1 is db2

        with tempfile.TemporaryDirectory() as tmpdir2:
            db3 = get_vector_db_service(tmpdir2)
            assert db3 is not db1

    def test_direct_instantiation_is_not_singleton(self) -> None:
        """测试直接实例化不再是单例（更利于多 profile 生命周期由上层管理）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db1 = ChromaDBImpl(path=tmpdir)
            db2 = ChromaDBImpl(path=tmpdir)
            assert db1 is not db2

    @pytest.mark.asyncio
    async def test_initialize(self, temp_db_path: str) -> None:
        """测试初始化"""
        db = ChromaDBImpl(path=temp_db_path)
        assert not db._initialized

        await db.initialize(temp_db_path)
        assert db._initialized
        assert db._client is not None

        await db.close()

    @pytest.mark.asyncio
    async def test_get_or_create_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试获取或创建集合"""
        collection1 = await chroma_db.get_or_create_collection("test_collection")
        assert collection1 is not None

        # 再次获取应该返回同一个集合
        collection2 = await chroma_db.get_or_create_collection("test_collection")
        assert collection1 is collection2

    @pytest.mark.asyncio
    async def test_add_and_query(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试添加和查询数据"""
        collection_name = "test_collection"

        # 添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            documents=["document 1", "document 2"],
            metadatas=[{"key": "value1"}, {"key": "value2"}],
            ids=["id1", "id2"],
        )

        # 查询数据
        results = await chroma_db.query(
            collection_name=collection_name,
            query_embeddings=[[0.1, 0.2, 0.3]],
            n_results=2,
        )

        assert "ids" in results
        assert "distances" in results
        assert "metadatas" in results
        assert "documents" in results

    @pytest.mark.asyncio
    async def test_query_with_where(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试带 where 条件的查询"""
        collection_name = "test_collection"

        # 添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            documents=["document 1", "document 2"],
            metadatas=[{"category": "A"}, {"category": "B"}],
            ids=["id1", "id2"],
        )

        # 带 where 条件查询
        results = await chroma_db.query(
            collection_name=collection_name,
            query_embeddings=[[0.1, 0.2, 0.3]],
            n_results=2,
            where={"category": "A"},
        )

        assert "ids" in results

    @pytest.mark.asyncio
    async def test_count(self, chroma_db: ChromaDBImpl) -> None:
        """测试计数功能"""
        collection_name = "test_collection"

        # 初始计数应该为 0
        count = await chroma_db.count(collection_name)
        assert count == 0

        # 添加数据后计数应该增加
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            ids=["id1", "id2"],
        )

        count = await chroma_db.count(collection_name)
        assert count == 2

    @pytest.mark.asyncio
    async def test_get(self, chroma_db: ChromaDBImpl) -> None:
        """测试获取数据"""
        collection_name = "test_collection"

        # 添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3]],
            documents=["test document"],
            ids=["id1"],
        )

        # 获取数据
        result = await chroma_db.get(
            collection_name=collection_name,
            ids=["id1"],
            include=["documents", "metadatas"],
        )

        assert "ids" in result
        assert "documents" in result

    @pytest.mark.asyncio
    async def test_delete(self, chroma_db: ChromaDBImpl) -> None:
        """测试删除数据"""
        collection_name = "test_collection"

        # 添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            ids=["id1", "id2"],
        )

        count_before = await chroma_db.count(collection_name)
        assert count_before == 2

        # 删除数据
        await chroma_db.delete(collection_name=collection_name, ids=["id1"])

        count_after = await chroma_db.count(collection_name)
        assert count_after == 1

    @pytest.mark.asyncio
    async def test_delete_collection(self, chroma_db: ChromaDBImpl) -> None:
        """测试删除集合"""
        collection_name = "test_collection"

        # 创建集合并添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3]],
            ids=["id1"],
        )

        # 删除集合
        await chroma_db.delete_collection(collection_name)

        # 集合应该不存在了
        count = await chroma_db.count(collection_name)
        assert count == 0

    @pytest.mark.asyncio
    async def test_close(self, chroma_db: ChromaDBImpl) -> None:
        """测试关闭连接"""
        assert chroma_db._initialized

        await chroma_db.close()

        assert not chroma_db._initialized
        assert chroma_db._client is None
        assert len(chroma_db._collections) == 0

    def test_process_where_condition_single_field(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试处理单个字段的 where 条件"""
        # 简单条件
        result = chroma_db._process_where_condition({"key": "value"})
        assert result == {"key": "value"}

        # 列表值（单个元素）
        result = chroma_db._process_where_condition({"key": ["value1"]})
        assert result == {"key": "value1"}

        # 列表值（多个元素）
        result = chroma_db._process_where_condition({"key": ["value1", "value2"]})
        assert result == {"key": {"$in": ["value1", "value2"]}}

    def test_process_where_condition_multiple_fields(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试处理多个字段的 where 条件"""
        result = chroma_db._process_where_condition(
            {"key1": "value1", "key2": "value2"}
        )
        expected = {"$and": [{"key1": "value1"}, {"key2": "value2"}]}
        assert result == expected

    def test_process_where_condition_empty(self, chroma_db: ChromaDBImpl) -> None:
        """测试处理空的 where 条件"""
        result = chroma_db._process_where_condition({})
        assert result is None

        result = chroma_db._process_where_condition(None)
        assert result is None


class TestModuleExports:
    """测试模块导出的接口"""

    def test_get_vector_db_service(self) -> None:
        """测试获取向量数据库服务"""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = get_vector_db_service(db_path=tmpdir)
            assert isinstance(service, VectorDBBase)
            assert isinstance(service, ChromaDBImpl)

    def test_vector_db_service_singleton(self) -> None:
        """测试全局服务实例"""
        assert isinstance(vector_db_service, VectorDBBase)
        assert isinstance(vector_db_service, ChromaDBImpl)

        # 多次调用应该返回同一个实例（如果路径相同）
        service1 = get_vector_db_service()
        service2 = get_vector_db_service()
        assert service1 is service2


class TestChromaDBImplEdgeCases:
    """测试 ChromaDBImpl 的边缘情况和异常处理"""

    @pytest.fixture
    async def chroma_db(self, tmp_path: Path) -> Any:  # type: ignore[override]
        """创建 ChromaDB 实例"""
        db_path = str(tmp_path / "chroma_test")
        db = ChromaDBImpl(path=db_path)
        await db.initialize(db_path)
        yield db
        await db.close()

    @pytest.mark.asyncio
    async def test_query_with_invalid_where_fallback(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试 where 条件异常时的回退逻辑"""
        collection_name = "test_collection"

        # 添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3]],
            ids=["id1"],
        )

        # 使用复杂的 where 条件（可能触发异常）
        # 这里我们直接测试，如果 ChromaDB 不支持某些条件，会回退
        results = await chroma_db.query(
            collection_name=collection_name,
            query_embeddings=[[0.1, 0.2, 0.3]],
            n_results=1,
            where={"key": "value"},
        )

        # 即使回退，也应该返回结果（可能为空或包含所有结果）
        assert isinstance(results, dict)

    @pytest.mark.asyncio
    async def test_get_with_invalid_where_fallback(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试 get 方法 where 条件异常时的回退逻辑"""
        collection_name = "test_collection"

        # 添加数据
        await chroma_db.add(
            collection_name=collection_name,
            embeddings=[[0.1, 0.2, 0.3]],
            documents=["test"],
            ids=["id1"],
        )

        # 使用 where 条件
        result = await chroma_db.get(
            collection_name=collection_name,
            where={"key": "value"},
            include=["documents"],
        )

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_process_where_condition_exception(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试 where 条件处理异常时的回退"""
        # 测试空字典
        result = chroma_db._process_where_condition({})
        assert result is None

        # 测试 None
        result = chroma_db._process_where_condition(None)
        assert result is None

        # 测试复杂的嵌套列表（可能触发异常）
        # 这个测试确保即使处理失败，也能返回有效的条件
        result = chroma_db._process_where_condition({"key": ["val1", "val2", "val3"]})
        assert result == {"key": {"$in": ["val1", "val2", "val3"]}}

    @pytest.mark.asyncio
    async def test_add_to_nonexistent_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试向不存在的集合添加数据（应该自动创建）"""
        # 直接向不存在的集合添加数据
        await chroma_db.add(
            collection_name="new_collection",
            embeddings=[[0.1, 0.2, 0.3]],
            ids=["id1"],
        )

        # 验证集合已创建
        count = await chroma_db.count("new_collection")
        assert count == 1

    @pytest.mark.asyncio
    async def test_query_nonexistent_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试查询不存在的集合"""
        results = await chroma_db.query(
            collection_name="nonexistent_collection",
            query_embeddings=[[0.1, 0.2, 0.3]],
        )

        # ChromaDB 会自动创建集合，返回空结果但不是空字典
        assert isinstance(results, dict)
        assert "ids" in results
        assert "distances" in results

    @pytest.mark.asyncio
    async def test_get_nonexistent_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试获取不存在的集合"""
        result = await chroma_db.get(
            collection_name="nonexistent_collection",
            ids=["id1"],
        )

        # ChromaDB 会自动创建集合，返回空结果但不是空字典
        assert isinstance(result, dict)
        assert "ids" in result

    @pytest.mark.asyncio
    async def test_delete_from_nonexistent_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试从不存在的集合删除数据（不应该抛出异常）"""
        # 应该不抛出异常
        await chroma_db.delete(
            collection_name="nonexistent_collection",
            ids=["id1"],
        )

    @pytest.mark.asyncio
    async def test_count_nonexistent_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试统计不存在的集合"""
        count = await chroma_db.count("nonexistent_collection")
        assert count == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_collection(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试删除不存在的集合（不应该抛出异常）"""
        # 应该不抛出异常
        await chroma_db.delete_collection("nonexistent_collection")

    @pytest.mark.asyncio
    async def test_multiple_initializations(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试多次初始化（应该只初始化一次）"""
        # 第一次初始化已经在 fixture 中完成
        assert chroma_db._initialized

        # 再次初始化应该被忽略
        await chroma_db.initialize(chroma_db._path)

        assert chroma_db._initialized

    @pytest.mark.asyncio
    async def test_collection_caching(self, chroma_db: ChromaDBImpl) -> None:
        """测试集合缓存机制"""
        # 创建集合
        collection1 = await chroma_db.get_or_create_collection("cached_collection")

        # 再次获取应该返回缓存的集合
        collection2 = await chroma_db.get_or_create_collection("cached_collection")

        assert collection1 is collection2
        assert "cached_collection" in chroma_db._collections

    @pytest.mark.asyncio
    async def test_close_clears_collections(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试关闭连接时清空集合缓存"""
        # 创建一些集合
        await chroma_db.get_or_create_collection("collection1")
        await chroma_db.get_or_create_collection("collection2")

        assert len(chroma_db._collections) > 0

        # 关闭连接
        await chroma_db.close()

        # 集合缓存应该被清空
        assert len(chroma_db._collections) == 0
        assert not chroma_db._initialized

    def test_process_where_condition_with_list_fallback(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试 where 条件处理中列表值的边界情况"""
        # 空列表
        result = chroma_db._process_where_condition({"key": []})
        # 空列表应该保持不变或在后续处理中被过滤
        if result:
            assert "key" in result

        # 列表包含单个元素
        result = chroma_db._process_where_condition({"key": ["single"]})
        assert result == {"key": "single"}

    @pytest.mark.asyncio
    async def test_process_where_condition_multiple_fields_with_lists(
        self, chroma_db: ChromaDBImpl
    ) -> None:
        """测试多字段 where 条件中包含列表的情况"""
        result = chroma_db._process_where_condition(
            {"key1": ["val1", "val2"], "key2": "single", "key3": ["val3"]}
        )

        expected = {
            "$and": [
                {"key1": {"$in": ["val1", "val2"]}},
                {"key2": "single"},
                {"key3": "val3"},
            ]
        }
        assert result == expected
