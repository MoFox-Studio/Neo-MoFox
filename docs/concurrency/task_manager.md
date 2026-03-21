# TaskManager API 详解

## 概述

TaskManager 是 Concurrency 模块的核心，提供全局统一的异步任务管理接口。它是一个单例对象，负责追踪所有创建的异步任务并提供查询和管理接口。

---

## 获取实例

### get_task_manager()

获取全局 TaskManager 单例实例。

```python
from src.kernel.concurrency import get_task_manager

tm = get_task_manager()
# 多次调用返回同一实例
tm2 = get_task_manager()
assert tm is tm2
```

**返回**：`TaskManager` 全局单例

---

## 任务创建

### create_task()

创建一个新的异步任务。

```python
task_info = tm.create_task(
    coro: Coroutine,              # 必须：协程对象
    name: str | None = None,      # 可选：任务名称
    daemon: bool = False,         # 可选：是否为守护任务
    timeout: float | None = None, # 可选：超时时间（秒）
    group_name: str | None = None,# 可选：所属任务组名称
    metadata: dict | None = None  # 可选：自定义元数据
) -> TaskInfo
```

**参数说明**：

- `coro`：要执行的协程对象（必须）
- `name`：任务名称，用于调试和日志输出。如果不提供，会自动生成
- `daemon`：是否为守护任务。守护任务不被 `wait_all_tasks()` 等待，也不被 WatchDog 超时检查
- `timeout`：超时时间（秒）。如果任务运行时间超过此值，会被 WatchDog 自动取消
- `group_name`：任务所属的组名。同一组的任务可以通过 `group()` 获取
- `metadata`：自定义元数据字典，用于存储额外的上下文信息

**返回**：`TaskInfo` 对象，包含任务的元数据和操作方法

**异常**：
- `RuntimeError`：如果不在异步上下文中调用

**使用示例**：

```python
import asyncio
from src.kernel.concurrency import get_task_manager

async def download_file(url: str):
    print(f"下载 {url}")
    await asyncio.sleep(2)
    return f"Downloaded {url}"

async def main():
    tm = get_task_manager()
    
    # 基本任务创建
    task1 = tm.create_task(download_file("file1.zip"))
    
    # 带名称的任务
    task2 = tm.create_task(
        download_file("file2.zip"),
        name="download_file2"
    )
    
    # 带超时的任务
    task3 = tm.create_task(
        download_file("file3.zip"),
        timeout=10
    )
    
    # 带元数据的任务
    task4 = tm.create_task(
        download_file("file4.zip"),
        name="download_file4",
        metadata={
            "priority": "high",
            "user": "admin",
            "retry_count": 0
        }
    )
    
    # 守护任务
    task5 = tm.create_task(
        download_file("file5.zip"),
        name="background_download",
        daemon=True  # 不阻塞应用关闭
    )
    
    await tm.wait_all_tasks()
    print("所有任务完成")

asyncio.run(main())
```

---

## 任务查询

### get_task()

获取指定任务的信息。

```python
task_info = tm.get_task(task_id: str) -> TaskInfo
```

**参数**：
- `task_id`：任务唯一标识符（由 `create_task()` 返回）

**返回**：`TaskInfo` 对象

**异常**：
- `TaskNotFoundError`：任务不存在

**使用示例**：

```python
tm = get_task_manager()

# 创建任务，获得 task_info
task_info = tm.create_task(my_coro())
task_id = task_info.task_id

# 稍后通过 task_id 查询
retrieved = tm.get_task(task_id)
print(retrieved.name)
print(retrieved.is_done())
```

### get_all_tasks()

获取所有任务（包括已完成和活跃）。

```python
tasks = tm.get_all_tasks() -> list[TaskInfo]
```

**返回**：所有 TaskInfo 对象的列表

**使用示例**：

```python
all_tasks = tm.get_all_tasks()
print(f"总共有 {len(all_tasks)} 个任务")

for task in all_tasks:
    print(f"  {task.name}: {task.is_done() and '完成' or '运行中'}")
```

