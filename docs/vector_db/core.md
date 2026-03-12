# Vector DB 核心实现

## 概述

Vector DB 模块基于 ChromaDB 实现向量存储和检索。核心设计：

- **抽象接口**：VectorDBBase 定义统一协议
- **具体实现**：ChromaDBImpl 提供 ChromaDB 集成
- **全局服务**：get_vector_db_service() 管理实例生命周期
- **异步操作**：所有 I/O 通过 asyncio 进行

---

## VectorDBBase 抽象接口

### 设计目的

定义所有向量数据库实现必须遵循的标准接口，实现平台无关的向量存储和检索能力。

```python
class VectorDBBase(ABC):
    """向量数据库抽象基类"""
    
    @abstractmethod
    async def initialize(self, path: str, **kwargs: Any) -> None:
        """初始化数据库"""
        pass
    
    @abstractmethod
    async def add(self, collection_name: str, ...) -> None:
        """添加向量"""
        pass
    
    @abstractmethod
    async def query(self, collection_name: str, ...) -> dict:
        """查询向量"""
        pass
```

### 核心方法

| 方法 | 功能 | 异步? |
|------|------|-------|
| initialize() | 初始化连接 | ✓ |
| get_or_create_collection() | 集合管理 | ✓ |
| add() | 添加向量 | ✓ |
| query() | 相似度查询 | ✓ |
| get() | 按条件获取 | ✓ |
| delete() | 删除数据 | ✓ |
| count() | 统计条数 | ✓ |
| delete_collection() | 删除集合 | ✓ |
| close() | 关闭连接 | ✓ |

---

## ChromaDBImpl 实现

### 架构

```python
class ChromaDBImpl(VectorDBBase):
    _path: str                          # 数据库路径
    _client: ClientAPI | None           # ChromaDB 客户端
    _collections: dict[str, Any]        # 集合缓存
    _initialized: bool                  # 初始化状态
    _init_lock: asyncio.Lock            # 初始化锁
```

### 初始化流程

```python
async def initialize(self, path: str, **kwargs: Any) -> None:
    """
    流程：
    1. 获取初始化锁（防止并发初始化）
    2. 检查已初始化
    3. 在线程中创建 ChromaDB 客户端
    4. 标记初始化完成
    """
    async with self._init_lock:
        if self._initialized:
            if path != self._path:
                raise ValueError("已初始化于不同路径")
            return
        
        try:
            self._client = await asyncio.to_thread(
                chromadb.PersistentClient,
                path=path,
                settings=Settings(anonymized_telemetry=False),
            )
            self._path = path
            self._initialized = True
        except Exception as e:
            raise ConnectionError(f"初始化失败: {e}") from e
```

**关键点**：

1. **双重检查锁**：避免重复初始化
2. **线程执行**：ChromaDB 是同步的，使用 `asyncio.to_thread()` 在线程池中执行
3. **异常转换**：将底层异常转换为 ConnectionError

---

### 集合管理

#### get_or_create_collection()

```python
async def get_or_create_collection(self, name: str, **kwargs: Any) -> Any:
    """
    流程：
    1. 确保已初始化
    2. 检查缓存
    3. 从 ChromaDB 获取或创建
    4. 缓存集合对象
    """
    if not self._initialized or not self._client:
        await self.initialize(self._path)
    
    if name in self._collections:
        return self._collections[name]
    
    try:
        collection = await asyncio.to_thread(
            self._client.get_or_create_collection,
            name,
            **kwargs,
        )
        self._collections[name] = collection
        return collection
    except Exception as e:
        logger.error(f"获取或创建集合失败: {e}")
        return None
```

**集合缓存**：
- 减少 ChromaDB 调用
- 加快集合获取速度
- 需要手动清理（delete_collection 时）

---

