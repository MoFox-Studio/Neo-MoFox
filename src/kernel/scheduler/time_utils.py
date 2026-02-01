"""scheduler 时间相关纯函数。

该文件只放与调度时间计算相关的无副作用函数，便于单元测试与复用。
"""

from __future__ import annotations

from datetime import datetime, timedelta


def next_after(now: datetime, scheduled: datetime, interval_seconds: float) -> datetime:
    """返回严格晚于 now 的下一次计划触发时间。

    语义：给定一个“计划触发时间 scheduled”和周期 interval_seconds，当 now 已经
    追上或超过 scheduled 时，推进 scheduled 直到它严格大于 now。

    Args:
        now: 当前时间
        scheduled: 当前计划触发时间（可能 <= now）
        interval_seconds: 周期（秒）

    Returns:
        下一次计划触发时间

    Note:
        当 interval_seconds <= 0 时，这是一个无意义的调度配置；为保持调度器
        的鲁棒性，这里选择返回 now（让上层逻辑自行决定如何处理）。
    """

    if interval_seconds <= 0:
        return now

    delta_seconds = (now - scheduled).total_seconds()
    if delta_seconds < 0:
        return scheduled

    steps = int(delta_seconds // interval_seconds) + 1
    return scheduled + timedelta(seconds=interval_seconds * steps)
