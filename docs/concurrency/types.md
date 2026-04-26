# Concurrency 类型定义

## 概述

本文档介绍了 Concurrency 模块中的所有重要数据类和类型定义。

---

## TaskInfo 数据类

任务信息对象，存储异步任务的元数据和提供查询/操作接口。

### 定义

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Coroutine
import asyncio

@dataclass
class TaskInfo:
    """异步任务信息"""
    
    task_id: str
    name: str | None
    coro: Coroutine[Any, Any, Any] | None
    task: asyncio.Task[Any] | None
    daemon: bool
    timeout: float | None
    created_at: datetime
    group_name: str | None
    metadata: dict[str, Any]
```

### 属性

| 属性 | 类型 | 描述 |
|------|------|------|
| `task_id` | `str` | 任务唯一标识符（UUID） |
| `name` | `str \| None` | 任务名称 |
| `coro` | `Coroutine` | 任务协程对象 |
| `task` | `asyncio.Task \| None` | asyncio.Task 对象 |
| `daemon` | `bool` | 是否为守护任务 |
| `timeout` | `float \| None` | 超时时间（秒） |
| `created_at` | `datetime` | 任务创建时间 |
| `group_name` | `str \| None` | 所属任务组名称 |
| `metadata` | `dict[str, Any]` | 自定义元数据 |

### 方法

#### is_done()

检查任务是否已完成。

```python
def is_done() -> bool
```

**返回**：`True` 如果任务已完成（无论是成功、失败还是取消）

**使用示例**：

```python
task_info = tm.create_task(my_task())

if task_info.is_done():
    print("任务已完成")
else:
    print("任务仍在运行")
```

#### is_cancelled()

检查任务是否被取消。

```python
def is_cancelled() -> bool
```

**返回**：`True` 如果任务被取消

**使用示例**：

```python
if task_info.is_cancelled():
    print("任务被取消")
```

#### is_failed()

检查任务是否失败。

```python
def is_failed() -> bool
```

**返回**：`True` 如果任务完成且抛出异常

**使用示例**：

```python
if task_info.is_failed():
    print("任务失败")
    print(f"错误: {task_info.get_exception()}")
```

#### get_exception()

获取任务的异常（如果有）。

```python
def get_exception() -> BaseException | None
```

**返回**：异常对象，如果任务成功则返回 `None`

**使用示例**：

```python
exc = task_info.get_exception()
if exc:
    print(f"任务异常: {type(exc).__name__}: {exc}")
```

#### get_result()

获取任务的返回值。

```python
def get_result() -> Any
```

**返回**：任务的返回值

**异常**：
- `InvalidStateError`：如果任务未完成
- 任务抛出的异常

**使用示例**：

```python
if task_info.is_done():
    try:
        result = task_info.get_result()
        print(f"结果: {result}")
    except Exception as e:
        print(f"任务异常: {e}")
```

#### cancel()

取消任务。

```python
def cancel() -> bool
```

**返回**：`True` 如果成功发送取消请求，`False` 如果任务已完成

**使用示例**：

```python
if task_info.cancel():
    print("取消请求已发送")
else:
    print("无法取消（任务已完成）")
```

#### __repr__()

获取任务的字符串表示。

```python
def __repr__() -> str
```

**使用示例**：

```python
task_info = tm.create_task(my_task(), name="my_task")
print(task_info)
# 输出: TaskInfo(id=a1b2c3d4, name=my_task, status=running)

# 任务完成后
# 输出: TaskInfo(id=a1b2c3d4, name=my_task, status=completed)

# 任务被取消
# 输出: TaskInfo(id=a1b2c3d4, name=my_task, status=cancelled)
```

---

## TaskGroup 数据类

任务组对象，提供作用域化的任务管理。

### 定义

```python
@dataclass
class TaskGroup:
    """任务组上下文管理器"""
    
    name: str
    timeout: float | None
    cancel_on_error: bool
    tasks: list[TaskInfo]
```

### 属性

| 属性 | 类型 | 描述 |
|------|------|------|
| `name` | `str` | 任务组名称 |
| `timeout` | `float \| None` | 整组超时时间（秒） |
| `cancel_on_error` | `bool` | 任一任务异常时是否取消其他任务 |
| `tasks` | `list[TaskInfo]` | 组内所有任务列表 |

### 使用方式

TaskGroup 是异步上下文管理器，需要使用 `async with` 语句：

```python
tm = get_task_manager()

async with tm.group(
    name="batch_process",
    timeout=60,
    cancel_on_error=True
) as tg:
    # 在组内创建任务
    for item in items:
        tg.create_task(process(item))
    
    # 退出时自动等待所有任务完成
```

### 方法

#### create_task()

在组内创建任务。

```python
def create_task(
    coro: Coroutine,
    name: str | None = None
) -> TaskInfo
```

**参数**：
- `coro`：协程对象
- `name`：任务名称

**返回**：TaskInfo 对象

**说明**：
- 在 TaskGroup 内创建的任务自动属于该组
- 退出上下文时会等待所有组内任务完成

**使用示例**：

```python
async with tm.group(name="api_calls") as tg:
    tg.create_task(fetch("url1"), name="fetch_1")
    tg.create_task(fetch("url2"), name="fetch_2")
    tg.create_task(fetch("url3"), name="fetch_3")