### 添加向量

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
    流程：
    1. 获取集合
    2. 验证输入
    3. 在线程中调用 ChromaDB add()
    4. 错误处理和日志
    """
    collection = await self.get_or_create_collection(collection_name)
    if collection:
        try:
            await asyncio.to_thread(
                collection.add,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
        except Exception as e:
            logger.error(f"添加数据失败: {e}")
```

**参数说明**：

- `embeddings`：必需，向量列表
- `documents`：可选，与向量关联的文本
- `metadatas`：可选，每个向量的元数据
- `ids`：可选，自定义 ID（不指定则自动生成）

---

### 相似度查询

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
    流程：
    1. 获取集合
    2. 构建查询参数
    3. 处理 where 条件
    4. 在线程中执行查询
    5. 返回结果
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
        
        if where:
            processed_where = self._process_where_condition(where)
            if processed_where:
                query_params["where"] = processed_where
        
        return await asyncio.to_thread(collection.query, **query_params)
    except Exception as e:
        logger.error(f"查询失败: {e}")
    return {}
```

**返回结果格式**：

```python
{
    'ids': [['id1', 'id2', ...]],           # ID 列表（二维）
    'distances': [[0.1, 0.2, ...]],        # 距离值
    'metadatas': [[{...}, {...}, ...]],    # 元数据
    'documents': [['doc1', 'doc2', ...]]   # 文档内容
}
```

---

### Where 条件处理

```python
def _process_where_condition(self, where: dict[str, Any]) -> dict[str, Any] | None:
    """
    转换自定义 where 条件为 ChromaDB 格式。
    
    支持：
    - 简单条件：{"field": "value"}
    - 列表条件：{"field": [value1, value2]}  → {"field": {"$in": [...]}}
    - 多字段条件：{"field1": value1, "field2": value2}  → {"$and": [...]}
    """
    if not where:
        return None
    
    try:
        if len(where) == 1:
            key, value = next(iter(where.items()))
            
            # 列表值转换为 $in
            if isinstance(value, list):
                if len(value) == 1:
                    return {key: value[0]}
                else:
                    return {key: {"$in": value}}
            else:
                return {key: value}
        
        # 多字段用 $and 连接
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
        logger.warning(f"处理 where 条件失败: {e}")
        return None
```

**转换例子**：

```python
# 输入
where = {"user_id": "user_123"}
# 输出
{"user_id": "user_123"}

# 输入
where = {"status": ["pending", "done"]}
# 输出
{"status": {"$in": ["pending", "done"]}}

# 输入
where = {"user_id": "user_123", "status": ["pending"]}
# 输出
{"$and": [{"user_id": "user_123"}, {"status": "pending"}]}
```

---

### 获取数据

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
    获取数据（不进行相似度排序）。
    
    与 query() 区别：
    - query()：执行相似度搜索，返回相似的向量
    - get()：按条件过滤，返回匹配的数据
    """
    collection = await self.get_or_create_collection(collection_name)
    if not collection:
        return {}
    
    try:
        processed_where = None
        if where:
            processed_where = self._process_where_condition(where)
        
        return await asyncio.to_thread(
            collection.get,
            ids=ids,
            where=processed_where,
            limit=limit,
            offset=offset,
            where_document=where_document,
            include=include or ["documents", "metadatas", "embeddings"],
        )
    except Exception as e:
        logger.error(f"获取数据失败: {e}")
    return {}
```

---

### 删除数据

```python
async def delete(
    self,
    collection_name: str,
    ids: list[str] | None = None,
    where: dict[str, Any] | None = None,
) -> None:
    """按 ID 或条件删除数据"""
    collection = await self.get_or_create_collection(collection_name)
    if collection:
        try:
            await asyncio.to_thread(collection.delete, ids=ids, where=where)
        except Exception as e:
            logger.error(f"删除数据失败: {e}")
```

---

### 其他操作

#### count()

```python
async def count(self, collection_name: str) -> int:
    """统计集合中的条目数"""
    collection = await self.get_or_create_collection(collection_name)
    if collection:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.count()
            )
        except Exception as e:
            logger.error(f"统计失败: {e}")
    return 0
