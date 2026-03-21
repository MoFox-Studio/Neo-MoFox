# Core 模块

## 概述

`core.py` 实现调度器核心逻辑，包含：

- `SchedulerConfig`：调度配置
- `ScheduleTask`：任务模型
- `UnifiedScheduler`：调度器主类

---

## `SchedulerConfig`

```python
@dataclass
class SchedulerConfig:
    check_interval: float = 1.0

    task_default_timeout: float = 300.0
    task_cancel_timeout: float = 10.0
    shutdown_timeout: float = 30.0

    max_concurrent_tasks: int = 100
    enable_task_semaphore: bool = True

    enable_retry: bool = True
    max_retries: int = 3
    retry_delay: float = 5.0

    cleanup_interval: float = 60.0
    keep_completed_tasks: int = 100
```

说明：

- `check_interval`：主循环检查频率
- `task_default_timeout`：任务默认超时
- `max_concurrent_tasks`：最大并发执行任务数
- `enable_retry` + `retry_delay`：失败重试策略
- `keep_completed_tasks`：已完成一次性任务的归档上限

---

## `ScheduleTask`

`ScheduleTask` 是调度内部任务对象，主要字段：

- 基本信息：`schedule_id`、`task_name`、`callback`
- 触发信息：`trigger_type`、`trigger_config`、`is_recurring`
- 回调参数：`callback_args`、`callback_kwargs`
- 状态：`status`、`created_at`、`last_triggered_at`
- 统计：`trigger_count`、`success_count`、`failure_count`、`total_execution_time`
- 执行历史：`execution_history`、`current_execution`
- 重试与超时：`max_retries`、`retry_count`、`last_error`、`timeout`

关键方法：

- `is_active()`
- `can_trigger()`
- `start_execution()`
- `finish_execution(success, result=None, error=None)`

---

## `UnifiedScheduler`

### 初始化

```python
from src.kernel.scheduler import SchedulerConfig, UnifiedScheduler

scheduler = UnifiedScheduler(SchedulerConfig(check_interval=0.5))
```

或单例入口：

```python
from src.kernel.scheduler import get_unified_scheduler

scheduler = get_unified_scheduler()
```

### 生命周期

```python
await scheduler.start()
await scheduler.stop()
```

`start()` 会启动主检查循环和清理循环；`stop()` 会尝试取消后台任务并清理资源。

---

## 任务管理 API

### `create_schedule()`

```python
await scheduler.create_schedule(
    callback,
    trigger_type,
    trigger_config,
    is_recurring=False,
    task_name=None,
    callback_args=None,
    callback_kwargs=None,
    force_overwrite=False,
    timeout=None,
    max_retries=0,
) -> str
```

返回：`schedule_id`。

注意：

- 未 `start()` 时会抛 `RuntimeError`
- 同名活跃任务默认会抛 `ValueError`
- `force_overwrite=True` 会移除同名活跃任务后重建

### 删除与查找

```python
await scheduler.remove_schedule(schedule_id) -> bool
await scheduler.remove_schedule_by_name(task_name) -> bool
await scheduler.find_schedule_by_name(task_name) -> str | None
```

### 手动触发与暂停恢复

```python
await scheduler.trigger_schedule(schedule_id) -> bool
await scheduler.pause_schedule(schedule_id) -> bool
await scheduler.resume_schedule(schedule_id) -> bool
```

### 事件触发

```python
await scheduler.trigger_event(event_name, event_params=None) -> None
```

仅触发订阅了对应 `event_name` 且可触发状态的任务。

---

## 查询 API

### 获取单任务信息

```python
await scheduler.get_task_info(schedule_id) -> dict[str, Any] | None
```

返回字段包含：

- `schedule_id` / `task_name`
- `trigger_type` / `is_recurring`
- `status`
- `trigger_count` / `success_count` / `failure_count`
- `retry_count` / `max_retries`
- `avg_execution_time` / `total_execution_time`
- `trigger_config` / `timeout` / `last_error`

### 任务列表

```python
await scheduler.list_tasks(trigger_type=None, status=None) -> list[dict[str, Any]]
```

支持按触发类型与状态过滤。

### 全局统计

```python
scheduler.get_statistics() -> dict[str, Any]
```

包括运行时长、任务分布、总执行数、失败数、超时数、成功率等。

---

## 执行机制要点

- 回调支持同步/异步：同步函数会通过 `asyncio.to_thread()` 执行
- 并发通过可选信号量控制
- 任务执行带 `asyncio.wait_for()` 超时保护
- 单次执行失败可按任务重试配置重试
- 一次性任务完成后会移动到 `_completed_tasks` 归档

---

## 与旧文档差异（当前实现为准）

以下接口当前不存在：

- `get_schedule()`
- `list_all_schedules()`

请使用：

- `get_task_info()`
- `list_tasks()`

---

## 参考

- [README.md](README.md)
- [types.md](types.md)
- [time_utils.md](time_utils.md)
- [src/kernel/scheduler/core.py](../../src/kernel/scheduler/core.py)
