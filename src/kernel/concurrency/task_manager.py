"""
任务管理器

提供全局统一的异步任务管理接口，替代直接使用 asyncio.create_task。
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from functools import partial
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Any, Coroutine, ParamSpec, TypeVar
from uuid import uuid4

from .task_info import TaskInfo
from .task_group import TaskGroup
from .exceptions import TaskNotFoundError

if TYPE_CHECKING:
    from .watchdog import WatchDog

P = ParamSpec("P")
R = TypeVar("R")


@dataclass
class _ProcessPoolOwner:
    """记录当前独立运行实际创建的池及其代际。"""

    pools: dict[TaskManager, ProcessPoolExecutor] = field(default_factory=dict)
    active: bool = True


_process_pool_owner: ContextVar[_ProcessPoolOwner | None] = ContextVar(
    "process_pool_owner", default=None
)


@contextmanager
def process_pool_lifespan(timeout: float = 1.0) -> Iterator[None]:
    """在 runner 外回收本作用域新建的池，不接管此前存在的宿主池。

    Args:
        timeout: 所有池、worker 和管理线程共享的回收等待预算（秒）。
            不限制 runner 本身，也不承诺整个解释器的硬退出时限。
    """
    owner = _ProcessPoolOwner()
    token = _process_pool_owner.set(owner)
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        owner.active = False
        _process_pool_owner.reset(token)
        deadline = monotonic() + max(0.0, timeout)
        errors: list[Exception] = []
        for manager, pool in owner.pools.items():
            try:
                manager.shutdown_process_pool(
                    wait=False,
                    expected_pool=pool,
                    deadline=deadline,
                )
            except Exception as exc:
                errors.append(exc)
        owner.pools.clear()
        if errors:
            if primary_error is None:
                raise ExceptionGroup("进程池回收失败", errors)
            primary_error.add_note(f"进程池回收失败: {errors!r}")


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

    def __init__(self, process_workers: int = 4) -> None:
        """初始化任务管理器"""
        self._tasks: dict[str, TaskInfo] = {}
        self._groups: dict[str, TaskGroup] = {}
        self._lock = Lock()
        self._task_ids_by_task: weakref.WeakKeyDictionary[asyncio.Task[Any], str] = (
            weakref.WeakKeyDictionary()
        )
        self._watchdog: WatchDog | None = None  # WatchDog 实例，稍后注入
        self._process_workers = max(1, process_workers)
        self._process_pool: ProcessPoolExecutor | None = None
        self._ensure_process_pool()
        self._initialized = True

    def configure_process_pool(self, process_workers: int) -> None:
        """重新配置进程池大小。

        Args:
            process_workers: 进程池进程数量，最小为 1
        """
        process_workers = max(1, process_workers)
        with self._lock:
            pool = self._process_pool
            if process_workers == self._process_workers and pool is not None:
                return
            owner = _process_pool_owner.get()
            if (
                pool is not None
                and owner is not None
                and owner.active
                and owner.pools.get(self) is not pool
            ):
                # 独立入口只能配置自己的池，借用时保留宿主配置。
                return
            self._process_workers = process_workers

        if pool is not None:
            self.shutdown_process_pool(wait=False, expected_pool=pool)
        self._ensure_process_pool()

    def _ensure_process_pool(self) -> ProcessPoolExecutor:
        """确保进程池存在，并仅在实际创建时登记当前所有者。"""
        with self._lock:
            if self._process_pool is None:
                self._process_pool = ProcessPoolExecutor(max_workers=self._process_workers)
                owner = _process_pool_owner.get()
                if owner is not None and owner.active:
                    owner.pools[self] = self._process_pool
            return self._process_pool

    def set_watchdog(self, watchdog: WatchDog) -> None:
        """设置 WatchDog 实例

        Args:
            watchdog: WatchDog 实例
        """
        self._watchdog = watchdog

    async def to_process(
        self,
        func: Callable[P, R],
        *args: P.args,
        timeout: float | None = 15.0, # type: ignore
        **kwargs: P.kwargs,
    ) -> R:
        """在独立进程中执行同步函数，适合 CPU 密集任务。

        Args:
            func: 要提交到进程池执行的可 pickle 同步函数
            *args: 函数位置参数
            timeout: 超时时间（秒），默认 15 秒；None 表示不限制
            **kwargs: 函数关键字参数

        Returns:
            R: 函数返回值

        Raises:
            RuntimeError: 如果不在异步上下文中调用
            asyncio.TimeoutError: 如果执行超过 timeout
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                "TaskManager.to_process must be called within an async context"
            )

        call = partial(func, *args, **kwargs)
        future = loop.run_in_executor(self._ensure_process_pool(), call)
        if timeout is None:
            return await future
        return await asyncio.wait_for(future, timeout=timeout)

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
                "process_workers": self._process_workers,
                "process_pool_running": self._process_pool is not None,
            }

    def shutdown_process_pool(
        self,
        wait: bool = True,
        cancel_futures: bool = True,
        *,
        expected_pool: ProcessPoolExecutor | None = None,
        timeout: float = 0.4,
        deadline: float | None = None,
    ) -> None:
        """关闭当前池；非等待模式中所有 join 共享 timeout 预算。

        Args:
            wait: 是否保持 executor 原有的等待任务完成语义。
            cancel_futures: 是否取消尚未开始的作业。
            expected_pool: 所有权凭据；不匹配当前代际时不执行回收。
            timeout: wait=False 时 worker 与管理线程共享的等待秒数。
            deadline: 外层所有者传入的共同绝对截止时间，优先于 timeout。
        """
        if deadline is None:
            deadline = monotonic() + max(0.0, timeout)
        with self._lock:
            process_pool = self._process_pool
            if process_pool is None or (
                expected_pool is not None and process_pool is not expected_pool
            ):
                return
            # 在任何关闭动作前摘除旧代，不能在收尾时清空后来创建的新池。
            self._process_pool = None

        # shutdown 会清空 executor 的内部引用，必须先保留实际创建的资源。
        worker_processes: list[BaseProcess] = list(
            (getattr(process_pool, "_processes", None) or {}).values()
        )
        manager_thread = vars(process_pool).get("_executor_manager_thread")
        errors: list[Exception] = []
        try:
            process_pool.shutdown(wait=wait, cancel_futures=cancel_futures)
        except Exception as exc:
            errors.append(exc)

        if not wait:
            try:
                self._terminate_worker_processes(worker_processes, deadline=deadline)
            except Exception as exc:
                errors.append(exc)
            if manager_thread is not None:
                manager_thread.join(timeout=max(0.0, deadline - monotonic()))
                if manager_thread.is_alive():
                    errors.append(TimeoutError("进程池管理线程未在回收预算内退出"))

        if errors:
            raise ExceptionGroup("进程池关闭失败", errors)

    def _terminate_worker_processes(
        self,
        worker_processes: list[BaseProcess],
        *,
        deadline: float | None = None,
    ) -> None:
        """先通知所有 worker，再按共同 deadline 等待、强杀并回收。"""
        if deadline is None:
            deadline = monotonic() + 0.4
        alive: list[BaseProcess] = []
        survivors: list[BaseProcess] = []
        errors: list[Exception] = []
        for process in worker_processes:
            try:
                if process.is_alive():
                    alive.append(process)
                    process.terminate()
            except (ProcessLookupError, ValueError):
                # executor 管理线程可能已回收并关闭该进程句柄。
                continue
            except OSError as exc:
                errors.append(exc)

        for process in alive:
            try:
                process.join(timeout=min(0.2, max(0.0, deadline - monotonic())))
                if process.is_alive():
                    survivors.append(process)
            except (ProcessLookupError, ValueError):
                continue
            except OSError as exc:
                errors.append(exc)
                survivors.append(process)

        for process in survivors:
            try:
                process.kill()
            except (ProcessLookupError, ValueError):
                continue
            except OSError as exc:
                errors.append(exc)

        for process in survivors:
            try:
                process.join(timeout=min(0.2, max(0.0, deadline - monotonic())))
                if process.is_alive():
                    errors.append(TimeoutError("进程池 worker 未在回收预算内退出"))
            except (ProcessLookupError, ValueError):
                continue
            except OSError as exc:
                errors.append(exc)

        if errors:
            raise ExceptionGroup("进程池 worker 回收失败", errors)

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


def get_task_manager(process_workers: int | None = None) -> TaskManager:
    """获取全局 TaskManager 实例

    Args:
        process_workers: 可选进程池大小；首次创建时使用，已存在时显式重配。
            仅在活跃独立作用域借用宿主池时保留宿主配置。

    Returns:
        TaskManager: 全局任务管理器单例
    """
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager(process_workers=process_workers or 4)
    elif process_workers is not None:
        _task_manager.configure_process_pool(process_workers)
    return _task_manager
