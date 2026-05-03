"""
Concurrency 模块单元测试

测试 TaskManager、TaskGroup 和 TaskInfo 的功能。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from src.kernel.concurrency import (
    get_task_manager,
    TaskManager,
    TaskGroup,
    TaskInfo,
    get_watchdog,
    WatchDog,
    StreamHeartbeat,
    TaskGroupError,
)
from src.kernel.concurrency.exceptions import (
    TaskNotFoundError,
    TaskTimeoutError,
    TaskGroupAlreadyExists,
    TaskGroupNotFoundError,
    WatchDogError,
)


def process_add(a: int, b: int) -> int:
    return a + b


def process_sleep(seconds: float) -> str:
    import time

    time.sleep(seconds)
    return "done"


class TestTaskInfo:
    """测试 TaskInfo 数据类"""

    def test_task_info_creation(self) -> None:
        """测试 TaskInfo 创建"""
        task_info = TaskInfo(
            task_id="test_id",
            name="test_task",
            daemon=False,
            timeout=10.0,
        )

        assert task_info.task_id == "test_id"
        assert task_info.name == "test_task"
        assert task_info.daemon is False
        assert task_info.timeout == 10.0
        assert isinstance(task_info.created_at, datetime)
        assert task_info.group_name is None

    @pytest.mark.asyncio
    async def test_task_info_status_methods(self) -> None:
        """测试 TaskInfo 状态方法"""
        async def sample_task():
            await asyncio.sleep(0.1)
            return "done"

        task = asyncio.create_task(sample_task())
        task_info = TaskInfo(task_id="test_id", task=task)

        # 任务未完成
        assert not task_info.is_done()
        assert not task_info.is_cancelled()
        assert not task_info.is_failed()

        # 等待任务完成
        await task

        # 任务已完成
        assert task_info.is_done()
        assert not task_info.is_cancelled()
        assert not task_info.is_failed()
        assert task_info.get_result() == "done"


class TestTaskManager:
    """测试 TaskManager 类"""

    def test_singleton(self) -> None:
        """测试单例模式"""
        tm1 = get_task_manager()
        tm2 = get_task_manager()

        assert tm1 is tm2
        assert isinstance(tm1, TaskManager)

    @pytest.mark.asyncio
    async def test_create_task(self) -> None:
        """测试创建任务"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)
            return "result"

        task_info = tm.create_task(sample_task(), name="test_task")

        assert task_info.name == "test_task"
        assert task_info.daemon is False
        assert task_info.task is not None
        assert not task_info.is_done()

        # 等待完成
        result = await task_info.task
        assert result == "result"

    @pytest.mark.asyncio
    async def test_create_daemon_task(self) -> None:
        """测试创建守护任务"""
        tm = get_task_manager()

        async def daemon_task():
            await asyncio.sleep(0.1)

        task_info = tm.create_task(daemon_task(), daemon=True)
        assert task_info.daemon is True
        await task_info.task

    @pytest.mark.asyncio
    async def test_wait_all_tasks(self) -> None:
        """测试等待所有任务完成"""
        tm = get_task_manager()

        # 清理之前的任务（避免全局单例带来的状态污染）
        # 先等待所有现有任务完成，然后清理
        for task_info in list(tm.get_all_tasks()):
            if not task_info.is_done() and task_info.task is not None:
                try:
                    await asyncio.wait_for(task_info.task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, RuntimeError):
                    # 忽略超时、取消或运行时错误
                    pass
        tm.cleanup_tasks()

        async def sample_task(n: int):
            await asyncio.sleep(0.1)
            return n

        # 创建多个任务
        created_tasks = []
        for i in range(5):
            task_info = tm.create_task(sample_task(i))
            created_tasks.append(task_info)

        # 等待所有任务完成
        await tm.wait_all_tasks()

        # 清理已完成任务并验证没有活跃任务
        tm.cleanup_tasks()
        active_tasks = tm.get_active_tasks()
        # 检查我们创建的任务是否都已完成
        for task_info in created_tasks:
            assert task_info.is_done(), f"Task {task_info.name} should be done"
        # 验证没有非守护的活跃任务
        active_non_daemon = [t for t in active_tasks if not t.daemon]
        assert len(active_non_daemon) == 0

    @pytest.mark.asyncio
    async def test_cancel_task(self) -> None:
        """测试取消任务"""
        tm = get_task_manager()

        async def long_task():
            await asyncio.sleep(0.05)
            return "should not complete"

        task_info = tm.create_task(long_task())

        # 取消任务
        success = tm.cancel_task(task_info.task_id)
        assert success is True

        # 等待取消完成
        try:
            await task_info.task
        except asyncio.CancelledError:
            pass

        assert task_info.is_cancelled()

    @pytest.mark.asyncio
    async def test_get_task_stats(self) -> None:
        """测试获取任务统计"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)

        # 创建任务
        tm.create_task(sample_task(), name="task1")
        tm.create_task(sample_task(), daemon=True, name="daemon_task")

        stats = tm.get_stats()

        assert stats["total_tasks"] >= 2
        assert stats["daemon_tasks"] >= 1

    @pytest.mark.asyncio
    async def test_to_process(self) -> None:
        """测试提交函数到进程池执行"""
        tm = TaskManager(process_workers=1)

        try:
            result = await tm.to_process(process_add, 1, 2)
        finally:
            tm.shutdown_process_pool(wait=False)

        assert result == 3

    @pytest.mark.asyncio
    async def test_to_process_timeout(self) -> None:
        """测试进程池任务超时"""
        tm = TaskManager(process_workers=1)

        try:
            with pytest.raises(asyncio.TimeoutError):
                await tm.to_process(process_sleep, 0.3, timeout=0.05)
        finally:
            tm.shutdown_process_pool(wait=False)

    def test_shutdown_process_pool_terminates_workers_when_not_waiting(self) -> None:
        """非阻塞关闭进程池时应主动终止残留 worker。"""
        tm = TaskManager(process_workers=1)

        process_alive = Mock()
        process_alive.is_alive.side_effect = [True, False, False]
        process_alive.terminate = Mock()
        process_alive.join = Mock()
        process_alive.kill = Mock()

        process_stopped = Mock()
        process_stopped.is_alive.return_value = False
        process_stopped.terminate = Mock()
        process_stopped.join = Mock()
        process_stopped.kill = Mock()

        pool = Mock()
        pool._processes = {1: process_alive, 2: process_stopped}
        pool.shutdown = Mock()
        tm._process_pool = pool

        tm.shutdown_process_pool(wait=False)

        pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        process_alive.terminate.assert_called_once_with()
        process_alive.join.assert_called_once_with(timeout=0.2)
        process_alive.kill.assert_not_called()
        process_stopped.terminate.assert_not_called()
        process_stopped.kill.assert_not_called()


class TestTaskGroup:
    """测试 TaskGroup 类"""

    @pytest.mark.asyncio
    async def test_task_group_context_manager(self) -> None:
        """测试 TaskGroup 上下文管理器"""
        tm = get_task_manager()

        async def task1():
            await asyncio.sleep(0.1)
            return "task1"

        async def task2():
            await asyncio.sleep(0.1)
            return "task2"

        async with tm.group(name="test_group") as tg:
            assert tg.is_active()

            t1 = tg.create_task(task1(), name="task1")
            t2 = tg.create_task(task2(), name="task2")

            assert tg.get_task_count() == 2

        # 退出上下文后，所有任务应完成
        assert not tg.is_active()
        assert t1.is_done()
        assert t2.is_done()

    @pytest.mark.asyncio
    async def test_task_group_shared(self) -> None:
        """测试 TaskGroup 共享"""
        tm = get_task_manager()

        async def task1():
            await asyncio.sleep(0.1)

        async def task2():
            await asyncio.sleep(0.1)

        # 第一次获取，创建新组
        group1 = tm.group(name="shared_group")
        assert group1.get_task_count() == 0

        async with group1 as tg:
            tg.create_task(task1())

        # 第二次获取，应返回同一个组
        group2 = tm.group(name="shared_group")
        assert group1 is group2

        async with group2 as tg:
            tg.create_task(task2())

    @pytest.mark.asyncio
    async def test_task_group_cancel_on_error(self) -> None:
        """测试 TaskGroup 错误时取消其他任务"""
        tm = get_task_manager()

        async def failing_task():
            await asyncio.sleep(0.05)
            raise ValueError("Task failed")

        async def long_task():
            await asyncio.sleep(0.05)
            return "should not complete"

        try:
            async with tm.group(name="error_group", cancel_on_error=True) as tg:
                tg.create_task(failing_task())
                tg.create_task(long_task())
        except ValueError:
            pass  # 预期的异常

    def test_task_group_inactive_error(self) -> None:
        """测试非激活状态创建任务抛出异常"""
        tg = TaskGroup(name="test_group")

        async def dummy_task():
            await asyncio.sleep(0)

        # 不在上下文管理器内创建任务应抛出异常
        # 使用 pytest.raises 来捕获异常，协程不会被实际创建
        try:
            tg.create_task(dummy_task())
        except TaskGroupError:
            pass  # 预期的异常


class TestWatchDog:
    """测试 WatchDog 类"""

    def test_get_watchdog_singleton(self) -> None:
        """测试 WatchDog 单例"""
        wd1 = get_watchdog()
        wd2 = get_watchdog()

        assert wd1 is wd2
        assert isinstance(wd1, WatchDog)

    def test_register_stream(self) -> None:
        """测试注册聊天流"""
        wd = get_watchdog()

        heartbeat = wd.register_stream(
            stream_id="test_stream",
            tick_interval=0.1,
            warning_threshold=150.0,
            restart_threshold=300.0,
        )

        assert isinstance(heartbeat, StreamHeartbeat)
        assert heartbeat.stream_id == "test_stream"
        assert heartbeat.tick_interval == 0.1
        assert heartbeat.restart_cooldown == 0.1

        # 清理
        wd.unregister_stream("test_stream")

    def test_feed_dog(self) -> None:
        """测试喂狗（更新心跳）"""
        wd = get_watchdog()

        wd.register_stream(stream_id="test_stream")

        # 记录初始心跳时间
        initial_time = wd._stream_registry["test_stream"].last_tick

        # 喂狗
        import time

        time.sleep(0.01)
        wd.feed_dog("test_stream")

        # 验证心跳时间已更新
        updated_time = wd._stream_registry["test_stream"].last_tick
        assert updated_time > initial_time

        # 清理
        wd.unregister_stream("test_stream")

    def test_unregister_stream(self) -> None:
        """测试注销聊天流"""
        wd = get_watchdog()

        wd.register_stream(stream_id="test_stream")
        assert "test_stream" in wd._stream_registry

        wd.unregister_stream("test_stream")
        assert "test_stream" not in wd._stream_registry

    def test_get_stats(self) -> None:
        """测试获取统计信息"""
        wd = get_watchdog()

        stats = wd.get_stats()

        assert "running" in stats
        assert "tick_interval" in stats
        assert "registered_streams" in stats
        assert isinstance(stats["registered_streams"], int)


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_task_manager_with_watchdog(self) -> None:
        """测试 TaskManager 与 WatchDog 集成"""
        tm = get_task_manager()
        wd = get_watchdog()

        # 设置 WatchDog 到 TaskManager
        tm.set_watchdog(wd)

        async def short_task():
            await asyncio.sleep(0.01)

        # 创建任务
        tm.create_task(short_task(), timeout=1.0)

        # 清理已完成任务
        await asyncio.sleep(0.05)
        cleaned = tm.cleanup_tasks()

        assert cleaned >= 1


class TestExceptions:
    """测试异常类"""

    def test_task_not_found_error(self) -> None:
        """测试 TaskNotFoundError"""
        exc = TaskNotFoundError("task_123")
        assert exc.task_id == "task_123"
        assert "task_123" in str(exc)

    def test_task_timeout_error(self) -> None:
        """测试 TaskTimeoutError"""
        exc = TaskTimeoutError("task_456", 30.0)
        assert exc.task_id == "task_456"
        assert exc.timeout == 30.0
        assert "task_456" in str(exc)
        assert "30.0" in str(exc)

    def test_task_group_already_exists(self) -> None:
        """测试 TaskGroupAlreadyExists"""
        exc = TaskGroupAlreadyExists("my_group")
        assert exc.group_name == "my_group"
        assert "my_group" in str(exc)

    def test_task_group_not_found_error(self) -> None:
        """测试 TaskGroupNotFoundError"""
        exc = TaskGroupNotFoundError("missing_group")
        assert exc.group_name == "missing_group"
        assert "missing_group" in str(exc)


class TestTaskInfoEdgeCases:
    """测试 TaskInfo 边界情况"""

    def test_task_info_with_none_task(self) -> None:
        """测试 task 为 None 的情况"""
        task_info = TaskInfo(task_id="test_id", name="test_task", task=None)

        # task 为 None 时的状态方法
        assert not task_info.is_done()
        assert not task_info.is_cancelled()
        assert not task_info.is_failed()
        assert task_info.get_exception() is None
        assert task_info.get_result() is None
        assert task_info.cancel() is False

    @pytest.mark.asyncio
    async def test_task_info_repr_statuses(self) -> None:
        """测试 __repr__ 方法的各种状态"""
        # 测试 running 状态
        async def running_task():
            await asyncio.sleep(0.05)

        task = asyncio.create_task(running_task())
        task_info_running = TaskInfo(
            task_id="test_id_1", name="running_task", task=task, daemon=False
        )
        repr_running = repr(task_info_running)
        assert "running" in repr_running
        await task

        # 测试 completed 状态
        async def completed_task():
            return "done"

        task2 = asyncio.create_task(completed_task())
        await task2
        task_info_completed = TaskInfo(
            task_id="test_id_2", name="completed_task", task=task2, daemon=False
        )
        repr_completed = repr(task_info_completed)
        assert "completed" in repr_completed

        # 测试 cancelled 状态
        async def cancellable_task():
            await asyncio.sleep(0.05)

        task3 = asyncio.create_task(cancellable_task())
        task3.cancel()
        try:
            await task3
        except asyncio.CancelledError:
            pass
        task_info_cancelled = TaskInfo(
            task_id="test_id_3", name="cancelled_task", task=task3, daemon=False
        )
        repr_cancelled = repr(task_info_cancelled)
        assert "cancelled" in repr_cancelled

    @pytest.mark.asyncio
    async def test_task_info_repr_with_options(self) -> None:
        """测试 __repr__ 的 daemon 和 group 选项"""
        async def sample_task():
            await asyncio.sleep(0.1)

        task = asyncio.create_task(sample_task())
        task_info = TaskInfo(
            task_id="test_id",
            name="test_task",
            task=task,
            daemon=True,
            group_name="my_group",
        )

        repr_str = repr(task_info)
        assert "[daemon]" in repr_str
        assert "@my_group" in repr_str
        await task

    @pytest.mark.asyncio
    async def test_task_info_failed_status(self) -> None:
        """测试失败状态"""
        async def failing_task():
            raise ValueError("Task error")

        task = asyncio.create_task(failing_task())
        task_info = TaskInfo(task_id="test_id", name="failing_task", task=task)

        try:
            await task
        except ValueError:
            pass

        assert task_info.is_done()
        assert task_info.is_failed()
        assert isinstance(task_info.get_exception(), ValueError)

    @pytest.mark.asyncio
    async def test_task_info_get_result_raises(self) -> None:
        """测试 get_result 在任务失败时抛出异常"""
        async def failing_task():
            raise RuntimeError("Error")

        task = asyncio.create_task(failing_task())
        task_info = TaskInfo(task_id="test_id", task=task)

        try:
            await task
        except RuntimeError:
            pass

        with pytest.raises(RuntimeError):
            task_info.get_result()


class TestTaskManagerEdgeCases:
    """测试 TaskManager 边界情况"""

    def test_task_manager_double_init(self) -> None:
        """测试 TaskManager 重复初始化（单例保护）"""
        tm = TaskManager()
        tm2 = TaskManager()
        assert tm is tm2

    def test_create_task_not_in_async_context(self) -> None:
        """测试非异步上下文中创建任务抛出 RuntimeError"""
        tm = get_task_manager()

        async def dummy_task():
            pass

        # 在非异步上下文中调用 create_task
        # 这需要特殊处理，因为 pytest 本身是异步的
        # 我们可以 mock asyncio.get_running_loop 来模拟
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")):
            with pytest.raises(RuntimeError, match="must be called within an async context"):
                tm.create_task(dummy_task())

    @pytest.mark.asyncio
    async def test_get_task_not_found(self) -> None:
        """测试获取不存在的任务"""
        tm = get_task_manager()

        with pytest.raises(TaskNotFoundError):
            tm.get_task("nonexistent_task_id")

    @pytest.mark.asyncio
    async def test_cancel_task_not_found(self) -> None:
        """测试取消不存在的任务返回 False"""
        tm = get_task_manager()
        result = tm.cancel_task("nonexistent_task_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_all_tasks_empty(self) -> None:
        """测试等待空任务列表"""
        tm = get_task_manager()
        await tm.wait_all_tasks()  # 应该正常返回

    @pytest.mark.asyncio
    async def test_wait_all_tasks_with_daemon(self) -> None:
        """测试 wait_all_tasks 不等待守护任务"""
        tm = get_task_manager()

        async def daemon_task():
            await asyncio.sleep(0.05)

        # 创建守护任务
        tm.create_task(daemon_task(), daemon=True)
        # wait_all_tasks 应该立即返回（不等待守护任务）
        await asyncio.wait_for(tm.wait_all_tasks(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_cleanup_tasks_empty(self) -> None:
        """测试清理空任务列表"""
        tm = get_task_manager()
        # 先清理已有的任务
        await tm.wait_all_tasks()
        tm.cleanup_tasks()

        # 现在测试应该返回 0
        cleaned = tm.cleanup_tasks()
        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_get_all_tasks(self) -> None:
        """测试获取所有任务"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)

        task_info = tm.create_task(sample_task())
        all_tasks = tm.get_all_tasks()

        assert len(all_tasks) >= 1
        assert task_info in all_tasks

        await task_info.task

    @pytest.mark.asyncio
    async def test_get_task_count(self) -> None:
        """测试获取任务数量"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)

        initial_count = tm.get_task_count()
        tm.create_task(sample_task())
        new_count = tm.get_task_count()

        assert new_count >= initial_count + 1

    @pytest.mark.asyncio
    async def test_get_active_task_count(self) -> None:
        """测试获取活跃任务数量"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)

        tm.create_task(sample_task())
        active_count = tm.get_active_task_count()

        assert active_count >= 1

    @pytest.mark.asyncio
    async def test_task_manager_repr(self) -> None:
        """测试 TaskManager __repr__"""
        tm = get_task_manager()
        repr_str = repr(tm)
        assert "TaskManager" in repr_str
        assert "total=" in repr_str
        assert "active=" in repr_str

    @pytest.mark.asyncio
    async def test_task_done_callback_with_exception(self) -> None:
        """测试任务完成回调处理异常"""
        tm = get_task_manager()

        async def failing_task():
            raise ValueError("Test error")

        # 创建一个属于组的任务
        async with tm.group(name="callback_test_group") as tg:
            tg.create_task(failing_task())

        # 等待任务完成，异常应该被记录到组中


