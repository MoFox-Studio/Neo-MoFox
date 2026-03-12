# TaskGroup 详解

## 概述

TaskGroup 是一个异步上下文管理器，提供作用域化的任务管理。它允许在一个特定的上下文中创建和管理一组相关的异步任务，确保所有任务在退出上下文时都已完成。

**核心特性**：
- 作用域化任务管理
- 自动等待所有任务完成
- 组级别的超时控制
- 异常处理和自动取消机制
- 跨模块共享（通过名称）

---

## 基本使用

### 创建任务组

使用 TaskManager 的 `group()` 方法获取任务组：

```python
from src.kernel.concurrency import get_task_manager

tm = get_task_manager()

async with tm.group(name="my_group") as tg:
    # 在上下文内使用任务组
    pass
```

### 在组内创建任务

```python
async def download_file(url: str):
    print(f"下载 {url}")
    await asyncio.sleep(2)
    return f"下载完成: {url}"

async def main():
    tm = get_task_manager()
    
    urls = [
        "http://example.com/file1.zip",
        "http://example.com/file2.zip",
        "http://example.com/file3.zip",
    ]
    
    async with tm.group(name="downloads") as tg:
        for url in urls:
            tg.create_task(download_file(url), name=f"download_{urls.index(url)}")
    
    print("所有文件已下载")

asyncio.run(main())
```

---

## 参数配置

### group() 方法签名

```python
def group(
    name: str,
    timeout: float | None = None,
    cancel_on_error: bool = True,
) -> TaskGroup
```

### 参数说明

#### name：任务组名称

**类型**：`str`  
**必须**：是  
**用途**：唯一标识任务组，用于共享

**说明**：
- 同名的任务组会被共享，多个模块可以向同一组添加任务
- 组名应该有意义，便于调试

**示例**：

```python
# 模块 A
async with tm.group(name="data_processing") as tg:
    tg.create_task(process_user_data())

# 模块 B（稍后）
async with tm.group(name="data_processing") as tg:  # 获取同一个组
    tg.create_task(process_order_data())
    # 会等待所有任务完成
```

#### timeout：超时时间

**类型**：`float | None`  
**默认**：`None`（无超时）  
**单位**：秒  
**用途**：设置整组的超时时间

**说明**：
- 如果组内任何任务超过此时间，所有未完成任务会被取消
- `None` 表示没有超时限制

**示例**：

```python
# 30秒内完成所有任务
async with tm.group(name="quick_tasks", timeout=30) as tg:
    for i in range(10):
        tg.create_task(quick_process(i))
    # 如果 30 秒后还有任务未完成，会被取消
```

#### cancel_on_error：错误时取消

**类型**：`bool`  
**默认**：`True`  
**用途**：控制当任一任务失败时的行为

**说明**：
- `True`：任一任务异常时，立即取消所有其他任务
- `False`：任务异常不影响其他任务，但异常会被抛出

**示例**：

```python
# 示例 1：任一任务失败立即取消其他任务
async with tm.group(name="critical_tasks", cancel_on_error=True) as tg:
    tg.create_task(critical_step_1())
    tg.create_task(critical_step_2())
    tg.create_task(critical_step_3())
    # 如果任一步骤失败，其他步骤立即取消

# 示例 2：任务独立执行
async with tm.group(name="independent_tasks", cancel_on_error=False) as tg:
    tg.create_task(collect_data_1())
    tg.create_task(collect_data_2())
    tg.create_task(collect_data_3())
    # 各任务独立运行，不相互影响
```

---

## create_task() 方法

在任务组内创建任务。

### 方法签名

```python
def create_task(
    coro: Coroutine[Any, Any, Any],
    name: str | None = None
) -> TaskInfo
```

### 参数

- `coro`：协程对象（必须）
- `name`：任务名称（可选）

### 返回值

返回 `TaskInfo` 对象，用于查询和控制任务。

### 说明

- 只能在 `async with` 块内调用
- 在上下文外调用会抛出 `TaskGroupError`
- 任务自动属于该组

