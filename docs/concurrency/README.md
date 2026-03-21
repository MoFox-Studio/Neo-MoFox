# Concurrency 模块

## 概述

`concurrency` 模块提供了统一的异步任务管理能力，包括任务创建、追踪、超时管理和监控。它的核心目标是替代不规范的 `asyncio.create_task`，避免任务泄漏和资源浪费。

**主要特性**：
- 🎯 **统一任务管理** - 通过 TaskManager 集中管理所有异步任务
- 📊 **任务追踪** - 自动追踪每个任务的元数据和运行状态
- ⏱️ **超时控制** - 支持任务级别和组级别的超时设置
- 👀 **监控系统** - WatchDog 提供独立线程的后台监控
- 💚 **心跳检测** - 聊天流驱动器的健康状态监控
- 🔄 **任务分组** - 支持作用域化的任务组管理

---

## 快速开始

### 安装

```python
from src.kernel.concurrency import get_task_manager, get_watchdog
```

### 基本任务创建

```python
import asyncio
from src.kernel.concurrency import get_task_manager

# 定义一个异步任务
async def my_task():
    print("任务开始")
    await asyncio.sleep(2)
    print("任务完成")
    return "结果"

# 获取全局 TaskManager 实例
tm = get_task_manager()

async def main():
    # 创建任务
    task_info = tm.create_task(my_task(), name="my_first_task")
    
    # 等待所有任务完成
    await tm.wait_all_tasks()
    
    print(f"任务完成: {task_info.get_result()}")

asyncio.run(main())
```

**输出**：
```
任务开始
任务完成
任务完成: 结果
```

### 任务组管理

```python
import asyncio
from src.kernel.concurrency import get_task_manager

async def fetch_data(item_id: int):
    await asyncio.sleep(1)
    return f"Data {item_id}"

async def main():
    tm = get_task_manager()
    
    # 使用任务组
    async with tm.group(name="fetch_group", timeout=10) as tg:
        for i in range(5):
            tg.create_task(fetch_data(i), name=f"fetch_{i}")
    
    print("所有数据获取完成")

asyncio.run(main())
```

### 并行执行多个任务

```python
import asyncio
from src.kernel.concurrency import get_task_manager

async def task1():
    await asyncio.sleep(1)
    return "Task 1 完成"

async def task2():
    await asyncio.sleep(2)
    return "Task 2 完成"

async def task3():
    await asyncio.sleep(1.5)
    return "Task 3 完成"

async def main():
    tm = get_task_manager()
    
    # 使用 TaskManager 的 gather 方法并行执行多个任务
    results = await tm.gather(task1(), task2(), task3(), return_exceptions=False)
    
    for result in results:
        print(result)

asyncio.run(main())
```

**输出**：
```
Task 1 完成
Task 3 完成
Task 2 完成
```

---

## 核心概念

### 1. TaskManager - 任务管理器

TaskManager 是一个全局单例，负责：
- 创建和追踪异步任务
- 存储任务元数据
- 管理任务组
- 提供任务查询和统计接口

```python
from src.kernel.concurrency import get_task_manager

tm = get_task_manager()

# 获取任务管理器统计信息
stats = tm.get_stats()
print(f"活跃任务数: {stats['active_tasks']}")
print(f"守护任务数: {stats['daemon_tasks']}")
print(f"任务组数: {stats['groups']}")
```

### 2. TaskInfo - 任务信息

每个任务都对应一个 TaskInfo 对象，包含：
- `task_id` - 唯一标识符
- `name` - 任务名称
- `status` - 运行状态（running, completed, failed, cancelled）
- `created_at` - 创建时间
- `timeout` - 超时设置
- `metadata` - 自定义元数据

```python
task_info = tm.create_task(
    my_task(),
    name="important_task",
    timeout=30,  # 30秒超时
    metadata={"priority": "high", "user_id": "123"}
)

# 查询任务状态
print(f"任务状态: {task_info}")
print(f"是否完成: {task_info.is_done()}")
print(f"是否失败: {task_info.is_failed()}")
print(f"异常信息: {task_info.get_exception()}")
```

### 3. TaskGroup - 任务组

