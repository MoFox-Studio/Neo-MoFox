# Vector DB 高级用法

## 概述

本文档介绍 Vector DB 的高级模式、性能优化和超大规模应用。

---

## 1. 多向量存储

### 模式 1A: 多种 Embedding 模型

存储相同文本的多种 embedding 表示。

```python
from src.kernel.vector_db import get_vector_db_service

async def store_multi_embedding(text, doc_id):
    """使用多种模型对文本编码"""
    
    vdb = get_vector_db_service()
    
    # 使用不同模型编码
    embedding_dense = dense_model.embed(text)      # 1536 维
    embedding_sparse = sparse_model.embed(text)    # 10000 维
    embedding_cross_encoder = cross_encoder.embed(text)  # 384 维
    
    # 存储到不同集合
    await vdb.add(
        collection_name="dense_embeddings",
        embeddings=[embedding_dense],
        documents=[text],
        metadatas=[{"doc_id": doc_id, "model": "dense"}],
        ids=[f"{doc_id}_dense"]
    )
    
    await vdb.add(
        collection_name="sparse_embeddings",
        embeddings=[embedding_sparse],
        documents=[text],
        metadatas=[{"doc_id": doc_id, "model": "sparse"}],
        ids=[f"{doc_id}_sparse"]
    )

async def hybrid_search(query_text, query_embeddings):
    """混合搜索：结合多种 embedding 模型"""
    
    vdb = get_vector_db_service()
    
    # 在多个集合中搜索
    results_dense = await vdb.query(
        collection_name="dense_embeddings",
        query_embeddings=[query_embeddings["dense"]],
        n_results=10
    )
    
    results_sparse = await vdb.query(
        collection_name="sparse_embeddings",
        query_embeddings=[query_embeddings["sparse"]],
        n_results=10
    )
    
    # 合并和重排结果
    combined = merge_results(results_dense, results_sparse)
    return combined
```

### 模式 1B: 多语言支持

```python
async def store_multilingual(text, translations, doc_id):
    """存储多语言文本的 embedding"""
    
    vdb = get_vector_db_service()
    
    for lang, translated_text in translations.items():
        embedding = multilingual_model.embed(translated_text, language=lang)
        
        await vdb.add(
            collection_name=f"embeddings_{lang}",
            embeddings=[embedding],
            documents=[translated_text],
            metadatas=[{
                "original_id": doc_id,
                "language": lang
            }],
            ids=[f"{doc_id}_{lang}"]
        )

async def multilingual_search(query, languages):
    """多语言搜索"""
    
    vdb = get_vector_db_service()
    all_results = []
    
    for lang in languages:
        query_translated = translator.translate(query, to_lang=lang)
        embedding = multilingual_model.embed(query_translated, language=lang)
        
        results = await vdb.query(
            collection_name=f"embeddings_{lang}",
            query_embeddings=[embedding],
            n_results=5
        )
        all_results.extend(results["documents"][0])
    
    return all_results
```

---

## 2. 分层向量存储

### 模式 2A: 粗粒度 + 细粒度

```python
async def hierarchical_store(document):
    """分层存储：先存文档级别，再存段落级别"""
    
    vdb = get_vector_db_service()
    
    # 1. 存储整个文档（粗粒度）
    doc_embedding = embedding_model.embed(document["title"])
    
    await vdb.add(
        collection_name="documents",
        embeddings=[doc_embedding],
        documents=[document["title"]],
        metadatas=[{
            "type": "document",
            "doc_id": document["id"]
        }],
        ids=[f"doc_{document['id']}"]
    )
    
    # 2. 存储段落（细粒度）
    for i, paragraph in enumerate(document["paragraphs"]):
        para_embedding = embedding_model.embed(paragraph)
        
        await vdb.add(
            collection_name="paragraphs",
            embeddings=[para_embedding],
            documents=[paragraph],
            metadatas=[{
                "type": "paragraph",
                "doc_id": document["id"],
                "para_index": i
            }],
            ids=[f"para_{document['id']}_{i}"]
        )

async def hierarchical_search(query):
    """分层搜索"""
    
    vdb = get_vector_db_service()
    
    query_embedding = embedding_model.embed(query)
    
    # 1. 先在文档级别搜索
    doc_results = await vdb.query(
        collection_name="documents",
        query_embeddings=[query_embedding],
        n_results=3
    )
    
    # 2. 在匹配文档中的段落中搜索
    relevant_doc_ids = [meta["doc_id"] for meta in doc_results["metadatas"][0]]
    
    para_results = await vdb.query(
        collection_name="paragraphs",
        query_embeddings=[query_embedding],
        n_results=10,
        where={"doc_id": relevant_doc_ids}
    )
    
    return para_results
```

