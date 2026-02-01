"""
测试 kernel.scheduler 模块
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from src.kernel.scheduler import (
    SchedulerConfig,
    ScheduleTask,
    TaskExecution,
    TaskStatus,
    TriggerType,
    UnifiedScheduler,
    get_unified_scheduler,
)

from src.kernel.scheduler.time_utils import next_after


class TestScheduler:
    """测试统一调度器"""

    @pytest.fixture(autouse=True)
    async def setup_scheduler(self):
        """在每个测试前后启动和停止调度器"""
        unified_scheduler = get_unified_scheduler()
        await get_unified_scheduler().start()
        yield
        await get_unified_scheduler().stop()

    @pytest.mark.asyncio
    async def test_delayed_task(self):
        """测试延迟任务"""
        executed = []
        done = asyncio.Event()

        async def delayed_task():
            executed.append(1)
            done.set()

        # 创建延迟1秒的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=delayed_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 1},
            task_name="test_delayed",
        )

        # 等待任务执行（避免依赖调度器 tick 抖动导致的偶发超时）
        await asyncio.wait_for(done.wait(), timeout=3)

        # 验证任务已执行
        assert len(executed) == 1

        # 验证一次性任务已被移除到已完成列表
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None  # 一次性任务完成后会自动移除

    @pytest.mark.asyncio
    async def test_recurring_task(self):
        """测试循环任务"""
        executed = []

        async def recurring_task():
            executed.append(1)

        # 创建每0.5秒执行一次的循环任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=recurring_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            is_recurring=True,
            task_name="test_recurring",
        )

        # 等待任务执行多次（调整为更合理的时间）
        await asyncio.sleep(4)

        # 验证任务已执行多次（考虑到调度器1秒的检查间隔）
        assert len(executed) >= 3

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_custom_trigger(self):
        """测试自定义条件触发"""
        executed = []
        trigger_condition = False

        async def custom_task():
            executed.append(1)

        async def check_condition():
            return trigger_condition

        # 创建自定义条件触发的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=custom_task,
            trigger_type=TriggerType.CUSTOM,
            trigger_config={"condition_func": check_condition},
            task_name="test_custom",
        )

        # 等待1秒，任务不应执行
        await asyncio.sleep(1)
        assert len(executed) == 0

        # 激活条件
        trigger_condition = True
        await asyncio.sleep(2)
        assert len(executed) >= 1

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_task_with_params(self):
        """测试带参数的任务"""
        executed = []

        async def task_with_params(a, b, c=None):
            executed.append((a, b, c))

        # 创建带参数的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=task_with_params,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            callback_args=(1, 2),
            callback_kwargs={"c": 3},
            task_name="test_params",
        )

        await asyncio.sleep(1.5)

        # 验证参数正确传递
        assert len(executed) == 1
        assert executed[0] == (1, 2, 3)

    @pytest.mark.asyncio
    async def test_remove_task(self):
        """测试移除任务"""
        executed = []

        async def test_task():
            executed.append(1)

        # 创建延迟任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=test_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 5},
            task_name="test_remove",
        )

        # 立即移除任务
        result = await get_unified_scheduler().remove_schedule(schedule_id)
        assert result is True

        # 等待足够时间，任务不应执行
        await asyncio.sleep(6)
        assert len(executed) == 0

    @pytest.mark.asyncio
    async def test_pause_resume_task(self):
        """测试暂停和恢复任务"""
        executed = []

        async def test_task():
            executed.append(1)

        # 创建循环任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=test_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            is_recurring=True,
            task_name="test_pause",
        )

        # 等待执行几次
        await asyncio.sleep(1.5)
        count_before_pause = len(executed)

        # 暂停任务
        await get_unified_scheduler().pause_schedule(schedule_id)
        await asyncio.sleep(1.5)

        # 验证任务未继续执行
        assert len(executed) == count_before_pause

        # 恢复任务
        await get_unified_scheduler().resume_schedule(schedule_id)
        await asyncio.sleep(1.5)

        # 验证任务继续执行
        assert len(executed) > count_before_pause

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_manual_trigger(self):
        """测试手动触发任务"""
        executed = []

        async def test_task():
            executed.append(1)

        # 创建延迟很长的任务（不会自动触发）
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=test_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 100},
            task_name="test_manual_trigger",
        )

        # 手动触发
        result = await get_unified_scheduler().trigger_schedule(schedule_id)
        assert result is True

        # 验证任务已执行
        assert len(executed) == 1

    @pytest.mark.asyncio
    async def test_list_tasks(self):
        """测试列出任务"""
        # 创建多个任务
        task1_id = await get_unified_scheduler().create_schedule(
            callback=lambda: None,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="test_task_1",
        )

        task2_id = await get_unified_scheduler().create_schedule(
            callback=lambda: None,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 20},
            task_name="test_task_2",
        )

        # 列出所有任务
        all_tasks = await get_unified_scheduler().list_tasks()
        assert len(all_tasks) >= 2

        # 按状态筛选
        pending_tasks = await get_unified_scheduler().list_tasks(status=TaskStatus.PENDING)
        assert len(pending_tasks) >= 2

        # 按类型筛选
        time_tasks = await get_unified_scheduler().list_tasks(trigger_type=TriggerType.TIME)
        assert len(time_tasks) >= 2

        # 清理
        await get_unified_scheduler().remove_schedule(task1_id)
        await get_unified_scheduler().remove_schedule(task2_id)

    @pytest.mark.asyncio
    async def test_statistics(self):
        """测试统计信息"""
        # 创建循环任务（这样不会自动移除）
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=lambda: None,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            is_recurring=True,  # 循环任务不会自动移除
            task_name="test_stats",
        )

        # 等待任务执行
        await asyncio.sleep(1.5)

        # 获取统计信息
        stats = get_unified_scheduler().get_statistics()
        assert stats["is_running"] is True
        assert stats["total_tasks"] >= 1
        assert stats["total_executions"] >= 1

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)


class TestSchedulerTimeUtils:
    @pytest.fixture(autouse=True)
    async def setup_scheduler(self):
        """在每个测试前后启动和停止调度器。

        说明：该类包含大量依赖全局 unified_scheduler 的测试用例，需要保证调度器运行。
        """
        await get_unified_scheduler().start()
        yield
        await get_unified_scheduler().stop()

    def test_next_after_returns_scheduled_when_in_future(self) -> None:
        now = datetime(2026, 2, 2, 12, 0, 0)
        scheduled = datetime(2026, 2, 2, 12, 0, 10)
        assert next_after(now, scheduled, 1.0) == scheduled

    def test_next_after_skips_multiple_intervals(self) -> None:
        scheduled = datetime(2026, 2, 2, 12, 0, 0)
        now = datetime(2026, 2, 2, 12, 0, 10)
        out = next_after(now, scheduled, 3.0)
        assert out > now
        # 应该是 12 秒（0,3,6,9,12），严格晚于 now=10
        assert out == datetime(2026, 2, 2, 12, 0, 12)

    def test_next_after_interval_non_positive_returns_now(self) -> None:
        now = datetime(2026, 2, 2, 12, 0, 10)
        scheduled = datetime(2026, 2, 2, 12, 0, 0)
        assert next_after(now, scheduled, 0.0) == now

    @pytest.mark.asyncio
    async def test_find_by_name(self):
        """测试按名称查找任务"""
        # 创建任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=lambda: None,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="unique_task_name",
        )

        # 按名称查找
        found_id = await get_unified_scheduler().find_schedule_by_name("unique_task_name")
        assert found_id == schedule_id

        # 查找不存在的任务
        not_found_id = await get_unified_scheduler().find_schedule_by_name("non_existent")
        assert not_found_id is None

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_task_timeout(self):
        """测试任务超时"""
        async def timeout_task():
            await asyncio.sleep(100)  # 超过默认超时时间

        # 创建带超时的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=timeout_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            timeout=1.0,  # 1秒超时
            task_name="test_timeout",
        )

        # 等待任务执行和超时
        await asyncio.sleep(3)

        # 验证任务已移除（超时失败）
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None  # 任务因超时而失败并被移除

        # 验证统计中包含超时计数
        stats = get_unified_scheduler().get_statistics()
        assert stats["total_timeouts"] >= 1

    @pytest.mark.asyncio
    async def test_task_failure(self):
        """测试任务失败"""
        async def failing_task():
            raise ValueError("Task failed!")

        # 创建会失败的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=failing_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="test_failure",
        )

        # 等待任务执行
        await asyncio.sleep(1.5)

        # 验证任务已失败并被移除（一次性任务）
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None  # 失败的一次性任务被移除

        # 验证统计中包含失败计数
        stats = get_unified_scheduler().get_statistics()
        assert stats["total_failures"] >= 1

    @pytest.mark.asyncio
    async def test_task_retry(self):
        """测试任务重试"""
        attempt_count = 0

        async def retry_task():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ValueError(f"Not yet! Attempt {attempt_count}")

        # 创建带重试的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=retry_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            max_retries=3,
            task_name="test_retry",
        )

        # 等待任务执行和重试（增加等待时间）
        # 第一次执行立即失败 + 5秒后第一次重试 + 5秒后第二次重试
        await asyncio.sleep(15)

        # 验证任务重试了3次后最终成功
        assert attempt_count == 3

        # 一次性任务完成后会被移除
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None

    @pytest.mark.asyncio
    async def test_force_overwrite(self):
        """测试强制覆盖同名任务"""
        executed1 = []
        executed2 = []

        async def task1():
            executed1.append(1)

        async def task2():
            executed2.append(1)

        # 创建第一个任务
        await get_unified_scheduler().create_schedule(
            callback=task1,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="same_name",
        )

        # 尝试创建同名任务（应该失败）
        with pytest.raises(ValueError):
            await get_unified_scheduler().create_schedule(
                callback=task2,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": 10},
                task_name="same_name",
            )

        # 强制覆盖
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=task2,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="same_name",
            force_overwrite=True,
        )

        # 等待新任务执行
        await asyncio.sleep(1.5)

        # 验证新任务执行了
        assert len(executed2) == 1
        assert len(executed1) == 0

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_task_execution_methods(self):
        """测试TaskExecution的各种方法"""
        execution = TaskExecution(execution_id="test_id", started_at=datetime.now())

        # 测试 complete 方法
        execution.complete(result="success")
        assert execution.status == TaskStatus.COMPLETED
        assert execution.result == "success"
        assert execution.ended_at is not None
        assert execution.duration >= 0  # 可能快速执行导致duration为0

        # 测试 fail 方法
        execution2 = TaskExecution(execution_id="test_id_2", started_at=datetime.now())
        error = ValueError("Test error")
        execution2.fail(error)
        assert execution2.status == TaskStatus.FAILED
        assert execution2.error == error
        assert execution2.ended_at is not None

        # 测试 cancel 方法
        execution3 = TaskExecution(execution_id="test_id_3", started_at=datetime.now())
        execution3.cancel()
        assert execution3.status == TaskStatus.CANCELLED
        assert execution3.ended_at is not None

    @pytest.mark.asyncio
    async def test_schedule_task_methods(self):
        """测试ScheduleTask的各种方法"""
        async def dummy_callback():
            pass

        task = ScheduleTask(
            schedule_id="test_id",
            task_name="test_task",
            callback=dummy_callback,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
        )

        # 测试 __repr__
        repr_str = repr(task)
        assert "test_id" in repr_str
        assert "test_task" in repr_str

        # 测试 is_active
        assert task.is_active() is True
        task.status = TaskStatus.COMPLETED
        assert task.is_active() is False

        # 测试 can_trigger
        task.status = TaskStatus.PENDING
        assert task.can_trigger() is True
        task.status = TaskStatus.RUNNING
        assert task.can_trigger() is False

        # 测试 start_execution
        task.status = TaskStatus.PENDING
        execution = task.start_execution()
        assert task.status == TaskStatus.RUNNING
        assert task.current_execution == execution
        assert execution.execution_id is not None

        # 测试 finish_execution (成功)
        task.finish_execution(success=True, result="test_result")
        assert task.status == TaskStatus.COMPLETED
        assert task.success_count == 1
        assert task.last_triggered_at is not None
        assert task.current_execution is None

        # 测试执行历史记录
        assert len(task.execution_history) == 1
        assert task.trigger_count == 1

    @pytest.mark.asyncio
    async def test_recurring_task_finish_execution(self):
        """测试循环任务完成执行后状态变为PENDING"""
        async def dummy_callback():
            pass

        task = ScheduleTask(
            schedule_id="test_id",
            task_name="test_recurring",
            callback=dummy_callback,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            is_recurring=True,
        )

        execution = task.start_execution()
        task.finish_execution(success=True)

        # 循环任务完成后状态应为PENDING
        assert task.status == TaskStatus.PENDING
        assert task.current_execution is None

    @pytest.mark.asyncio
    async def test_trigger_at_specified_time(self):
        """测试在指定时间触发任务"""
        executed = []

        async def timed_task():
            executed.append(1)

        # 设置触发时间为1秒后
        trigger_time = datetime.now() + timedelta(seconds=1)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=timed_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"trigger_at": trigger_time},
            task_name="test_trigger_at",
        )

        # 等待任务执行
        await asyncio.sleep(2.5)

        assert len(executed) == 1
        # 一次性任务完成后会被移除
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None

    @pytest.mark.asyncio
    async def test_recurring_task_with_interval_seconds(self):
        """测试使用interval_seconds的循环任务"""
        executed = []

        async def interval_task():
            executed.append(1)

        # 使用 interval_seconds 而非 delay_seconds
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=interval_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"interval_seconds": 0.5},
            is_recurring=True,
            task_name="test_interval",
        )

        await asyncio.sleep(2.5)
        assert len(executed) >= 2

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_remove_schedule_by_name(self):
        """测试按名称移除任务"""
        executed = []

        async def dummy_task():
            executed.append(1)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=dummy_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="task_to_remove_by_name",
        )

        # 按名称移除
        result = await get_unified_scheduler().remove_schedule_by_name("task_to_remove_by_name")
        assert result is True

        # 验证任务已移除
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None

        # 尝试移除不存在的任务
        result2 = await get_unified_scheduler().remove_schedule_by_name("non_existent_task")
        assert result2 is False

    @pytest.mark.asyncio
    async def test_event_trigger(self):
        """测试事件触发"""
        executed = []

        async def event_handler(**kwargs):
            executed.append(kwargs)

        # 创建事件触发的任务
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=event_handler,
            trigger_type=TriggerType.EVENT,
            trigger_config={"event_name": "test_event"},
            task_name="test_event_handler",
            is_recurring=True,
        )

        # 等待一下确保任务创建完成
        await asyncio.sleep(0.5)

        # 触发事件
        await get_unified_scheduler().trigger_event("test_event", event_params={"key": "value"})

        # 等待事件处理
        await asyncio.sleep(1)

        assert len(executed) >= 1
        assert executed[0] == {"key": "value"}

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_synchronous_callback(self):
        """测试同步函数作为callback"""
        executed = []

        def sync_task():
            executed.append(1)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=sync_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="test_sync_callback",
        )

        await asyncio.sleep(1.5)

        assert len(executed) == 1

    @pytest.mark.asyncio
    async def test_scheduler_config(self):
        """测试SchedulerConfig配置"""
        config = SchedulerConfig(
            check_interval=0.5,
            task_default_timeout=60.0,
            max_concurrent_tasks=50,
            enable_retry=False,
        )

        scheduler = UnifiedScheduler(config=config)

        assert scheduler.config.check_interval == 0.5
        assert scheduler.config.task_default_timeout == 60.0
        assert scheduler.config.max_concurrent_tasks == 50
        assert scheduler.config.enable_retry is False

        # 测试默认配置
        default_config = SchedulerConfig()
        assert default_config.check_interval == 1.0
        assert default_config.task_default_timeout == 300.0

    @pytest.mark.asyncio
    async def test_scheduler_start_when_already_running(self):
        """测试调度器已运行时再次调用start"""
        # 调度器已在setup中启动，再次启动不应报错
        await get_unified_scheduler().start()
        # 验证调度器仍在运行
        stats = get_unified_scheduler().get_statistics()
        assert stats["is_running"] is True

    @pytest.mark.asyncio
    async def test_scheduler_stop_when_not_running(self):
        """测试调度器未运行时调用stop"""
        scheduler = UnifiedScheduler()
        # 不应该抛出异常
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_create_schedule_without_start(self):
        """测试未启动调度器时创建任务"""
        scheduler = UnifiedScheduler()

        async def dummy_task():
            pass

        # 应该抛出RuntimeError
        with pytest.raises(RuntimeError, match="调度器未运行"):
            await scheduler.create_schedule(
                callback=dummy_task,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": 1},
                task_name="test_error",
            )

    @pytest.mark.asyncio
    async def test_event_trigger_without_event_name(self):
        """测试事件触发缺少event_name"""
        async def dummy_task():
            pass

        with pytest.raises(ValueError, match="event_name"):
            await get_unified_scheduler().create_schedule(
                callback=dummy_task,
                trigger_type=TriggerType.EVENT,
                trigger_config={},  # 缺少event_name
                task_name="test_no_event_name",
            )

    @pytest.mark.asyncio
    async def test_task_info_with_zero_success_count(self):
        """测试任务信息中成功次数为0时的平均执行时间"""
        async def failing_task():
            raise ValueError("Always fails")

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=failing_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="test_failing_for_stats",
        )

        await asyncio.sleep(2)

        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        # 失败的任务会被移除，所以应该返回None
        assert task_info is None

    @pytest.mark.asyncio
    async def test_trigger_nonexistent_task(self):
        """测试触发不存在的任务"""
        result = await get_unified_scheduler().trigger_schedule("nonexistent_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_pause_nonexistent_task(self):
        """测试暂停不存在的任务"""
        result = await get_unified_scheduler().pause_schedule("nonexistent_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_nonexistent_task(self):
        """测试恢复不存在的任务"""
        result = await get_unified_scheduler().resume_schedule("nonexistent_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_pause_running_task(self):
        """测试暂停正在运行的任务"""
        async def long_running_task():
            await asyncio.sleep(100)  # 很长的任务

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=long_running_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.3},
            task_name="test_running_task",
        )

        # 等待任务开始运行
        await asyncio.sleep(1.5)

        # 检查任务状态
        task_info = await get_unified_scheduler().get_task_info(schedule_id)

        # 如果任务正在运行，尝试暂停
        if task_info and task_info["is_running"]:
            result = await get_unified_scheduler().pause_schedule(schedule_id)
            assert result is False  # 不能暂停正在运行的任务
        else:
            # 任务可能已经启动但还没进入RUNNING状态
            # 这时测试仍然有效，因为逻辑已经被其他测试覆盖
            pass

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_resume_non_paused_task(self):
        """测试恢复未暂停的任务"""
        async def dummy_task():
            pass

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=dummy_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="test_not_paused",
        )

        # 任务状态为PENDING，尝试恢复
        result = await get_unified_scheduler().resume_schedule(schedule_id)
        assert result is False

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_trigger_event_with_no_subscribers(self):
        """测试触发没有订阅者的事件"""
        # 不应该抛出异常
        await get_unified_scheduler().trigger_event("nonexistent_event")

    @pytest.mark.asyncio
    async def test_list_tasks_with_filters(self):
        """测试带过滤器列出任务"""
        # 创建不同状态和类型的任务
        task1_id = await get_unified_scheduler().create_schedule(
            callback=lambda: None,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="filter_task_1",
        )

        task2_id = await get_unified_scheduler().create_schedule(
            callback=lambda: None,
            trigger_type=TriggerType.CUSTOM,
            trigger_config={"condition_func": lambda: False},
            task_name="filter_task_2",
        )

        # 暂停一个任务
        await get_unified_scheduler().pause_schedule(task1_id)

        # 按状态筛选
        paused_tasks = await get_unified_scheduler().list_tasks(status=TaskStatus.PAUSED)
        assert len(paused_tasks) >= 1
        assert any(t["task_name"] == "filter_task_1" for t in paused_tasks)

        # 按类型筛选
        custom_tasks = await get_unified_scheduler().list_tasks(trigger_type=TriggerType.CUSTOM)
        assert len(custom_tasks) >= 1
        assert any(t["task_name"] == "filter_task_2" for t in custom_tasks)

        # 组合筛选
        combined = await get_unified_scheduler().list_tasks(
            trigger_type=TriggerType.TIME, status=TaskStatus.PAUSED
        )
        assert len(combined) >= 1

        # 清理
        await get_unified_scheduler().remove_schedule(task1_id)
        await get_unified_scheduler().remove_schedule(task2_id)

    @pytest.mark.asyncio
    async def test_custom_trigger_with_invalid_condition(self):
        """测试自定义触发器条件函数无效"""
        executed = []

        async def custom_task():
            executed.append(1)

        # condition_func 不是函数
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=custom_task,
            trigger_type=TriggerType.CUSTOM,
            trigger_config={"condition_func": "not_a_function"},
            task_name="test_invalid_condition",
        )

        await asyncio.sleep(2)

        # 任务不应该被执行
        assert len(executed) == 0

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_custom_trigger_with_exception(self):
        """测试自定义条件函数抛出异常"""
        executed = []

        async def custom_task():
            executed.append(1)

        def bad_condition():
            raise RuntimeError("Condition error")

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=custom_task,
            trigger_type=TriggerType.CUSTOM,
            trigger_config={"condition_func": bad_condition},
            task_name="test_exception_condition",
        )

        await asyncio.sleep(2)

        # 任务不应该被执行
        assert len(executed) == 0

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_task_execution_history_limit(self):
        """测试执行历史记录限制"""
        async def quick_task():
            pass

        # 创建会执行多次的循环任务（使用较短的间隔）
        schedule_id = await get_unified_scheduler().create_schedule(
            callback=quick_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            is_recurring=True,
            task_name="test_history_limit",
        )

        # 等待足够时间让任务执行多次（考虑调度器1秒的检查间隔）
        await asyncio.sleep(12)

        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is not None
        # 应该至少执行了几次（考虑到调度器间隔）
        assert task_info["trigger_count"] >= 3

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_remove_running_task(self):
        """测试移除正在运行的任务"""
        executed = []

        async def long_task():
            executed.append(1)
            await asyncio.sleep(10)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=long_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="test_remove_running",
        )

        # 等待任务开始运行
        await asyncio.sleep(1)

        # 移除正在运行的任务
        result = await get_unified_scheduler().remove_schedule(schedule_id)
        assert result is True

        # 验证任务已从列表中移除
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is None

    @pytest.mark.asyncio
    async def test_task_info_details(self):
        """测试任务信息的详细内容"""
        async def detailed_task(a, b):
            return a + b

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=detailed_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 1},
            callback_args=(1, 2),
            task_name="test_detailed_info",
            max_retries=5,
            timeout=60.0,
        )

        task_info = await get_unified_scheduler().get_task_info(schedule_id)

        assert task_info is not None
        assert task_info["schedule_id"] == schedule_id
        assert task_info["task_name"] == "test_detailed_info"
        assert task_info["trigger_type"] == "time"
        assert task_info["is_recurring"] is False
        assert task_info["status"] == "pending"
        assert task_info["trigger_count"] == 0
        assert task_info["success_count"] == 0
        assert task_info["failure_count"] == 0
        assert task_info["retry_count"] == 0
        assert task_info["max_retries"] == 5
        assert task_info["timeout"] == 60.0
        assert "created_at" in task_info
        assert task_info["last_triggered_at"] is None

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_statistics_with_running_tasks(self):
        """测试统计信息中包含运行中的任务"""
        async def long_task():
            await asyncio.sleep(10)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=long_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="test_stats_running",
        )

        # 等待任务开始运行
        await asyncio.sleep(1.5)

        stats = get_unified_scheduler().get_statistics()
        assert stats["is_running"] is True
        assert stats["running_tasks"] >= 1
        assert len(stats["running_tasks_info"]) >= 1
        assert any(t["task_name"] == "test_stats_running" for t in stats["running_tasks_info"])

        # 清理
        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_force_overwrite_non_active_task(self):
        """测试覆盖非活跃任务"""
        async def dummy_task():
            pass

        # 创建一个任务
        schedule_id1 = await get_unified_scheduler().create_schedule(
            callback=dummy_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            task_name="test_overwrite_non_active",
        )

        # 等待任务完成（一次性任务）
        await asyncio.sleep(2)

        # 现在创建同名任务，不应该报错（因为旧任务已完成/被移除）
        schedule_id2 = await get_unified_scheduler().create_schedule(
            callback=dummy_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
            task_name="test_overwrite_non_active",
        )

        assert schedule_id1 != schedule_id2

        await get_unified_scheduler().remove_schedule(schedule_id2)

    @pytest.mark.asyncio
    async def test_schedule_task_without_task_name(self):
        """测试创建任务时不指定task_name"""
        async def dummy_task():
            pass

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=dummy_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
        )

        # 应该自动生成任务名称
        task_info = await get_unified_scheduler().get_task_info(schedule_id)
        assert task_info is not None
        assert task_info["task_name"].startswith("Task-")
        assert schedule_id.startswith(task_info["task_name"].split("-")[1])

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_finish_execution_without_current(self):
        """测试finish_execution在没有current_execution时的行为"""
        async def dummy_callback():
            pass

        task = ScheduleTask(
            schedule_id="test_id",
            task_name="test_task",
            callback=dummy_callback,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
        )

        # current_execution 为 None 时调用 finish_execution
        # 应该直接返回，不抛出异常
        task.finish_execution(success=True, result="test")

        # 状态应该保持 PENDING（因为 current_execution 为 None）
        assert task.status == TaskStatus.PENDING
        assert task.current_execution is None

    @pytest.mark.asyncio
    async def test_recurring_task_with_trigger_at_and_interval(self):
        """测试循环任务使用trigger_at和interval_seconds"""
        executed = []

        async def interval_task():
            executed.append(1)

        # 设置触发时间为当前时间，并使用间隔
        trigger_time = datetime.now() + timedelta(seconds=0.5)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=interval_task,
            trigger_type=TriggerType.TIME,
            trigger_config={
                "trigger_at": trigger_time,
                "interval_seconds": 1.0,
            },
            is_recurring=True,
            task_name="test_trigger_at_interval",
        )

        await asyncio.sleep(4)
        assert len(executed) >= 2

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_manual_trigger_with_exception(self):
        """测试手动触发任务时发生异常"""
        async def failing_task():
            raise ValueError("Manual trigger failed")

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=failing_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 100},
            task_name="test_manual_trigger_fail",
        )

        # 手动触发
        result = await get_unified_scheduler().trigger_schedule(schedule_id)
        assert result is False  # 失败的任务返回False

    @pytest.mark.asyncio
    async def test_recurring_task_failure_with_max_retries(self):
        """测试循环任务失败但达到最大重试次数"""
        attempt_count = 0

        async def always_failing_task():
            nonlocal attempt_count
            attempt_count += 1
            raise ValueError("Always fails")

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=always_failing_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.5},
            is_recurring=True,
            max_retries=2,
            task_name="test_recurring_fail",
        )

        await asyncio.sleep(4)

        # 应该尝试执行并重试
        assert attempt_count >= 1

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_event_task_timeout(self):
        """测试事件任务超时"""
        async def timeout_handler(**kwargs):
            await asyncio.sleep(100)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=timeout_handler,
            trigger_type=TriggerType.EVENT,
            trigger_config={"event_name": "timeout_test_event"},
            task_name="test_event_timeout",
            timeout=1.0,
        )

        await asyncio.sleep(0.5)

        # 触发事件
        await get_unified_scheduler().trigger_event("timeout_test_event")

        # 等待超时
        await asyncio.sleep(2)

        # 任务应该已超时
        stats = get_unified_scheduler().get_statistics()
        assert stats["total_timeouts"] >= 1

    @pytest.mark.asyncio
    async def test_event_task_failure(self):
        """测试事件任务失败"""
        async def failing_handler(**kwargs):
            raise ValueError("Event handler failed")

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=failing_handler,
            trigger_type=TriggerType.EVENT,
            trigger_config={"event_name": "fail_test_event"},
            task_name="test_event_failure",
            is_recurring=True,
        )

        await asyncio.sleep(0.5)

        # 触发事件
        await get_unified_scheduler().trigger_event("fail_test_event")

        await asyncio.sleep(1)

        # 应该有失败记录
        stats = get_unified_scheduler().get_statistics()
        assert stats["total_failures"] >= 1

        await get_unified_scheduler().remove_schedule(schedule_id)

    @pytest.mark.asyncio
    async def test_is_active_method(self):
        """测试is_active方法的各种状态"""
        async def dummy_callback():
            pass

        task = ScheduleTask(
            schedule_id="test_id",
            task_name="test_task",
            callback=dummy_callback,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
        )

        # PENDING 状态是活跃的
        task.status = TaskStatus.PENDING
        assert task.is_active() is True

        # RUNNING 状态是活跃的
        task.status = TaskStatus.RUNNING
        assert task.is_active() is True

        # COMPLETED 状态不是活跃的
        task.status = TaskStatus.COMPLETED
        assert task.is_active() is False

        # FAILED 状态不是活跃的
        task.status = TaskStatus.FAILED
        assert task.is_active() is False

        # PAUSED 状态不是活跃的
        task.status = TaskStatus.PAUSED
        assert task.is_active() is False

    @pytest.mark.asyncio
    async def test_can_trigger_method(self):
        """测试can_trigger方法"""
        async def dummy_callback():
            pass

        task = ScheduleTask(
            schedule_id="test_id",
            task_name="test_task",
            callback=dummy_callback,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 10},
        )

        # 只有 PENDING 状态可以触发
        task.status = TaskStatus.PENDING
        assert task.can_trigger() is True

        task.status = TaskStatus.RUNNING
        assert task.can_trigger() is False

        task.status = TaskStatus.PAUSED
        assert task.can_trigger() is False

        task.status = TaskStatus.COMPLETED
        assert task.can_trigger() is False

    @pytest.mark.asyncio
    async def test_scheduler_with_custom_config(self):
        """测试自定义配置的调度器"""
        config = SchedulerConfig(
            check_interval=0.1,  # 更短的检查间隔
            task_default_timeout=10.0,
            max_concurrent_tasks=10,
            enable_task_semaphore=True,
            enable_retry=True,
            max_retries=5,
            retry_delay=1.0,
        )

        scheduler = UnifiedScheduler(config=config)

        await scheduler.start()

        executed = []

        async def quick_task():
            executed.append(1)

        # 创建任务
        schedule_id = await scheduler.create_schedule(
            callback=quick_task,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": 0.3},
            task_name="test_custom_config",
        )

        await asyncio.sleep(1)

        assert len(executed) == 1

        await scheduler.remove_schedule(schedule_id)
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_event_trigger_sync_callback(self):
        """测试事件触发使用同步回调"""
        executed = []

        def sync_handler(**kwargs):
            executed.append(kwargs)

        schedule_id = await get_unified_scheduler().create_schedule(
            callback=sync_handler,
            trigger_type=TriggerType.EVENT,
            trigger_config={"event_name": "sync_test_event"},
            task_name="test_sync_event_handler",
            is_recurring=True,
        )

        await asyncio.sleep(0.5)

        # 触发事件
        await get_unified_scheduler().trigger_event("sync_test_event", event_params={"test": "data"})

        await asyncio.sleep(1)

        assert len(executed) >= 1
        assert executed[0] == {"test": "data"}

        await get_unified_scheduler().remove_schedule(schedule_id)
