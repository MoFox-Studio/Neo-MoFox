# Scheduler 模块文档

## 概述

Scheduler 模块提供了一个统一的异步任务调度系统，支持基于时间、事件和自定义条件的任务触发。它是 Neo-MoFox 框架中用于管理后台任务、定时任务和事件驱动任务的核心组件。

### 核心特性

- **多种触发方式**：时间触发（延迟、周期、指定时间）、事件触发、自定义条件触发
- **任务隔离**：每个任务独立执行，互不阻塞
- **优雅降级**：失败任务不影响其他任务执行
- **资源管理**：自动清理完成的任务，防止内存泄漏
- **超时保护**：防止任务永久挂起
- **并发控制**：使用信号量限制并发任务数
- **重试机制**：自动重试失败的任务
- **完整的统计**：追踪任务执行情况、成功率、延迟等

## 模块结构

```
kernel/scheduler/
├── __init__.py           # 公开 API 导出
├── core.py              # 核心调度器实现
├── types.py             # 类型定义（TriggerType、TaskStatus 等）
├── time_utils.py        # 时间计算工具函数
└── README.md            # 本文档
```

## 快速开始

### 基础示例 - 延迟执行

```python
import asyncio
from kernel.scheduler import get_unified_scheduler, TriggerType

async def main():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    # 定义任务回调
    async def hello_task():
        print("Hello, scheduled task!")
    
    # 创建延迟 3 秒执行的任务
    schedule_id = await scheduler.create_schedule(
        callback=hello_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 3},
        task_name="hello"
    )
    
    # 等待任务执行
    await asyncio.sleep(5)
    
    await scheduler.stop()

asyncio.run(main())
```

### 周期性任务

```python
async def main():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    counter = 0
    async def count_task():
        nonlocal counter
        counter += 1
        print(f"计数: {counter}")
    
    # 每隔 2 秒执行一次
    await scheduler.create_schedule(
        callback=count_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"interval_seconds": 2},
        is_recurring=True,
        task_name="counter"
    )
    
    # 运行 10 秒
    await asyncio.sleep(10)
    
    await scheduler.stop()

asyncio.run(main())
```

### 自定义条件触发

```python
async def main():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    state = {"ready": False}
    
    async def condition_check():
        return state["ready"]
    
    async def conditional_task():
        print("条件满足，任务执行!")
    
    # 创建条件触发的任务
    await scheduler.create_schedule(
        callback=conditional_task,
        trigger_type=TriggerType.CUSTOM,
        trigger_config={"condition_func": condition_check},
        is_recurring=True,
        task_name="conditional"
    )
    
    # 改变状态触发任务
    await asyncio.sleep(3)
    state["ready"] = True
    
    await asyncio.sleep(5)
    
    await scheduler.stop()

asyncio.run(main())
```

## 核心概念

### 1. 触发类型（TriggerType）

#### TIME - 时间触发

支持三种时间触发方式：

**延迟触发**：在指定的延迟后执行一次
```python
trigger_config = {
    "delay_seconds": 5  # 5秒后执行
}
```

**周期触发**：每隔指定的间隔时间执行一次
```python
trigger_config = {
    "interval_seconds": 10  # 每10秒执行一次
}
is_recurring = True
```

**指定时间触发**：在指定的时间执行
```python
from datetime import datetime, timedelta

trigger_time = datetime.now() + timedelta(hours=1)
trigger_config = {
    "trigger_at": trigger_time  # 1小时后执行
}
```

#### EVENT - 事件触发（预留）

预留接口，用于未来集成事件系统。当指定事件触发时，对应的任务会被立即执行。

```python
trigger_config = {
    "event_name": "user_login"  # 当 user_login 事件触发时执行
}
```

#### CUSTOM - 自定义条件触发

基于自定义条件函数的触发。任务会定期检查条件，当条件为真时执行。

```python
async def my_condition():
    return some_flag == True

trigger_config = {
    "condition_func": my_condition
}
```

### 2. 任务状态（TaskStatus）

| 状态 | 含义 | 说明 |
|------|------|------|
| `PENDING` | 等待中 | 任务已创建但未执行 |
| `RUNNING` | 执行中 | 任务正在执行 |
| `COMPLETED` | 已完成 | 任务执行成功 |
| `FAILED` | 已失败 | 任务执行失败 |
| `CANCELLED` | 已取消 | 任务被手动取消 |
| `PAUSED` | 已暂停 | 任务暂停中（预留） |
| `TIMEOUT` | 超时 | 任务执行超时 |