---

## 3. 超大规模应用

### 模式 3A: 分片存储

```python
async def store_large_corpus(corpus, shard_size=10000):
    """将大规模语料库分片存储"""
    
    vdb = get_vector_db_service()
    
    shards = {}
    for i, doc in enumerate(corpus):
        shard_id = i // shard_size
        
        if shard_id not in shards:
            shards[shard_id] = []
        
        shards[shard_id].append(doc)
    
    # 为每个分片创建集合
    for shard_id, docs in shards.items():
        collection_name = f"corpus_shard_{shard_id}"
        
        embeddings = [embedding_model.embed(doc["text"]) for doc in docs]
        documents = [doc["text"] for doc in docs]
        metadatas = [{"shard": shard_id, "doc_id": doc["id"]} for doc in docs]
        ids = [f"shard_{shard_id}_{doc['id']}" for doc in docs]
        
        await vdb.add(
            collection_name=collection_name,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )

async def search_sharded_corpus(query, total_shards):
    """跨分片搜索"""
    
    vdb = get_vector_db_service()
    query_embedding = embedding_model.embed(query)
    
    all_results = []
    
    for shard_id in range(total_shards):
        collection_name = f"corpus_shard_{shard_id}"
        
        results = await vdb.query(
            collection_name=collection_name,
            query_embeddings=[query_embedding],
            n_results=5
        )
        
        all_results.extend(results["documents"][0])
    
    # 重新排序
    return sorted(all_results, key=lambda x: score(query, x))
```

### 模式 3B: 增量索引

```python
class IncrementalIndexer:
    """增量索引管理"""
    
    def __init__(self):
        self.vdb = get_vector_db_service()
        self.indexed_ids = set()
    
    async def index_new_documents(self, new_docs):
        """仅索引新文档"""
        
        to_index = [d for d in new_docs if d["id"] not in self.indexed_ids]
        
        if not to_index:
            return
        
        embeddings = [embedding_model.embed(doc["text"]) for doc in to_index]
        documents = [doc["text"] for doc in to_index]
        ids = [f"doc_{doc['id']}" for doc in to_index]
        
        await self.vdb.add(
            collection_name="knowledge_base",
            embeddings=embeddings,
            documents=documents,
            ids=ids
        )
        
        # 更新索引集合
        self.indexed_ids.update(d["id"] for d in to_index)
    
    async def load_checkpoint(self):
        """从检查点恢复"""
        # 可从存储中加载已索引 ID
        pass
```

---

## 4. 元数据优化

### 模式 4A: 复杂元数据查询

```python
async def query_with_complex_filters(query_text):
    """复杂元数据过滤"""
    
    vdb = get_vector_db_service()
    query_embedding = embedding_model.embed(query_text)
    
    # 多条件过滤：特定用户、特定时间范围、特定类型
    where = {
        "user_id": "user_123",
        "timestamp": {"$gte": start_time, "$lte": end_time},
        "doc_type": ["article", "blog"]  # 多选
    }
    
    results = await vdb.query(
        collection_name="documents",
        query_embeddings=[query_embedding],
        n_results=10,
        where=where
    )
    
    return results
```

### 模式 4B: 动态元数据

