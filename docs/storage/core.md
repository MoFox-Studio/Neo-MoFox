# Storage 模块核心实现

## 概述

Storage 模块提供轻量级的 JSON 本地文件存储服务。设计简洁，专注于：

- **异步 I/O**：基于 aiofiles，不阻塞事件循环
- **并发安全**：asyncio.Lock 防护
- **安全性**：路径检查防护，防止目录遍历攻击

---

## JSONStore 类设计

### 内部状态

```python
class JSONStore:
    _storage_dir: Path         # 存储目录
    _lock: asyncio.Lock        # 并发锁
```

### 单例模式

```python
@lru_cache
def _get_json_store() -> JSONStore:
    """全局单例（LRU 缓存实现）"""
    return JSONStore()

json_store = _get_json_store()
```

**特点**：
- `@lru_cache` 装饰器实现单例模式
- 确保应用共享同一实例
- 线程安全（Python GIL 保护）

---

## 核心方法详解

### __init__()

初始化存储服务。

```python
def __init__(self, storage_dir: str | Path = "data/json_storage") -> None:
    """
    Args:
        storage_dir: 存储目录路径，默认为 data/json_storage
    """
    self._storage_dir = Path(storage_dir)
    self._lock = asyncio.Lock()
```

**说明**：
- `_storage_dir` 转换为 Path 对象便于路径操作
- `_lock` 初始化为新的 asyncio.Lock（非复用）

---

### _ensure_dir()

确保存储目录存在。

```python
async def _ensure_dir(self) -> None:
    """异步创建目录（如不存在）"""
    if not self._storage_dir.exists():
        await makedirs(self._storage_dir, exist_ok=True)
```

**调用时机**：
- `save()` 时调用（需要目录存在）
- `list_all()` 时调用（初始化）

**说明**：
- 使用 aiofiles 的 makedirs（异步）
- `exist_ok=True` 避免目录已存在时的异常

---

### _get_file_path()

获取文件路径并验证安全性。

```python
def _get_file_path(self, name: str) -> Path:
    """
    检查名称合法性，返回完整文件路径。
    
    安全检查：
    - 不允许包含 /、\（路径分隔符）
    - 不允许包含 ..（目录遍历）
    """
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"Invalid storage name: {name}")
    
    return self._storage_dir / f"{name}.json"
```

**安全原理**：

```
# 保护场景
攻击1: await json_store.save("../../../etc/passwd", {...})
结果: 抛出 ValueError
原因: ".." 在黑名单中

攻击2: await json_store.save("subdir/config", {...})
结果: 抛出 ValueError
原因: "/" 在黑名单中

保证: 所有文件都在 _storage_dir 下
```

---

### save()

异步保存数据到 JSON 文件。

```python
async def save(self, name: str, data: dict[str, Any]) -> None:
    """
    流程：
    1. 获取锁（并发互斥）
    2. 确保目录存在
    3. 验证文件名合法性
    4. 序列化为 JSON
    5. 异步写入文件
    """
    async with self._lock:
        await self._ensure_dir()
        file_path = self._get_file_path(name)
        
        async with aio_open(file_path, mode="w", encoding="utf-8") as f:
            import json
            await f.write(json.dumps(
                data,
                ensure_ascii=False,  # 保留中文
                indent=2              # 格式化
            ))
```

**关键点**：

1. **锁的获取**
   ```python
   async with self._lock:
       # 独占访问，其他并发请求等待
   ```

2. **JSON 序列化选项**
   ```python
   ensure_ascii=False  # 保留中文不转义为 \uXXXX
   indent=2           # 美化格式，便于手动编辑
   ```

3. **异步文件写入**
   ```python
   async with aio_open(...) as f:
       await f.write(...)  # 异步操作
   ```

---

### load()

异步从 JSON 文件加载数据。

```python
async def load(self, name: str) -> dict[str, Any] | None:
    """
    流程：
    1. 获取锁
    2. 验证文件名
    3. 检查文件是否存在
    4. 异步读取文件
    5. JSON 解析
    """
    async with self._lock:
        file_path = self._get_file_path(name)
        
        if not file_path.exists():
            return None  # 文件不存在返回 None
        
        async with aio_open(file_path, mode="r", encoding="utf-8") as f:
            content = await f.read()
            import json
            return json.loads(content)
```

**返回值处理**：

```python
# 文件存在但内容为空或无效
data = await json_store.load("config")
if data is None:
    print("文件不存在")
elif not data:
    print("文件为空")
else:
    print(f"数据: {data}")
```

---

### delete()

删除数据文件。

```python
async def delete(self, name: str) -> bool:
    """
    Returns:
        True: 成功删除
        False: 文件不存在
    """
    async with self._lock:
        file_path = self._get_file_path(name)
        
        if not file_path.exists():
            return False
        
        file_path.unlink()  # 删除文件
        return True
```

**说明**：
- `unlink()` 是同步操作（文件系统很快）
- 返回布尔值便于调用者判断

---

### exists()

检查文件是否存在（不需要锁）。

```python
async def exists(self, name: str) -> bool:
    """
    检查文件是否存在。
    
    说明：此操作不需要锁（只读，不修改状态）
    """
    file_path = self._get_file_path(name)
    return file_path.exists()
```

**性能优化**：
- 无锁操作（只读）
- 避免不必要的同步点

---

### list_all()

列出所有已存储的数据。

