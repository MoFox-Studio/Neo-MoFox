# Vector DB 向量数据库

Neo-MoFox 框架中的向量存储和检索服务，使用 ChromaDB 作为后端。支持**多集合隔离、元数据过滤和相似度查询**。

## 核心特性

### 1. 向量存储和检索

- 存储**高维向量**（通常 384-1536 维）
- 快速**相似度搜索**（基于欧几里得距离或余弦相似度）
- 支持**批量操作**

### 2. 集合隔离

- 不同业务模块使用不同 collection
- 物理隔离，避免数据混淆
- 支持灵活的集合管理

### 3. 元数据支持

- 每个向量关联**元数据**（字典形式）
- 支持**条件过滤**（where 子句）
- 灵活的查询能力

### 4. 文本绑定

- 每个向量可绑定**源文本**
- 查询结果可返回原文本
- 便于语义理解

### 5. 多后端支持

- 当前基于 **ChromaDB**
- 抽象接口 **VectorDBBase**
- 易于扩展其他后端

---

## 快速开始

### 基本用法

```python
from src.kernel.vector_db import get_vector_db_service

# 获取向量数据库实例
vdb = get_vector_db_service()

# 初始化（首次必须调用）
await vdb.initialize("data/chroma_db")

# 获取或创建集合
await vdb.get_or_create_collection("semantic_cache")

# 添加向量数据
await vdb.add(
    collection_name="semantic_cache",
    embeddings=[[0.1, 0.2, 0.3], [0.2, 0.3, 0.4]],
    documents=["Hello world", "Good morning"],
    metadatas=[
        {"stream_id": "chat_1", "timestamp": 1000},
        {"stream_id": "chat_2", "timestamp": 2000}
    ],
    ids=["msg_001", "msg_002"]
)

# 相似度查询
results = await vdb.query(
    collection_name="semantic_cache",
    query_embeddings=[[0.11, 0.21, 0.31]],
    n_results=5,
    where={"chat_id": "chat_1"}
)

print(results)
# {
#   'ids': [['msg_001']],
#   'distances': [[0.015...]],
#   'metadatas': [[{'chat_id': 'chat_1', ...}]],
#   'documents': [['Hello world']]
# }
```

### 使用全局服务

```python
from src.kernel.vector_db import get_vector_db_service

# 获取缓存的全局实例
vdb = get_vector_db_service()

# 首次初始化
await vdb.initialize("data/chroma_db")

# 后续使用
await vdb.add(...)
results = await vdb.query(...)
```

---

## 核心概念

### VectorDB 的三个层次

```
应用层
  ↓
VectorDBService (get_vector_db_service)
  ↓
VectorDBBase (抽象接口)
  ↓
ChromaDBImpl (具体实现)
  ↓
ChromaDB 客户端
```

### Collection（集合）

向量数据的容器。

```python
# 不同业务模块使用不同集合
await vdb.get_or_create_collection("chat_memory")      # 聊天记忆
await vdb.get_or_create_collection("knowledge_base")   # 知识库
await vdb.get_or_create_collection("semantic_cache")   # 语义缓存
```

### Embedding（嵌入向量）

文本或数据的向量表示。

```python
# 使用 embedding 模型转换文本为向量
text = "How are you?"
embedding = model.embed_text(text)  # [0.1, 0.2, 0.3, ...]

# 向量通常由 embedding 模型生成
embeddings = [embedding1, embedding2, ...]
```

### Metadata（元数据）

每个向量的附加信息。

```python
metadata = {
    "chat_id": "chat_123",
    "user_id": "user_456",
    "timestamp": 1704067200.0,
    "source": "api"
}
```

---

## API 参考

### get_vector_db_service()

获取向量数据库服务实例。

```python
def get_vector_db_service(
    db_path: str = "data/chroma_db",
    use_cache: bool = True,
) -> VectorDBBase:
    """
    获取向量数据库实例。
    
    Args:
        db_path: 数据库存储路径
        use_cache: 是否使用缓存（默认 True）
    
    Returns:
        VectorDBBase 实例
    
    Note:
        默认会按 db_path 缓存实例，支持多路径并存
    """
    pass
```

**使用示例**：

```python
# 默认缓存（推荐）
vdb = get_vector_db_service()

# 自定义路径且缓存
vdb = get_vector_db_service("data/my_vectors")

# 不使用缓存（每次创建新实例）
vdb = get_vector_db_service(use_cache=False)
```

### initialize()

初始化数据库连接。

```python
async def initialize(self, path: str, **kwargs: Any) -> None:
    """
    异步初始化 ChromaDB 客户端。
    
    Args:
        path: 数据库存储路径
        **kwargs: 其他配置参数
    
    Raises:
        ConnectionError: 初始化失败
    """
    pass
```

**使用示例**：

```python
vdb = get_vector_db_service()
await vdb.initialize("data/chroma_db")
```

### get_or_create_collection()

获取或创建集合。

