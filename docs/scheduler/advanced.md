# Scheduler 高级用法

## 概述

本文聚焦当前实现可用的高级能力：并发控制、重试超时、事件触发、运行监控与运行期管理。

---

## 1. 并发控制

默认最大并发为 `100`，由 `SchedulerConfig.max_concurrent_tasks` 控制。

```python
from src.kernel.scheduler import SchedulerConfig, UnifiedScheduler

config = SchedulerConfig(
    max_concurrent_tasks=10,
    enable_task_semaphore=True,
)

scheduler = UnifiedScheduler(config)
await scheduler.start()
```

建议：

- I/O 密集任务可以适当放大并发
- CPU 密集任务应保守配置并发
- 禁用信号量（`enable_task_semaphore=False`）仅用于可控场景

---

## 2. 超时与重试

每个任务可单独设置 `timeout` 与 `max_retries`。

```python
async def unstable_job() -> None:
    ...

schedule_id = await scheduler.create_schedule(
    callback=unstable_job,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 1},
    task_name="unstable",
    timeout=5.0,
    max_retries=2,
)
```

行为要点：

- 超时后标记为 `TIMEOUT`，该次执行不重试
- 普通异常可按 `max_retries` 重试
- 重试间隔来自 `SchedulerConfig.retry_delay`

---

## 3. 同步回调与异步回调

调度器支持同步/异步回调：

- 异步函数：直接 `await`
- 同步函数：使用 `asyncio.to_thread()` 执行

```python
def sync_job(x: int) -> int:
    return x * 2

await scheduler.create_schedule(
    callback=sync_job,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 1},
    callback_args=(21,),
    task_name="sync_job",
)
```

---

## 4. 事件触发任务

创建 `TriggerType.EVENT` 任务后，通过 `trigger_event()` 触发。

```python
from src.kernel.scheduler import TriggerType

await scheduler.create_schedule(
    callback=handle_user_login,
    trigger_type=TriggerType.EVENT,
    trigger_config={"event_name": "user_login"},
    task_name="on_user_login",
)

await scheduler.trigger_event("user_login", {"user_id": "u-1"})
```

说明：

- 事件参数会并入回调 `kwargs`
- 仅触发当前可触发状态任务
- 事件功能是调度器内建触发机制，不依赖 event_bus 自动桥接

---

## 5. 运行期操作

### 同名任务覆盖

```python
await scheduler.create_schedule(..., task_name="refresh_cache")
await scheduler.create_schedule(..., task_name="refresh_cache", force_overwrite=True)
```

### 暂停与恢复

```python
ok = await scheduler.pause_schedule(schedule_id)
ok = await scheduler.resume_schedule(schedule_id)
```

### 立即触发

```python
ok = await scheduler.trigger_schedule(schedule_id)
```

---

## 6. 查询与监控

### 单任务信息

```python
info = await scheduler.get_task_info(schedule_id)
```

### 任务列表过滤

```python
running = await scheduler.list_tasks(status=TaskStatus.RUNNING)
time_tasks = await scheduler.list_tasks(trigger_type=TriggerType.TIME)
```

### 全局统计

```python
stats = scheduler.get_statistics()
```

可重点关注：

- `total_executions`
- `total_failures`
- `total_timeouts`
- `success_rate`
- `running_tasks_info`

---

## 7. 推荐实践

- 应用启动后统一 `await scheduler.start()`，退出前 `await scheduler.stop()`
- 给任务设置稳定 `task_name`，便于覆盖和排障
- 关键任务显式设置 `timeout`，避免长时间挂起
- 使用 `list_tasks()` + `get_statistics()` 做轻量健康检查
- 对高价值任务配合业务侧告警，不只依赖日志

---

## 8. 常见误区

以下接口在当前实现中不存在，请勿继续使用：

- `get_schedule()`
- `list_all_schedules()`

推荐替代：

- `get_task_info()`
- `list_tasks()`

---

## 参考

- [README.md](README.md)
- [core.md](core.md)
- [types.md](types.md)
- [src/kernel/scheduler/core.py](../../src/kernel/scheduler/core.py)