### 3. 任务执行记录（TaskExecution）

每次任务执行都会记录一条执行记录，包含：

- `execution_id`：执行唯一标识
- `started_at`：开始时间
- `ended_at`：结束时间（如果已结束）
- `status`：执行状态
- `error`：错误信息（如果失败）
- `result`：执行结果（如果成功）
- `duration`：执行耗时（秒）

### 4. 调度器配置（SchedulerConfig）

```python
@dataclass
class SchedulerConfig:
    check_interval: float = 1.0              # 检查间隔（秒）
    
    # 超时配置
    task_default_timeout: float = 300.0      # 默认任务超时（5分钟）
    task_cancel_timeout: float = 10.0        # 任务取消超时（10秒）
    shutdown_timeout: float = 30.0           # 关闭超时（30秒）
    
    # 并发控制
    max_concurrent_tasks: int = 100          # 最大并发任务数
    enable_task_semaphore: bool = True       # 是否启用任务信号量
    
    # 重试配置
    enable_retry: bool = True                # 是否启用重试
    max_retries: int = 3                     # 最大重试次数
    retry_delay: float = 5.0                 # 重试延迟（秒）
    
    # 资源管理
    cleanup_interval: float = 60.0           # 清理间隔（秒）
    keep_completed_tasks: int = 100          # 保留的已完成任务数
```

## 详细 API

### UnifiedScheduler 类

#### 生命周期管理

##### start()

启动调度器。必须在创建任务之前调用。

```python
scheduler = get_unified_scheduler()
await scheduler.start()
```

##### stop()

优雅地停止调度器。会：
1. 取消后台检查循环
2. 等待所有正在执行的任务完成（带超时保护）
3. 清理所有资源

```python
await scheduler.stop()
```

#### 任务创建与管理

##### create_schedule()

创建调度任务。

```python
schedule_id = await scheduler.create_schedule(
    callback: Callable[..., Awaitable[Any]],           # 回调函数
    trigger_type: TriggerType,                         # 触发类型
    trigger_config: dict[str, Any],                    # 触发配置
    is_recurring: bool = False,                        # 是否循环
    task_name: str | None = None,                      # 任务名称
    callback_args: tuple | None = None,                # 位置参数
    callback_kwargs: dict | None = None,               # 关键字参数
    force_overwrite: bool = False,                     # 强制覆盖
    timeout: float | None = None,                      # 超时时间（秒）
    max_retries: int = 0                               # 最大重试次数
) -> str
```

**返回值**：创建的任务 ID

**异常**：
- `RuntimeError`：调度器未运行
- `ValueError`：同名任务已存在且未启用强制覆盖

**示例**：
```python
schedule_id = await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5},
    task_name="my_task",
    timeout=30.0,
    max_retries=2
)
```

##### remove_schedule()

移除指定的调度任务。如果任务正在执行，会先取消。

```python
success = await scheduler.remove_schedule(schedule_id: str) -> bool
```

##### remove_schedule_by_name()

根据任务名称移除任务。

```python
success = await scheduler.remove_schedule_by_name(task_name: str) -> bool
```

##### find_schedule_by_name()

查找任务 ID。

```python
schedule_id = await scheduler.find_schedule_by_name(task_name: str) -> str | None
```

##### trigger_schedule()

强制立即执行指定的任务。

```python
success = await scheduler.trigger_schedule(schedule_id: str) -> bool
```

##### get_schedule()

获取任务信息。

```python
task = await scheduler.get_schedule(schedule_id: str) -> ScheduleTask | None
```

#### 查询与统计

##### list_all_schedules()

列出所有活跃任务。

```python
tasks = await scheduler.list_all_schedules() -> list[ScheduleTask]
```

##### get_task_execution_history()

获取任务的执行历史记录。

```python
history = await scheduler.get_task_execution_history(
    schedule_id: str,
    limit: int | None = None
) -> list[TaskExecution]
```

##### get_statistics()

获取调度器的统计信息。

```python
stats = await scheduler.get_statistics() -> dict[str, Any]
```

返回包含以下信息的字典：
- `uptime_seconds`：运行时长
- `total_executions`：总执行次数
- `total_failures`：总失败次数
- `total_timeouts`：总超时次数
- `active_tasks`：活跃任务数
- `completed_tasks`：已完成任务数

