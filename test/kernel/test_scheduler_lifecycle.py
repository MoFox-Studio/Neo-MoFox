"""验证预注册任务的执行边界和关闭清理。"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from src.kernel.scheduler import SchedulerConfig, TriggerType, UnifiedScheduler


@pytest.mark.asyncio
@pytest.mark.parametrize("recurring", [False, True])
async def test_registered_task_only_runs_after_start(recurring):
    """任务已过期且让出事件循环后仍不运行，启动后才执行。"""
    scheduler = UnifiedScheduler(SchedulerConfig(check_interval=0.01))
    done = asyncio.Event()
    callback = AsyncMock(side_effect=lambda: done.set())
    try:
        task_id = await scheduler.create_schedule(
            callback,
            TriggerType.TIME,
            {"delay_seconds": 60},
            is_recurring=recurring,
        )
        scheduler._tasks[task_id].created_at = datetime.now() - timedelta(seconds=65)
        await asyncio.sleep(0.03)
        callback.assert_not_awaited()

        await scheduler.start()
        await asyncio.wait_for(done.wait(), timeout=2)
        await asyncio.sleep(0.03)
        callback.assert_awaited_once()
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_manual_and_event_trigger_require_start():
    """所有公开执行入口都必须遵守启动边界。"""
    scheduler = UnifiedScheduler()
    callback = AsyncMock()
    task_id = await scheduler.create_schedule(
        callback, TriggerType.EVENT, {"event_name": "ready"}, is_recurring=True
    )
    try:
        with pytest.raises(RuntimeError, match="未运行"):
            await scheduler.trigger_schedule(task_id)
        with pytest.raises(RuntimeError, match="未运行"):
            await scheduler.trigger_event("ready")
        callback.assert_not_awaited()
        await scheduler.start()
        await scheduler.trigger_event("ready")
        callback.assert_awaited_once()
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_stop_before_start_clears_pending_tasks():
    """初始化失败也能清理预注册任务，并拒绝关闭后的新任务。"""
    scheduler = UnifiedScheduler()
    callback = AsyncMock()
    await scheduler.create_schedule(callback, TriggerType.EVENT, {"event_name": "ready"})
    await scheduler.stop()
    assert scheduler.list_schedule_names() == []
    assert scheduler._event_subscriptions == {}
    with pytest.raises(RuntimeError, match="停止"):
        await scheduler.create_schedule(callback, TriggerType.TIME, {"delay_seconds": 0})
    await scheduler.stop()
    try:
        await scheduler.start()
        await scheduler.trigger_event("ready")
        callback.assert_not_awaited()
        task_id = await scheduler.create_schedule(
            callback, TriggerType.TIME, {"delay_seconds": 60}
        )
        await scheduler.trigger_schedule(task_id)
        callback.assert_awaited_once()
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_invalid_event_does_not_replace_existing_task():
    """注册失败既不留下残留，也不破坏同名有效任务。"""
    scheduler = UnifiedScheduler()
    callback = AsyncMock()
    try:
        task_id = await scheduler.create_schedule(
            callback, TriggerType.EVENT, {"event_name": "ready"}, task_name="existing"
        )
        with pytest.raises(ValueError, match="event_name"):
            await scheduler.create_schedule(
                callback, TriggerType.EVENT, {}, task_name="existing", force_overwrite=True
            )
        assert await scheduler.find_schedule_by_name("existing") == task_id
        assert len(await scheduler.list_tasks()) == 1
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_registration_rejected_while_stopping():
    """关闭过程让出事件循环时也不能插入将被清空的新任务。"""
    scheduler = UnifiedScheduler()
    await scheduler.start()
    stopping = asyncio.create_task(scheduler.stop())
    try:
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="关闭"):
            await scheduler.create_schedule(
                AsyncMock(), TriggerType.TIME, {"delay_seconds": 0}
            )
    finally:
        await stopping