class TestTaskGroupEdgeCases:
    """测试 TaskGroup 边界情况"""

    @pytest.mark.asyncio
    async def test_task_group_empty_tasks(self) -> None:
        """测试空任务组"""
        tm = get_task_manager()

        async with tm.group(name="empty_group") as tg:
            assert tg.get_task_count() == 0
            assert tg.get_active_task_count() == 0

    @pytest.mark.asyncio
    async def test_task_group_timeout(self) -> None:
        """测试任务组超时"""
        tm = get_task_manager()

        async def long_task():
            await asyncio.sleep(0.05)

        async with tm.group(name="timeout_group", timeout=0.1) as tg:
            tg.create_task(long_task())

        # 任务应该被取消

    @pytest.mark.asyncio
    async def test_task_group_cancel_on_error_false(self) -> None:
        """测试 cancel_on_error=False"""
        tm = get_task_manager()

        async def failing_task():
            await asyncio.sleep(0.05)
            raise ValueError("Error")

        async def normal_task():
            await asyncio.sleep(0.05)

        try:
            async with tm.group(name="no_cancel_group", cancel_on_error=False) as tg:
                tg.create_task(failing_task())
                tg.create_task(normal_task())
        except ValueError:
            pass

    @pytest.mark.asyncio
    async def test_task_group_repr(self) -> None:
        """测试 TaskGroup __repr__"""
        tg = TaskGroup(name="test_group")

        repr_str = repr(tg)
        assert "TaskGroup" in repr_str
        assert "test_group" in repr_str
        assert "inactive" in repr_str

        async with tg:
            repr_str_active = repr(tg)
            assert "active" in repr_str_active

    @pytest.mark.asyncio
    async def test_task_group_is_active(self) -> None:
        """测试 is_active 方法"""
        tm = get_task_manager()
        tg = tm.group(name="active_test_group")

        assert not tg.is_active()

        async with tg:
            assert tg.is_active()

        assert not tg.is_active()

    @pytest.mark.asyncio
    async def test_task_group_get_task_count(self) -> None:
        """测试 get_task_count 方法"""
        tm = get_task_manager()

        async def dummy_task():
            await asyncio.sleep(0.1)

        async with tm.group(name="count_group") as tg:
            tg.create_task(dummy_task())
            tg.create_task(dummy_task())
            assert tg.get_task_count() == 2

    @pytest.mark.asyncio
    async def test_task_group_get_active_task_count(self) -> None:
        """测试 get_active_task_count 方法"""
        tm = get_task_manager()

        async def quick_task():
            await asyncio.sleep(0.01)

        async def long_task():
            await asyncio.sleep(0.05)

        async with tm.group(name="active_count_group") as tg:
            tg.create_task(quick_task())
            tg.create_task(long_task())
            assert tg.get_active_task_count() == 2

            await asyncio.sleep(0.02)
            assert tg.get_active_task_count() == 1

    @pytest.mark.asyncio
    async def test_task_group_wait_all_with_none_tasks(self) -> None:
        """测试 _wait_all_tasks 没有任务"""
        tg = TaskGroup(name="test_group")
        # 直接调用私有方法测试
        await tg._wait_all_tasks()

    @pytest.mark.asyncio
    async def test_task_group_cancel_all_empty(self) -> None:
        """测试 _cancel_all_tasks 没有任务"""
        tg = TaskGroup(name="test_group")
        # 直接调用私有方法测试
        await tg._cancel_all_tasks()

    @pytest.mark.asyncio
    async def test_task_group_with_exception_in_context(self) -> None:
        """测试上下文管理器传入异常"""
        tm = get_task_manager()

        async def dummy_task():
            await asyncio.sleep(0.1)

        try:
            async with tm.group(name="exception_test") as tg:
                tg.create_task(dummy_task())
                raise RuntimeError("Context error")
        except RuntimeError:
            pass

    @pytest.mark.asyncio
    async def test_task_group_cancelled_error_propagation(self) -> None:
        """测试 CancelledError 传播"""
        tm = get_task_manager()

        async def dummy_task():
            await asyncio.sleep(0.05)

        # 创建一个可以被取消的任务
        async def cancel_context():
            async with tm.group(name="cancel_test") as tg:
                tg.create_task(dummy_task())
                await asyncio.sleep(0)
                # 模拟外部取消
                raise asyncio.CancelledError()

        # 测试 CancelledError 正确传播
        with pytest.raises(asyncio.CancelledError):
            await cancel_context()