```

---

## StreamHeartbeat 数据类

聊天流心跳信息，用于 WatchDog 监控。

### 定义

```python
@dataclass
class StreamHeartbeat:
    """聊天流心跳信息"""
    
    stream_id: str
    last_tick: datetime
    tick_interval: float
    warning_threshold: float
    restart_threshold: float
    restart_callback: Callable[[], Any] | None
```

### 属性

| 属性 | 类型 | 描述 |
|------|------|------|
| `stream_id` | `str` | 聊天流唯一标识符 |
| `last_tick` | `datetime` | 最后一次心跳时间 |
| `tick_interval` | `float` | 正常 tick 间隔（秒） |
| `warning_threshold` | `float` | 警告阈值（秒） |
| `restart_threshold` | `float` | 重启阈值（秒） |
| `restart_callback` | `Callable \| None` | 重启回调函数 |

### 使用示例

```python
from src.kernel.concurrency import get_watchdog

watchdog = get_watchdog()

def on_restart():
    print("聊天流已重启")

# 注册聊天流
heartbeat = watchdog.register_stream(
    stream_id="stream_123",
    tick_interval=1.0,
    warning_threshold=2.0,  # 超过 2 秒输出警告
    restart_threshold=5.0,  # 超过 5 秒尝试重启
    restart_callback=on_restart
)

# 定期发送心跳
watchdog.feed_dog("stream_123")
```

---

## 异常类型

### ConcurrencyError

所有并发模块异常的基类。

```python
class ConcurrencyError(Exception):
    """并发模块基础异常"""
    pass
```

### TaskNotFoundError

任务未找到异常。

```python
class TaskNotFoundError(ConcurrencyError):
    """任务未找到异常"""
    
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id  # 未找到的任务 ID
```

**使用示例**：

```python
from src.kernel.concurrency import TaskNotFoundError

try:
    task = tm.get_task("invalid_id")
except TaskNotFoundError as e:
    print(f"任务 {e.task_id} 未找到")
```

### TaskTimeoutError

任务超时异常。

```python
class TaskTimeoutError(ConcurrencyError):
    """任务超时异常"""
    
    def __init__(self, task_id: str, timeout: float) -> None:
        self.task_id = task_id      # 超时的任务 ID
        self.timeout = timeout      # 超时设置的时间
```

**使用示例**：

```python
from src.kernel.concurrency import TaskTimeoutError

try:
    # 处理超时情况
    if task_info.is_cancelled() and task_info.timeout:
        raise TaskTimeoutError(task_info.task_id, task_info.timeout)
except TaskTimeoutError as e:
    print(f"任务 {e.task_id} 在 {e.timeout}s 后超时")
```

### TaskGroupError

任务组异常。

```python
class TaskGroupError(ConcurrencyError):
    """任务组异常"""
    pass
```

**常见场景**：

```python
from src.kernel.concurrency import TaskGroupError

try:
    # 尝试在未激活的任务组中创建任务
    group = tm.group(name="group")
    group.create_task(my_task())  # 异常！未进入 async with
except TaskGroupError as e:
    print(f"任务组错误: {e}")
```

### TaskGroupAlreadyExists

任务组已存在异常。

```python
class TaskGroupAlreadyExists(TaskGroupError):
    """任务组已存在异常"""
    
    def __init__(self, group_name: str) -> None:
        self.group_name = group_name
```

### TaskGroupNotFoundError

任务组未找到异常。

```python
class TaskGroupNotFoundError(TaskGroupError):
    """任务组未找到异常"""
    
    def __init__(self, group_name: str) -> None:
        self.group_name = group_name
```

### WatchDogError

WatchDog 异常。

```python
class WatchDogError(ConcurrencyError):
    """WatchDog 异常"""
    pass
```

**使用示例**：

```python
from src.kernel.concurrency import WatchDogError, get_watchdog

watchdog = get_watchdog()

try:
    watchdog.start()
    watchdog.start()  # 异常！已经运行
except WatchDogError as e:
    print(f"WatchDog 错误: {e}")
```

---

## 类型提示速查表

| 类型 | 用途 |
|------|------|
| `TaskInfo` | 单个任务的信息 |
| `TaskGroup` | 任务组管理器 |
| `StreamHeartbeat` | 流心跳信息 |
| `ConcurrencyError` | 基础异常 |
| `TaskNotFoundError` | 任务不存在异常 |
| `TaskTimeoutError` | 任务超时异常 |
| `TaskGroupError` | 任务组异常 |

---

## 使用建议

1. **导入所需类型**：
   ```python
   from src.kernel.concurrency import (
       TaskInfo,
       TaskGroup,
       StreamHeartbeat,
       ConcurrencyError,
       TaskNotFoundError,
       TaskTimeoutError,
   )
   ```

2. **类型注解**：
   ```python
   def process_task(task_info: TaskInfo) -> None:
       if task_info.is_done():
           print(f"任务 {task_info.name} 已完成")
   ```

3. **异常处理**：
   ```python
   from src.kernel.concurrency import TaskNotFoundError, ConcurrencyError
   
   try:
       task = tm.get_task(task_id)
   except TaskNotFoundError:
       print("任务不存在")
   except ConcurrencyError:
       print("并发错误")
   ```

---

## 相关资源

- [Concurrency 主文档](./README.md) - 概览
- [TaskManager 详解](./task_manager.md) - 任务管理器
- [TaskGroup 详解](./task_group.md) - 任务组管理
- [WatchDog 监控](./watchdog.md) - 监控系统