```python
async def list_all(self) -> list[str]:
    """
    1. 确保目录存在
    2. 使用 glob() 查找 .json 文件
    3. 提取文件名（不含后缀）
    """
    await self._ensure_dir()
    
    json_files = list(self._storage_dir.glob("*.json"))
    return [f.stem for f in json_files]
```

**说明**：
- `glob("*.json")` 找出所有 JSON 文件
- `f.stem` 返回文件名（不含扩展名）

**例子**：

```
文件系统:
data/json_storage/
├── config.json
├── cache.json
└── users.json

返回值: ["config", "cache", "users"]
```

---

### get_storage_dir()

获取存储目录绝对路径。

```python
def get_storage_dir(self) -> Path:
    """
    Returns:
        存储目录的绝对路径（已调用 resolve()）
    """
    return self._storage_dir.resolve()
```

**使用场景**：
- 调试时查看文件位置
- 与其他工具集成

---

## 并发安全机制

### Lock 机制

```python
self._lock = asyncio.Lock()
```

**保护的操作**：

| 操作 | 是否加锁 | 原因 |
|------|--------|------|
| save() | ✓ | 写入操作，需要互斥 |
| load() | ✓ | 读操作也加锁，防止读写竞争 |
| delete() | ✓ | 删除操作需要互斥 |
| exists() | ✗ | 只读，不修改状态 |
| list_all() | 部分 | 只在 _ensure_dir() 时加锁 |

**场景：并发写入**

```
时刻 T1: Task A 执行 save("config", {...})
         获得 lock
         
时刻 T2: Task B 尝试 save("config", {...})
         等待 lock

时刻 T3: Task A 完成，释放 lock
         Task B 获得 lock，开始执行
```

---

## 路径安全

### 安全检查

```python
def _get_file_path(self, name: str) -> Path:
    # 黑名单检查
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"Invalid storage name: {name}")
    
    # 构造文件路径
    return self._storage_dir / f"{name}.json"
```

**防护的攻击**：

```python
# 攻击1：路径遍历
await json_store.save("../../etc/passwd", {...})
# 被阻止：".." 在黑名单中

# 攻击2：子目录逃逸
await json_store.save("../backup/data", {...})
# 被阻止：".." 在黑名单中

# 攻击3：绝对路径
await json_store.save("/etc/config", {...})
# 被阻止：没有直接检查，但无法遍历上级

# 安全用法
await json_store.save("user_123_config", {...})
# ✓ 允许：名称合法
```

---

## 错误处理

### 异常场景

```python
# 1. 名称非法
await json_store.save("path/to/file", {})
# → ValueError("Invalid storage name")

# 2. JSON 格式错误（加载时）
# 手动编辑 JSON 文件导致语法错误
data = await json_store.load("config")
# → json.JSONDecodeError

# 3. 权限不足
await json_store.save("config", {})
# → PermissionError（操作系统）

# 4. 磁盘满
await json_store.save("config", large_data)
# → OSError（磁盘满）
```

### 推荐的错误处理

```python
try:
    data = await json_store.save("config", {...})
except ValueError as e:
    logger.error(f"文件名非法: {e}")
except IOError as e:
    logger.error(f"I/O 错误: {e}")
```

---

## 性能特性

### 异步非阻塞

```python
# 使用 aiofiles 完全异步
async with aio_open(file_path, mode="w") as f:
    await f.write(...)  # 异步写入，不阻塞事件循环
```

**对比**：

```python
# 同步 I/O（阻塞）
with open(file_path, "w") as f:
    f.write(...)  # 阻塞事件循环，其他任务无法进行

# 异步 I/O（非阻塞）
async with aio_open(file_path, "w") as f:
    await f.write(...)  # 异步，事件循环可处理其他任务
```

### 序列化优化

```python
json.dumps(data, ensure_ascii=False, indent=2)
```

**性能考虑**：
- `ensure_ascii=False`：较快（中文保留不转义）
- `indent=2`：文件更大但可读性好
- 对性能影响有限（JSON 序列化通常不是瓶颈）

---

## 全局单例实现

### LRU 缓存单例

```python
from functools import lru_cache

@lru_cache
def _get_json_store() -> JSONStore:
    return JSONStore()

json_store = _get_json_store()
```

**为什么用 @lru_cache？**

1. **简单**：一行代码实现单例
2. **线程安全**：Python 装饰器原子性
3. **缓存好处**：重复调用不重新创建

**等价于**：

```python
_instance = None

def get_json_store():
    global _instance
    if _instance is None:
        _instance = JSONStore()
    return _instance
```

---

## 与其他模块集成

### 与 Logger 模块

JSONStore 本身使用 logger 记录操作（未在源码中体现，但可添加）

### 与异步框架

```python
# 完全基于 asyncio
import asyncio

async def main():
    await json_store.save("config", {...})
    data = await json_store.load("config")

asyncio.run(main())
```

---

## 设计权衡

### 取舍决策

| 特性 | 采用? | 原因 |
|------|------|------|
| 自动备份 | ✗ | 简单化设计 |
| 版本控制 | ✗ | 用户可扩展 |
| 加密 | ✗ | 交给上层应用 |
| 压缩 | ✗ | 大多数场景不需要 |
| 异步 I/O | ✓ | 核心 feature |
| 并发安全 | ✓ | 框架要求 |
| 路径安全 | ✓ | 安全考虑 |

---

## 相关资源

- [Storage 主文档](./README.md) - API 和使用方法
- [高级用法](./advanced.md) - 缓存、序列化
- [Vector DB 模块](../vector_db/README.md) - 向量存储
