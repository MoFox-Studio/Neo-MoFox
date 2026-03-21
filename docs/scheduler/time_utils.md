# Time Utils 模块

## 概述

`time_utils.py` 当前提供一个纯函数：`next_after()`。

它用于周期任务的“下一次触发时间”推进计算，避免由于检查周期或系统延迟导致重复触发。

---

## `next_after()`

```python
next_after(now: datetime, scheduled: datetime, interval_seconds: float) -> datetime
```

语义：返回一个**严格晚于** `now` 的下一次计划触发时间。

### 参数

- `now`：当前时间
- `scheduled`：当前计划触发时间
- `interval_seconds`：间隔秒数

### 返回

- 下一次触发时间（`datetime`）

---

## 行为规则

1. 当 `interval_seconds <= 0` 时，返回 `now`
2. 当 `scheduled > now` 时，返回 `scheduled`
3. 否则按周期推进 `scheduled`，直到结果严格大于 `now`

对应实现逻辑：

```text
delta = (now - scheduled).total_seconds()
steps = floor(delta / interval_seconds) + 1
next_time = scheduled + steps * interval_seconds
```

---

## 示例

```python
from datetime import datetime

from src.kernel.scheduler.time_utils import next_after

now = datetime(2026, 3, 19, 12, 30, 45)
scheduled = datetime(2026, 3, 19, 12, 0, 0)
interval_seconds = 600  # 10 分钟

result = next_after(now, scheduled, interval_seconds)
# 结果: 12:40:00
```

### 无效周期

```python
result = next_after(now, scheduled, 0)
# 返回 now
```

---

## 在调度器中的作用

在 `core.py` 的时间触发逻辑中，循环任务会维护 `_scheduled_trigger_time`，并通过 `next_after()` 计算下一次计划时间。

好处：

- 避免同一时间窗口重复触发
- 时钟跳变或检查延迟时仍保持节奏稳定
- 逻辑可测试、可复用

---

## 参考

- [README.md](README.md)
- [core.md](core.md)
- [src/kernel/scheduler/time_utils.py](../../src/kernel/scheduler/time_utils.py)