### 使用示例

```python
async def main():
    tm = get_task_manager()
    
    async with tm.group(name="batch") as tg:
        # 创建命名任务
        task1 = tg.create_task(fetch("url1"), name="fetch_url1")
        
        # 创建匿名任务（自动生成名称）
        task2 = tg.create_task(process())
        
        # 获取任务 ID 用于后续查询
        print(f"Task 1 ID: {task1.task_id}")
        
        # 任务信息
        print(f"Task 1 name: {task1.name}")
        print(f"Task 1 status: {task1}")

asyncio.run(main())
```

---

## 错误处理

### 在上下文外创建任务

```python
tm = get_task_manager()

group = tm.group(name="my_group")

# ✗ 错误！未进入 async with
try:
    group.create_task(my_task())  # TaskGroupError!
except Exception as e:
    print(f"错误: {e}")

# ✓ 正确！进入 async with
async with group as tg:
    tg.create_task(my_task())
```

### 处理任务异常

```python
async def task_that_fails():
    raise ValueError("操作失败")

async def main():
    tm = get_task_manager()
    
    try:
        async with tm.group(name="group", cancel_on_error=True) as tg:
            tg.create_task(task_that_fails(), name="failing_task")
            tg.create_task(long_running_task(), name="long_task")
            # long_task 会被自动取消（因为 failing_task 异常且 cancel_on_error=True）
    except ValueError as e:
        print(f"任务组异常: {e}")

asyncio.run(main())
```

### 处理超时

```python
async def slow_task():
    await asyncio.sleep(100)

async def main():
    tm = get_task_manager()
    
    try:
        async with tm.group(name="group", timeout=5) as tg:
            tg.create_task(slow_task())
            # 5 秒后任务会被取消
    except asyncio.CancelledError:
        print("任务被超时取消")

asyncio.run(main())
```

---

## 使用模式

### 模式 1: 简单的任务批处理

```python
async def process_item(item_id: int):
    print(f"处理 {item_id}")
    await asyncio.sleep(1)

async def main():
    tm = get_task_manager()
    
    items = range(10)
    
    async with tm.group(name="process_items") as tg:
        for item_id in items:
            tg.create_task(process_item(item_id), name=f"process_{item_id}")
    
    print("所有项目已处理")

asyncio.run(main())
```

### 模式 2: 数据并行收集

```python
async def fetch_user(user_id: int):
    # 模拟 API 调用
    await asyncio.sleep(0.5)
    return {"id": user_id, "name": f"User {user_id}"}

async def main():
    tm = get_task_manager()
    
    user_ids = [1, 2, 3, 4, 5]
    users = []
    
    async with tm.group(name="fetch_users", timeout=10) as tg:
        tasks = []
        for user_id in user_ids:
            task = tg.create_task(fetch_user(user_id), name=f"fetch_user_{user_id}")
            tasks.append(task)
    
    # 任务组退出后收集结果
    for task_info in tasks:
        users.append(task_info.get_result())
    
    print(f"获取的用户: {users}")

asyncio.run(main())
```

### 模式 3: 多步骤处理流程

```python
async def step1(data):
    await asyncio.sleep(1)
    return f"Step1({data})"

async def step2(data):
    await asyncio.sleep(1)
    return f"Step2({data})"

async def step3(data):
    await asyncio.sleep(1)
    return f"Step3({data})"

async def main():
    tm = get_task_manager()
    
    data = "input"
    
    # 三个步骤必须全部成功
    try:
        async with tm.group(
            name="pipeline",
            cancel_on_error=True,
            timeout=10
        ) as tg:
            t1 = tg.create_task(step1(data), name="step_1")
            t2 = tg.create_task(step2(data), name="step_2")
            t3 = tg.create_task(step3(data), name="step_3")
        
        print("流程完成")
        print(f"结果: {t1.get_result()}, {t2.get_result()}, {t3.get_result()}")
    except Exception as e:
        print(f"流程失败: {e}")

asyncio.run(main())
```

### 模式 4: 跨模块任务共享

