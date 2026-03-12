# Storage 模块高级用法

## 概述

本文档介绍 Storage 模块的高级模式、性能优化和集成方案。

---

## 1. 缓存策略

### 模式 1A: 多层缓存

结合内存缓存和磁盘缓存。

```python
from src.kernel.storage import json_store
import time

class TwoLevelCache:
    """内存 + 磁盘两层缓存"""
    
    def __init__(self, ttl=3600):
        self._memory_cache = {}
        self._memory_ttl = ttl
        self._timestamps = {}
    
    async def get(self, key):
        # L1: 内存缓存
        if key in self._memory_cache:
            if time.time() - self._timestamps[key] < self._memory_ttl:
                return self._memory_cache[key]
        
        # L2: 磁盘缓存
        data = await json_store.load(key)
        if data:
            self._memory_cache[key] = data
            self._timestamps[key] = time.time()
        
        return data
    
    async def set(self, key, value):
        # 同时更新两层缓存
        self._memory_cache[key] = value
        self._timestamps[key] = time.time()
        await json_store.save(key, value)
    
    async def delete(self, key):
        self._memory_cache.pop(key, None)
        self._timestamps.pop(key, None)
        await json_store.delete(key)

# 使用示例
cache = TwoLevelCache(ttl=300)

# 第一次加载：从磁盘读取
config = await cache.get("app_config")

# 第二次加载：从内存获取（快速）
config = await cache.get("app_config")

# TTL 过期后重新从磁盘加载
```

### 模式 1B: LRU 缓存

限制内存占用的缓存。

```python
from functools import lru_cache
from src.kernel.storage import json_store

class LRUStorage:
    """带 LRU 的存储层"""
    
    def __init__(self, max_memory_items=100):
        self._cache = {}
        self._max_items = max_memory_items
    
    async def get(self, key):
        # 内存命中
        if key in self._cache:
            return self._cache[key]
        
        # 磁盘读取
        data = await json_store.load(key)
        if data:
            self._add_to_cache(key, data)
        
        return data
    
    def _add_to_cache(self, key, value):
        # 超过限制时删除最早的项
        if len(self._cache) >= self._max_items:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        
        self._cache[key] = value
    
    async def set(self, key, value):
        self._add_to_cache(key, value)
        await json_store.save(key, value)
```

---

## 2. 数据序列化

### 模式 2A: 对象持久化

```python
from dataclasses import dataclass, asdict
from datetime import datetime

@dataclass
class User:
    id: str
    name: str
    email: str
    created_at: datetime
    
    def to_dict(self):
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data):
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        return cls(**data)

async def save_user(user):
    await json_store.save(f"user_{user.id}", user.to_dict())

async def load_user(user_id):
    data = await json_store.load(f"user_{user_id}")
    return User.from_dict(data) if data else None
```

### 模式 2B: 复杂类型序列化

```python
import json
from enum import Enum
from datetime import datetime

class CustomJSONEncoder(json.JSONEncoder):
    """自定义 JSON 编码器"""
    
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        
        if isinstance(obj, Enum):
            return obj.value
        
        if isinstance(obj, set):
            return list(obj)
        
        return super().default(obj)

async def save_complex_data(key, data):
    """保存包含复杂类型的数据"""
    json_str = json.dumps(data, cls=CustomJSONEncoder, ensure_ascii=False, indent=2)
    
    # 手动写入（演示）
    import aiofiles
    path = json_store._storage_dir / f"{key}.json"
    async with aiofiles.open(path, 'w', encoding='utf-8') as f:
        await f.write(json_str)
```

---

## 3. 数据分片

### 模式 3A: 超大文件分片存储

```python
async def save_large_data(key, large_list, chunk_size=1000):
    """将大列表分片存储"""
    
    chunks = []
    for i in range(0, len(large_list), chunk_size):
        chunk = large_list[i:i+chunk_size]
        chunks.append(chunk)
    
    # 存储分片信息和索引
    metadata = {
        "chunks": len(chunks),
        "total": len(large_list),
        "chunk_size": chunk_size
    }
    
    await json_store.save(f"{key}_metadata", metadata)
    
    # 存储每个分片
    for i, chunk in enumerate(chunks):
        await json_store.save(f"{key}_chunk_{i}", {"data": chunk})

async def load_large_data(key):
    """加载分片数据"""
    
    metadata = await json_store.load(f"{key}_metadata")
    if not metadata:
        return None
    
    result = []
    for i in range(metadata["chunks"]):
        chunk_data = await json_store.load(f"{key}_chunk_{i}")
        if chunk_data:
            result.extend(chunk_data["data"])
    
    return result
```

