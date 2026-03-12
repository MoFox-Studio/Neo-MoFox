# Types 模块

## 概述

`types.py` 定义了调度器中使用的所有类型、枚举和数据模型，包括触发类型、任务状态和执行记录。

## 枚举类型

### TriggerType - 触发类型

```python
class TriggerType(Enum):
    """触发类型枚举"""
    
    TIME = "time"          # 时间触发
    EVENT = "event"        # 事件触发
    CUSTOM = "custom"      # 自定义条件触发
```

**用途**：指定任务的触发方式。

**使用示例**：
```python
from kernel.scheduler import TriggerType

# 时间触发
await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5}
)

# 自定义条件触发
await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.CUSTOM,
    trigger_config={"condition_func": check_condition}
)
```

---

### TaskStatus - 任务状态

```python
class TaskStatus(Enum):
    """任务状态枚举"""
    
    PENDING = "pending"       # 等待触发
    RUNNING = "running"       # 正在执行
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 执行失败
    CANCELLED = "cancelled"   # 已取消
    PAUSED = "paused"         # 已暂停（预留）
    TIMEOUT = "timeout"       # 执行超时
```

**说明**：

| 状态 | 进入时机 | 含义 |
|------|---------|------|
| `PENDING` | 任务创建时 | 任务已创建但未触发 |
| `RUNNING` | 任务开始执行时 | 任务正在执行中 |
| `COMPLETED` | 任务执行成功 | 对于循环任务，状态返回 PENDING；对于一次性任务，保持 COMPLETED |
| `FAILED` | 任务执行失败且重试次数用尽 | 任务执行出错 |
| `CANCELLED` | 手动取消或调度器关闭时 | 任务被主动取消 |
| `PAUSED` | 预留 | 暂不使用 |
| `TIMEOUT` | 任务执行超时 | 任务执行时间超过设定的超时时间 |

**查询示例**：
```python
task = await scheduler.get_schedule(schedule_id)
if task.status == TaskStatus.RUNNING:
    print("任务正在执行")
elif task.status == TaskStatus.COMPLETED:
    print("任务已完成")
elif task.status == TaskStatus.FAILED:
    print(f"任务失败: {task.last_error}")
```

---

## 数据模型

### TaskExecution - 任务执行记录

```python
@dataclass
class TaskExecution:
    """任务执行记录"""
    
    execution_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: TaskStatus = TaskStatus.RUNNING
    error: Exception | None = None
    result: Any = None
    duration: float = 0.0
```

**属性说明**：

| 属性 | 类型 | 说明 |
|------|------|------|
| `execution_id` | `str` | 执行的唯一标识 |
| `started_at` | `datetime` | 执行开始时间 |
| `ended_at` | `datetime \| None` | 执行结束时间（未结束时为 None） |
| `status` | `TaskStatus` | 执行状态 |
| `error` | `Exception \| None` | 执行失败时的错误对象 |
| `result` | `Any` | 执行成功时的返回值 |
| `duration` | `float` | 执行耗时（秒） |

**方法**：

#### complete()

标记执行完成。

```python
execution.complete(result: Any = None) -> None
```

**作用**：
- 设置 `ended_at` 为当前时间
- 设置 `status` 为 `COMPLETED`
- 设置 `result`
- 计算 `duration`

**示例**：
```python
execution = task.current_execution
execution.complete(result={"processed": 100})
```

#### fail()

标记执行失败。

```python
execution.fail(error: Exception) -> None
```

**作用**：
- 设置 `ended_at` 为当前时间
- 设置 `status` 为 `FAILED`
- 保存错误信息
- 计算 `duration`

**示例**：
```python
try:
    await task.callback()
except Exception as e:
    execution.fail(e)
```

#### cancel()

标记执行被取消。

```python
execution.cancel() -> None
```

**作用**：
- 设置 `ended_at` 为当前时间
- 设置 `status` 为 `CANCELLED`
- 计算 `duration`

**示例**：
```python
if should_cancel:
    execution.cancel()
```

---

## 使用示例

### 检查执行记录

```python
from kernel.scheduler import TaskStatus

# 获取任务
task = await scheduler.get_schedule(schedule_id)

# 查看最近的执行记录
if task.execution_history:
    latest = task.execution_history[-1]
    print(f"最后执行时间: {latest.started_at}")
    print(f"执行状态: {latest.status.value}")
    print(f"执行耗时: {latest.duration:.2f}s")
    
    if latest.status == TaskStatus.FAILED:
        print(f"失败原因: {latest.error}")
    elif latest.status == TaskStatus.COMPLETED:
        print(f"执行结果: {latest.result}")
```

### 统计任务执行情况

```python
# 查看任务统计
print(f"总执行次数: {task.trigger_count}")
print(f"成功次数: {task.success_count}")
print(f"失败次数: {task.failure_count}")
print(f"总耗时: {task.total_execution_time:.2f}s")
print(f"平均耗时: {task.total_execution_time / task.trigger_count:.2f}s" if task.trigger_count > 0 else "N/A")
```

### 遍历所有执行记录

```python
for execution in task.execution_history:
    print(f"ID: {execution.execution_id}")
    print(f"  开始: {execution.started_at.isoformat()}")
    print(f"  结束: {execution.ended_at.isoformat() if execution.ended_at else 'N/A'}")
    print(f"  状态: {execution.status.value}")
    print(f"  耗时: {execution.duration:.3f}s")
    if execution.error:
        print(f"  错误: {execution.error}")
    print()
```

---

## 相关资源

- [Core 模块](./core.md) - 调度器核心实现
- [主文档](./README.md) - 完整使用指南
