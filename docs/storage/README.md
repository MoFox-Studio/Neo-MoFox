# Storage 存储模块

简单易用的 JSON 本地持久化存储服务。通过异步 API 提供快速的数据读写功能，适合存储小型结构化数据。

## 核心特性

### 1. 异步 I/O

- 完全基于 asyncio 和 aiofiles
- 不阻塞事件循环
- 支持高并发读写

### 2. 本地文件存储

- JSON 格式存储
- 自动目录创建
- 支持自定义存储路径

### 3. 线程安全

- 内置 asyncio.Lock 机制
- 并发读写互斥保护
- 避免数据竞争

### 4. 安全性

- 防止路径遍历攻击
- 文件名白名单检查
- 自动转义处理

---

## 快速开始

### 基本用法

```python
from src.kernel.storage import json_store

# 保存数据
await json_store.save("user_profile", {
    "name": "Alice",
    "age": 30,
    "email": "alice@example.com"
})

# 读取数据
user = await json_store.load("user_profile")
print(user)  # {"name": "Alice", "age": 30, "email": "alice@example.com"}

# 检查存在性
if await json_store.exists("user_profile"):
    print("数据存在")

# 列出所有数据
all_keys = await json_store.list_all()
print(all_keys)  # ["user_profile", ...]

# 删除数据
await json_store.delete("user_profile")
```

### 创建自定义实例

```python
from src.kernel.storage import JSONStore

# 创建自定义路径的存储实例
store = JSONStore(storage_dir="my_data/storage")

# 使用方式相同
await store.save("config", {"theme": "dark"})
config = await store.load("config")
```

---

## API 参考

### save()

保存数据到 JSON 文件。

```python
async def save(name: str, data: dict[str, Any]) -> None:
    """
    Args:
        name: 数据名称（文件名，不含 .json 后缀）
        data: 要保存的数据字典
    
    Raises:
        ValueError: 名称包含非法字符（/, \, ..）
        IOError: 文件写入失败
    """
    pass
```

**使用示例**：

```python
# 简单数据
await json_store.save("app_config", {
    "theme": "dark",
    "language": "zh_CN"
})

# 嵌套数据
await json_store.save("user_settings", {
    "ui": {
        "theme": "dark",
        "sidebar": True
    },
    "notifications": {
        "email": True,
        "push": False
    }
})
```

**文件位置**：

```
data/json_storage/
├── app_config.json
└── user_settings.json
```

### load()

从 JSON 文件加载数据。

```python
async def load(name: str) -> dict[str, Any] | None:
    """
    Args:
        name: 数据名称（不含 .json 后缀）
    
    Returns:
        数据字典，或 None 如果文件不存在
    
    Raises:
        ValueError: 名称包含非法字符
        json.JSONDecodeError: JSON 格式错误
    """
    pass
```

**使用示例**：

```python
# 加载存在的数据
config = await json_store.load("app_config")
if config:
    print(f"主题: {config['theme']}")

# 加载不存在的数据
missing = await json_store.load("nonexistent")
print(missing)  # None
```

### exists()

检查数据是否存在。

```python
async def exists(name: str) -> bool:
    """
    Args:
        name: 数据名称
    
    Returns:
        数据文件是否存在
    
    Raises:
        ValueError: 名称包含非法字符
    """
    pass
```

**使用示例**：

```python
if await json_store.exists("user_profile"):
    user = await json_store.load("user_profile")
else:
    print("用户数据不存在")
```

### delete()

删除数据文件。

```python
async def delete(name: str) -> bool:
    """
    Args:
        name: 数据名称
    
    Returns:
        True 如果成功删除，False 如果文件不存在
    
    Raises:
        ValueError: 名称包含非法字符
    """
    pass
```

**使用示例**：

```python
if await json_store.delete("user_profile"):
    print("删除成功")
else:
    print("文件不存在")
```

### list_all()

列出所有已存储的数据名称。

```python
async def list_all() -> list[str]:
    """
    Returns:
        数据名称列表（不含 .json 后缀）
    """
    pass
```

**使用示例**：

```python
all_data = await json_store.list_all()
for name in all_data:
    print(f"- {name}")

# 输出示例
# - user_profile
# - app_config
# - cache_data
```

### get_storage_dir()

获取存储目录的绝对路径。

```python
def get_storage_dir() -> Path:
    """
    Returns:
        存储目录的绝对路径（Path 对象）
    """
    pass
```

**使用示例**：

```python
storage_path = json_store.get_storage_dir()
print(f"存储目录: {storage_path}")
# 输出: 存储目录: C:\project\data\json_storage
```

---

## 使用场景

### 场景 1: 应用配置存储

```python
async def save_app_config(theme, language, auto_update):
    await json_store.save("app_config", {
        "theme": theme,
        "language": language,
        "auto_update": auto_update,
        "last_updated": datetime.now().isoformat()
    })

async def load_app_config():
    config = await json_store.load("app_config")
    return config or {"theme": "light", "language": "en"}
```

### 场景 2: 缓存数据