#### 事件处理（预留）

##### trigger_event()

触发事件，执行所有订阅该事件的任务。

```python
await scheduler.trigger_event(
    event_name: str,
    event_params: dict[str, Any] | None = None
)
```

## 使用模式

### 模式 1：定时清理任务

```python
async def cleanup_old_files():
    """清理旧文件"""
    print("正在清理旧文件...")
    # 清理逻辑

scheduler = get_unified_scheduler()
await scheduler.start()

# 每天凌晨 2 点执行一次
await scheduler.create_schedule(
    callback=cleanup_old_files,
    trigger_type=TriggerType.TIME,
    trigger_config={
        "trigger_at": datetime.now().replace(hour=2, minute=0, second=0) + timedelta(days=1),
        "interval_seconds": 86400  # 每24小时重复
    },
    is_recurring=True,
    task_name="daily_cleanup"
)
```

### 模式 2：条件触发重试

```python
async def check_service_ready():
    """检查服务是否准备好"""
    return service_is_ready

async def initialize_after_ready():
    """服务准备好后初始化"""
    print("服务已准备好，开始初始化...")
    await do_initialization()

await scheduler.create_schedule(
    callback=initialize_after_ready,
    trigger_type=TriggerType.CUSTOM,
    trigger_config={"condition_func": check_service_ready},
    is_recurring=False,  # 只执行一次
    task_name="init_when_ready"
)
```

### 模式 3：带重试的关键任务

```python
async def critical_operation():
    """关键操作，可能失败"""
    if random_failure():
        raise Exception("操作失败")
    print("操作成功!")

await scheduler.create_schedule(
    callback=critical_operation,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 1},
    task_name="critical_op",
    timeout=10.0,
    max_retries=3  # 失败后最多重试 3 次
)
```

### 模式 4：传递参数到任务

```python
async def process_data(data_id: int, action: str):
    """处理数据"""
    print(f"处理数据 {data_id}，操作: {action}")

await scheduler.create_schedule(
    callback=process_data,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 2},
    task_name="process_data",
    callback_args=(123,),
    callback_kwargs={"action": "update"}
)
```

## 常见问题

### Q: 如何确保任务不会永久挂起？

A: 调度器内置超时保护。每个任务都有一个超时时间，默认为 5 分钟。超过超时时间后，任务会被强制取消。你也可以在创建任务时指定 `timeout` 参数：

```python
await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 10},
    timeout=30.0  # 30秒超时
)
```

### Q: 周期性任务精度如何？

A: 调度器每 1 秒检查一次所有任务。周期性任务的精度取决于检查间隔和系统负载。对于精度要求不是非常高的应用（如日志清理、数据同步等）已经足够。对于高精度的任务，建议使用专门的时间库。

### Q: 如何监控任务执行情况？

A: 你可以：

1. 访问任务的 `execution_history`：
```python
task = await scheduler.get_schedule(schedule_id)
for execution in task.execution_history:
    print(f"执行 ID: {execution.execution_id}")
    print(f"状态: {execution.status.value}")
    print(f"耗时: {execution.duration}s")
```

2. 获取整体统计：
```python
stats = await scheduler.get_statistics()
print(f"总执行: {stats['total_executions']}")
print(f"失败: {stats['total_failures']}")
```

### Q: 调度器关闭时正在执行的任务会怎样？

A: 调度器关闭是优雅的：
1. 首先停止接收新任务
2. 等待所有正在执行的任务完成（最多 30 秒）
3. 如果超时，强制取消剩余任务
4. 清理所有资源

### Q: 是否支持同步回调函数？

A: 支持。同步函数会在线程池中运行，避免阻塞事件循环：

```python
def sync_task():
    print("这是同步任务")
    time.sleep(1)

await scheduler.create_schedule(
    callback=sync_task,  # 同步函数也可以
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5}
)
```

## 性能考虑

- **检查间隔**：默认 1 秒，决定了任务触发的粒度
- **并发限制**：默认最多 100 个并发任务，可通过配置调整
- **内存管理**：已完成任务会定期清理，每个任务最多保留 10 条执行记录
- **CPU 开销**：主要来自于定期检查和任务执行，通常很小

## 相关资源

- [Types 定义](./types.md) - 类型和枚举定义
- [Core 实现](./core.md) - 核心实现细节
- [Time Utils](./time_utils.md) - 时间工具函数

## 版本信息

当前版本：1.0.0