```python
async def get_or_create_collection(
    self,
    name: str,
    **kwargs: Any,
) -> Any:
    """
    Args:
        name: 集合名称
        **kwargs: 集合配置
    
    Returns:
        集合对象
    """
    pass
```

**使用示例**：

```python
# 获取或创建集合
collection = await vdb.get_or_create_collection("chat_memory")
```

### add()

添加向量数据。

```python
async def add(
    self,
    collection_name: str,
    embeddings: list[list[float]],
    documents: list[str] | None = None,
    metadatas: list[dict[str, Any]] | None = None,
    ids: list[str] | None = None,
) -> None:
    """
    Args:
        collection_name: 集合名称
        embeddings: 向量列表
        documents: 文档列表（可选）
        metadatas: 元数据列表（可选）
        ids: ID 列表（可选，不指定则自动生成）
    """
    pass
```

**使用示例**：

```python
await vdb.add(
    collection_name="memories",
    embeddings=[[0.1, 0.2], [0.3, 0.4]],
    documents=["First message", "Second message"],
    metadatas=[
        {"sender": "user"},
        {"sender": "assistant"}
    ],
    ids=["msg_1", "msg_2"]
)
```

### query()

相似度查询。

```python
async def query(
    self,
    collection_name: str,
    query_embeddings: list[list[float]],
    n_results: int = 1,
    where: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, list[Any]]:
    """
    Args:
        collection_name: 集合名称
        query_embeddings: 查询向量列表
        n_results: 返回结果数量
        where: 元数据过滤条件
        **kwargs: 其他参数
    
    Returns:
        {
            'ids': [[...]],
            'distances': [[...]],
            'metadatas': [[...]],
            'documents': [[...]]
        }
    """
    pass
```

**使用示例**：

```python
# 简单查询
results = await vdb.query(
    collection_name="memories",
    query_embeddings=[[0.12, 0.22]],
    n_results=10
)

# 带过滤条件的查询
results = await vdb.query(
    collection_name="memories",
    query_embeddings=[[0.12, 0.22]],
    n_results=5,
    where={"sender": "user"}  # 只查询用户消息
)
```

### get()

根据条件获取数据。

```python
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
    """
    Args:
        collection_name: 集合名称
        ids: 指定 ID 列表
        where: 元数据过滤
        limit: 返回数量限制
        offset: 返回偏移量
        where_document: 文档内容过滤
        include: 返回的字段 ['documents', 'metadatas', 'embeddings']
    
    Returns:
        匹配的数据字典
    """
    pass
```

**使用示例**：

```python
# 按 ID 获取
data = await vdb.get(
    collection_name="memories",
    ids=["msg_1", "msg_2"],
    include=["documents", "metadatas"]
)

# 按条件获取
data = await vdb.get(
    collection_name="memories",
    where={"sender": "user"},
    limit=10,
    include=["documents"]
)
```

### delete()

删除数据。

```python
async def delete(
    self,
    collection_name: str,
    ids: list[str] | None = None,
    where: dict[str, Any] | None = None,
) -> None:
    """
    Args:
        collection_name: 集合名称
        ids: 按 ID 删除
        where: 按条件删除
    """
    pass
```

**使用示例**：

```python
# 按 ID 删除
await vdb.delete(
    collection_name="memories",
    ids=["msg_1"]
)

# 按条件删除
await vdb.delete(
    collection_name="memories",
    where={"sender": "assistant"}
)
```

### count()

统计集合中的数据。

```python
async def count(self, collection_name: str) -> int:
    """
    Returns:
        集合中的条目总数
    """
    pass
```

**使用示例**：

```python
total = await vdb.count("memories")
print(f"总条数: {total}")
```

### delete_collection()

删除整个集合。

```python
async def delete_collection(self, name: str) -> None:
    """
    Args:
        name: 集合名称
    """
    pass
```

### close()

关闭数据库连接。

```python
async def close(self) -> None:
    """关闭连接并清理资源"""
    pass
```

---

## 使用场景

### 场景 1: 语义缓存

```python
async def cache_semantic_response(query, response, embedding):
    """缓存 LLM 响应"""
    
    vdb = get_vector_db_service()
    
    await vdb.add(
        collection_name="semantic_cache",
        embeddings=[embedding],
        documents=[response],
        metadatas=[{
            "query": query,
            "timestamp": time.time()
        }],
        ids=[f"response_{query_hash}"]
    )

async def query_semantic_cache(query_embedding):
    """查询缓存中的相似回复"""
    
    vdb = get_vector_db_service()
    
    results = await vdb.query(
        collection_name="semantic_cache",
        query_embeddings=[query_embedding],
        n_results=1
    )
    
    if results['documents']:
        return results['documents'][0][0]
    
    return None
```

### 场景 2: 知识库检索