```python
async def cache_api_response(endpoint, data):
    await json_store.save(f"cache_{endpoint}", {
        "data": data,
        "timestamp": time.time()
    })

async def get_cached_data(endpoint, max_age=3600):
    cache = await json_store.load(f"cache_{endpoint}")
    
    if cache:
        age = time.time() - cache["timestamp"]
        if age < max_age:
            return cache["data"]
    
    return None
```

### 场景 3: 用户数据存储

```python
async def save_user(user_id, user_data):
    await json_store.save(f"user_{user_id}", user_data)

async def load_user(user_id):
    return await json_store.load(f"user_{user_id}")

async def get_all_users():
    all_keys = await json_store.list_all()
    users = []
    
    for key in all_keys:
        if key.startswith("user_"):
            user = await json_store.load(key)
            users.append(user)
    
    return users
```

### 场景 4: 插件持久化数据

```python
class MyPlugin:
    def __init__(self):
        self.storage_key = "plugin_my_plugin"
    
    async def save_state(self, state):
        await json_store.save(self.storage_key, state)
    
    async def load_state(self):
        return await json_store.load(self.storage_key)
```

---

## 最佳实践

### DO ✓

1. **总是使用 await**
   ```python
   # 正确
   data = await json_store.load("config")
   ```

2. **检查返回值**
   ```python
   # 正确
   config = await json_store.load("config")
   if config:
       process(config)
   ```

3. **使用有意义的名称**
   ```python
   # 好
   await json_store.save("user_profile", data)
   
   # 不好
   await json_store.save("data1", data)
   ```

4. **使用自定义实例进行隔离**
   ```python
   # 不同模块使用不同存储目录
   plugin_store = JSONStore("data/plugins/my_plugin")
   cache_store = JSONStore("data/cache")
   ```

### DON'T ✗

1. **不要使用非法字符在名称中**
   ```python
   # 错误 - 包含路径分隔符
   await json_store.save("user/profile", data)
   
   # 错误 - 路径遍历
   await json_store.save("../../../etc/passwd", data)
   
   # 正确
   await json_store.save("user_profile", data)
   ```

2. **不要忽视文件不存在情况**
   ```python
   # 错误
   data = await json_store.load("config")
   print(data["key"])  # 可能抛异常
   
   # 正确
   data = await json_store.load("config")
   if data:
       print(data.get("key", "default"))
   ```

3. **不要存储过大的数据**
   ```python
   # 不适合 (应该用数据库)
   large_dataset = [generate_huge_list()]
   await json_store.save("big_data", large_dataset)
   
   # 适合
   metadata = {"count": 1000, "last_updated": now()}
   await json_store.save("metadata", metadata)
   ```

4. **不要频繁写入同一文件**
   ```python
   # 不好 (频繁磁盘 I/O)
   for item in items:
       item["status"] = "processed"
       await json_store.save("items", [item])
   
   # 好 (一次性写入)
   for item in items:
       item["status"] = "processed"
   await json_store.save("items", items)
   ```

---

## 性能考虑

### 1. 批量操作

```python
# 不好：多次保存
for user in users:
    await json_store.save(f"user_{user['id']}", user)

# 好：合并后保存
user_map = {user['id']: user for user in users}
await json_store.save("users", user_map)
```

### 2. 缓存策略

```python
# 使用内存缓存避免频繁磁盘 I/O
class CachedStore:
    def __init__(self):
        self._cache = {}
    
    async def get(self, key):
        if key in self._cache:
            return self._cache[key]
        
        data = await json_store.load(key)
        if data:
            self._cache[key] = data
        return data
    
    async def set(self, key, data):
        self._cache[key] = data
        await json_store.save(key, data)
```

### 3. 异步并发

```python
# 正确：并发读取
results = await asyncio.gather(
    json_store.load("config"),
    json_store.load("cache"),
    json_store.load("metadata")
)

# 避免：串联读取
config = await json_store.load("config")
cache = await json_store.load("cache")
metadata = await json_store.load("metadata")
```

---

## 故障排除

### 问题：文件被锁定

**原因**：多个异步任务同时读写

**解决**：内置 asyncio.Lock 已处理，正常使用即可

### 问题：JSON 格式错误

**原因**：手动编辑文件导致格式错误

**解决**：
```python
try:
    data = await json_store.load("config")
except json.JSONDecodeError:
    logger.error("配置文件格式错误，使用默认值")
    data = {"theme": "light"}
```

### 问题：权限被拒绝

**原因**：存储目录权限不足

**解决**：确保应用有目录的读写权限

```python
store_dir = json_store.get_storage_dir()
print(f"存储路径: {store_dir}")
```

---

## 与其他模块集成

### 与 Logger 模块

```python
from src.kernel.logger import get_logger
from src.kernel.storage import json_store

logger = get_logger("app.storage")

async def save_with_logging(name, data):
    logger.info(f"保存数据: {name}")
    await json_store.save(name, data)
    logger.debug(f"保存完成: {name}")
```

### 与 Config 模块

```python
# storage 可以作为 config 持久化的后端
async def persist_config(config):
    await json_store.save("app_config", config.to_dict())

async def load_persisted_config():
    return await json_store.load("app_config")
```

---

## 相关资源

- [核心实现细节](./core.md) - JSONStore 内部机制
- [高级用法](./advanced.md) - 缓存、序列化等
- [Vector DB 模块](../vector_db/README.md) - 向量存储
