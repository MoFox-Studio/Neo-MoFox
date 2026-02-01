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
        metadatas=[{"chat_id": "12345", "timestamp": 123456789.0}],
        ids=["msg_001"]
    )

    # 3. 相似度查询
    results = await vector_db_service.query(
        collection_name="memory",
        query_embeddings=[[0.11, 0.21, 0.31]],
        n_results=5,
        where={"chat_id": "12345"}
    )
    # results 字典包含: 'ids', 'distances', 'metadatas', 'documents'
    ```
"""

from .base import VectorDBBase
from .chromadb_impl import ChromaDBImpl

# 默认数据库路径
_DEFAULT_DB_PATH = "data/chroma_db"


def get_vector_db_service(
    db_path: str = _DEFAULT_DB_PATH,
) -> VectorDBBase:
    """获取向量数据库服务实例

    Args:
        db_path: 数据库存储路径，默认为 "data/chroma_db"

    Returns:
        向量数据库服务实例

    Note:
        ChromaDBImpl 是一个单例，所以相同路径的调用会返回同一个实例
    """
    # TODO: 未来可以从全局配置中读取数据库类型和路径
    return ChromaDBImpl(path=db_path)


# 全局向量数据库服务实例
vector_db_service: VectorDBBase = get_vector_db_service()

__all__ = [
    "VectorDBBase",
    "ChromaDBImpl",
    "vector_db_service",
    "get_vector_db_service",
]