```python
async def add_with_dynamic_metadata(text, doc_id):
    """动态生成元数据"""
    
    vdb = get_vector_db_service()
    
    # 动态生成元数据
    metadata = {
        "doc_id": doc_id,
        "timestamp": time.time(),
        "length": len(text),
        "language": detect_language(text),
        "importance": calculate_importance(text),
        "tags": extract_tags(text)
    }
    
    embedding = embedding_model.embed(text)
    
    await vdb.add(
        collection_name="documents",
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata],
        ids=[f"doc_{doc_id}"]
    )
```

---

## 5. 混合检索

### 模式 5A: 语义 + 关键词混合

```python
async def hybrid_retrieval(query):
    """语义搜索 + 关键词搜索混合"""
    
    vdb = get_vector_db_service()
    
    # 1. 语义搜索
    query_embedding = embedding_model.embed(query)
    semantic_results = await vdb.query(
        collection_name="documents",
        query_embeddings=[query_embedding],
        n_results=20
    )
    
    # 2. 关键词搜索（在 where_document 中）
    keywords = extract_keywords(query)
    keyword_results = await vdb.get(
        collection_name="documents",
        where_document={"$contains": keywords[0]},
        limit=20
    )
    
    # 3. 结合两种结果
    combined = merge_results(
        semantic_results,
        keyword_results,
        weights={"semantic": 0.7, "keyword": 0.3}
    )
    
    return combined
```

### 模式 5B: 多阶段检索

```python
async def multi_stage_retrieval(query):
    """多阶段检索：粗排 + 精排"""
    
    vdb = get_vector_db_service()
    query_embedding = embedding_model.embed(query)
    
    # 阶段1：粗排（快速，召回率高）
    candidates = await vdb.query(
        collection_name="documents",
        query_embeddings=[query_embedding],
        n_results=100
    )
    
    # 阶段2：精排（慢但准确）
    refined_results = []
    for doc in candidates["documents"][0]:
        score = calculate_relevance_score(query, doc)
        refined_results.append((doc, score))
    
    # 按分数排序，返回top-10
    final_results = sorted(refined_results, key=lambda x: x[1], reverse=True)[:10]
    
    return final_results
```

---

## 6. 实时更新

### 模式 6A: 增量更新

```python
async def update_document(doc_id, updated_text):
    """更新文档"""
    
    vdb = get_vector_db_service()
    
    # 1. 删除旧数据
    await vdb.delete(
        collection_name="documents",
        ids=[f"doc_{doc_id}"]
    )
    
    # 2. 添加新数据
    embedding = embedding_model.embed(updated_text)
    
    await vdb.add(
        collection_name="documents",
        embeddings=[embedding],
        documents=[updated_text],
        metadatas=[{"doc_id": doc_id, "updated_at": time.time()}],
        ids=[f"doc_{doc_id}"]
    )
```

### 模式 6B: 批量更新

```python
async def batch_update(updates):
    """批量更新"""
    
    vdb = get_vector_db_service()
    
    # 1. 批量删除
    ids_to_delete = [f"doc_{doc_id}" for doc_id, _ in updates]
    await vdb.delete(
        collection_name="documents",
        ids=ids_to_delete
    )
    
    # 2. 批量添加
    embeddings = [embedding_model.embed(text) for _, text in updates]
    documents = [text for _, text in updates]
    ids = [f"doc_{doc_id}" for doc_id, _ in updates]
    
    await vdb.add(
        collection_name="documents",
        embeddings=embeddings,
        documents=documents,
        ids=ids
    )
```

---

## 7. 缓存策略

### 模式 7A: 查询结果缓存