### get_active_tasks()

获取所有活跃任务（未完成）。

```python
active_tasks = tm.get_active_tasks() -> list[TaskInfo]
```

**返回**：活跃 TaskInfo 对象的列表

**使用示例**：

```python
active = tm.get_active_tasks()
print(f"当前有 {len(active)} 个活跃任务")

# 打印所有活跃任务的信息
for task_info in active:
    elapsed = (datetime.now() - task_info.created_at).total_seconds()
    print(f"  {task_info.name}: 运行 {elapsed:.1f}s")
```

### get_task_count()

获取任务总数。

```python
count = tm.get_task_count() -> int
```

**使用示例**：

```python
total = tm.get_task_count()
print(f"任务总数: {total}")
```

### get_active_task_count()

获取活跃任务数量。

```python
count = tm.get_active_task_count() -> int
```

**使用示例**：

```python
active_count = tm.get_active_task_count()
print(f"活跃任务数: {active_count}")
```

### get_stats()

获取任务管理器的统计信息。

```python
stats = tm.get_stats() -> dict[str, Any]
```

**返回字典包含**：
- `total_tasks`：任务总数
- `active_tasks`：活跃任务数
- `daemon_tasks`：守护任务数
- `grouped_tasks`：属于某个组的任务数
- `groups`：任务组数量

**使用示例**：

```python
stats = tm.get_stats()
print(f"任务统计: {stats}")
# 输出: {'total_tasks': 10, 'active_tasks': 3, 'daemon_tasks': 2, 'grouped_tasks': 5, 'groups': 2}

# 显示详细信息
print(f"总任务: {stats['total_tasks']}")
print(f"活跃: {stats['active_tasks']}")
print(f"守护: {stats['daemon_tasks']}")
print(f"组: {stats['groups']}")
```

---

## 任务控制

### cancel_task()

取消指定的任务。

```python
cancelled = tm.cancel_task(task_id: str) -> bool
```

**参数**：
- `task_id`：任务唯一标识符

**返回**：`True` 如果成功取消，`False` 如果任务已完成或不存在

**使用示例**：

```python
task_info = tm.create_task(long_running_task())
task_id = task_info.task_id

# 稍后取消
if tm.cancel_task(task_id):
    print("任务已取消")
else:
    print("任务已完成或不存在")
```

### wait_all_tasks()

等待所有非守护任务完成。

```python
await tm.wait_all_tasks() -> None
```

**说明**：
- 等待所有活跃的非守护任务（`daemon=False`）完成
- 不等待守护任务
- 如果没有活跃任务，立即返回

**使用示例**：

```python
async def main():
    tm = get_task_manager()
    
    # 创建多个任务
    for i in range(10):
        tm.create_task(process_item(i))
    
    # 等待所有任务完成
    await tm.wait_all_tasks()
    print("所有任务已完成")

asyncio.run(main())
```

### cleanup_tasks()

清理已完成的任务，释放内存。

```python
cleaned = tm.cleanup_tasks() -> int
```

**返回**：清理的任务数量

**说明**：
- 删除所有已完成的任务（包括成功、失败、取消的）
- 返回清理的任务数
- WatchDog 会自动调用此方法

**使用示例**：

```python
# 创建并运行任务
for i in range(1000):
    tm.create_task(quick_task())

await tm.wait_all_tasks()

# 清理已完成的任务
cleaned = tm.cleanup_tasks()
print(f"清理了 {cleaned} 个已完成任务")

# 现在 get_all_tasks() 返回空列表
print(f"剩余任务: {len(tm.get_all_tasks())}")
```

---

## 任务组管理

### group()

获取或创建一个任务组。

```python
group = tm.group(
    name: str,
    timeout: float | None = None,
    cancel_on_error: bool = True
) -> TaskGroup
```

**参数**：
- `name`：任务组名称（用于共享）
- `timeout`：整组超时时间（秒），默认无超时
- `cancel_on_error`：任一任务异常时是否取消其他任务，默认 `True`

