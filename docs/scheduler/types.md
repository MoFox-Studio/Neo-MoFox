# Types 模块

## 概述

`types.py` 定义调度器的核心枚举与执行记录模型：

- `TriggerType`
- `TaskStatus`
- `TaskExecution`

---

## `TriggerType`

```python
class TriggerType(Enum):
    TIME = "time"
    EVENT = "event"
    CUSTOM = "custom"
```

### 含义

- `TIME`：时间触发（延迟/周期/指定时间）
- `EVENT`：事件触发（通过 `trigger_event()`）
- `CUSTOM`：自定义条件函数触发

### 示例

```python
from src.kernel.scheduler import TriggerType

# 时间触发
trigger_type = TriggerType.TIME

# 自定义条件触发
trigger_type = TriggerType.CUSTOM
```

---

## `TaskStatus`

```python
class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    TIMEOUT = "timeout"
```

### 状态流转（常见）

- 一次性成功：`PENDING -> RUNNING -> COMPLETED`
- 一次性失败：`PENDING -> RUNNING -> FAILED`
- 一次性超时：`PENDING -> RUNNING -> TIMEOUT`
- 循环任务成功：`PENDING -> RUNNING -> PENDING`
- 手动暂停：`PENDING -> PAUSED`
- 恢复运行：`PAUSED -> PENDING`

---

## `TaskExecution`

```python
@dataclass
class TaskExecution:
    execution_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: TaskStatus = TaskStatus.RUNNING
    error: Exception | None = None
    result: Any = None
    duration: float = 0.0
```

### 字段说明

- `execution_id`：本次执行唯一标识
- `started_at` / `ended_at`：开始/结束时间
- `status`：执行状态
- `error`：失败时的异常对象
- `result`：成功时返回值
- `duration`：执行耗时（秒）

### 方法

```python
execution.complete(result=None) -> None
execution.fail(error: Exception) -> None
execution.cancel() -> None
```

这些方法会写入结束时间并更新状态与耗时。

---

## 在调度器中的使用

`ScheduleTask.execution_history` 会保留最近 10 次执行记录。

可通过 `get_task_info(schedule_id)` 获取汇总统计，也可在调度器内部对象上查看执行历史。

---

## 示例

```python
from src.kernel.scheduler import TaskStatus

info = await scheduler.get_task_info(schedule_id)
if info and info["status"] == TaskStatus.RUNNING.value:
    print("任务运行中")
```

---

## 参考

- [README.md](README.md)
- [core.md](core.md)
- [src/kernel/scheduler/types.py](../../src/kernel/scheduler/types.py)