TaskGroup 提供作用域化的任务管理：
- 所有任务在退出上下文时等待完成
- 支持整组超时设置
- 支持任一任务异常时取消其他任务
- 可被多个模块共享（通过名称）

```python
# 创建任务组
async with tm.group(
    name="batch_processing",
    timeout=60,
    cancel_on_error=True  # 任一任务失败时取消其他任务
) as tg:
    for item in items:
        tg.create_task(process_item(item))
    # 退出时自动等待所有任务完成
```

### 4. WatchDog - 监控系统

WatchDog 是一个独立的后台线程，提供：
- 任务超时监控和自动取消
- 聊天流心跳检测
- 已完成任务的清理
- 系统健康检查

```python
from src.kernel.concurrency import get_watchdog

# 启动 WatchDog
watchdog = get_watchdog()
watchdog.start()

# 注册聊天流
heartbeat = watchdog.register_stream(
    stream_id="stream_123",
    tick_interval=1.0,
    warning_threshold=2.0,
    restart_threshold=5.0
)

# 在聊天流中定期发送心跳
watchdog.feed_dog("stream_123")

# 停止监控
watchdog.stop()
```

---

## 模块结构

```
kernel/concurrency/
  ├── __init__.py              # 模块入口，公共 API
  ├── task_manager.py          # TaskManager 实现
  ├── task_group.py            # TaskGroup 实现
  ├── task_info.py             # TaskInfo 数据类
  ├── watchdog.py              # WatchDog 监控系统
  └── exceptions.py            # 异常定义
```

### 公共 API

**获取实例**：
- `get_task_manager()` - 获取全局 TaskManager 单例
- `get_watchdog()` - 获取全局 WatchDog 单例

**数据类型**：
- `TaskInfo` - 任务信息对象
- `TaskGroup` - 任务组上下文管理器
- `StreamHeartbeat` - 聊天流心跳信息

**异常类**：
- `ConcurrencyError` - 基础异常
- `TaskNotFoundError` - 任务未找到异常
- `TaskTimeoutError` - 任务超时异常
- `TaskGroupError` - 任务组基础异常
- `TaskGroupAlreadyExists` - 任务组已存在异常
- `TaskGroupNotFoundError` - 任务组未找到异常
- `WatchDogError` - WatchDog 监控异常

---

## 使用模式

### 模式 1: 单个任务

```python
async def download_file(url: str):
    # 下载逻辑
    pass

tm = get_task_manager()
task_info = tm.create_task(download_file("http://example.com/file.zip"))

# 稍后查询任务
print(f"任务完成: {task_info.is_done()}")
```

### 模式 2: 多个独立任务

```python
async def process_item(item_id: int):
    # 处理逻辑
    pass

tm = get_task_manager()

for i in range(10):
    tm.create_task(process_item(i), name=f"process_{i}")

# 等待所有任务
await tm.wait_all_tasks()
```

### 模式 3: 任务组

```python
async def batch_process():
    tm = get_task_manager()
    
    async with tm.group(name="batch", timeout=30) as tg:
        for item in items:
            tg.create_task(process_item(item))
```

### 模式 4: 并行执行（gather）

```python
tm = get_task_manager()

results = await tm.gather(
    task1(),
    task2(),
    task3(),
    return_exceptions=True  # 捕获异常而不中断
)
```

### 模式 5: 守护任务

```python
# 创建一个后台守护任务，不会被 wait_all_tasks 等待
tm.create_task(
    background_monitor(),
    name="monitor",
    daemon=True  # 不阻塞应用关闭
)
```

### 模式 6: 带超时的任务

```python
tm.create_task(
    long_running_operation(),
    name="long_task",
    timeout=60  # 60秒后如果未完成会被取消
)
```

### 模式 7: 任务元数据

```python
task_info = tm.create_task(
    fetch_data(),
    name="fetch",
    metadata={
        "source": "database",
        "priority": "high",
        "user_id": "user_123",
        "retry_count": 0
    }
)

# 查询元数据
print(task_info.metadata["source"])
```

---

## 最佳实践

### 1. 始终使用 TaskManager 而不是 asyncio.create_task

