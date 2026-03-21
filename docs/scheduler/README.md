# Scheduler 模块文档

## 概述

`src.kernel.scheduler` 提供统一的异步任务调度能力，支持：

- 时间触发（延迟、周期、指定时间）
- 事件触发（通过 `trigger_event()` 手动触发）
- 自定义条件触发
- 超时与重试
- 并发上限控制
- 执行统计与任务查询

模块对外主入口是 `get_unified_scheduler()`。

---

## 模块结构

```text
src/kernel/scheduler/
├── __init__.py
├── core.py
├── types.py
└── time_utils.py
```

文档索引：

- [README.md](README.md)：总览与快速开始
- [core.md](core.md)：`UnifiedScheduler` / `SchedulerConfig` / `ScheduleTask`
- [types.md](types.md)：`TriggerType` / `TaskStatus` / `TaskExecution`
- [time_utils.md](time_utils.md)：`next_after()`
- [advanced.md](advanced.md)：实践与运维建议

---

## 快速开始

### 1. 启动调度器

```python
import asyncio

from src.kernel.scheduler import TriggerType, get_unified_scheduler


async def hello(name: str) -> None:
    print("hello", name)


async def main() -> None:
    scheduler = get_unified_scheduler()
    await scheduler.start()

    await scheduler.create_schedule(
        callback=hello,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 3},
        task_name="hello_once",
        callback_args=("scheduler",),
    )

    await asyncio.sleep(5)
    await scheduler.stop()


asyncio.run(main())
```

### 2. 周期任务

```python
await scheduler.create_schedule(
    callback=hello,
    trigger_type=TriggerType.TIME,
    trigger_config={"interval_seconds": 2},
    is_recurring=True,
    task_name="hello_loop",
    callback_args=("loop",),
)
```

### 3. 自定义条件触发

```python
state = {"ready": False}


def condition_func() -> bool:
    return state["ready"]


await scheduler.create_schedule(
    callback=hello,
    trigger_type=TriggerType.CUSTOM,
    trigger_config={"condition_func": condition_func},
    is_recurring=True,
    task_name="custom_ready",
    callback_args=("ready",),
)
```

---

## 公开 API（`src.kernel.scheduler`）

```python
from src.kernel.scheduler import (
    get_unified_scheduler,
    UnifiedScheduler,
    SchedulerConfig,
    ScheduleTask,
    TriggerType,
    TaskStatus,
    TaskExecution,
)
```

---

## `UnifiedScheduler` 常用方法

### 生命周期

- `await start()`
- `await stop()`

### 任务创建/删除

- `await create_schedule(...) -> str`
- `await remove_schedule(schedule_id: str) -> bool`
- `await remove_schedule_by_name(task_name: str) -> bool`
- `await find_schedule_by_name(task_name: str) -> str | None`

### 任务控制

- `await trigger_schedule(schedule_id: str) -> bool`
- `await pause_schedule(schedule_id: str) -> bool`
- `await resume_schedule(schedule_id: str) -> bool`
- `await trigger_event(event_name: str, event_params: dict[str, Any] | None = None) -> None`

### 查询与统计

- `await get_task_info(schedule_id: str) -> dict[str, Any] | None`
- `await list_tasks(trigger_type: TriggerType | None = None, status: TaskStatus | None = None) -> list[dict[str, Any]]`
- `get_statistics() -> dict[str, Any]`

---

## 触发配置说明

### `TriggerType.TIME`

支持两类配置：

1. 延迟 / 周期：

```python
{"delay_seconds": 5}
{"interval_seconds": 2}  # 通常配合 is_recurring=True
```

2. 指定时刻：

```python
{"trigger_at": "2026-03-19T20:00:00"}
# 或 datetime 实例
```

对于循环任务，也可与 `interval_seconds` 组合，形成“从 trigger_at 起按周期推进”。

### `TriggerType.CUSTOM`

```python
{"condition_func": callable}
```

`condition_func` 可以是同步或异步可调用对象，返回值会转为 `bool`。

### `TriggerType.EVENT`

```python
{"event_name": "user_login"}
```

创建事件任务后，通过 `trigger_event("user_login", {...})` 触发。

---

## 注意事项

- 调度器未启动时调用 `create_schedule()` 会抛出 `RuntimeError`
- 同名且活跃任务默认不允许重复创建，可使用 `force_overwrite=True`
- 回调函数支持同步/异步，同步函数会在线程中执行
- 文件中不存在 `get_schedule()`、`list_all_schedules()` 等接口，请使用 `get_task_info()` 和 `list_tasks()`

---

## 参考

- [core.md](core.md)
- [types.md](types.md)
- [time_utils.md](time_utils.md)
- [advanced.md](advanced.md)
- [src/kernel/scheduler/__init__.py](../../src/kernel/scheduler/__init__.py)
- [src/kernel/scheduler/core.py](../../src/kernel/scheduler/core.py)
- [src/kernel/scheduler/types.py](../../src/kernel/scheduler/types.py)
- [src/kernel/scheduler/time_utils.py](../../src/kernel/scheduler/time_utils.py)