**模块 A**：
```python
async def fetch_data():
    await asyncio.sleep(1)
    return "data"

async def start_task_a():
    tm = get_task_manager()
    async with tm.group(name="shared_group") as tg:
        tg.create_task(fetch_data(), name="fetch")
```

**模块 B**：
```python
async def process_data():
    await asyncio.sleep(1)
    return "processed"

async def start_task_b():
    tm = get_task_manager()
    async with tm.group(name="shared_group") as tg:  # 同一个组
        tg.create_task(process_data(), name="process")
```

**主程序**：
```python
async def main():
    await start_task_a()
    await start_task_b()
    # 所有任务都已完成

asyncio.run(main())
```

---

## 最佳实践

### 1. 总是提供有意义的组名

```python
# ✗ 不好
async with tm.group(name="group1") as tg:
    pass

# ✓ 好
async with tm.group(name="user_data_processing") as tg:
    pass
```

### 2. 为任务提供名称便于调试

```python
async with tm.group(name="fetch_group") as tg:
    # ✓ 好
    for user_id in user_ids:
        tg.create_task(fetch_user(user_id), name=f"fetch_user_{user_id}")
    
    # ✗ 不好 - 难以区分
    for user_id in user_ids:
        tg.create_task(fetch_user(user_id))
```

### 3. 设置合理的超时时间

```python
# ✓ 设置超时，防止卡顿
async with tm.group(name="api_calls", timeout=30) as tg:
    for url in urls:
        tg.create_task(fetch(url))

# ✗ 不设置超时，可能无限等待
async with tm.group(name="api_calls") as tg:
    for url in urls:
        tg.create_task(fetch(url))
```

### 4. 根据需要选择 cancel_on_error

```python
# 关键步骤：任一失败时立即中止
async with tm.group(
    name="critical",
    cancel_on_error=True
) as tg:
    tg.create_task(critical_step_1())
    tg.create_task(critical_step_2())

# 数据收集：各自独立
async with tm.group(
    name="collection",
    cancel_on_error=False
) as tg:
    tg.create_task(collect_from_api1())
    tg.create_task(collect_from_api2())
    tg.create_task(collect_from_api3())
```

### 5. 收集任务结果

```python
async with tm.group(name="group") as tg:
    tasks = []
    for i in range(10):
        task = tg.create_task(compute(i), name=f"compute_{i}")
        tasks.append(task)

# 任务组退出后收集结果
results = [task.get_result() for task in tasks]
```

---

## 与 TaskManager 的关系

TaskGroup 通过 TaskManager 获取：

```python
tm = get_task_manager()

# 两种使用方式：

# 方式 1：直接使用
async with tm.group(name="group") as tg:
    pass

# 方式 2：先获取再使用（不推荐）
group = tm.group(name="group")
async with group as tg:
    pass
```

---

## 常见问题

### Q: 如何知道任务组内所有任务都完成了？

A: 退出 `async with` 块时，所有任务都已完成。

```python
async with tm.group(name="group") as tg:
    tg.create_task(task1())
    tg.create_task(task2())

# 到这里，两个任务都已完成
```

### Q: 能否在任务组外使用它？

A: 不能。在 `async with` 块外调用 `create_task()` 会抛出 `TaskGroupError`。

### Q: 同名的任务组是否会被合并？

A: 是的。多个 `tm.group("name")` 调用会返回同一个 TaskGroup 实例。

```python
group1 = tm.group(name="shared")
group2 = tm.group(name="shared")
assert group1 is group2  # True
```

### Q: 如何处理组内任务的异常？

A: 异常会在离开 `async with` 块时被抛出。

```python
async with tm.group(name="group", cancel_on_error=False) as tg:
    tg.create_task(failing_task())

# 会在这里抛出异常
```

---

## 相关资源

- [Concurrency 主文档](./README.md) - 概览
- [TaskManager 详解](./task_manager.md) - 任务管理器
- [类型定义](./types.md) - TaskInfo 等类型
- [WatchDog 监控](./watchdog.md) - 后台监控
