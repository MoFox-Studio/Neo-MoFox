"""向量数据库模块 (kernel/vector_db)

提供标准化的向量存储与检索接口，支持多集合隔离与元数据过滤。
目前底层基于 ChromaDB 实现。

对外接口：
- vector_db_service: 全局向量数据库服务实例
- VectorDBBase: 向量数据库抽象基类

使用示例：
    ```python
    from src.kernel.vector_db import vector_db_service

    # 1. 获取或创建集合
    await vector_db_service.get_or_create_collection(name="semantic_cache")

    # 2. 添加向量数据
    await vector_db_service.add(
        collection_name="memory",
        embeddings=[[0.1, 0.2, 0.3]],
        documents=["这是一个人类发送的消息"],
        metadatas=[{"stream_id": "12345", "timestamp": 123456789.0}],
        ids=["msg_001"]
    )

    # 3. 相似度查询
    results = await vector_db_service.query(
        collection_name="memory",
        query_embeddings=[[0.11, 0.21, 0.31]],
        n_results=5,
        where={"stream_id": "12345"}
    )
    # results 字典包含: 'ids', 'distances', 'metadatas', 'documents'
    ```
"""

import os
from threading import Lock

from .base import VectorDBBase
from .chromadb_impl import ChromaDBImpl

# 默认数据库路径
_DEFAULT_DB_PATH = "data/chroma_db"

_vector_db_lock = Lock()
_vector_db_by_path: dict[str, VectorDBBase] = {}


def _normalize_db_path(db_path: str) -> str:
    # 统一相对路径/分隔符，避免同一路径被重复缓存
    return os.path.normpath(os.path.abspath(db_path))


def get_vector_db_service(
    db_path: str = _DEFAULT_DB_PATH,
    *,
    use_cache: bool = True,
) -> VectorDBBase:
    """获取向量数据库服务实例

    Args:
        db_path: 数据库存储路径，默认为 "data/chroma_db"

    Returns:
        向量数据库服务实例

    Note:
        默认会按 db_path 缓存实例，便于多 profile/多配置并存。
        若希望生命周期完全交给上层管理，可传入 use_cache=False。
    """
    # TODO: 未来可以从全局配置中读取数据库类型和路径
    if not use_cache:
        return ChromaDBImpl(path=db_path)

    key = _normalize_db_path(db_path)
    with _vector_db_lock:
        instance = _vector_db_by_path.get(key)
        if instance is None:
            instance = ChromaDBImpl(path=db_path)
            _vector_db_by_path[key] = instance
        return instance


def create_vector_db_service(db_path: str = _DEFAULT_DB_PATH) -> VectorDBBase:
    """创建一个新的 VectorDB 实例（不走缓存）。"""

    return ChromaDBImpl(path=db_path)


async def close_vector_db_service(db_path: str = _DEFAULT_DB_PATH) -> None:
    """关闭并移除指定 db_path 对应的缓存实例。"""

    key = _normalize_db_path(db_path)
    with _vector_db_lock:
        instance = _vector_db_by_path.pop(key, None)

    if instance is not None:
        await instance.close()


async def close_all_vector_db_services() -> None:
    """关闭并清空所有缓存的 VectorDB 实例。"""

    with _vector_db_lock:
        instances = list(_vector_db_by_path.values())
        _vector_db_by_path.clear()

    for instance in instances:
        await instance.close()


def __getattr__(name: str):
    if name == "vector_db_service":
        instance = get_vector_db_service()
        globals()["vector_db_service"] = instance
        return instance
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = [
    "VectorDBBase",
    "ChromaDBImpl",
    "get_vector_db_service",
    "create_vector_db_service",
    "close_vector_db_service",
    "close_all_vector_db_services",
]