```python
# ✗ 不推荐 - 任务可能泄漏
task = asyncio.create_task(my_coro())

# ✓ 推荐 - 任务被正确追踪
tm = get_task_manager()
task_info = tm.create_task(my_coro())
```

### 2. 为任务提供有意义的名称

```python
# ✗ 不好 - 难以调试
task = tm.create_task(fetch_data())

# ✓ 好 - 便于追踪
task = tm.create_task(
    fetch_data("user_123"),
    name="fetch_user_data_123"
)
```

### 3. 使用任务组管理相关任务

```python
# ✗ 不好 - 难以整体管理
tm.create_task(process(1))
tm.create_task(process(2))
tm.create_task(process(3))

# ✓ 好 - 统一管理
async with tm.group(name="batch_process") as tg:
    for i in range(1, 4):
        tg.create_task(process(i))
```

### 4. 设置合理的超时时间

```python
# ✓ 好的实践
tm.create_task(
    database_query(),
    name="db_query",
    timeout=30  # 30秒超时
)
```

### 5. 使用元数据追踪上下文

```python
tm.create_task(
    process_order(order_id),
    name=f"process_order_{order_id}",
    metadata={
        "order_id": order_id,
        "user_id": user_id,
        "timestamp": datetime.now().isoformat()
    }
)
```

### 6. 正确处理异常

```python
results = await gather(
    task1(),
    task2(),
    task3(),
    return_exceptions=True  # 返回异常而非抛出
)

for i, result in enumerate(results):
    if isinstance(result, Exception):
        print(f"Task {i} 失败: {result}")
    else:
        print(f"Task {i} 结果: {result}")
```

### 7. 启动 WatchDog 监控

```python
# 在应用启动时
watchdog = get_watchdog()
watchdog.start()

# 在应用关闭时
watchdog.stop()
```

---

## 常见问题

### Q: TaskManager 是线程安全的吗？

A: 是的。TaskManager 在所有关键操作中使用线程锁确保线程安全。可以从多个线程调用 `get_task_manager()` 获取单例实例。

### Q: 为什么需要 WatchDog？

A: WatchDog 提供后台监控，包括：
1. 超时任务自动取消
2. 聊天流心跳检测（用于检测驱动器卡死）
3. 已完成任务的自动清理
4. 系统健康检查

### Q: 守护任务和普通任务的区别？

A: 
- **普通任务**：会被 `wait_all_tasks()` 等待，会被 WatchDog 超时检查
- **守护任务**：不被 `wait_all_tasks()` 等待，也不被 WatchDog 超时检查

### Q: 如何监控任务执行？

A: 使用 TaskManager 的查询接口：

```python
tm = get_task_manager()

# 获取统计信息
stats = tm.get_stats()
print(f"活跃任务: {stats['active_tasks']}")

# 获取所有活跃任务
active_tasks = tm.get_active_tasks()
for task_info in active_tasks:
    print(f"{task_info.name}: {task_info}")
```

### Q: 如何取消任务？

A: 有两种方式：

```python
# 方式 1：通过 task_id 取消
tm.cancel_task(task_id)

# 方式 2：直接取消
task_info.cancel()

# 方式 3：在任务组中，任一异常时自动取消其他任务
async with tm.group(name="group", cancel_on_error=True) as tg:
    tg.create_task(task1())  # 失败会取消其他任务
    tg.create_task(task2())
```

### Q: 如何处理任务超时？

A: 设置 `timeout` 参数，WatchDog 会自动取消超时任务：

```python
tm.create_task(
    slow_operation(),
    timeout=30  # 30秒后取消
)

# 之后可以通过元数据追踪
task_info = tm.get_task(task_id)
if task_info.is_cancelled():
    print("任务被 WatchDog 取消（超时）")
```

---

## 相关资源

- [TaskManager API 详解](./task_manager.md) - 任务管理器完整文档
- [TaskGroup 详解](./task_group.md) - 任务组管理详解
- [WatchDog 监控](./watchdog.md) - 监控系统详解
- [异常类型](./exceptions.md) - 异常类型参考
- [高级用法](./advanced.md) - 性能优化和高级模式
