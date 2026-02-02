"""Scheduler 模块使用示例

演示 kernel.scheduler 的核心用法：
- 启动和停止调度器
- 创建延迟任务（一次性）
- 创建周期任务
- 创建指定时间触发的任务
- 创建自定义条件触发的任务
- 暂停和恢复任务
- 强制触发任务
- 获取任务信息和列表
- 统计信息
- 移除任务

运行：
    uv run python examples/src/kernel/scheduler/scheduler_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import asyncio
from datetime import datetime, timedelta

# 允许从任意工作目录直接运行该示例文件
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.kernel.scheduler import get_unified_scheduler, TriggerType, TaskStatus
from src.kernel.logger import get_logger, COLOR


# 全局变量用于演示
execution_counter = {"count": 0}
custom_condition = {"trigger": False}


# ==================== 任务回调函数 ====================

async def simple_task() -> None:
    """简单任务"""
    execution_counter["count"] += 1
    logger.info(f"[执行] 简单任务执行 (第{execution_counter['count']}次)")


async def task_with_args(name: str, value: int) -> None:
    """带参数的任务"""
    execution_counter["count"] += 1
    logger.info(f"[执行] 带参数任务: {name} = {value} (第{execution_counter['count']}次)")


async def failing_task() -> None:
    """会失败的任务"""
    execution_counter["count"] += 1
    logger.info(f"[执行] 即将失败的任务 (第{execution_counter['count']}次)")
    raise ValueError("这是一个预期的错误！")


async def long_running_task() -> None:
    """长时间运行的任务"""
    logger.info("[执行] 长时间任务开始...")
    await asyncio.sleep(2)
    logger.info("[执行] 长时间任务完成")


def sync_task() -> None:
    """同步任务（演示调度器会自动在线程中运行）"""
    execution_counter["count"] += 1
    logger.info(f"[执行] 同步任务 (第{execution_counter['count']}次)")


async def check_custom_condition() -> bool:
    """自定义条件函数"""
    logger.debug(f"[检查] 自定义条件: {custom_condition['trigger']}")
    return custom_condition["trigger"]


# ==================== 主函数 ====================

async def main() -> None:
    """主函数"""
    global logger
    logger = get_logger("scheduler_example", display="Scheduler", color=COLOR.YELLOW)

    logger.print_panel("1. 启动调度器")

    # 获取统一调度器实例
    scheduler = get_unified_scheduler()

    # 启动调度器
    await scheduler.start()
    logger.info("[OK] 调度器已启动")

    logger.print_panel("2. 创建延迟任务（一次性）")

    # 3秒后执行一次
    schedule_id_1 = await scheduler.create_schedule(
        callback=simple_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 3},
        task_name="delayed_task",
        is_recurring=False
    )
    logger.info(f"[OK] 创建3秒延迟任务: {schedule_id_1[:8]}...")

    # 带参数的延迟任务
    schedule_id_2 = await scheduler.create_schedule(
        callback=task_with_args,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 4},
        task_name="task_with_args",
        callback_args=("test_param", 42),
        is_recurring=False
    )
    logger.info(f"[OK] 创建带参数的4秒延迟任务: {schedule_id_2[:8]}...")

    logger.print_panel("3. 创建周期任务")

    # 每2秒执行一次
    schedule_id_3 = await scheduler.create_schedule(
        callback=simple_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"interval_seconds": 2},
        task_name="recurring_task",
        is_recurring=True
    )
    logger.info(f"[OK] 创建每2秒执行的周期任务: {schedule_id_3[:8]}...")

    logger.print_panel("4. 创建指定时间触发的任务")

    # 5秒后触发
    trigger_time = datetime.now() + timedelta(seconds=5)
    schedule_id_4 = await scheduler.create_schedule(
        callback=simple_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"trigger_at": trigger_time},
        task_name="specific_time_task",
        is_recurring=False
    )
    logger.info(f"[OK] 创建指定时间任务: {schedule_id_4[:8]}... (触发于 {trigger_time.strftime('%H:%M:%S')})")

    logger.print_panel("5. 创建带超时的任务")

    # 1秒后执行，但任务超时设置为0.5秒（会超时）
    schedule_id_5 = await scheduler.create_schedule(
        callback=long_running_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 1},
        task_name="timeout_task",
        timeout=0.5,
        is_recurring=False
    )
    logger.info(f"[OK] 创建会超时的任务: {schedule_id_5[:8]}... (超时: 0.5秒)")

    logger.print_panel("6. 创建带重试的任务")

    # 2秒后执行，会失败但会重试
    schedule_id_6 = await scheduler.create_schedule(
        callback=failing_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 2},
        task_name="failing_task_with_retry",
        max_retries=2,
        is_recurring=False
    )
    logger.info(f"[OK] 创建带重试的失败任务: {schedule_id_6[:8]}... (重试2次)")

    logger.print_panel("7. 创建自定义条件任务")

    # 创建一个自定义条件触发的任务
    schedule_id_7 = await scheduler.create_schedule(
        callback=simple_task,
        trigger_type=TriggerType.CUSTOM,
        trigger_config={"condition_func": check_custom_condition},
        task_name="custom_condition_task",
        is_recurring=False
    )
    logger.info(f"[OK] 创建自定义条件任务: {schedule_id_7[:8]}...")

    # 等待一段时间，观察周期任务执行
    logger.info("\n" + "=" * 60)
    logger.info("观察周期任务执行 (等待10秒)...")
    logger.info("=" * 60)
    await asyncio.sleep(10)

    logger.print_panel("8. 触发自定义条件")

    # 触发自定义条件
    custom_condition["trigger"] = True
    logger.info("[OK] 已设置自定义条件为 True，任务应该会触发")
    await asyncio.sleep(2)

    logger.print_panel("9. 暂停和恢复任务")

    # 暂停周期任务
    await scheduler.pause_schedule(schedule_id_3)
    logger.info(f"[OK] 暂停周期任务: {schedule_id_3[:8]}...")

    # 等待几秒验证任务确实被暂停
    logger.info("等待3秒验证任务已暂停...")
    await asyncio.sleep(3)

    # 恢复周期任务
    await scheduler.resume_schedule(schedule_id_3)
    logger.info(f"[OK] 恢复周期任务: {schedule_id_3[:8]}...")

    # 等待几秒验证任务恢复执行
    logger.info("等待3秒验证任务已恢复...")
    await asyncio.sleep(3)

    logger.print_panel("10. 强制触发任务")

    # 强制触发周期任务
    logger.info("强制触发周期任务...")
    await scheduler.trigger_schedule(schedule_id_3)
    await asyncio.sleep(1)

    logger.print_panel("11. 获取任务信息")

    # 获取任务信息
    task_info = await scheduler.get_task_info(schedule_id_3)
    if task_info:
        logger.info(f"[OK] 任务信息:")
        logger.info(f"  - 任务ID: {task_info['schedule_id']}")
        logger.info(f"  - 任务名: {task_info['task_name']}")
        logger.info(f"  - 触发类型: {task_info['trigger_type']}")
        logger.info(f"  - 是否周期: {task_info['is_recurring']}")
        logger.info(f"  - 状态: {task_info['status']}")
        logger.info(f"  - 创建时间: {task_info['created_at']}")
        logger.info(f"  - 触发次数: {task_info['trigger_count']}")
        logger.info(f"  - 成功次数: {task_info['success_count']}")
        logger.info(f"  - 失败次数: {task_info['failure_count']}")
        logger.info(f"  - 平均执行时间: {task_info['avg_execution_time']:.3f}秒")

    logger.print_panel("12. 列出所有任务")

    # 列出所有任务
    all_tasks = await scheduler.list_tasks()
    logger.info(f"[OK] 所有任务 (共{len(all_tasks)}个):")
    for task in all_tasks:
        logger.info(f"  - {task['task_name']}: {task['status']} ({task['trigger_type']})")

    # 只列出周期任务
    recurring_tasks = await scheduler.list_tasks(trigger_type=TriggerType.TIME)
    recurring_only = [t for t in recurring_tasks if t['is_recurring']]
    logger.info(f"[OK] 周期任务 (共{len(recurring_only)}个):")
    for task in recurring_only:
        logger.info(f"  - {task['task_name']}: {task['status']}")

    logger.print_panel("13. 获取统计信息")

    # 获取统计信息
    stats = scheduler.get_statistics()
    logger.info("[OK] 调度器统计:")
    logger.info(f"  - 是否运行: {stats['is_running']}")
    logger.info(f"  - 运行时长: {stats['uptime_seconds']:.1f}秒")
    logger.info(f"  - 总任务数: {stats['total_tasks']}")
    logger.info(f"  - 活跃任务: {stats['active_tasks']}")
    logger.info(f"  - 运行中任务: {stats['running_tasks']}")
    logger.info(f"  - 暂停任务: {stats['paused_tasks']}")
    logger.info(f"  - 周期任务: {stats['recurring_tasks']}")
    logger.info(f"  - 一次性任务: {stats['one_time_tasks']}")
    logger.info(f"  - 总执行次数: {stats['total_executions']}")
    logger.info(f"  - 总失败次数: {stats['total_failures']}")
    logger.info(f"  - 总超时次数: {stats['total_timeouts']}")
    logger.info(f"  - 成功率: {stats['success_rate']:.1%}")

    logger.print_panel("14. 根据名称查找和移除任务")

    # 根据名称查找任务
    found_id = await scheduler.find_schedule_by_name("recurring_task")
    logger.info(f"[OK] 查找任务 'recurring_task': {found_id[:8] if found_id else None}...")

    # 移除周期任务
    removed = await scheduler.remove_schedule_by_name("recurring_task")
    logger.info(f"[OK] 移除任务 'recurring_task': {removed}")

    # 验证任务已移除
    remaining_tasks = await scheduler.list_tasks()
    logger.info(f"[OK] 移除后剩余任务数: {len(remaining_tasks)}")

    logger.print_panel("15. 演示同步函数支持")

    # 创建同步函数任务
    sync_schedule_id = await scheduler.create_schedule(
        callback=sync_task,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 1},
        task_name="sync_task_demo",
        is_recurring=False
    )
    logger.info(f"[OK] 创建同步任务: {sync_schedule_id[:8]}...")
    await asyncio.sleep(2)

    logger.info("\n" + "=" * 60)
    logger.info("演示完成！")
    logger.info("=" * 60)

    # 显示最终统计
    final_stats = scheduler.get_statistics()
    logger.info(f"\n最终统计:")
    logger.info(f"  - 总执行次数: {final_stats['total_executions']}")
    logger.info(f"  - 总失败次数: {final_stats['total_failures']}")
    logger.info(f"  - 总超时次数: {final_stats['total_timeouts']}")
    logger.info(f"  - 成功率: {final_stats['success_rate']:.1%}")

    # 停止调度器
    logger.info("\n正在停止调度器...")
    await scheduler.stop()
    logger.info("[OK] 调度器已停止")


if __name__ == "__main__":
    asyncio.run(main())