```python
from functools import lru_cache
import hashlib

class QueryCache:
    """查询结果缓存"""
    
    def __init__(self, max_cache=1000, ttl=3600):
        self.cache = {}
        self.max_cache = max_cache
        self.ttl = ttl
        self.timestamps = {}
    
    def _cache_key(self, collection, embedding):
        """生成缓存键"""
        embedding_str = str(embedding)
        return hashlib.md5(
            f"{collection}:{embedding_str}".encode()
        ).hexdigest()
    
    async def query(self, vdb, collection, embedding, n_results):
        """带缓存的查询"""
        
        key = self._cache_key(collection, embedding)
        now = time.time()
        
        # 检查缓存
        if key in self.cache:
            if now - self.timestamps[key] < self.ttl:
                return self.cache[key]
        
        # 缓存未命中，执行查询
        results = await vdb.query(
            collection_name=collection,
            query_embeddings=[embedding],
            n_results=n_results
        )
        
        # 存储缓存
        if len(self.cache) >= self.max_cache:
            # LRU：删除最早的缓存
            oldest = min(self.timestamps, key=self.timestamps.get)
            del self.cache[oldest]
            del self.timestamps[oldest]
        
        self.cache[key] = results
        self.timestamps[key] = now
        
        return results
```

---

## 8. 监控和度量

### 模式 8A: 集合统计

```python
async def collection_stats(vdb, collection_name):
    """获取集合统计"""
    
    count = await vdb.count(collection_name)
    
    # 获取样本数据统计
    sample = await vdb.get(
        collection_name=collection_name,
        limit=100,
        include=["embeddings"]
    )
    
    if sample["embeddings"]:
        embeddings = sample["embeddings"]
        # 计算向量维度
        vector_dim = len(embeddings[0])
        
        return {
            "total_count": count,
            "vector_dimension": vector_dim,
            "sample_size": len(embeddings),
            "storage_size_mb": count * vector_dim * 4 / (1024 * 1024)
        }
```

### 模式 8B: 查询性能监控

```python
import time

class QueryPerformanceMonitor:
    """查询性能监控"""
    
    def __init__(self):
        self.metrics = {
            "query_count": 0,
            "total_time": 0,
            "avg_time": 0,
            "min_time": float('inf'),
            "max_time": 0
        }
    
    async def monitored_query(self, vdb, **kwargs):
        """带监控的查询"""
        
        start = time.time()
        
        try:
            results = await vdb.query(**kwargs)
            return results
        finally:
            elapsed = time.time() - start
            
            self.metrics["query_count"] += 1
            self.metrics["total_time"] += elapsed
            self.metrics["avg_time"] = self.metrics["total_time"] / self.metrics["query_count"]
            self.metrics["min_time"] = min(self.metrics["min_time"], elapsed)
            self.metrics["max_time"] = max(self.metrics["max_time"], elapsed)
```

---

## 最佳实践总结

### DO ✓

1. **为不同业务使用不同集合**
   ```python
   await vdb.get_or_create_collection("chat_memory")
   await vdb.get_or_create_collection("knowledge_base")
   ```

2. **使用元数据过滤减少搜索范围**
   ```python
   where = {"user_id": user_id}  # 先过滤
   ```

3. **批量操作**
   ```python
   embeddings = [...]
   await vdb.add(embeddings=embeddings, ...)
   ```

4. **监控集合大小**
   ```python
   count = await vdb.count(collection)
   ```

### DON'T ✗

1. **不要在未初始化时使用**
   ```python
   vdb = get_vector_db_service()
   await vdb.initialize(...)  # 必须先初始化
   ```

2. **不要混合维度不同的向量**
   ```python
   # 错误
   embeddings = [[0.1, 0.2], [0.3, 0.4, 0.5]]
   ```

3. **不要频繁创建新实例**
   ```python
   # 错误
   for i in range(1000):
       vdb = get_vector_db_service(use_cache=False)
   
   # 正确
   vdb = get_vector_db_service()
   ```

4. **不要忽视错误处理**
   ```python
   # 需要处理
   try:
       results = await vdb.query(...)
   except Exception as e:
       logger.error(f"查询失败: {e}")
   ```

---

## 相关资源

- [Vector DB 主文档](./README.md) - API 和基本用法
- [核心实现](./core.md) - 内部机制
- [Storage 模块](../storage/README.md) - 本地持久化