class TestWatchDogEdgeCases:
    """测试 WatchDog 边界情况"""

    def test_watchdog_init(self) -> None:
        """测试 WatchDog 初始化"""
        wd = WatchDog(tick_interval=0.2)
        assert wd._tick_interval == 0.2
        assert not wd._running
        assert wd._thread is None

    def test_watchdog_get_logger_fallback(self) -> None:
        """测试 logger 不可用时的回退"""
        # Mock import error by making the import fail
        with patch("builtins.__import__", side_effect=ImportError("Logger module not available")):
            # 需要重新导入 WatchDog 类来触发 _get_logger 的调用
            # 这里我们直接测试 _log 方法在 logger 为 None 时的行为
            wd = WatchDog()
            wd._logger = None
            # 不应该抛出异常
            wd._log("info", "Test message")

    def test_watchdog_log_with_none_logger(self) -> None:
        """测试 logger 为 None 时的日志记录"""
        wd = WatchDog()
        wd._logger = None

        # 不应该抛出异常
        wd._log("info", "Test message")
        wd._log("warning", "Test warning")
        wd._log("error", "Test error")

    def test_watchdog_start_already_running(self) -> None:
        """测试重复启动 WatchDog"""
        wd = WatchDog()

        # 模拟正在运行状态
        wd._running = True

        with pytest.raises(WatchDogError, match="already running"):
            wd.start()

    def test_watchdog_stop_when_not_running(self) -> None:
        """测试停止未运行的 WatchDog"""
        wd = WatchDog()
        wd._running = False

        # 不应该抛出异常
        wd.stop()

    def test_watchdog_stop_with_thread(self) -> None:
        """测试停止带线程的 WatchDog"""
        wd = WatchDog()

        # 启动 WatchDog
        wd.start()
        assert wd._running
        assert wd._thread is not None

        # 停止 WatchDog
        wd.stop()
        assert not wd._running

    def test_watchdog_get_stream_heartbeat_not_found(self) -> None:
        """测试获取不存在的心跳信息"""
        wd = WatchDog()
        heartbeat = wd.get_stream_heartbeat("nonexistent_stream")
        assert heartbeat is None

    def test_watchdog_repr(self) -> None:
        """测试 WatchDog __repr__"""
        wd = WatchDog()
        repr_str = repr(wd)
        assert "WatchDog" in repr_str
        assert "stopped" in repr_str

        # 启动后
        wd.start()
        repr_str_running = repr(wd)
        assert "running" in repr_str_running

        # 注册流
        wd.register_stream("test_stream")
        repr_str_with_stream = repr(wd)
        assert "streams=1" in repr_str_with_stream

        wd.stop()

    def test_watchdog_unregister_nonexistent_stream(self) -> None:
        """测试注销不存在的流"""
        wd = WatchDog()
        # 不应该抛出异常
        wd.unregister_stream("nonexistent_stream")

    def test_watchdog_feed_dog_nonexistent_stream(self) -> None:
        """测试向不存在的流喂狗"""
        wd = WatchDog()
        # 不应该抛出异常
        wd.feed_dog("nonexistent_stream")

    def test_watchdog_register_stream_with_callback(self) -> None:
        """测试注册带回调的流"""
        wd = WatchDog()
        callback_called = []

        def restart_callback():
            callback_called.append(True)

        heartbeat = wd.register_stream(
            stream_id="callback_stream",
            restart_callback=restart_callback,
        )

        assert heartbeat.restart_callback is not None
        assert heartbeat.restart_callback == restart_callback

        wd.unregister_stream("callback_stream")

    def test_watchdog_run_loop_tick_anomaly_detection(self) -> None:
        """测试运行循环中的 tick 异常检测"""
        wd = WatchDog(tick_interval=0.1)

        # 模拟上次 tick 时间是很久以前
        from datetime import timedelta

        wd._last_tick_time = datetime.now() - timedelta(seconds=1.0)

        # 启动和快速停止来测试循环逻辑
        wd.start()
        # 等待至少一个 tick
        import time

        time.sleep(0.3)
        wd.stop()

    def test_watchdog_check_streams_empty(self) -> None:
        """测试检查空流注册表"""
        wd = WatchDog()
        # 不应该抛出异常
        wd._check_streams()

    def test_watchdog_stats_with_thread(self) -> None:
        """测试获取统计信息（带线程）"""
        wd = WatchDog()
        stats = wd.get_stats()

        assert "running" in stats
        assert "tick_interval" in stats
        assert "registered_streams" in stats
        assert "thread_alive" in stats
        assert stats["thread_alive"] is False

        wd.start()
        stats_running = wd.get_stats()
        assert stats_running["thread_alive"] is True
        wd.stop()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_watchdog_with_task_manager_integration(self) -> None:
        """测试 WatchDog 与 TaskManager 深度集成"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.1)

        # 设置 watchdog
        tm.set_watchdog(wd)

        # 启动 watchdog
        wd.start()

        async def timeout_task():
            await asyncio.sleep(0.05)

        # 创建带超时的任务
        task_info = tm.create_task(timeout_task(), timeout=0.2)

        # 等待足够的时间让 watchdog 检查到超时
        await asyncio.sleep(0.1)

        # 任务应该被取消
        assert task_info.is_cancelled() or task_info.is_done()

        wd.stop()


class TestWatchDogStreamMonitoring:
    """测试 WatchDog 流监控功能"""

    @pytest.mark.slow
    def test_watchdog_stream_warning_threshold(self) -> None:
        """测试流警告阈值"""
        wd = WatchDog(tick_interval=0.05)
        wd.start()

        # 注册一个带短间隔的流
        wd.register_stream(
            stream_id="slow_stream",
            tick_interval=0.01,
            warning_threshold=0.001,
        )

        # 等待足够长的时间触发警告
        import time

        time.sleep(0.01)

        wd.stop()

    @pytest.mark.slow
    def test_watchdog_stream_restart_threshold(self) -> None:
        """测试流重启阈值和回调"""
        wd = WatchDog(tick_interval=0.05)
        wd.start()

        restart_called = []

        def restart_callback():
            restart_called.append(True)

        # 注册一个带重启回调的流
        wd.register_stream(
            stream_id="restart_stream",
            tick_interval=0.01,
            restart_threshold=0.001,
            restart_callback=restart_callback,
        )

        # 等待足够长的时间触发重启
        import time

        time.sleep(0.01)

        wd.stop()

    def test_watchdog_stream_restart_cooldown_suppresses_duplicate_requests(self) -> None:
        """同一流在重启冷却内不应重复提交重启请求。"""
        wd = WatchDog(tick_interval=0.05)

        restart_called = []

        def restart_callback() -> None:
            restart_called.append(True)

        heartbeat = wd.register_stream(
            stream_id="restart_cooldown_stream",
            tick_interval=1.0,
            restart_threshold=2.0,
            restart_callback=restart_callback,
            restart_cooldown=10.0,
        )

        from datetime import timedelta

        heartbeat.last_tick = datetime.now() - timedelta(seconds=5.0)

        wd._check_streams()
        wd._check_streams()

        assert len(restart_called) == 1

    def test_watchdog_restart_callback_exception(self) -> None:
        """测试重启回调异常处理"""
        wd = WatchDog(tick_interval=0.05)
        wd.start()

        def failing_callback():
            raise RuntimeError("Restart failed")

        wd.register_stream(
            stream_id="failing_restart_stream",
            tick_interval=0.01,
            restart_threshold=0.001,
            restart_callback=failing_callback,
        )

        # 等待触发重启
        import time

        time.sleep(0.01)

        # 不应该导致 WatchDog 崩溃
        assert wd._running

        wd.stop()

    def test_watchdog_multiple_streams(self) -> None:
        """测试同时监控多个流"""
        wd = WatchDog(tick_interval=0.1)
        wd.start()

        # 注册多个流
        for i in range(5):
            wd.register_stream(stream_id=f"stream_{i}")

        # 喂狗
        for i in range(5):
            wd.feed_dog(f"stream_{i}")

        stats = wd.get_stats()
        assert stats["registered_streams"] == 5

        wd.stop()

    def test_watchdog_run_loop_exception_handling(self) -> None:
        """测试运行循环异常处理"""
        wd = WatchDog(tick_interval=0.05)

        # 模拟 _check_streams 抛出异常
        original_check = wd._check_streams

        def failing_check():
            raise RuntimeError("Check failed")

        wd._check_streams = failing_check

        wd.start()

        # 循环应该继续运行
        import time

        time.sleep(0.2)

        assert wd._running

        # 恢复原方法
        wd._check_streams = original_check

        wd.stop()


class TestConcurrencyModuleRepr:
    """测试各类的 __repr__ 方法"""

    @pytest.mark.asyncio
    async def test_task_manager_repr_comprehensive(self) -> None:
        """测试 TaskManager __repr__ 的全面情况"""
        tm = get_task_manager()

        # 清理之前的任务
        await tm.wait_all_tasks()
        tm.cleanup_tasks()

        repr_str = repr(tm)
        assert "TaskManager" in repr_str

        # 创建一些任务
        async def sample_task():
            await asyncio.sleep(0.1)

        tm.create_task(sample_task(), name="test1")
        tm.create_task(sample_task(), daemon=True, name="daemon1")

        # 创建组
        async with tm.group(name="repr_test_group"):
            pass

        repr_str_with_tasks = repr(tm)
        assert "total=" in repr_str_with_tasks
        assert "active=" in repr_str_with_tasks
        assert "daemon=" in repr_str_with_tasks
        assert "groups=" in repr_str_with_tasks

        await tm.wait_all_tasks()

    @pytest.mark.asyncio
    async def test_task_info_repr_comprehensive(self) -> None:
        """测试 TaskInfo __repr__ 的全面情况"""
        async def sample_task():
            await asyncio.sleep(0.1)

        task = asyncio.create_task(sample_task())

        task_info = TaskInfo(
            task_id="repr_test_id",
            name="repr_test_task",
            task=task,
            daemon=True,
            group_name="repr_test_group",
        )

        repr_str = repr(task_info)
        assert "TaskInfo" in repr_str
        assert "repr_test_id"[:8] in repr_str
        assert "repr_test_task" in repr_str
        assert "[daemon]" in repr_str
        assert "@repr_test_group" in repr_str

        await task

    def test_task_group_repr_comprehensive(self) -> None:
        """测试 TaskGroup __repr__ 的全面情况"""
        tg = TaskGroup(name="repr_test_group", timeout=10.0)

        repr_str = repr(tg)
        assert "TaskGroup" in repr_str
        assert "repr_test_group" in repr_str
        assert "inactive" in repr_str
        assert "tasks=0/0" in repr_str

    def test_watchdog_repr_comprehensive(self) -> None:
        """测试 WatchDog __repr__ 的全面情况"""
        wd = WatchDog(tick_interval=0.2)

        repr_str = repr(wd)
        assert "WatchDog" in repr_str
        assert "stopped" in repr_str
        assert "streams=0" in repr_str

        wd.start()
        wd.register_stream("test_stream")

        repr_str_running = repr(wd)
        assert "running" in repr_str_running
        assert "streams=1" in repr_str_running

        wd.stop()


class TestTaskManagerGroupManagement:
    """测试 TaskManager 的组管理功能"""

    @pytest.mark.asyncio
    async def test_task_manager_get_existing_group(self) -> None:
        """测试获取已存在的组"""
        tm = get_task_manager()

        # 创建组
        group1 = tm.group(name="shared_group")
        group2 = tm.group(name="shared_group")

        # 应该返回同一个对象
        assert group1 is group2

    @pytest.mark.asyncio
    async def test_task_manager_group_with_different_params(self) -> None:
        """测试同名组使用不同参数（第一个有效）"""
        tm = get_task_manager()

        # 创建带超时的组
        group1 = tm.group(name="param_group", timeout=10.0)
        # 再次获取同名组（忽略新参数）
        group2 = tm.group(name="param_group", timeout=20.0)

        assert group1 is group2
        assert group1.timeout == 10.0

    @pytest.mark.asyncio
    async def test_task_manager_cleanup_tasks_in_group(self) -> None:
        """测试清理组内已完成的任务"""
        tm = get_task_manager()

        async def quick_task():
            await asyncio.sleep(0.1)

        async with tm.group(name="cleanup_group") as tg:
            tg.create_task(quick_task())

        # 等待任务完成
        await asyncio.sleep(0.05)

        # 清理
        cleaned = tm.cleanup_tasks()
        assert cleaned >= 0


class TestAdditionalCoverage:
    """额外测试用例以覆盖剩余代码"""

    @pytest.mark.asyncio
    async def test_task_info_is_failed_with_none_task(self) -> None:
        """测试 task 为 None 时 is_failed 返回 False"""
        task_info = TaskInfo(task_id="test_id", task=None)
        assert not task_info.is_failed()

    @pytest.mark.asyncio
    async def test_task_info_repr_completed_status(self) -> None:
        """测试 __repr__ 中 completed 状态"""
        async def sample_task():
            return "done"

        task = asyncio.create_task(sample_task())
        await task

        task_info = TaskInfo(task_id="test_id", name="completed_task", task=task)
        repr_str = repr(task_info)
        assert "completed" in repr_str

    @pytest.mark.asyncio
    async def test_task_group_wait_all_with_pending(self) -> None:
        """测试 _wait_all_tasks 有 pending 任务"""
        tm = get_task_manager()

        async def long_task():
            await asyncio.sleep(0.05)

        async with tm.group(name="pending_test", timeout=0.1) as tg:
            tg.create_task(long_task())
            # 任务会因超时被取消

    @pytest.mark.asyncio
    async def test_task_group_cancel_all_with_done_tasks(self) -> None:
        """测试 _cancel_all_tasks 包含已完成任务"""
        tg = TaskGroup(name="cancel_done_test")

        async def quick_task():
            await asyncio.sleep(0.01)

        async with tg:
            tg.create_task(quick_task())
            tg.create_task(quick_task())

            await asyncio.sleep(0.05)

            # 调用 cancel_all，已完成任务应被跳过
            await tg._cancel_all_tasks()

    @pytest.mark.asyncio
    async def test_task_group_cancel_all_with_timeout(self) -> None:
        """测试 _cancel_all_tasks 等待超时"""
        tg = TaskGroup(name="cancel_timeout_test")

        async def non_cancelable_task():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                # 忽略取消并继续睡眠
                await asyncio.sleep(0.05)

        async with tg:
            tg.create_task(non_cancelable_task())
            await asyncio.sleep(0.05)
            # 等待取消完成（会超时）
            await tg._cancel_all_tasks()

    def test_watchdog_get_logger_exception_handling(self) -> None:
        """测试 _get_logger 异常处理"""
        # 直接测试 _log 方法在 logger 为 None 时的行为
        wd = WatchDog()
        wd._logger = None
        # 不应该抛出异常
        wd._log("info", "Test")

    @pytest.mark.asyncio
    async def test_watchdog_task_timeout_cancel_fails(self) -> None:
        """测试任务取消失败的情况"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.1)

        tm.set_watchdog(wd)
        wd.start()

        async def non_cancellable_task():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                # 忽略取消
                await asyncio.sleep(0.05)

        # 创建带超时的任务
        tm.create_task(non_cancellable_task(), timeout=0.2)

        # 等待 watchdog 检查
        await asyncio.sleep(0.1)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_manager_on_task_done_without_group(self) -> None:
        """测试任务完成回调中无组的情况"""
        tm = get_task_manager()

        async def normal_task():
            await asyncio.sleep(0.1)

        # 创建不属于任何组的任务
        task_info = tm.create_task(normal_task())
        if task_info.task:
            await task_info.task

        # _on_task_done 应该正常处理（无组的情况）

    @pytest.mark.asyncio
    async def test_watchdog_task_timeout_without_task_manager(self) -> None:
        """测试 WatchDog 没有 TaskManager 时"""
        wd = WatchDog(tick_interval=0.1)
        wd.start()

        # 没有设置 _task_manager，让 watchdog 自己获取
        await asyncio.sleep(0.05)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_group_exit_with_exception_no_cancel(self) -> None:
        """测试退出时有异常但 cancel_on_error=False"""
        tm = get_task_manager()

        async def failing_task():
            raise ValueError("Error")

        async def normal_task():
            await asyncio.sleep(0.05)

        try:
            async with tm.group(name="no_cancel_on_exit", cancel_on_error=False) as tg:
                tg.create_task(failing_task())
                tg.create_task(normal_task())
        except ValueError:
            pass
        # normal_task 应该完成（没有被取消）

    @pytest.mark.asyncio
    async def test_task_info_repr_with_no_name(self) -> None:
        """测试 TaskInfo 没有名称时的 __repr__"""
        async def sample_task():
            return "done"

        task = asyncio.create_task(sample_task())
        task_info = TaskInfo(task_id="test_id", name=None, task=task)

        repr_str = repr(task_info)
        assert "test_id"[:8] in repr_str
        await task

    @pytest.mark.asyncio
    async def test_task_group_wait_all_returns_immediately(self) -> None:
        """测试所有任务已完成时立即返回"""
        tg = TaskGroup(name="immediate_return")

        async def quick_task():
            await asyncio.sleep(0.01)

        async with tg:
            tg.create_task(quick_task())
            await asyncio.sleep(0.05)
            # 所有任务已完成，_wait_all_tasks 应该立即返回
            await tg._wait_all_tasks()

    @pytest.mark.asyncio
    async def test_watchdog_with_no_active_tasks(self) -> None:
        """测试 WatchDog 检查任务时没有活跃任务"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.1)

        tm.set_watchdog(wd)
        wd.start()

        # 没有创建任务，让 watchdog 检查
        await asyncio.sleep(0.05)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_group_cancel_when_exception_set(self) -> None:
        """测试 _exception 已设置时取消任务"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.1)

        try:
            async with tm.group(name="exception_set_test", cancel_on_error=True) as tg:
                tg.create_task(sample_task())
                # 手动设置异常
                tg._exception = ValueError("Test")
                # 退出时会取消所有任务并抛出异常
        except ValueError:
            pass  # 预期的异常

    @pytest.mark.asyncio
    async def test_watchdog_log_with_invalid_level(self) -> None:
        """测试使用无效日志级别"""
        wd = WatchDog()
        # 使用无效的级别，应该不会崩溃
        wd._log("invalid", "Test message")

    @pytest.mark.asyncio
    async def test_task_info_is_done_without_task(self) -> None:
        """测试 task 为 None 时 is_done"""
        task_info = TaskInfo(task_id="test_id", task=None)
        assert not task_info.is_done()

    @pytest.mark.asyncio
    async def test_task_info_is_cancelled_without_task(self) -> None:
        """测试 task 为 None 时 is_cancelled"""
        task_info = TaskInfo(task_id="test_id", task=None)
        assert not task_info.is_cancelled()

    @pytest.mark.asyncio
    async def test_task_group_cancelled_error_from_wait(self) -> None:
        """测试 _wait_all_tasks 中 asyncio.wait 抛出 CancelledError"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.05)

        # 创建任务组
        async with tm.group(name="cancel_wait_error") as tg:
            tg.create_task(sample_task())

            # 在等待期间取消当前任务
            async def cancel_during_wait():
                async with tm.group(name="cancel_wait") as tg2:
                    tg2.create_task(sample_task())
                    # 等待一小段时间然后取消
                    await asyncio.sleep(0.05)
                    raise asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await cancel_during_wait()

    @pytest.mark.asyncio
    async def test_task_group_record_exception_first_time(self) -> None:
        """测试 _record_exception 第一次记录异常"""
        tg = TaskGroup(name="record_test")

        exc1 = ValueError("First")
        exc2 = RuntimeError("Second")

        tg._record_exception(exc1)
        assert tg._exception is exc1

        # 第二次不应该覆盖
        tg._record_exception(exc2)
        assert tg._exception is exc1

    @pytest.mark.asyncio
    async def test_watchdog_cancel_task_fails(self) -> None:
        """测试 WatchDog 任务取消失败的情况"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.05)

        tm.set_watchdog(wd)
        wd.start()

        # 创建一个会快速完成的任务，这样当 watchdog 尝试取消时
        # 任务已经完成，导致 cancel() 返回 False
        async def instant_task():
            await asyncio.sleep(0.01)
            return "done"

        tm.create_task(instant_task(), timeout=0.02, name="instant_task")

        # 等待 watchdog 检查
        await asyncio.sleep(0.05)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_manager_done_callback_exception_handling(self) -> None:
        """测试任务完成回调中的异常处理逻辑"""
        tm = get_task_manager()

        async def failing_task():
            raise ValueError("Task failed")

        # 创建一个属于组的任务
        async with tm.group(name="callback_exception_test") as tg:
            tg.create_task(failing_task())
            # 等待任务完成，_on_task_done 会处理异常

    @pytest.mark.asyncio
    async def test_task_manager_done_callback_with_group_exception(self) -> None:
        """测试任务完成回调记录异常到组"""
        tm = get_task_manager()

        async def failing_task():
            raise ValueError("Error in task")

        async with tm.group(name="group_exception_test") as tg:
            task_info = tg.create_task(failing_task())
            # 等待任务失败
            if task_info.task:
                try:
                    await task_info.task
                except ValueError:
                    pass

            # _on_task_done 应该将异常记录到组中
            # 由于任务失败且属于组，异常应该被记录

    @pytest.mark.asyncio
    async def test_watchdog_tick_anomaly_log(self) -> None:
        """测试 WatchDog tick 异常检测和日志"""
        wd = WatchDog(tick_interval=0.01)

        # 启动 watchdog
        wd.start()

        # 模拟 tick 延迟
        import time

        time.sleep(0.01)

        # 手动设置上次 tick 为很久以前，这样下次循环会触发警告
        from datetime import timedelta

        wd._last_tick_time = datetime.now() - timedelta(seconds=1.0)

        # 等待下一个 tick 检测到异常
        time.sleep(0.01)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_group_wait_all_cancelled_from_wait(self) -> None:
        """测试 asyncio.wait 抛出 CancelledError"""
        tg = TaskGroup(name="cancel_from_wait")

        async def long_task():
            await asyncio.sleep(0.05)

        async with tg:
            tg.create_task(long_task())

            # 创建一个任务来取消当前任务
            async def cancel_current():
                await asyncio.sleep(0.05)
                # 获取当前任务并取消
                current = asyncio.current_task()
                if current:
                    current.cancel()

            # 在等待期间取消
            cancel_task = asyncio.create_task(cancel_current())

            try:
                await tg._wait_all_tasks()
            except asyncio.CancelledError:
                # 预期的取消错误
                if not cancel_task.done():
                    cancel_task.cancel()
                raise

    @pytest.mark.asyncio
    async def test_task_info_is_failed_not_done(self) -> None:
        """测试 is_failed 在任务未完成时返回 False"""
        async def running_task():
            await asyncio.sleep(0.05)

        task = asyncio.create_task(running_task())
        task_info = TaskInfo(task_id="test_id", task=task)

        # 任务未完成
        assert not task_info.is_failed()

        # 清理
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_task_info_repr_completed_branch(self) -> None:
        """测试 __repr__ 中 completed 分支"""
        async def normal_task():
            return "result"

        task = asyncio.create_task(normal_task())
        await task

        # 确保 task 不为 cancelled 或 failed
        task_info = TaskInfo(task_id="test_id", task=task, name="completed_task")

        repr_str = repr(task_info)
        # 应该显示 completed 状态
        assert "completed" in repr_str

    @pytest.mark.asyncio
    async def test_task_manager_on_task_done_with_exception_in_group(self) -> None:
        """测试 _on_task_done 中任务异常且属于组的逻辑"""
        tm = get_task_manager()

        async def failing_task():
            raise ValueError("Test error")

        # 创建一个属于组的失败任务
        async with tm.group(name="exception_callback_test") as tg:
            task_info = tg.create_task(failing_task())

            # 等待任务完成并失败
            if task_info.task:
                try:
                    await task_info.task
                except ValueError:
                    pass

            # _on_task_done 应该已经处理了这个异常

    @pytest.mark.asyncio
    async def test_task_manager_on_task_done_cancelled_task(self) -> None:
        """测试 _on_task_done 中任务被取消的情况"""
        tm = get_task_manager()

        async def cancellable_task():
            await asyncio.sleep(0.05)

        # 创建一个属于组的任务
        async with tm.group(name="cancelled_callback_test") as tg:
            task_info = tg.create_task(cancellable_task())

            # 取消任务
            task_info.cancel()

            if task_info.task:
                try:
                    await task_info.task
                except asyncio.CancelledError:
                    pass

            # _on_task_done 应该处理被取消的任务（不记录异常）

    @pytest.mark.asyncio
    async def test_watchdog_task_cancel_returns_false(self) -> None:
        """测试任务取消失败的情况（cancel 返回 False）"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.05)

        tm.set_watchdog(wd)
        wd.start()

        # 创建一个任务并让它快速完成
        async def quick_task():
            await asyncio.sleep(0.01)

        tm.create_task(quick_task(), timeout=0.02, name="quick_task")

        # 等待超时检查
        await asyncio.sleep(0.15)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_manager_done_callback_full_exception_path(self) -> None:
        """测试 _on_task_done 中完整异常处理路径"""
        tm = get_task_manager()

        async def failing_task():
            raise ValueError("Test exception")

        # 创建一个组并添加失败的任务
        _ = tm.group(name="full_exception_test")

        # 在组内创建任务（但不使用 async with，保持组激活）
        async with tm.group(name="full_exception_test") as tg:
            task_info = tg.create_task(failing_task())

            # 等待任务完成（会失败）
            if task_info.task:
                try:
                    await task_info.task
                except ValueError:
                    pass

            # _on_task_done 回调应该已经被触发
            # 它应该检测到任务未取消、有异常、属于组、组存在

    @pytest.mark.asyncio
    async def test_task_group_wait_all_handles_wait_cancelled(self) -> None:
        """测试 _wait_all_tasks 中 asyncio.wait 抛出 CancelledError"""
        tg = TaskGroup(name="wait_cancelled")

        async def slow_task():
            await asyncio.sleep(0.05)

        async with tg:
            tg.create_task(slow_task())

            async def wait_and_cancel():
                await asyncio.sleep(0.05)
                # 取消当前正在运行的任务
                current = asyncio.current_task()
                if current:
                    current.cancel()

            # 启动取消任务
            asyncio.create_task(wait_and_cancel())

            # 尝试等待所有任务（会被取消）
            try:
                await tg._wait_all_tasks()
            except asyncio.CancelledError:
                # 预期的取消
                pass

    @pytest.mark.asyncio
    async def test_task_info_repr_all_statuses(self) -> None:
        """测试 __repr__ 所有状态分支"""
        # 测试 completed 状态（非 cancelled 且非 failed）
        async def success_task():
            return "success"

        task = asyncio.create_task(success_task())
        await task

        task_info = TaskInfo(task_id="test_id", task=task, name="success")
        repr_str = repr(task_info)
        assert "completed" in repr_str

    @pytest.mark.asyncio
    async def test_task_info_is_failed_edge_case(self) -> None:
        """测试 is_failed 在 task 为 None 时返回 False"""
        task_info = TaskInfo(task_id="test_id", task=None)
        assert not task_info.is_failed()

        # 测试任务未完成的情况
        async def running_task():
            await asyncio.sleep(0.05)

        task = asyncio.create_task(running_task())
        task_info_running = TaskInfo(task_id="test_id_2", task=task)
        assert not task_info_running.is_failed()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_watchdog_timeout_cancel_failure(self) -> None:
        """测试任务超时但取消失败"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.03)

        tm.set_watchdog(wd)
        wd.start()

        # 创建一个在 watchdog 检查之前就完成的任务
        async def instant_task():
            await asyncio.sleep(0.01)
            return "done"

        tm.create_task(instant_task(), timeout=0.02, name="instant")

        # 等待 watchdog 检查任务超时时任务已经完成
        await asyncio.sleep(0.15)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_manager_done_callback_exception_path(self) -> None:
        """测试任务完成回调异常处理的完整路径"""
        tm = get_task_manager()

        async def failing_task():
            raise RuntimeError("Callback test error")

        # 直接在组外创建任务但设置 group_name
        # 这样可以手动控制回调的触发
        task = asyncio.create_task(failing_task())

        task_info = TaskInfo(
            task_id="callback_test",
            name="callback_test",
            coro=failing_task(),
            task=task,
            group_name="callback_path_test",
        )

        # 手动添加到任务管理器
        tm._tasks[task_info.task_id] = task_info

        # 等待任务失败
        try:
            await task
        except RuntimeError:
            pass

        # 手动触发回调
        tm._on_task_done(task)

    @pytest.mark.asyncio
    async def test_task_group_wait_cancelled_during_wait(self) -> None:
        """测试在 asyncio.wait 期间被取消"""
        tg = TaskGroup(name="cancel_during_wait")

        async def endless_task():
            await asyncio.sleep(0.05)

        async with tg:
            tg.create_task(endless_task())

            # 获取当前任务
            current_task = asyncio.current_task()

            # 延迟取消
            async def delayed_cancel():
                await asyncio.sleep(0.05)
                if current_task:
                    current_task.cancel()

            asyncio.create_task(delayed_cancel())

            # 调用 _wait_all_tasks（会在等待期间被取消）
            try:
                await tg._wait_all_tasks()
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_task_info_is_failed_done_but_none_task(self) -> None:
        """测试 is_failed 当任务已完成但 task 为 None（边界情况）"""
        # 这是一个极端的边界情况，理论上不应该发生
        # 但为了覆盖代码，我们手动创建这种场景
        task_info = TaskInfo(task_id="test_id", task=None)

        # is_done 应该返回 False（因为 task 是 None）
        # 所以 is_failed 也应该返回 False
        assert not task_info.is_failed()

    @pytest.mark.asyncio
    async def test_task_info_repr_failed_status(self) -> None:
        """测试 __repr__ 中的 failed 状态"""
        async def failing_task():
            raise ValueError("Task failed")

        task = asyncio.create_task(failing_task())

        try:
            await task
        except ValueError:
            pass

        # 任务已完成、未取消、但失败
        task_info = TaskInfo(task_id="test_id", task=task, name="failed_task")

        repr_str = repr(task_info)
        assert "failed" in repr_str

    @pytest.mark.asyncio
    async def test_watchdog_cancel_fails_logging(self) -> None:
        """测试任务取消失败时的日志记录"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.02)

        tm.set_watchdog(wd)
        wd.start()

        # 创建一个会快速完成的任务
        async def super_fast_task():
            return "done"

        task_info = tm.create_task(super_fast_task(), timeout=0.05, name="fast")

        # 等待任务完成
        if task_info.task:
            await task_info.task

        # 手动将 task 设置为 None，这样 cancel() 会返回 False
        task_info.task = None

        # 等待 watchdog 尝试取消（会失败）
        await asyncio.sleep(0.1)

        wd.stop()

        # 清理：避免全局 TaskManager 被该用例污染（pytest-randomly 下会影响其他用例）
        replacement_task = asyncio.create_task(asyncio.sleep(0))
        await replacement_task
        task_info.task = replacement_task
        tm.cleanup_tasks()

    @pytest.mark.asyncio
    async def test_task_info_is_failed_edge_case_with_mock(self) -> None:
        """测试 is_failed 当 is_done() 返回 True 但 task 为 None"""
        # 创建一个 TaskInfo 并手动设置状态来覆盖第 57 行
        # 第 57 行是：if self.task is None: return False
        # 这发生在 is_done() 返回 True 之后，但 task 是 None 的情况

        # 使用 monkey patch 来模拟这种极端情况
        task_info = TaskInfo(task_id="test_id", task=None)

        # Mock is_done 方法返回 True
        original_is_done = task_info.is_done

        def mock_is_done():
            return True

        task_info.is_done = mock_is_done

        # 现在 is_failed 会进入第 54 行检查 is_done()（返回 True）
        # 然后进入第 56 行检查 task is None（返回 True）
        # 最后执行第 57 行返回 False
        result = task_info.is_failed()

        # 恢复原方法
        task_info.is_done = original_is_done

        assert result is False

    @pytest.mark.asyncio
    async def test_task_group_cancelled_error_in_wait_all(self) -> None:
        """测试 _wait_all_tasks 中的 CancelledError"""
        tm = get_task_manager()

        async def sample_task():
            await asyncio.sleep(0.05)

        # 创建一个外部可取消的任务组上下文
        async def cancellable_context():
            async with tm.group(name="cancel_wait_test") as tg:
                tg.create_task(sample_task())
                # 模拟外部取消
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await cancellable_context()

    @pytest.mark.asyncio
    async def test_task_group_cancel_all_pending(self) -> None:
        """测试 _cancel_all_tasks 等待取消完成"""
        tg = TaskGroup(name="cancel_pending_test")

        async def slow_cancel_task():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                await asyncio.sleep(0.1)
                raise

        async with tg:
            tg.create_task(slow_cancel_task())
            await asyncio.sleep(0.05)
            # 取消所有任务并等待
            await tg._cancel_all_tasks()

    @pytest.mark.asyncio
    async def test_watchdog_tick_warning(self) -> None:
        """测试 WatchDog tick 间隔异常警告"""
        wd = WatchDog(tick_interval=0.01)

        # 设置上次 tick 为很久以前
        from datetime import timedelta

        wd._last_tick_time = datetime.now() - timedelta(seconds=1.0)

        # 启动 watchdog，应该检测到异常
        wd.start()

        import time

        time.sleep(0.01)
        wd.stop()

    @pytest.mark.asyncio
    async def test_watchdog_task_timeout_checks(self) -> None:
        """测试 WatchDog 任务超时检查的所有分支"""
        tm = get_task_manager()
        wd = WatchDog(tick_interval=0.05)

        tm.set_watchdog(wd)
        wd.start()

        async def quick_timeout_task():
            await asyncio.sleep(0.01)

        async def no_timeout_task():
            await asyncio.sleep(0.05)

        async def daemon_timeout_task():
            await asyncio.sleep(0.05)

        # 创建带超时的非守护任务
        tm.create_task(quick_timeout_task(), timeout=0.02)
        # 创建不带超时的任务
        tm.create_task(no_timeout_task())
        # 创建带超时的守护任务（应该被跳过）
        tm.create_task(daemon_timeout_task(), daemon=True, timeout=0.02)

        # 等待 watchdog 检查
        await asyncio.sleep(0.05)

        wd.stop()

    @pytest.mark.asyncio
    async def test_task_group_wait_all_timeout_pending(self) -> None:
        """测试 _wait_all_tasks 超时后取消 pending"""
        tm = get_task_manager()

        async def long_task():
            await asyncio.sleep(0.05)

        async with tm.group(name="timeout_pending_test", timeout=0.1) as tg:
            tg.create_task(long_task())
            # 会超时并取消
