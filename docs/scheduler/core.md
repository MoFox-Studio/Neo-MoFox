# Core 模块

## 概述

`core.py` 包含调度器的核心实现，包括 `SchedulerConfig`、`ScheduleTask` 和 `UnifiedScheduler` 三个主要类。

## 类定义

### SchedulerConfig - 调度器配置

```python
@dataclass
class SchedulerConfig:
    """调度器配置"""
    
    # 检查间隔
    check_interval: float = 1.0
    
    # 超时配置
    task_default_timeout: float = 300.0
    task_cancel_timeout: float = 10.0
    shutdown_timeout: float = 30.0
    
    # 并发控制
    max_concurrent_tasks: int = 100
    enable_task_semaphore: bool = True
    
    # 重试配置
    enable_retry: bool = True
    max_retries: int = 3
    retry_delay: float = 5.0
    
    # 资源管理
    cleanup_interval: float = 60.0
    keep_completed_tasks: int = 100
```

**配置说明**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `check_interval` | 1.0s | 主循环检查任务触发的间隔 |
| `task_default_timeout` | 300s | 任务的默认超时时间 |
| `task_cancel_timeout` | 10s | 取消任务时的超时时间 |
| `shutdown_timeout` | 30s | 调度器关闭的超时时间 |
| `max_concurrent_tasks` | 100 | 最大并发执行的任务数 |
| `enable_task_semaphore` | true | 是否启用并发控制信号量 |
| `enable_retry` | true | 是否启用失败重试 |
| `max_retries` | 3 | 失败任务的最大重试次数 |
| `retry_delay` | 5s | 重试间隔时间 |
| `cleanup_interval` | 60s | 清理已完成任务的间隔 |
| `keep_completed_tasks` | 100 | 保留的已完成任务记录数 |

**使用示例**：
```python
from kernel.scheduler import SchedulerConfig, get_unified_scheduler

config = SchedulerConfig(
    check_interval=0.5,           # 更频繁地检查
    task_default_timeout=60.0,    # 1分钟超时
    max_concurrent_tasks=50,      # 限制并发为 50
    enable_retry=True,
    max_retries=2,
)

scheduler = UnifiedScheduler(config)
await scheduler.start()
```

---

### ScheduleTask - 调度任务

```python
@dataclass
class ScheduleTask:
    """调度任务模型"""
    
    # 基本信息
    schedule_id: str
    task_name: str
    callback: Callable[..., Awaitable[Any]]
    
    # 触发配置
    trigger_type: TriggerType
    trigger_config: dict[str, Any]
    is_recurring: bool = False
    
    # 回调参数
    callback_args: tuple[Any, ...] = field(default_factory=tuple)
    callback_kwargs: dict[str, Any] = field(default_factory=dict)
    
    # 状态信息
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    last_triggered_at: datetime | None = None
    
    # 统计信息
    trigger_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_execution_time: float = 0.0
    
    # 执行记录
    execution_history: list[TaskExecution] = field(default_factory=list)
    current_execution: TaskExecution | None = None
    
    # 重试配置
    max_retries: int = 0
    retry_count: int = 0
    last_error: Exception | None = None
    
    # 超时配置
    timeout: float | None = None
```

**属性说明**：

**基本信息**：
- `schedule_id`：任务的唯一标识符
- `task_name`：任务名称
- `callback`：任务的回调函数

**触发配置**：
- `trigger_type`：触发方式（TIME、EVENT、CUSTOM）
- `trigger_config`：触发的配置参数
- `is_recurring`：是否为循环任务

**状态与统计**：
- `status`：当前任务状态
- `trigger_count`：被触发的总次数
- `success_count`：成功执行次数
- `failure_count`：失败次数
- `total_execution_time`：总执行耗时
- `execution_history`：最近 10 次执行记录

**方法**：

#### is_active()

判断任务是否活跃（可以被触发）。

```python
if task.is_active():
    print("任务活跃中")
```

#### can_trigger()

判断任务是否可以被触发。

```python
if task.can_trigger():
    # 继续触发
    pass
```

#### start_execution()

开始一次执行，返回新的 `TaskExecution` 对象。

```python
execution = task.start_execution()
```

#### finish_execution()

完成一次执行。

```python
task.finish_execution(
    success=True,
    result={"processed": 100}
)
```

---

### UnifiedScheduler - 统一调度器

核心调度器类，提供完整的任务调度功能。

#### 初始化

```python
from kernel.scheduler import get_unified_scheduler

scheduler = get_unified_scheduler()  # 获取全局单例
```

或使用自定义配置：

```python
config = SchedulerConfig(check_interval=0.5)
scheduler = UnifiedScheduler(config)
```

#### 生命周期管理

##### start()

启动调度器。

```python
async def main():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    # 现在可以创建任务
    
    await scheduler.stop()
```

**行为**：
- 启动主循环（`_check_loop`）
- 启动清理循环（`_cleanup_loop`）
- 设置内部运行状态

##### stop()

优雅停止调度器。

```python
await scheduler.stop()
```

**行为**：
- 停止接收新任务
- 等待所有正在执行的任务完成
- 清理所有资源
- 超时时间：`shutdown_timeout`

#### 任务管理

##### create_schedule()

创建新的调度任务。