```

#### delete_collection()

```python
async def delete_collection(self, name: str) -> None:
    """删除整个集合"""
    if not self._initialized or not self._client:
        await self.initialize(self._path)
    
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.delete_collection(name=name)
        )
        if name in self._collections:
            del self._collections[name]
    except Exception as e:
        logger.error(f"删除集合失败: {e}")
```

#### close()

```python
async def close(self) -> None:
    """关闭数据库连接"""
    if self._initialized:
        self._collections.clear()
        self._client = None
        self._initialized = False
```

---

## 全局服务管理

### get_vector_db_service()

```python
_vector_db_by_path: dict[str, VectorDBBase] = {}
_vector_db_lock = Lock()

def get_vector_db_service(
    db_path: str = _DEFAULT_DB_PATH,
    use_cache: bool = True,
) -> VectorDBBase:
    """
    获取或创建向量数据库实例。
    
    缓存策略：
    - 按 db_path 缓存实例
    - 支持多路径共存
    - use_cache=False 时不缓存
    """
    if not use_cache:
        return ChromaDBImpl(path=db_path)
    
    key = _normalize_db_path(db_path)
    with _vector_db_lock:
        instance = _vector_db_by_path.get(key)
        if instance is None:
            instance = ChromaDBImpl(path=db_path)
            _vector_db_by_path[key] = instance
        return instance
```

### 路径规范化

```python
def _normalize_db_path(db_path: str) -> str:
    """统一路径格式，避免重复缓存"""
    return os.path.normpath(os.path.abspath(db_path))
```

**目的**：
- `"data/chroma_db"` 和 `"data/./chroma_db"` 视为同一路径
- 避免同一数据库被创建多个实例

---

## 异步 I/O 模式

### 线程池执行

ChromaDB 是同步库，所有调用都在线程池中执行：

```python
# 方式1：asyncio.to_thread()
result = await asyncio.to_thread(synchronous_function, arg1, arg2)

# 方式2：run_in_executor()
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, synchronous_function, arg1)
```

**优点**：
- 不阻塞事件循环
- 支持高并发
- ChromaDB 操作透明

**缺点**：
- 线程池开销
- 对于小操作可能不值得

---

## 错误处理策略

### 异常链

```python
try:
    # ChromaDB 操作
except Exception as e:
    logger.error(f"操作失败: {e}")
    # 某些操作返回默认值
    return {} or 0 or None
```

**原则**：
- 错误被记录
- 不会崩溃（防御式）
- 返回合理的默认值

---

## 性能优化

### 1. 集合缓存

```python
self._collections: dict[str, Any] = {}
```

**效果**：
- 避免重复创建集合对象
- 快速访问

### 2. 批量操作

```python
# 单次添加多个向量
await vdb.add(
    embeddings=[vec1, vec2, vec3, ...],
    documents=[doc1, doc2, doc3, ...],
)
```

**优于**：
```python
# 多次单向量添加
for vec in vectors:
    await vdb.add(embeddings=[vec], ...)
```

### 3. 条件预过滤

```python
# where 条件减少搜索范围
results = await vdb.query(
    ...,
    where={"user_id": "user_123"}  # 先按 user_id 过滤
)
```

---

## 与 VectorDBBase 的多态设计

### 扩展新后端

```python
class PineconeImpl(VectorDBBase):
    """Pinecone 实现"""
    
    async def initialize(self, path: str, **kwargs):
        # 连接到 Pinecone
        pass
    
    async def add(self, collection_name, ...):
        # Pinecone add() 逻辑
        pass
    
    # ... 其他方法
```

**无需改变调用代码**：
```python
# 只需修改 get_vector_db_service() 的工厂逻辑
def get_vector_db_service(...) -> VectorDBBase:
    if backend == "pinecone":
        return PineconeImpl(...)
    else:
        return ChromaDBImpl(...)
```

---

## 相关资源

- [Vector DB 主文档](./README.md) - API 和使用方法
- [高级用法](./advanced.md) - 优化和模式
- [Storage 模块](../storage/README.md) - 本地持久化
