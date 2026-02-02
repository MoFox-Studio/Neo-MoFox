"""
统一调度器核心实现

提供基于时间、事件和自定义条件的异步任务调度能力。

核心特性:
1. 任务隔离 - 每个任务独立执行，互不阻塞
2. 优雅降级 - 失败任务不影响其他任务
3. 资源管理 - 自动清理完成的任务
4. 超时保护 - 防止任务永久挂起
5. 并发控制 - 使用 concurrency 模块统一管理任务
"""

import asyncio
import uuid
import weakref
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger
from src.kernel.scheduler.types import TaskExecution, TaskStatus, TriggerType
from src.kernel.scheduler.time_utils import next_after

logger = get_logger("kernel.scheduler", display="调度器")


@dataclass
class SchedulerConfig:
    """调度器配置"""

    # 检查间隔
    check_interval: float = 1.0  # 主循环检查间隔(秒)

    # 超时配置
    task_default_timeout: float = 300.0  # 默认任务超时(5分钟)
    task_cancel_timeout: float = 10.0  # 任务取消超时(10秒)
    shutdown_timeout: float = 30.0  # 关闭超时(30秒)

    # 并发控制
    max_concurrent_tasks: int = 100  # 最大并发任务数
    enable_task_semaphore: bool = True  # 是否启用任务信号量

    # 重试配置
    enable_retry: bool = True  # 是否启用失败重试
    max_retries: int = 3  # 最大重试次数
    retry_delay: float = 5.0  # 重试延迟(秒)

    # 资源管理
    cleanup_interval: float = 60.0  # 清理已完成任务的间隔(秒)
    keep_completed_tasks: int = 100  # 保留的已完成任务数


@dataclass
class ScheduleTask:
    """调度任务模型"""

    # 基本信息
    schedule_id: str
    task_name: str
    callback: Callable[..., Awaitable[Any]]

    # 触发配置
    trigger_type: TriggerType
    trigger_config: dict[str, Any]
    is_recurring: bool = False

    # 回调参数
    callback_args: tuple[Any, ...] = field(default_factory=tuple)
    callback_kwargs: dict[str, Any] = field(default_factory=dict)

    # 状态信息
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    last_triggered_at: datetime | None = None

    # 循环任务的预定触发时间（用于精确调度）
    _scheduled_trigger_time: datetime | None = field(default=None, init=False, repr=False)

    # 统计信息
    trigger_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_execution_time: float = 0.0

    # 执行记录
    execution_history: list[TaskExecution] = field(default_factory=list)
    current_execution: TaskExecution | None = None

    # 重试配置
    max_retries: int = 0
    retry_count: int = 0
    last_error: Exception | None = None

    # 超时配置
    timeout: float | None = None

    # 运行时引用
    _asyncio_task_id: str | None = field(default=None, init=False, repr=False)  # 存储 task_manager 的 task_id
    _weak_scheduler: Any = field(default=None, init=False, repr=False)  # weakref.ref[UnifiedScheduler]

    def __repr__(self) -> str:
        return (
            f"ScheduleTask(id={self.schedule_id[:8]}..., "
            f"name={self.task_name}, type={self.trigger_type.value}, "
            f"status={self.status.value}, recurring={self.is_recurring})"
        )

    def is_active(self) -> bool:
        """任务是否活跃（可以被触发）"""
        return self.status in (TaskStatus.PENDING, TaskStatus.RUNNING)

    def can_trigger(self) -> bool:
        """任务是否可以被触发"""
        return self.status == TaskStatus.PENDING

    def start_execution(self) -> TaskExecution:
        """开始新的执行"""
        execution = TaskExecution(execution_id=str(uuid.uuid4()), started_at=datetime.now())
        self.current_execution = execution
        self.status = TaskStatus.RUNNING
        return execution

    def finish_execution(self, success: bool, result: Any = None, error: Exception | None = None) -> None:
        """完成当前执行"""
        if not self.current_execution:
            return

        if success:
            self.current_execution.complete(result)
            self.success_count += 1
            self.retry_count = 0  # 重置重试计数
        else:
            self.current_execution.fail(error or Exception("Unknown error"))
            self.failure_count += 1
            self.last_error = error

        self.total_execution_time += self.current_execution.duration

        # 保留最近10条执行记录
        self.execution_history.append(self.current_execution)
        if len(self.execution_history) > 10:
            self.execution_history.pop(0)

        self.current_execution = None
        self.last_triggered_at = datetime.now()
        self.trigger_count += 1

        # 更新状态
        if self.is_recurring:
            self.status = TaskStatus.PENDING
        else:
            self.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED


class UnifiedScheduler:
    """统一调度器

    提供基于时间、事件和自定义条件的异步任务调度能力。

    特点：
    - 每秒检查一次所有任务
    - 自动执行到期任务
    - 支持循环和一次性任务
    - 提供完整的任务管理API
    - 内置超时保护和重试机制
    """

    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig()

        # 获取 task_manager 实例
        self._task_manager = get_task_manager()

        # 任务存储
        self._tasks: dict[str, ScheduleTask] = {}
        self._tasks_by_name: dict[str, str] = {}  # task_name -> schedule_id 快速查找

        # 运行状态
        self._running = False
        self._stopping = False

        # 后台任务（存储 TaskInfo）
        self._check_loop_task_id: str | None = None
        self._cleanup_task_id: str | None = None
        # 防止主循环重复投递 check_and_trigger 导致堆积
        self._check_trigger_task_id: str | None = None

        # 事件订阅追踪（预留，未来集成 event 模块）
        self._event_subscriptions: dict[str, set[str]] = defaultdict(set)  # event -> {task_ids}

        # 并发控制
        self._task_semaphore: asyncio.Semaphore | None = None
        if self.config.enable_task_semaphore:
            self._task_semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)

        # 统计信息
        self._total_executions = 0
        self._total_failures = 0
        self._total_timeouts = 0
        self._start_time: datetime | None = None

        # 已完成任务缓存（用于统计）
        self._completed_tasks: list[ScheduleTask] = []

    # ==================== 生命周期管理 ====================

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("调度器已在运行中")
            return

        self._running = True
        self._stopping = False
        self._start_time = datetime.now()

        # 启动后台任务（使用 task_manager）
        check_task = self._task_manager.create_task(
            self._check_loop(),
            name="scheduler_check_loop",
            daemon=True,  # 后台任务，不受超时检查
        )
        self._check_loop_task_id = check_task.task_id

        cleanup_task = self._task_manager.create_task(
            self._cleanup_loop(),
            name="scheduler_cleanup",
            daemon=True,
        )
        self._cleanup_task_id = cleanup_task.task_id

        logger.debug("统一调度器已启动")

    async def stop(self) -> None:
        """停止调度器（优雅关闭）"""
        if not self._running:
            return

        logger.debug("正在停止统一调度器...")
        self._stopping = True
        self._running = False

        # 取消后台任务（使用 task_manager）
        background_task_ids = [
            self._check_loop_task_id,
            self._cleanup_task_id,
            self._check_trigger_task_id,
        ]

        for task_id in background_task_ids:
            if task_id:
                try:
                    self._task_manager.cancel_task(task_id)
                except Exception:
                    pass  # 任务可能已经完成

        # 等待后台任务完成
        await asyncio.sleep(0.1)  # 给一点时间让任务清理

        # 取消所有正在执行的任务
        await self._cancel_all_running_tasks()

        # 清理资源
        self._tasks.clear()
        self._tasks_by_name.clear()
        self._event_subscriptions.clear()
        self._completed_tasks.clear()

        logger.info("统一调度器已停止")

    async def _cancel_all_running_tasks(self) -> None:
        """取消所有正在运行的任务"""
        running_task_ids = [
            task._asyncio_task_id
            for task in self._tasks.values()
            if task.status == TaskStatus.RUNNING and task._asyncio_task_id
        ]

        if not running_task_ids:
            return

        logger.info(f"正在取消 {len(running_task_ids)} 个运行中的任务...")

        # 通过 task_manager 取消任务
        for task_id in running_task_ids:
            try:
                self._task_manager.cancel_task(task_id)
            except Exception:
                pass  # 任务可能已经完成

        # 等待取消完成（带超时）
        try:
            await asyncio.sleep(self.config.shutdown_timeout)
            logger.info("所有任务已取消")
        except Exception:
            logger.warning(f"部分任务取消超时（{self.config.shutdown_timeout}秒），强制停止")

    # ==================== 后台循环 ====================

    async def _check_loop(self) -> None:
        """主循环：定期检查和触发任务"""
        logger.debug("调度器主循环已启动")

        while self._running:
            try:
                await asyncio.sleep(self.config.check_interval)

                if not self._stopping:
                    # 使用 task_manager 创建任务，避免阻塞循环；同时避免重入堆积
                    if self._check_trigger_task_id is not None:
                        try:
                            existing = self._task_manager.get_task(self._check_trigger_task_id)
                            if existing.task is not None and not existing.is_done():
                                continue
                        except Exception:
                            # 任务可能已完成或已被清理
                            pass

                    task_info = self._task_manager.create_task(
                        self._check_and_trigger_tasks(),
                        name="check_trigger_tasks",
                        daemon=True,
                    )
                    self._check_trigger_task_id = task_info.task_id

            except asyncio.CancelledError:
                logger.debug("调度器主循环被取消")
                break
            except Exception as e:
                logger.error(f"调度器主循环发生错误: {e}")

    async def _cleanup_loop(self) -> None:
        """清理循环：定期清理已完成的任务"""
        logger.debug("清理循环已启动")

        while self._running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)

                if not self._stopping:
                    await self._cleanup_completed_tasks()

            except asyncio.CancelledError:
                logger.debug("清理循环被取消")
                break
            except Exception as e:
                logger.error(f"清理循环发生错误: {e}")

    # ==================== 任务触发逻辑 ====================

    async def _check_and_trigger_tasks(self) -> None:
        """检查并触发到期任务"""
        current_time = datetime.now()
        tasks_to_trigger: list[ScheduleTask] = []

        # 第一阶段：收集需要触发的任务
        for task in list(self._tasks.values()):
            if not task.can_trigger():
                continue

            try:
                should_trigger = await self._should_trigger_task(task, current_time)
                if should_trigger:
                    tasks_to_trigger.append(task)
            except Exception as e:
                logger.error(f"检查任务 {task.task_name} 触发条件时出错: {e}")

        # 第二阶段：并发触发所有任务
        if tasks_to_trigger:
            await self._trigger_tasks_concurrently(tasks_to_trigger)

    async def _should_trigger_task(self, task: ScheduleTask, current_time: datetime) -> bool:
        """判断任务是否应该触发"""
        if task.trigger_type == TriggerType.TIME:
            return self._check_time_trigger(task, current_time)
        elif task.trigger_type == TriggerType.CUSTOM:
            return await self._check_custom_trigger(task)
        # EVENT 类型由外部触发
        return False

    def _check_time_trigger(self, task: ScheduleTask, current_time: datetime) -> bool:
        """检查时间触发条件"""
        config = task.trigger_config

        # 1) delay_seconds / interval_seconds：延迟触发与循环间隔
        interval_key = (
            "interval_seconds" if task.is_recurring and "interval_seconds" in config else "delay_seconds"
        )

        if interval_key in config:
            interval = float(config[interval_key])

            # 一次性任务：从创建时间算起
            if not task.is_recurring:
                elapsed = (current_time - task.created_at).total_seconds()
                return elapsed >= interval

            # 循环任务：维护“下一次触发时间”以避免重复触发
            if task._scheduled_trigger_time is None:
                task._scheduled_trigger_time = task.created_at + timedelta(seconds=interval)

            if current_time >= task._scheduled_trigger_time:
                task._scheduled_trigger_time = next_after(
                    current_time,
                    task._scheduled_trigger_time,
                    interval,
                )
                return True

            return False

        # 2) trigger_at（指定时间触发）
        elif "trigger_at" in config:
            trigger_time = config["trigger_at"]
            if isinstance(trigger_time, str):
                trigger_time = datetime.fromisoformat(trigger_time)

            if task.is_recurring and "interval_seconds" in config:
                # 循环任务：维护“下一次触发时间”
                interval = float(config["interval_seconds"])

                if task._scheduled_trigger_time is None:
                    task._scheduled_trigger_time = trigger_time

                if current_time >= task._scheduled_trigger_time:
                    task._scheduled_trigger_time = next_after(
                        current_time,
                        task._scheduled_trigger_time,
                        interval,
                    )
                    return True

                return False
            else:
                # 一次性任务：检查是否到达触发时间
                return current_time >= trigger_time

        return False

    async def _check_custom_trigger(self, task: ScheduleTask) -> bool:
        """检查自定义触发条件"""
        condition_func = task.trigger_config.get("condition_func")
        if not condition_func or not callable(condition_func):
            logger.warning(f"任务 {task.task_name} 的自定义条件函数无效")
            return False

        try:
            if asyncio.iscoroutinefunction(condition_func):
                result = await condition_func()
            else:
                result = condition_func()
            return bool(result)
        except Exception as e:
            logger.error(f"执行任务 {task.task_name} 的自定义条件函数时出错: {e}")
            return False

    async def _trigger_tasks_concurrently(self, tasks: list[ScheduleTask]) -> None:
        """并发触发多个任务"""
        logger.debug(f"并发触发 {len(tasks)} 个任务")

        # 为每个任务创建独立的执行 Task（使用 task_manager）
        execution_tasks = []
        for task in tasks:
            task_info = self._task_manager.create_task(
                self._execute_task(task),
                name=f"exec_{task.task_name}",
                daemon=True,  # 调度器执行的任务不受超时检查
            )
            task._asyncio_task_id = task_info.task_id
            execution_tasks.append(task_info.task_id)

        # 等待所有任务完成（不阻塞主循环）
        # 使用 return_exceptions=True 确保单个任务失败不影响其他任务
        for task_id in execution_tasks:
            try:
                task_info = self._task_manager.get_task(task_id)
                if task_info.task and not task_info.is_done():
                    await task_info.task
            except Exception:
                pass  # 任务可能已经完成或失败

    async def _execute_task(self, task: ScheduleTask) -> None:
        """执行单个任务（完全隔离）"""
        task.start_execution()

        try:
            # 使用信号量控制并发
            async with self._acquire_semaphore():
                # 应用超时保护
                timeout = task.timeout or self.config.task_default_timeout

                # 重试循环
                while True:
                    try:
                        await asyncio.wait_for(self._run_callback(task), timeout=timeout)

                        # 执行成功
                        task.finish_execution(success=True)
                        self._total_executions += 1
                        logger.debug(f"任务 {task.task_name} 执行成功 (第{task.trigger_count}次)")
                        break  # 成功，退出重试循环

                    except asyncio.TimeoutError:
                        # 任务超时
                        logger.warning(f"任务 {task.task_name} 执行超时 ({timeout}秒)")
                        if task.current_execution:
                            task.current_execution.status = TaskStatus.TIMEOUT
                        task.finish_execution(success=False, error=TimeoutError(f"Task timeout after {timeout}s"))
                        self._total_timeouts += 1
                        break  # 超时不重试

                    except asyncio.CancelledError:
                        # 任务被取消
                        logger.debug(f"任务 {task.task_name} 被取消")
                        if task.current_execution:
                            task.current_execution.cancel()
                        task.status = TaskStatus.CANCELLED
                        raise  # 重新抛出，让上层处理

                    except Exception as e:
                        # 任务执行失败
                        logger.error(f"任务 {task.task_name} 执行失败: {e}")
                        self._total_failures += 1

                        # 检查是否需要重试
                        if self.config.enable_retry and task.retry_count < task.max_retries:
                            task.retry_count += 1
                            logger.info(
                                f"任务 {task.task_name} 将在 {self.config.retry_delay}秒后重试 "
                                f"({task.retry_count}/{task.max_retries})"
                            )
                            await asyncio.sleep(self.config.retry_delay)
                            # 重置执行记录以进行重试
                            task.current_execution = TaskExecution(
                                execution_id=str(uuid.uuid4()), started_at=datetime.now()
                            )
                            # 继续重试循环
                        else:
                            # 不重试或重试次数用尽
                            task.finish_execution(success=False, error=e)
                            break  # 退出重试循环

        finally:
            # 清理
            task._asyncio_task_id = None

            # 如果是一次性任务且已完成（成功、失败或超时），移动到已完成列表
            if not task.is_recurring and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT):
                await self._move_to_completed(task)

    async def _run_callback(self, task: ScheduleTask) -> Any:
        """运行任务回调函数"""
        try:
            if asyncio.iscoroutinefunction(task.callback):
                result = await task.callback(*task.callback_args, **task.callback_kwargs)
            else:
                # 同步函数在线程中运行，避免阻塞事件循环
                result = await asyncio.to_thread(
                    task.callback,
                    *task.callback_args,
                    **task.callback_kwargs,
                )
            return result
        except Exception as e:
            logger.error(f"执行任务 {task.task_name} 的回调函数时出错: {e}")
            raise

    def _acquire_semaphore(self):
        """获取信号量（如果启用）"""
        if self._task_semaphore:
            return self._task_semaphore
        else:
            # 返回一个空的上下文管理器
            from contextlib import nullcontext

            return nullcontext()

    async def _move_to_completed(self, task: ScheduleTask) -> None:
        """将任务移动到已完成列表"""
        if task.schedule_id in self._tasks:
            self._tasks.pop(task.schedule_id)
            self._tasks_by_name.pop(task.task_name, None)

            # 清理事件订阅
            if task.trigger_type == TriggerType.EVENT:
                event_name = task.trigger_config.get("event_name")
                if event_name and event_name in self._event_subscriptions:
                    self._event_subscriptions[event_name].discard(task.schedule_id)
                    if not self._event_subscriptions[event_name]:
                        del self._event_subscriptions[event_name]

            # 添加到已完成列表
            self._completed_tasks.append(task)
            if len(self._completed_tasks) > self.config.keep_completed_tasks:
                self._completed_tasks.pop(0)

            logger.debug(f"一次性任务 {task.task_name} 已完成并移除")

    # ==================== 事件触发处理（预留接口）====================

    async def trigger_event(self, event_name: str, event_params: dict[str, Any] | None = None) -> None:
        """触发事件（预留接口，未来集成 event 模块）

        Args:
            event_name: 事件名称
            event_params: 事件参数
        """
        task_ids = self._event_subscriptions.get(event_name, set())
        if not task_ids:
            return

        event_params = event_params or {}

        # 收集需要触发的任务
        tasks_to_trigger = []
        for task_id in list(task_ids):  # 使用 list() 避免迭代时修改
            task = self._tasks.get(task_id)
            if task and task.can_trigger():
                tasks_to_trigger.append(task)

        if not tasks_to_trigger:
            return

        logger.debug(f"事件 '{event_name}' 触发 {len(tasks_to_trigger)} 个任务")

        # 并发执行所有事件任务（使用 task_manager）
        execution_task_ids = []
        for task in tasks_to_trigger:
            # 将事件参数注入到回调
            task_info = self._task_manager.create_task(
                self._execute_event_task(task, event_params),
                name=f"event_exec_{task.task_name}",
                daemon=True,
            )
            task._asyncio_task_id = task_info.task_id
            execution_task_ids.append(task_info.task_id)

        # 等待所有任务完成
        for task_id in execution_task_ids:
            try:
                task_info = self._task_manager.get_task(task_id)
                if task_info.task and not task_info.is_done():
                    await task_info.task
            except Exception:
                pass  # 任务可能已经完成或失败

    async def _execute_event_task(self, task: ScheduleTask, event_params: dict[str, Any]) -> None:
        """执行事件触发的任务"""
        task.start_execution()

        try:
            async with self._acquire_semaphore():
                timeout = task.timeout or self.config.task_default_timeout

                try:
                    # 合并事件参数和任务参数
                    merged_kwargs = {**task.callback_kwargs, **event_params}

                    if asyncio.iscoroutinefunction(task.callback):
                        await asyncio.wait_for(task.callback(*task.callback_args, **merged_kwargs), timeout=timeout)
                    else:
                        await asyncio.wait_for(
                            asyncio.to_thread(task.callback, *task.callback_args, **merged_kwargs),
                            timeout=timeout,
                        )

                    task.finish_execution(success=True)
                    self._total_executions += 1
                    logger.debug(f"事件任务 {task.task_name} 执行成功")

                except asyncio.TimeoutError:
                    logger.warning(f"事件任务 {task.task_name} 执行超时")
                    task.status = TaskStatus.TIMEOUT
                    task.finish_execution(success=False, error=TimeoutError())
                    self._total_timeouts += 1

                except asyncio.CancelledError:
                    logger.debug(f"事件任务 {task.task_name} 被取消")
                    if task.current_execution:
                        task.current_execution.cancel()
                    task.status = TaskStatus.CANCELLED
                    raise

                except Exception as e:
                    logger.error(f"事件任务 {task.task_name} 执行失败: {e}")
                    task.finish_execution(success=False, error=e)
                    self._total_failures += 1

        finally:
            task._asyncio_task_id = None

            if not task.is_recurring and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT):
                await self._move_to_completed(task)

    # ==================== 资源清理 ====================

    async def _cleanup_completed_tasks(self) -> None:
        """清理已完成的任务"""
        # 清理已完成的一次性任务
        completed_tasks = [
            task for task in self._tasks.values() if not task.is_recurring and task.status == TaskStatus.COMPLETED
        ]

        for task in completed_tasks:
            await self._move_to_completed(task)

        if completed_tasks:
            logger.debug(f"清理了 {len(completed_tasks)} 个已完成的任务")

        # 清理已完成的 task
        for task in list(self._tasks.values()):
            if task._asyncio_task_id:
                try:
                    task_info = self._task_manager.get_task(task._asyncio_task_id)
                    if task_info.is_done():
                        task._asyncio_task_id = None
                except Exception:
                    # 任务可能已经被清理
                    task._asyncio_task_id = None

    # ==================== 任务管理 API ====================

    async def create_schedule(
        self,
        callback: Callable[..., Awaitable[Any]],
        trigger_type: TriggerType,
        trigger_config: dict[str, Any],
        is_recurring: bool = False,
        task_name: str | None = None,
        callback_args: tuple | None = None,
        callback_kwargs: dict | None = None,
        force_overwrite: bool = False,
        timeout: float | None = None,
        max_retries: int = 0,
    ) -> str:
        """创建调度任务

        Args:
            callback: 回调函数（必须是异步函数）
            trigger_type: 触发类型
            trigger_config: 触发配置
            is_recurring: 是否循环任务
            task_name: 任务名称（建议提供，用于查找和管理）
            callback_args: 回调函数位置参数
            callback_kwargs: 回调函数关键字参数
            force_overwrite: 如果同名任务已存在，是否强制覆盖
            timeout: 任务超时时间（秒），None表示使用默认值
            max_retries: 最大重试次数

        Returns:
            str: 创建的 schedule_id

        Raises:
            ValueError: 如果同名任务已存在且未启用强制覆盖
            RuntimeError: 如果调度器未运行
        """
        if not self._running:
            raise RuntimeError("调度器未运行，请先调用 start()")

        # 生成任务ID和名称
        schedule_id = str(uuid.uuid4())
        if task_name is None:
            task_name = f"Task-{schedule_id[:8]}"

        # 检查同名任务
        if task_name in self._tasks_by_name:
            existing_id = self._tasks_by_name[task_name]
            existing_task = self._tasks.get(existing_id)

            if existing_task and existing_task.is_active():
                if force_overwrite:
                    logger.info(f"检测到同名活跃任务 '{task_name}'，启用强制覆盖，移除现有任务")
                    await self.remove_schedule(existing_id)
                else:
                    raise ValueError(
                        f"任务名称 '{task_name}' 已存在活跃任务 (ID: {existing_id[:8]}...)。"
                        f"如需覆盖，请设置 force_overwrite=True"
                    )

        # 创建任务
        task = ScheduleTask(
            schedule_id=schedule_id,
            task_name=task_name,
            callback=callback,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            is_recurring=is_recurring,
            callback_args=callback_args or (),
            callback_kwargs=callback_kwargs or {},
            timeout=timeout,
            max_retries=max_retries,
        )

        # 保存弱引用到调度器（避免循环引用）
        task._weak_scheduler = weakref.ref(self)

        # 注册任务
        self._tasks[schedule_id] = task
        self._tasks_by_name[task_name] = schedule_id

        # 如果是事件触发，注册事件订阅
        if trigger_type == TriggerType.EVENT:
            event_name = trigger_config.get("event_name")
            if not event_name:
                raise ValueError("事件触发类型必须提供 event_name")
            self._event_subscriptions[event_name].add(schedule_id)
            logger.debug(f"任务 {task_name} 订阅事件: {event_name}")

        logger.debug(f"创建调度任务: {task_name} (ID: {schedule_id[:8]}...)")
        return schedule_id

    async def remove_schedule(self, schedule_id: str) -> bool:
        """移除调度任务

        如果任务正在执行，会安全地取消执行中的任务

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功移除
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试移除不存在的任务: {schedule_id[:8]}...")
            return False

        # 如果任务正在运行，先取消（使用 task_manager）
        if task.status == TaskStatus.RUNNING and task._asyncio_task_id:
            try:
                self._task_manager.cancel_task(task._asyncio_task_id)
            except Exception:
                pass  # 任务可能已经完成

        # 从字典中移除
        self._tasks.pop(schedule_id, None)
        self._tasks_by_name.pop(task.task_name, None)

        # 清理事件订阅
        if task.trigger_type == TriggerType.EVENT:
            event_name = task.trigger_config.get("event_name")
            if event_name and event_name in self._event_subscriptions:
                self._event_subscriptions[event_name].discard(schedule_id)
                if not self._event_subscriptions[event_name]:
                    del self._event_subscriptions[event_name]
                    logger.debug(f"事件 '{event_name}' 已无订阅任务")

        logger.debug(f"移除调度任务: {task.task_name}")
        return True

    async def remove_schedule_by_name(self, task_name: str) -> bool:
        """根据任务名称移除调度任务

        Args:
            task_name: 任务名称

        Returns:
            bool: 是否成功移除
        """
        schedule_id = self._tasks_by_name.get(task_name)
        if schedule_id:
            return await self.remove_schedule(schedule_id)
        logger.warning(f"未找到名为 '{task_name}' 的任务")
        return False

    async def find_schedule_by_name(self, task_name: str) -> str | None:
        """根据任务名称查找 schedule_id

        Args:
            task_name: 任务名称

        Returns:
            str | None: 找到的 schedule_id，如果不存在则返回 None
        """
        return self._tasks_by_name.get(task_name)

    async def trigger_schedule(self, schedule_id: str) -> bool:
        """强制触发指定任务（立即执行）

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功触发
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试触发不存在的任务: {schedule_id[:8]}...")
            return False

        if not task.can_trigger():
            logger.warning(f"任务 {task.task_name} 当前状态 {task.status.value} 无法触发")
            return False

        logger.info(f"强制触发任务: {task.task_name}")

        # 创建执行任务（使用 task_manager）
        task_info = self._task_manager.create_task(
            self._execute_task(task),
            name=f"manual_trigger_{task.task_name}",
            daemon=True,
        )
        task._asyncio_task_id = task_info.task_id

        # 等待完成
        try:
            if task_info.task and not task_info.is_done():
                await task_info.task
            return task.status == TaskStatus.COMPLETED
        except Exception as e:
            logger.error(f"强制触发任务 {task.task_name} 失败: {e}")
            return False

    async def pause_schedule(self, schedule_id: str) -> bool:
        """暂停任务（不删除，但不会被触发）

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功暂停
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试暂停不存在的任务: {schedule_id[:8]}...")
            return False

        if task.status == TaskStatus.RUNNING:
            logger.warning(f"任务 {task.task_name} 正在运行，无法暂停")
            return False

        task.status = TaskStatus.PAUSED
        logger.debug(f"暂停任务: {task.task_name}")
        return True

    async def resume_schedule(self, schedule_id: str) -> bool:
        """恢复暂停的任务

        Args:
            schedule_id: 任务ID

        Returns:
            bool: 是否成功恢复
        """
        task = self._tasks.get(schedule_id)
        if not task:
            logger.warning(f"尝试恢复不存在的任务: {schedule_id[:8]}...")
            return False

        if task.status != TaskStatus.PAUSED:
            logger.warning(f"任务 {task.task_name} 状态为 {task.status.value}，无需恢复")
            return False

        task.status = TaskStatus.PENDING
        logger.debug(f"恢复任务: {task.task_name}")
        return True

    async def get_task_info(self, schedule_id: str) -> dict[str, Any] | None:
        """获取任务详细信息

        Args:
            schedule_id: 任务ID

        Returns:
            dict | None: 任务信息字典，如果不存在返回 None
        """
        task = self._tasks.get(schedule_id)
        if not task:
            return None

        # 计算平均执行时间
        avg_execution_time = 0.0
        if task.success_count > 0:
            avg_execution_time = task.total_execution_time / task.success_count

        return {
            "schedule_id": task.schedule_id,
            "task_name": task.task_name,
            "trigger_type": task.trigger_type.value,
            "is_recurring": task.is_recurring,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "last_triggered_at": task.last_triggered_at.isoformat() if task.last_triggered_at else None,
            "trigger_count": task.trigger_count,
            "success_count": task.success_count,
            "failure_count": task.failure_count,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "avg_execution_time": avg_execution_time,
            "total_execution_time": task.total_execution_time,
            "is_running": task.status == TaskStatus.RUNNING,
            "trigger_config": task.trigger_config.copy(),
            "timeout": task.timeout,
            "last_error": str(task.last_error) if task.last_error else None,
        }

    async def list_tasks(
        self,
        trigger_type: TriggerType | None = None,
        status: TaskStatus | None = None,
    ) -> list[dict[str, Any]]:
        """列出所有任务或指定类型/状态的任务

        Args:
            trigger_type: 触发类型过滤
            status: 状态过滤

        Returns:
            list: 任务信息列表
        """
        tasks = []
        for task in self._tasks.values():
            # 应用过滤器
            if trigger_type is not None and task.trigger_type != trigger_type:
                continue
            if status is not None and task.status != status:
                continue

            task_info = await self.get_task_info(task.schedule_id)
            if task_info:
                tasks.append(task_info)

        return tasks

    def get_statistics(self) -> dict[str, Any]:
        """获取调度器统计信息

        Returns:
            dict: 统计信息字典
        """
        # 统计各状态的任务数
        status_counts = defaultdict(int)
        for task in self._tasks.values():
            status_counts[task.status.value] += 1

        # 统计各类型的任务数
        type_counts = defaultdict(int)
        for task in self._tasks.values():
            type_counts[task.trigger_type.value] += 1

        # 计算运行时长
        uptime = 0.0
        if self._start_time:
            uptime = (datetime.now() - self._start_time).total_seconds()

        # 获取正在运行的任务
        running_tasks_info = []
        for task in self._tasks.values():
            if task.status == TaskStatus.RUNNING and task.current_execution:
                runtime = (datetime.now() - task.current_execution.started_at).total_seconds()
                running_tasks_info.append(
                    {
                        "schedule_id": task.schedule_id[:8] + "...",
                        "task_name": task.task_name,
                        "runtime": runtime,
                    }
                )

        return {
            "is_running": self._running,
            "uptime_seconds": uptime,
            "total_tasks": len(self._tasks),
            "active_tasks": status_counts[TaskStatus.PENDING.value],
            "running_tasks": status_counts[TaskStatus.RUNNING.value],
            "paused_tasks": status_counts[TaskStatus.PAUSED.value],
            "completed_tasks_archived": len(self._completed_tasks),
            "status_breakdown": dict(status_counts),
            "type_breakdown": dict(type_counts),
            "recurring_tasks": sum(1 for t in self._tasks.values() if t.is_recurring),
            "one_time_tasks": sum(1 for t in self._tasks.values() if not t.is_recurring),
            "registered_events": list(self._event_subscriptions.keys()),
            "total_executions": self._total_executions,
            "total_failures": self._total_failures,
            "total_timeouts": self._total_timeouts,
            "success_rate": (
                self._total_executions / (self._total_executions + self._total_failures)
                if self._total_executions + self._total_failures > 0
                else 0.0
            ),
            "running_tasks_info": running_tasks_info,
            "config": {
                "max_concurrent_tasks": self.config.max_concurrent_tasks,
                "task_default_timeout": self.config.task_default_timeout,
                "enable_retry": self.config.enable_retry,
                "max_retries": self.config.max_retries,
            },
        }