### 模式 3B: 按日期分片

```python
from datetime import datetime, timedelta

async def save_daily_log(log_entry):
    """按日期保存日志"""
    
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"logs_{today}"
    
    # 加载当前日志
    logs = await json_store.load(key) or []
    
    # 追加新日志
    logs.append({
        "timestamp": datetime.now().isoformat(),
        **log_entry
    })
    
    # 保存
    await json_store.save(key, logs)

async def get_logs_by_date(date_str):
    """获取指定日期的日志"""
    key = f"logs_{date_str}"
    return await json_store.load(key) or []

async def get_logs_range(start_date, end_date):
    """获取日期范围内的所有日志"""
    
    all_logs = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    while current <= end:
        logs = await get_logs_by_date(current.strftime("%Y-%m-%d"))
        all_logs.extend(logs)
        current += timedelta(days=1)
    
    return all_logs
```

---

## 4. 备份和恢复

### 模式 4A: 自动备份

```python
import shutil
from pathlib import Path
from datetime import datetime

class BackupManager:
    """自动备份管理"""
    
    def __init__(self, backup_dir="data/backups"):
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
    
    async def backup(self):
        """创建备份"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self._backup_dir / f"backup_{timestamp}"
        
        # 复制存储目录
        storage_dir = json_store.get_storage_dir()
        shutil.copytree(storage_dir, backup_path)
        
        return backup_path
    
    async def restore(self, backup_name):
        """恢复备份"""
        backup_path = self._backup_dir / backup_name
        
        if not backup_path.exists():
            raise ValueError(f"备份不存在: {backup_name}")
        
        storage_dir = json_store.get_storage_dir()
        
        # 清空当前存储
        for file in storage_dir.glob("*.json"):
            file.unlink()
        
        # 恢复备份
        for file in backup_path.glob("*.json"):
            shutil.copy(file, storage_dir / file.name)
    
    async def list_backups(self):
        """列出所有备份"""
        backups = list(self._backup_dir.iterdir())
        return sorted([b.name for b in backups])

# 使用
backup_mgr = BackupManager()
await backup_mgr.backup()  # 创建备份
await backup_mgr.restore("backup_20240101_120000")  # 恢复
```

### 模式 4B: 增量备份

```python
import hashlib

class IncrementalBackup:
    """增量备份（仅备份变化的文件）"""
    
    def __init__(self):
        self._file_hashes = {}
    
    def _get_file_hash(self, filepath):
        """计算文件 hash"""
        with open(filepath, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    
    async def backup(self):
        """增量备份"""
        storage_dir = json_store.get_storage_dir()
        changes = []
        
        for file in storage_dir.glob("*.json"):
            file_hash = self._get_file_hash(file)
            
            # 检查文件是否变化
            if file.name not in self._file_hashes or \
               self._file_hashes[file.name] != file_hash:
                changes.append({
                    "file": file.name,
                    "hash": file_hash,
                    "timestamp": datetime.now().isoformat()
                })
                self._file_hashes[file.name] = file_hash
        
        if changes:
            await json_store.save("_backup_log", changes)
        
        return changes
```

---

## 5. 数据压缩

### 模式 5A: GZIP 压缩

```python
import gzip
import json
import base64

async def save_compressed(key, data):
    """保存压缩的数据"""
    
    # JSON 序列化
    json_str = json.dumps(data, ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')
    
    # 压缩
    compressed = gzip.compress(json_bytes)
    
    # Base64 编码（JSON 兼容）
    encoded = base64.b64encode(compressed).decode('ascii')
    
    # 保存
    await json_store.save(key, {"_compressed": True, "data": encoded})

async def load_compressed(key):
    """加载压缩的数据"""
    
    container = await json_store.load(key)
    if not container or not container.get("_compressed"):
        return None
    
    # Base64 解码
    encoded = container["data"]
    compressed = base64.b64decode(encoded)
    
    # 解压
    json_bytes = gzip.decompress(compressed)
    
    # JSON 解析
    return json.loads(json_bytes.decode('utf-8'))

# 使用示例
large_data = {"items": list(range(10000))}
await save_compressed("large", large_data)

# 原始大小
original_size = len(json.dumps(large_data))

# 压缩后大小
container = await json_store.load("large")
compressed_size = len(container["data"])

print(f"压缩率: {compressed_size/original_size*100:.1f}%")
```

