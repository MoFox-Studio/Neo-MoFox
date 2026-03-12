"""
Scheduler 模块

统一的任务调度系统，支持时间触发、事件触发和自定义条件触发。

用法示例:
    from src.kernel.scheduler import get_unified_scheduler, TriggerType

    unified_scheduler = get_unified_scheduler()

    # 30秒后执行一次任务
    async def my_task():
        print("Task executed!")

    await unified_scheduler.create_schedule(
        callback=my_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 30},
        task_name="delayed_job"
    )

    # 每隔1小时执行一次
    await unified_scheduler.create_schedule(
        callback=my_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 3600},
        is_recurring=True,
        task_name="hourly_job"
    )

    # 使用自定义条件触发
    async def check_condition():
        return True  # 自定义条件逻辑

    await unified_scheduler.create_schedule(
        callback=my_task,
        trigger_type=TriggerType.CUSTOM,
        trigger_config={"condition_func": check_condition},
        task_name="custom_job"
    )
"""

from .core import SchedulerConfig, UnifiedScheduler, ScheduleTask
from .types import TaskStatus, TriggerType, TaskExecution

_unified_scheduler: UnifiedScheduler | None = None


def get_unified_scheduler() -> UnifiedScheduler:
    """获取全局 UnifiedScheduler（懒加载）。"""

    global _unified_scheduler
    if _unified_scheduler is None:
        _unified_scheduler = UnifiedScheduler()
    return _unified_scheduler

__all__ = [
    # 主要接口
    "get_unified_scheduler",
    "TriggerType",
    "TaskStatus",
    # 核心类
    "UnifiedScheduler",
    "SchedulerConfig",
    "ScheduleTask",
    "TaskExecution",
]

# 版本信息
__version__ = "1.0.0"
