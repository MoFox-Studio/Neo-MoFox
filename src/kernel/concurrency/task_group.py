"""
任务组上下文管理器

提供作用域化的任务组管理，支持共享和上下文管理器模式。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Coroutine

from .task_info import TaskInfo
from .exceptions import TaskGroupError


@dataclass
class TaskGroup:
    """任务组上下文管理器

    提供作用域化的任务管理，所有组内任务会在退出上下文时等待完成。
    同名的 TaskGroup 可以在不同模块间共享。

    Attributes:
        name: 任务组名称（用于共享）
        timeout: 整组超时时间（秒），None 表示不超时
        cancel_on_error: 任一任务异常时是否取消组内其他任务
        tasks: 组内任务列表
        _owner_task: 拥有此任务组的 asyncio.Task（用于检测死锁）
        _exception: 存储组内第一个异常
    """

    name: str
    timeout: float | None = None
    cancel_on_error: bool = True
    tasks: list[TaskInfo] = field(default_factory=list)
    _owner_task: asyncio.Task[Any] | None = None
    _exception: BaseException | None = None
    _active: bool = False

    def create_task(self, coro: Coroutine[Any, Any, Any], name: str | None = None) -> TaskInfo:
        """在组内创建一个新任务

        Args:
            coro: 要执行的协程
            name: 任务名称（可选）

        Returns:
            TaskInfo: 任务信息对象

        Raises:
            TaskGroupError: 如果任务组未激活（不在上下文管理器内）
        """
        if not self._active:
            raise TaskGroupError(
                f"TaskGroup '{self.name}' is not active. "
                f"Use 'async with' to activate the group."
            )

        # 不允许在 TaskGroup 内创建守护任务
        task_info = TaskInfo(
            name=name,
            coro=coro,
            daemon=False,  # TaskGroup 内禁止守护任务
            group_name=self.name,
        )

        # 创建 asyncio.Task
        task = asyncio.create_task(coro, name=name)
        task_info.task = task

        self.tasks.append(task_info)
        return task_info

    async def __aenter__(self) -> TaskGroup:
        """进入上下文"""
        self._active = True
        self._owner_task = asyncio.current_task()
        self._exception = None
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """退出上下文，等待所有任务完成"""
        self._active = False

        # 如果进入时有异常，先处理已有的异常
        if exc_val is not None:
            self._exception = exc_val

        try:
            # 等待所有任务完成
            await self._wait_all_tasks()

            # 如果有异常且 cancel_on_error=True，取消所有任务
            if self._exception is not None and self.cancel_on_error:
                await self._cancel_all_tasks()

            # 如果有异常，重新抛出
            if self._exception is not None:
                raise self._exception

        finally:
            # 清理引用
            self._owner_task = None

        # 不抑制传入的异常
        return False

    async def _wait_all_tasks(self) -> None:
        """等待所有组内任务完成"""
        if not self.tasks:
            return

        # 收集所有未完成的任务
        pending_tasks = [info.task for info in self.tasks if not info.is_done() and info.task is not None]

        if not pending_tasks:
            return

        # 等待所有任务完成或超时
        try:
            done, pending = await asyncio.wait(
                pending_tasks, timeout=self.timeout, return_when=asyncio.ALL_COMPLETED
            )

            # 处理超时
            if pending:
                for task in pending:
                    task.cancel()
                # 等待取消完成
                await asyncio.wait(pending, timeout=1.0)

        except asyncio.CancelledError:
            # 当前任务被取消，取消所有子任务
            await self._cancel_all_tasks()
            raise

    async def _cancel_all_tasks(self) -> None:
        """取消组内所有任务"""
        for task_info in self.tasks:
            if not task_info.is_done():
                task_info.cancel()

        # 等待所有任务处理取消
        pending_tasks = [
            info.task for info in self.tasks if not info.is_done() and info.task
        ]
        if pending_tasks:
            await asyncio.wait(pending_tasks, timeout=2.0)

    def _record_exception(self, exc: BaseException) -> None:
        """记录第一个异常"""
        if self._exception is None:
            self._exception = exc

    def is_active(self) -> bool:
        """检查任务组是否处于激活状态"""
        return self._active

    def get_task_count(self) -> int:
        """获取组内任务数量"""
        return len(self.tasks)

    def get_active_task_count(self) -> int:
        """获取组内仍在运行的任务数量"""
        return sum(1 for info in self.tasks if not info.is_done())

    def __repr__(self) -> str:
        """任务组字符串表示"""
        active_count = self.get_active_task_count()
        total_count = len(self.tasks)
        status = "active" if self._active else "inactive"
        return f"TaskGroup(name={self.name}, status={status}, tasks={active_count}/{total_count})"