---

## 6. 数据验证

### 模式 6A: Schema 验证

```python
from pydantic import BaseModel, ValidationError

class AppConfig(BaseModel):
    theme: str
    language: str
    auto_update: bool
    max_retries: int

async def save_validated_config(data):
    """保存经过验证的配置"""
    
    try:
        config = AppConfig(**data)
        await json_store.save("app_config", config.model_dump())
    except ValidationError as e:
        print(f"配置验证失败: {e}")

async def load_validated_config():
    """加载并验证配置"""
    
    data = await json_store.load("app_config")
    if not data:
        return AppConfig(theme="light", language="en", auto_update=True, max_retries=3)
    
    try:
        return AppConfig(**data)
    except ValidationError as e:
        print(f"配置格式错误: {e}")
        return None
```

---

## 7. 批量操作优化

### 模式 7A: 批量保存

```python
async def batch_save(items, key_func):
    """批量保存数据"""
    
    # 合并所有数据
    batch_data = {}
    for item in items:
        key = key_func(item)
        batch_data[key] = item
    
    # 一次性保存
    await json_store.save("items_batch", batch_data)

async def batch_load(prefix):
    """批量加载数据"""
    
    batch = await json_store.load("items_batch")
    
    if not batch:
        return []
    
    # 过滤指定前缀的项
    return [v for k, v in batch.items() if k.startswith(prefix)]
```

### 模式 7B: 流式处理

```python
async def process_large_dataset_streaming():
    """流式处理大型数据集"""
    
    all_keys = await json_store.list_all()
    
    for key in all_keys:
        data = await json_store.load(key)
        
        if data:
            # 处理单个数据项
            result = await process_item(data)
            
            # 立即存储结果，释放内存
            await json_store.save(f"result_{key}", result)
```

---

## 8. 监控和调试

### 模式 8A: 存储统计

```python
from pathlib import Path

class StorageStats:
    """存储统计"""
    
    async def get_stats(self):
        """获取存储统计"""
        
        storage_dir = json_store.get_storage_dir()
        
        files = list(storage_dir.glob("*.json"))
        total_size = sum(f.stat().st_size for f in files)
        
        return {
            "file_count": len(files),
            "total_size_kb": total_size // 1024,
            "total_size_mb": total_size // (1024 * 1024),
            "files": [f.name for f in files]
        }
    
    async def get_file_size(self, key):
        """获取单个文件大小"""
        
        storage_dir = json_store.get_storage_dir()
        file_path = storage_dir / f"{key}.json"
        
        if file_path.exists():
            return file_path.stat().st_size
        
        return None
```

### 模式 8B: 变更追踪

```python
class ChangeTracker:
    """追踪数据变更"""
    
    def __init__(self):
        self._snapshots = {}
    
    async def track(self, key):
        """追踪数据变更"""
        
        data = await json_store.load(key)
        
        if key not in self._snapshots:
            self._snapshots[key] = data
            return {"changed": False}
        
        # 比较
        old = self._snapshots[key]
        changed = old != data
        
        self._snapshots[key] = data
        
        return {
            "changed": changed,
            "old": old,
            "new": data
        }
```

---

## 最佳实践总结

### DO ✓

1. **使用多层缓存**
   ```python
   cache = TwoLevelCache(ttl=300)
   ```

2. **进行数据验证**
   ```python
   config = AppConfig(**data)
   ```

3. **定期备份**
   ```python
   await backup_mgr.backup()
   ```

4. **监控存储大小**
   ```python
   stats = await storage_stats.get_stats()
   ```

### DON'T ✗

1. **不要频繁小量写入**
   ```python
   # 错误
   for item in items:
       await json_store.save(f"item_{item['id']}", item)
   
   # 正确
   batch = {f"item_{item['id']}": item for item in items}
   await json_store.save("items", batch)
   ```

2. **不要忽视大文件**
   ```python
   # 需要分片
   if data_size > 10MB:
       await save_large_data(key, data, chunk_size=1000)
   ```

3. **不要跳过验证**
   ```python
   # 验证后再保存
   config = AppConfig(**data)
   await json_store.save("config", config.model_dump())
   ```

---

## 相关资源

- [Storage 主文档](./README.md) - API 和基本用法
- [核心实现](./core.md) - 内部机制
- [Vector DB 模块](../vector_db/README.md) - 向量存储