**返回**：`TaskGroup` 对象

**说明**：
- 同名的 TaskGroup 被共享，多次调用 `group("name")` 返回同一实例
- TaskGroup 是上下文管理器，需要使用 `async with` 语句
- 退出上下文时自动等待所有任务完成

**使用示例**：

```python
async def main():
    tm = get_task_manager()
    
    # 创建任务组
    async with tm.group(
        name="data_processing",
        timeout=60,
        cancel_on_error=True
    ) as tg:
        for item in items:
            tg.create_task(process_item(item), name=f"process_{item.id}")
    
    # 退出上下文时所有任务已完成

asyncio.run(main())
```

---

## 批量操作

### gather()

并行执行多个协程并返回结果列表。

```python
results = await tm.gather(
    *coros: Coroutine,
    return_exceptions: bool = False,
    group_name: str | None = None
) -> list[Any]
```

**参数**：
- `*coros`：要执行的协程（可变参数）
- `return_exceptions`：是否将异常作为结果返回。`False` 时抛出第一个异常
- `group_name`：可选的任务组名称

**返回**：结果列表，顺序与输入协程一致

**使用示例**：

```python
async def fetch(url: str):
    # 模拟网络请求
    await asyncio.sleep(1)
    return f"Response from {url}"

async def main():
    tm = get_task_manager()
    
    # 基本用法
    results = await tm.gather(
        fetch("http://api1.com"),
        fetch("http://api2.com"),
        fetch("http://api3.com")
    )
    
    for url, result in zip(urls, results):
        print(f"{url}: {result}")

asyncio.run(main())
```

**处理异常**：

```python
async def main():
    tm = get_task_manager()
    
    results = await tm.gather(
        fetch("good_url"),
        fetch("bad_url"),  # 会异常
        fetch("good_url2"),
        return_exceptions=True  # 返回异常而非抛出
    )
    
    for result in results:
        if isinstance(result, Exception):
            print(f"异常: {result}")
        else:
            print(f"结果: {result}")

asyncio.run(main())
```

---

## 错误处理

### TaskNotFoundError

任务不存在时抛出。

```python
from src.kernel.concurrency import TaskNotFoundError

try:
    task = tm.get_task("invalid_id")
except TaskNotFoundError as e:
    print(f"任务不存在: {e.task_id}")
```

---

## 使用模式

### 模式 1: 简单的单个任务

```python
async def download():
    pass

tm = get_task_manager()
task = tm.create_task(download(), name="download_task")
```

### 模式 2: 多个独立任务

```python
for i in range(10):
    tm.create_task(process(i), name=f"process_{i}")

await tm.wait_all_tasks()
```

### 模式 3: 任务组

```python
async with tm.group(name="batch", timeout=30) as tg:
    for item in items:
        tg.create_task(handle(item))
```

### 模式 4: 并行执行并处理结果

```python
tm = get_task_manager()
results = await tm.gather(task1(), task2(), task3())
for result in results:
    process_result(result)
```

### 模式 5: 带超时的任务

```python
tm.create_task(
    slow_operation(),
    timeout=60
)
```

### 模式 6: 守护任务

```python
tm.create_task(
    background_job(),
    daemon=True
)
```

---

## 最佳实践

1. **始终提供任务名称** - 便于调试和日志输出
2. **为重要任务设置超时** - 防止任务永远运行
3. **使用任务组管理相关任务** - 便于整体控制
4. **定期调用 cleanup_tasks()** - 释放内存
5. **使用元数据追踪上下文** - 便于后续查询
6. **合理使用守护任务** - 只用于后台任务

---

## 相关资源

- [Concurrency 主文档](./README.md) - 概览和快速开始
- [TaskGroup 详解](./task_group.md) - 任务组管理
- [WatchDog 监控](./watchdog.md) - 监控系统
- [高级用法](./advanced.md) - 性能优化和最佳实践
