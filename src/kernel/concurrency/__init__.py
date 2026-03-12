"""
Concurrency 模块

提供统一的异步任务管理能力，包括 TaskManager、TaskGroup 和 WatchDog。
用于替代不规范的 asyncio.create_task，避免任务泄漏和资源浪费。

用法示例:
    # 基本任务创建
    from src.kernel.concurrency import get_task_manager

    async def my_task():
        await asyncio.sleep(1)

    tm = get_task_manager()
    tm.create_task(my_task(), name="my_task")

    # 等待所有任务完成
    await tm.wait_all_tasks()

    # 使用 TaskGroup
    async with tm.group(
        name="my_group",
        timeout=30,
        cancel_on_error=True
    ) as tg:
        tg.create_task(my_task())

    # 使用 gather 并行执行任务
    from src.kernel.concurrency import gather

    results = await gather(
        my_task(),
        my_task(),
        my_task()
    )
"""

from .task_manager import TaskManager, get_task_manager
from .task_group import TaskGroup
from .task_info import TaskInfo
from .watchdog import WatchDog, get_watchdog, StreamHeartbeat
from .exceptions import (
    ConcurrencyError,
    TaskNotFoundError,
    TaskTimeoutError,
    TaskGroupError,
    TaskGroupAlreadyExists,
    TaskGroupNotFoundError,
    WatchDogError,
)

__all__ = [
    # 主要接口
    "get_task_manager",
    "TaskManager",
    "TaskGroup",
    "TaskInfo",
    "get_watchdog",
    "WatchDog",
    "StreamHeartbeat",
    # 异常
    "ConcurrencyError",
    "TaskNotFoundError",
    "TaskTimeoutError",
    "TaskGroupError",
    "TaskGroupAlreadyExists",
    "TaskGroupNotFoundError",
    "WatchDogError",
]

# 版本信息
__version__ = "1.0.0"
