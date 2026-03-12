"""
任务管理器

提供全局统一的异步任务管理接口，替代直接使用 asyncio.create_task。
"""

from __future__ import annotations

import asyncio
import weakref
from threading import Lock
from typing import TYPE_CHECKING, Any, Coroutine
from uuid import uuid4

from .task_info import TaskInfo
from .task_group import TaskGroup
from .exceptions import TaskNotFoundError

if TYPE_CHECKING:
    from .watchdog import WatchDog


class TaskManager:
    """任务管理器（全局单例）

    提供统一的异步任务创建、追踪和管理接口。
    用于替代不规范的 asyncio.create_task，避免任务泄漏。

    Attributes:
        _tasks: 存储所有任务的字典 {task_id: TaskInfo}
        _groups: 存储所有任务组的字典 {group_name: TaskGroup}
        _lock: 线程安全锁
        _watchdog: WatchDog 实例引用
    """

    def __init__(self) -> None:
        """初始化任务管理器"""
        self._tasks: dict[str, TaskInfo] = {}
        self._groups: dict[str, TaskGroup] = {}
        self._lock = Lock()
        self._task_ids_by_task: weakref.WeakKeyDictionary[asyncio.Task[Any], str] = (
            weakref.WeakKeyDictionary()
        )
        self._watchdog: WatchDog | None = None  # WatchDog 实例，稍后注入
        self._initialized = True

    def set_watchdog(self, watchdog: WatchDog) -> None:
        """设置 WatchDog 实例

        Args:
            watchdog: WatchDog 实例
        """
        self._watchdog = watchdog

    def create_task(
        self,
        coro: Coroutine[Any, Any, Any],
        name: str | None = None,
        daemon: bool = False,
        timeout: float | None = None,
        group_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskInfo:
        """创建一个新任务

        Args:
            coro: 要执行的协程
            name: 任务名称（可选，用于调试）
            daemon: 是否为守护任务（不会被 WatchDog 超时检查）
            timeout: 任务超时时间（秒），None 表示不超时
            group_name: 所属任务组名称（可选）
            metadata: 额外的元数据（可选）

        Returns:
            TaskInfo: 任务信息对象

        Raises:
            RuntimeError: 如果不在异步上下文中调用
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                "TaskManager.create_task must be called within an async context"
            )

        task_id = str(uuid4())

        # 创建 TaskInfo
        task_info = TaskInfo(
            task_id=task_id,
            name=name or f"task_{task_id[:8]}",
            coro=coro,
            daemon=daemon,
            timeout=timeout,
            group_name=group_name,
            metadata=metadata or {},
        )

        # 创建 asyncio.Task（始终使用解析后的 task_name，便于调试）
        task = asyncio.create_task(coro, name=task_info.name)
        task_info.task = task

        # 添加回调，在任务完成时自动清理
        task.add_done_callback(self._on_task_done)

        # 存储任务
        with self._lock:
            self._tasks[task_id] = task_info
            self._task_ids_by_task[task] = task_id

        return task_info

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        """任务完成时的回调

        Args:
            task: 已完成的 asyncio.Task
        """
        with self._lock:
            task_id = self._task_ids_by_task.pop(task, None)
            task_info = self._tasks.get(task_id) if task_id else None
            group = self._groups.get(task_info.group_name) if task_info and task_info.group_name else None

        if group is not None and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                group._record_exception(exc)

    def get_task(self, task_id: str) -> TaskInfo:
        """获取任务信息

        Args:
            task_id: 任务 ID

        Returns:
            TaskInfo: 任务信息对象

        Raises:
            TaskNotFoundError: 如果任务不存在
        """
        with self._lock:
            task_info = self._tasks.get(task_id)
            if task_info is None:
                raise TaskNotFoundError(task_id)
            return task_info

    def cancel_task(self, task_id: str) -> bool:
        """取消任务

        Args:
            task_id: 任务 ID

        Returns:
            bool: 是否成功取消
        """
        try:
            task_info = self.get_task(task_id)
            return task_info.cancel()
        except TaskNotFoundError:
            return False

    async def wait_all_tasks(self) -> None:
        """等待所有任务完成

        注意：此方法不会等待守护任务（daemon=True）。
        """
        with self._lock:
            # 过滤出非守护任务且未完成的任务
            pending_tasks = [
                info.task
                for info in self._tasks.values()
                if not info.daemon and not info.is_done() and info.task is not None
            ]

        if not pending_tasks:
            return

        # 等待所有任务完成
        await asyncio.wait(pending_tasks, return_when=asyncio.ALL_COMPLETED)

    def group(
        self,
        name: str,
        timeout: float | None = None,
        cancel_on_error: bool = True,
    ) -> TaskGroup:
        """获取或创建任务组

        同名的 TaskGroup 会被共享，这使得不同模块可以协作管理同一组任务。

        Args:
            name: 任务组名称
            timeout: 整组超时时间（秒），None 表示不超时
            cancel_on_error: 任一任务异常时是否取消组内其他任务

        Returns:
            TaskGroup: 任务组对象
        """
        with self._lock:
            # 如果组已存在，直接返回
            if name in self._groups:
                return self._groups[name]

            # 创建新组
            group = TaskGroup(
                name=name,
                timeout=timeout,
                cancel_on_error=cancel_on_error,
            )
            self._groups[name] = group
            return group

    def cleanup_tasks(self) -> int:
        """清理已完成的任务

        Returns:
            int: 清理的任务数量
        """
        cleaned = 0
        with self._lock:
            to_remove = []
            for task_id, task_info in self._tasks.items():
                if task_info.is_done():
                    to_remove.append(task_id)

            for task_id in to_remove:
                del self._tasks[task_id]
                cleaned += 1

        return cleaned

    def get_all_tasks(self) -> list[TaskInfo]:
        """获取所有任务

        Returns:
            list[TaskInfo]: 所有任务信息列表
        """
        with self._lock:
            return list(self._tasks.values())

    def get_active_tasks(self) -> list[TaskInfo]:
        """获取所有活跃任务（未完成）

        Returns:
            list[TaskInfo]: 活跃任务列表
        """
        with self._lock:
            return [info for info in self._tasks.values() if not info.is_done()]

    def get_task_count(self) -> int:
        """获取任务总数

        Returns:
            int: 任务数量
        """
        with self._lock:
            return len(self._tasks)

    def get_active_task_count(self) -> int:
        """获取活跃任务数量

        Returns:
            int: 活跃任务数量
        """
        with self._lock:
            return sum(1 for info in self._tasks.values() if not info.is_done())

    async def gather(
        self,
        *coros: Coroutine[Any, Any, Any],
        return_exceptions: bool = False,
        group_name: str | None = None,
    ) -> list[Any]:
        """并行执行多个协程并返回结果列表。

        类似 asyncio.gather，但使用 TaskManager 追踪所有任务。
        所有创建的任务会被追踪，完成后自动清理。

        Args:
            *coros: 要执行的协程
            return_exceptions: 是否将异常作为结果返回（False 则抛出第一个异常）
            group_name: 可选的任务组名称

        Returns:
            list[Any]: 结果列表，顺序与输入协程一致

        Raises:
            Exception: 第一个发生的异常（如果 return_exceptions=False）

        Examples:
            >>> tm = get_task_manager()
            >>> results = await tm.gather(
            ...     task1(),
            ...     task2(),
            ...     task3(),
            ...     return_exceptions=True
            ... )
        """
        if not coros:
            return []

        # 创建任务组（如果指定了 group_name）
        group_context: TaskGroup | None = None
        if group_name:
            group_context = self.group(name=group_name)

        # 创建所有任务
        tasks = []
        for coro in coros:
            task_info = self.create_task(
                coro=coro,
                name=f"gather_task_{len(tasks)}",
                group_name=group_name,
            )
            tasks.append(task_info.task)

        # 使用 TaskGroup 上下文（如果有）
        if group_context:
            async with group_context:
                # 等待所有任务完成
                results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)
        else:
            # 直接等待所有任务完成
            results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)

        return results

    def get_stats(self) -> dict[str, Any]:
        """获取任务统计信息

        Returns:
            dict: 统计信息字典
        """
        with self._lock:
            total = len(self._tasks)
            active = sum(1 for info in self._tasks.values() if not info.is_done())
            daemon = sum(1 for info in self._tasks.values() if info.daemon)
            grouped = sum(1 for info in self._tasks.values() if info.group_name)

            return {
                "total_tasks": total,
                "active_tasks": active,
                "daemon_tasks": daemon,
                "grouped_tasks": grouped,
                "groups": len(self._groups),
            }

    def __repr__(self) -> str:
        """任务管理器字符串表示"""
        stats = self.get_stats()
        return (
            f"TaskManager(total={stats['total_tasks']}, "
            f"active={stats['active_tasks']}, "
            f"daemon={stats['daemon_tasks']}, "
            f"groups={stats['groups']})"
        )


# 全局 TaskManager 实例
_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    """获取全局 TaskManager 实例

    Returns:
        TaskManager: 全局任务管理器单例
    """
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
