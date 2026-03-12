# Time Utils 模块

## 概述

`time_utils.py` 提供了时间相关的纯函数，用于支持调度器的时间计算逻辑。这些函数没有副作用，易于测试和复用。

## 函数

### next_after()

计算严格晚于当前时间的下一次计划触发时间。

```python
def next_after(
    now: datetime,
    scheduled: datetime,
    interval_seconds: float
) -> datetime:
    """返回严格晚于 now 的下一次计划触发时间。"""
```

**参数说明**：

- `now`：当前时间
- `scheduled`：当前计划触发时间
- `interval_seconds`：周期（秒）

**返回值**：下一次计划触发时间

**算法**：

当 `now` 已经追上或超过 `scheduled` 时，这个函数会计算需要推进多少个周期，使得新的计划时间严格大于 `now`。

**数学公式**：
```
如果 interval_seconds <= 0：返回 now
如果 now < scheduled：返回 scheduled
否则：
    steps = floor((now - scheduled) / interval_seconds) + 1
    return scheduled + interval_seconds * steps
```

**例子**：

```python
from datetime import datetime, timedelta
from kernel.scheduler.time_utils import next_after

# 当前时间
now = datetime(2026, 2, 4, 12, 0, 0)

# 原计划时间
scheduled = datetime(2026, 2, 4, 11, 0, 0)

# 周期：10 分钟 = 600 秒
interval = 600

# 计算下一次触发时间
next_time = next_after(now, scheduled, interval)

# 结果应该是 2026-02-04 12:10:00
# 因为：
#   (now - scheduled) = 3600 秒 = 6 个周期
#   steps = 6 + 1 = 7
#   next_time = 11:00:00 + 7 * 600 = 11:00:00 + 7000 秒 = 12:56:40 > 12:00:00 ✗
#   实际上应该是 6.1 个周期 = 11:00 + 61 分钟 = 12:01:00
```

**更准确的例子**：

```python
from datetime import datetime, timedelta
from kernel.scheduler.time_utils import next_after

# 当前时间：2026-02-04 12:30:45
now = datetime(2026, 2, 4, 12, 30, 45)

# 原计划时间：2026-02-04 12:00:00
scheduled = datetime(2026, 2, 4, 12, 0, 0)

# 周期：10 分钟 = 600 秒
interval = 600

# 计算：
# (now - scheduled) = 30 分 45 秒 = 1845 秒
# steps = floor(1845 / 600) + 1 = 3 + 1 = 4
# next_time = 12:00:00 + 4 * 600 秒 = 12:40:00

next_time = next_after(now, scheduled, interval)
assert next_time == datetime(2026, 2, 4, 12, 40, 0)
```

---

## 使用场景

### 周期性任务的精确调度

调度器使用 `next_after()` 来确保周期性任务不会被重复触发，同时保持精确的时间间隔。

```python
# 任务每 60 秒执行一次
interval_seconds = 60

# 首次计划时间
scheduled = datetime.now()

# 当到达检查时间时
now = datetime.now()

# 计算下一次应该触发的时间
next_scheduled = next_after(now, scheduled, interval_seconds)

# 如果 now >= scheduled，则立即触发，并计算下一次触发时间
if now >= next_scheduled:
    execute_task()
    next_scheduled = next_after(now, next_scheduled, interval_seconds)
```

### 应对系统时钟调整

如果系统时钟被向前调整，`next_after()` 能够确保任务不会被重复触发：

```python
# 计划每分钟执行一次
scheduled = datetime(2026, 2, 4, 12, 0, 0)
interval = 60

# 正常的 now
now1 = datetime(2026, 2, 4, 12, 0, 45)
next1 = next_after(now1, scheduled, interval)  # 12:01:00

# 系统时钟被调整到未来
now2 = datetime(2026, 2, 4, 12, 5, 0)
next2 = next_after(now2, next1, interval)  # 12:06:00
# 虽然已经跳过了几个周期，但任务不会重复触发

# 系统时钟被调整回过去（更常见）
now3 = datetime(2026, 2, 4, 12, 3, 0)
next3 = next_after(now3, next2, interval)  # 12:06:00
# 系统时钟回退时，任务仍然在正确的时间触发
```

---

## 边界情况

### 1. 无效的周期

```python
from datetime import datetime
from kernel.scheduler.time_utils import next_after

now = datetime.now()
scheduled = datetime.now()

# 周期为 0 或负数时
result = next_after(now, scheduled, 0)      # 返回 now
result = next_after(now, scheduled, -10)    # 返回 now
```

**说明**：无效的周期（<= 0）被认为是错误的配置，函数返回 `now` 让上层处理。

### 2. 计划时间在未来

```python
now = datetime(2026, 2, 4, 12, 0, 0)
scheduled = datetime(2026, 2, 4, 12, 5, 0)  # 5 分钟后
interval = 600

result = next_after(now, scheduled, interval)  # 返回 scheduled
assert result == datetime(2026, 2, 4, 12, 5, 0)
```

**说明**：如果计划时间还在未来（`now < scheduled`），直接返回计划时间。

### 3. 非常大的时间跨度

```python
now = datetime(2026, 2, 4, 12, 0, 0)
scheduled = datetime(2025, 1, 1, 0, 0, 0)  # 一年前
interval = 3600  # 每小时

result = next_after(now, scheduled, interval)
# 计算多少个小时已经过去，并推进到下一个小时
```

---

## 实现原理

### 精度控制

函数使用 `timedelta` 和整数计算来确保精度，避免浮点数误差：

```python
# 使用整数计算步数
steps = int(delta_seconds // interval_seconds) + 1

# 使用 timedelta 加法
return scheduled + timedelta(seconds=interval_seconds * steps)
```

### 性能

函数仅涉及：
- 时间差计算 O(1)
- 整数除法 O(1)
- 时间加法 O(1)

总体复杂度：**O(1)**，非常高效。

---

## 相关资源

- [Core 模块](./core.md) - 调度器核心实现
- [Types 定义](./types.md) - 类型定义
- [主文档](./README.md) - 完整使用指南