```python
async def add_to_knowledge_base(document, chunks_with_embeddings):
    """添加文档块到知识库"""
    
    vdb = get_vector_db_service()
    
    embeddings = [item['embedding'] for item in chunks_with_embeddings]
    documents = [item['text'] for item in chunks_with_embeddings]
    metadatas = [{
        "document_id": document['id'],
        "source": document['source'],
        "chunk_index": i
    } for i in range(len(chunks_with_embeddings))]
    
    await vdb.add(
        collection_name="knowledge_base",
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
        ids=[f"chunk_{document['id']}_{i}" for i in range(len(chunks_with_embeddings))]
    )

async def retrieve_from_knowledge_base(query_embedding, top_k=5):
    """检索相关知识"""
    
    vdb = get_vector_db_service()
    
    results = await vdb.query(
        collection_name="knowledge_base",
        query_embeddings=[query_embedding],
        n_results=top_k
    )
    
    return results['documents'][0]  # 返回相关文档块
```

### 场景 3: 长期记忆

```python
async def add_memory(content, embedding, memory_type):
    """添加长期记忆"""
    
    vdb = get_vector_db_service()
    
    await vdb.add(
        collection_name="long_term_memory",
        embeddings=[embedding],
        documents=[content],
        metadatas=[{
            "type": memory_type,
            "timestamp": time.time(),
            "importance": 0.5
        }],
        ids=[generate_id()]
    )

async def retrieve_relevant_memories(query_embedding, memory_type=None):
    """检索相关记忆"""
    
    vdb = get_vector_db_service()
    
    where = None
    if memory_type:
        where = {"type": memory_type}
    
    results = await vdb.query(
        collection_name="long_term_memory",
        query_embeddings=[query_embedding],
        n_results=10,
        where=where
    )
    
    return results['documents'][0]
```

---

## 元数据过滤

### 支持的过滤操作

```python
# 简单相等
where = {"type": "important"}

# 列表值（自动转换为 $in）
where = {"status": ["pending", "processing"]}

# 多条件 AND
where = {
    "user_id": "user_123",
    "timestamp": {"$gte": 1704067200}
}
```

### 过滤示例

```python
# 查询特定用户的消息
results = await vdb.query(
    collection_name="messages",
    query_embeddings=[embedding],
    n_results=5,
    where={"user_id": "user_123"}
)

# 查询特定时间范围的数据
results = await vdb.query(
    collection_name="logs",
    query_embeddings=[embedding],
    n_results=10,
    where={"timestamp": {"$gte": start_time, "$lte": end_time}}
)
```

---

## 最佳实践

### DO ✓

1. **为不同业务使用不同集合**
   ```python
   await vdb.get_or_create_collection("chat_memory")
   await vdb.get_or_create_collection("knowledge_base")
   ```

2. **在元数据中存储关键属性**
   ```python
   metadatas=[{
       "source": "user_message",
       "user_id": "user_123",
       "timestamp": time.time()
   }]
   ```

3. **使用有意义的 ID**
   ```python
   ids=[f"msg_{chat_id}_{timestamp}"]
   ```

4. **定期检查数据量**
   ```python
   count = await vdb.count("memories")
   ```

### DON'T ✗

1. **不要混合不同类型的数据**
   ```python
   # 错误：混合聊天和知识库
   await vdb.add(collection_name="mixed", ...)
   
   # 正确：分开存储
   await vdb.add(collection_name="chat_memory", ...)
   await vdb.add(collection_name="knowledge_base", ...)
   ```

2. **不要忘记初始化**
   ```python
   # 错误：未初始化直接使用
   vdb = get_vector_db_service()
   await vdb.add(...)  # 可能失败
   
   # 正确
   vdb = get_vector_db_service()
   await vdb.initialize("data/chroma_db")
   await vdb.add(...)
   ```

3. **不要忽视向量维度一致性**
   ```python
   # 正确：所有向量维度相同
   embeddings = [[0.1, 0.2, 0.3],  # 3 维
                 [0.4, 0.5, 0.6]]  # 3 维
   
   # 错误
   embeddings = [[0.1, 0.2],       # 2 维
                 [0.3, 0.4, 0.5]]  # 3 维
   ```

---

## 与其他模块集成

### 与 LLM 模块

```python
from src.kernel.llm import get_embedding_model
from src.kernel.vector_db import get_vector_db_service

# 获取 embedding 模型和向量数据库
embedding_model = get_embedding_model()
vdb = get_vector_db_service()

# 先转换为向量再存储
text = "Some important information"
embedding = embedding_model.embed(text)

await vdb.add(
    collection_name="knowledge",
    embeddings=[embedding],
    documents=[text]
)
```

### 与 Logger 模块

```python
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

logger = get_logger("vector_db")

vdb = get_vector_db_service()

try:
    results = await vdb.query(...)
    logger.debug(f"查询成功，返回 {len(results['ids'][0])} 条结果")
except Exception as e:
    logger.error(f"查询失败: {e}")
```

---

## 相关资源

- [核心实现细节](./core.md) - ChromaDB 和接口设计
- [高级用法](./advanced.md) - 多向量、超大规模、优化
- [Storage 模块](../storage/README.md) - 本地持久化
