"""
Concurrency 模块演示脚本

展示如何使用统一任务管理系统来替代 asyncio.create_task。
"""

import sys
import asyncio
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.kernel.concurrency import (
    get_task_manager,
    get_watchdog,
)
from src.kernel.logger import get_logger, COLOR


async def main():
    """演示 concurrency 模块的各种功能"""

    # 获取 logger
    logger = get_logger(
        "concurrency_demo",
        display="Concurrency",
        color=COLOR.CYAN,
    )

    logger.info("=" * 50)
    logger.info("Concurrency 模块演示开始")
    logger.info("=" * 50)

    # 1. 基本任务创建
    logger.print_panel("1. 基本任务创建", border_style="cyan")

    tm = get_task_manager()

    async def basic_task(name: str, delay: float):
        """基本异步任务"""
        logger.info(f"任务 {name} 开始执行", delay=delay)
        await asyncio.sleep(delay)
        logger.info(f"任务 {name} 完成")
        return f"{name} 的结果"

    # 创建几个任务
    tm.create_task(basic_task("Task1", 0.5), name="basic_task_1")
    tm.create_task(basic_task("Task2", 0.3), name="basic_task_2")

    # 等待所有任务完成
    await tm.wait_all_tasks()
    logger.info("所有基本任务已完成\n")

    # 2. 使用 TaskGroup
    logger.print_panel("2. TaskGroup 作用域管理", border_style="green")

    async def task_a():
        """任务 A"""
        logger.info("任务 A 开始")
        await asyncio.sleep(0.2)
        logger.info("任务 A 完成")
        return "A"

    async def task_b():
        """任务 B"""
        logger.info("任务 B 开始")
        await asyncio.sleep(0.1)
        logger.info("任务 B 完成")
        return "B"

    async def task_c():
        """任务 C"""
        logger.info("任务 C 开始")
        await asyncio.sleep(0.15)
        logger.info("任务 C 完成")
        return "C"

    # 使用 TaskGroup 管理一组任务
    async with tm.group(
        name="demo_group",
        timeout=30,
        cancel_on_error=True,
    ) as tg:
        result_a = tg.create_task(task_a())
        result_b = tg.create_task(task_b())
        result_c = tg.create_task(task_c())

    logger.info("TaskGroup 中的所有任务已完成\n")

    # 3. TaskGroup 共享演示
    logger.print_panel("3. TaskGroup 共享", border_style="yellow")

    async def module1_task():
        """模块1的任务"""
        logger.info("模块1的任务执行中...")
        await asyncio.sleep(0.1)
        logger.info("模块1的任务完成")

    async def module2_task():
        """模块2的任务"""
        logger.info("模块2的任务执行中...")
        await asyncio.sleep(0.1)
        logger.info("模块2的任务完成")

    # 在一个模块中使用 TaskGroup
    async with tm.group(name="shared_group", timeout=30) as tg:
        tg.create_task(module1_task())
        logger.info("模块1任务已创建")

    # 在另一个"模块"中使用同一个 TaskGroup
    async with tm.group(name="shared_group", timeout=30) as tg:
        tg.create_task(module2_task())
        logger.info("模块2任务已添加到同一个组\n")

    # 4. 守护任务
    logger.print_panel("4. 守护任务 (Daemon Tasks)", border_style="magenta")

    # 守护任务会持续运行，不会被 wait_all_tasks 等待
    async def daemon_task():
        """守护任务示例"""
        logger.info("守护任务启动，将持续运行")
        for _ in range(3):  # 只运行3次，不是无限循环
            await asyncio.sleep(0.1)
            logger.info("守护任务心跳（这不会阻塞程序退出）")

    # 创建守护任务
    tm.create_task(daemon_task(), name="watchdog_heartbeat", daemon=True)
    logger.info("守护任务已创建（不会被 wait_all_tasks 等待）")

    # 非守护任务
    async def normal_task():
        await asyncio.sleep(0.2)
        logger.info("普通任务完成")

    tm.create_task(normal_task(), name="normal_task")

    # wait_all_tasks 只等待非守护任务
    await tm.wait_all_tasks()
    logger.info("所有非守护任务已完成（守护任务仍在运行）\n")

    # 5. 错误处理和取消
    logger.print_panel("5. 错误处理", border_style="red")

    async def failing_task():
        """会失败的任务"""
        await asyncio.sleep(0.1)
        raise ValueError("任务失败了")

    async def normal_long_task():
        """需要较长时间的任务"""
        logger.info("长时间任务开始")
        await asyncio.sleep(1)
        logger.info("长时间任务完成")

    # TaskGroup 的 cancel_on_error 功能
    try:
        async with tm.group(
            name="error_group",
            cancel_on_error=True,
            timeout=30,
        ) as tg:
                tg.create_task(failing_task())
                tg.create_task(normal_long_task())
    except ValueError as e:
        logger.warning(f"捕获到异常: {e}")
        logger.info("由于 cancel_on_error=True，其他任务已被取消\n")

    # 6. 获取统计信息
    logger.print_panel("6. 任务统计信息", border_style="blue")

    # 创建一些任务用于演示统计
    for i in range(5):
        tm.create_task(
            basic_task(f"统计任务{i}", 0.1),
            name=f"stats_task_{i}",
        )

    stats = tm.get_stats()
    logger.info(f"总任务数: {stats['total_tasks']}")
    logger.info(f"活跃任务数: {stats['active_tasks']}")
    logger.info(f"守护任务数: {stats['daemon_tasks']}")
    logger.info(f"任务组数: {stats['groups']}")

    await tm.wait_all_tasks()
    logger.info("")

    # 7. WatchDog 演示
    logger.print_panel("7. WatchDog 监控系统", border_style="bright_yellow")

    wd = get_watchdog()

    # 注册聊天流到 WatchDog
    wd.register_stream(
        stream_id="demo_stream",
        tick_interval=1.0,
        warning_threshold=2.0,
        restart_threshold=5.0,
    )

    logger.info("已注册演示流到 WatchDog")
    logger.info("演示流每秒发送一次心跳...")

    # 模拟聊天流驱动器的 tick
    for i in range(3):
        await asyncio.sleep(0.8)
        wd.feed_dog("demo_stream")
        logger.info(f"发送心跳 #{i+1}")

    logger.info("演示完成")
    logger.info("")

    # 8. 任务超时演示
    logger.print_panel("8. 任务超时", border_style="red")

    async def timeout_task():
        """会超时的任务"""
        await asyncio.sleep(5)
        logger.info("这个任务不应该完成（超时）")

    # 创建带超时的任务（0.5秒超时）
    tm.create_task(timeout_task(), name="timeout_task", timeout=0.5)

    # 等待一小段时间
    await asyncio.sleep(1)

    # 清理
    logger.info("=" * 50)
    logger.info("演示完成！")
    logger.info("=" * 50)

    # 显示最终统计
    final_stats = tm.get_stats()
    logger.info(f"最终统计 - 总任务: {final_stats['total_tasks']}, "
                 f"活跃: {final_stats['active_tasks']}, "
                 f"守护: {final_stats['daemon_tasks']}")


if __name__ == "__main__":
    # 运行演示
    asyncio.run(main())