```python
schedule_id = await scheduler.create_schedule(
    callback=my_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 5},
    is_recurring=False,
    task_name="my_task",
    callback_args=(),
    callback_kwargs={},
    force_overwrite=False,
    timeout=None,
    max_retries=0
)
```

**参数**：
- `callback`：异步回调函数
- `trigger_type`：触发类型
- `trigger_config`：触发配置字典
- `is_recurring`：是否循环
- `task_name`：任务名称
- `callback_args`：回调函数位置参数
- `callback_kwargs`：回调函数关键字参数
- `force_overwrite`：同名任务存在时是否覆盖
- `timeout`：任务超时时间（秒）
- `max_retries`：最大重试次数

**异常**：
- `RuntimeError`：调度器未运行
- `ValueError`：同名任务已存在

**示例**：
```python
async def process_data(user_id: int, action: str):
    print(f"处理用户 {user_id}，动作: {action}")

schedule_id = await scheduler.create_schedule(
    callback=process_data,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 10},
    task_name="process_user_123",
    callback_args=(123,),
    callback_kwargs={"action": "update"},
    timeout=30.0,
    max_retries=2
)
```

##### remove_schedule()

移除任务。

```python
success = await scheduler.remove_schedule(schedule_id)
```

##### remove_schedule_by_name()

按名称移除任务。

```python
success = await scheduler.remove_schedule_by_name("my_task")
```

##### find_schedule_by_name()

查找任务 ID。

```python
schedule_id = await scheduler.find_schedule_by_name("my_task")
```

##### get_schedule()

获取任务详情。

```python
task = await scheduler.get_schedule(schedule_id)
if task:
    print(f"任务状态: {task.status.value}")
    print(f"执行次数: {task.trigger_count}")
```

##### list_all_schedules()

列出所有活跃任务。

```python
tasks = await scheduler.list_all_schedules()
for task in tasks:
    print(f"{task.task_name}: {task.status.value}")
```

##### trigger_schedule()

强制立即执行任务。

```python
success = await scheduler.trigger_schedule(schedule_id)
```

#### 查询与统计

##### get_task_execution_history()

获取任务的执行历史。

```python
history = await scheduler.get_task_execution_history(
    schedule_id,
    limit=10
)

for execution in history:
    print(f"执行 ID: {execution.execution_id}")
    print(f"状态: {execution.status.value}")
    print(f"耗时: {execution.duration:.3f}s")
```

##### get_statistics()

获取调度器统计信息。

```python
stats = await scheduler.get_statistics()
print(f"运行时长: {stats['uptime_seconds']}s")
print(f"总执行: {stats['total_executions']}")
print(f"失败: {stats['total_failures']}")
print(f"超时: {stats['total_timeouts']}")
print(f"活跃任务: {stats['active_tasks']}")
print(f"已完成任务: {stats['completed_tasks']}")
```

#### 事件处理

##### trigger_event()

触发事件（预留接口）。

```python
await scheduler.trigger_event(
    event_name="user_login",
    event_params={"user_id": 123}
)
```

---

## 内部实现细节

### 主循环工作流程

1. **检查间隔**：每 `check_interval` 秒执行一次检查
2. **收集任务**：找出所有应该触发的任务
3. **并发执行**：使用 `TaskManager` 并发执行所有任务
4. **错误处理**：单个任务失败不影响其他任务

### 任务执行流程

1. **启动执行**：创建 `TaskExecution` 记录，设置状态为 `RUNNING`
2. **信号量**：等待并发信号量（如启用）
3. **超时保护**：使用 `asyncio.wait_for()` 设置超时
4. **重试循环**：如果失败且未超过重试次数，继续重试
5. **完成**：更新执行结果，设置状态，保存到历史

### 清理机制

- **自动清理**：每 `cleanup_interval` 秒清理已完成的一次性任务
- **历史保留**：每个任务保留最近 10 条执行记录
- **任务缓存**：保留最近 `keep_completed_tasks` 个已完成任务用于统计

---

## 高级用法

### 自定义配置的调度器

```python
from kernel.scheduler import SchedulerConfig, UnifiedScheduler

config = SchedulerConfig(
    check_interval=0.5,              # 更频繁检查
    task_default_timeout=120.0,      # 2分钟超时
    max_concurrent_tasks=50,         # 最多 50 个并发
    enable_retry=True,
    max_retries=5,
    retry_delay=2.0,
)

scheduler = UnifiedScheduler(config)
await scheduler.start()
```

### 监控任务执行

```python
# 定期检查任务状态
async def monitor():
    while True:
        tasks = await scheduler.list_all_schedules()
        for task in tasks:
            print(f"{task.task_name}: {task.status.value} "
                  f"(触发 {task.trigger_count} 次, "
                  f"成功 {task.success_count} 次)")
        await asyncio.sleep(10)
```

### 动态调整任务

```python
# 创建初始任务
schedule_id = await scheduler.create_schedule(...)

# 根据条件删除任务
if should_stop:
    await scheduler.remove_schedule(schedule_id)

# 创建新任务替换
schedule_id = await scheduler.create_schedule(...)
```

---

## 相关资源

- [Types 定义](./types.md) - 类型和枚举
- [Time Utils](./time_utils.md) - 时间工具函数
- [主文档](./README.md) - 完整使用指南
