"""
Logger 模块演示脚本

展示如何使用基于 rich 库的日志系统。
"""

import sys
from pathlib import Path

# 添加 src 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.kernel.logger import get_logger, COLOR


def main():
    """演示 logger 的各种功能"""

    # 1. 基本用法
    print("\n=== 基本用法 ===")
    logger = get_logger("demo", display="演示", color=COLOR.CYAN)
    logger.info("这是一条信息日志")
    logger.warning("这是一条警告日志")
    logger.error("这是一条错误日志")

    # 2. 不同颜色的 logger
    print("\n=== 不同颜色的 Logger ===")
    red_logger = get_logger("red", display="红色日志", color=COLOR.RED)
    blue_logger = get_logger("blue", display="蓝色日志", color=COLOR.BLUE)
    green_logger = get_logger("green", display="绿色日志", color=COLOR.GREEN)

    red_logger.info("我是红色")
    blue_logger.info("我是蓝色")
    green_logger.info("我是绿色")

    # 3. 使用元数据
    print("\n=== 使用元数据 ===")
    logger = get_logger("metadata_demo", display="元数据演示", color=COLOR.YELLOW)
    logger.set_metadata("user_id", "12345")
    logger.set_metadata("session", "abc-def")

    logger.info("用户登录", ip="192.168.1.100", status="success")
    logger.info("数据库查询", table="users", duration="0.05s")

    # 4. 面板输出
    print("\n=== 面板输出 ===")
    logger = get_logger("panel_demo", display="面板演示", color=COLOR.MAGENTA)
    logger.print_panel("这是一条重要消息，需要特别关注！", title="重要通知")

    # 5. 直接使用 rich 打印
    print("\n=== Rich 格式化打印 ===")
    logger = get_logger("rich_demo", display="Rich 演示", color=COLOR.BLUE)
    logger.print_rich("[bold yellow]粗体黄色文字[/bold yellow]")
    logger.print_rich("[italic cyan]斜体青色文字[/italic cyan]")
    logger.print_rich("[underline green]下划线绿色文字[/underline green]")

    # 6. 不同日志级别
    print("\n=== 不同日志级别 ===")
    logger = get_logger("level_demo", display="级别演示", color=COLOR.WHITE)
    logger.debug("调试信息（通常只在开发时显示）")
    logger.info("普通信息")
    logger.warning("警告信息")
    logger.error("错误信息")
    logger.critical("严重错误信息")

    # 7. WatchDog 风格的日志
    print("\n=== WatchDog 风格日志 ===")
    watchdog = get_logger("WatchDog", display="WatchDog", color=COLOR.YELLOW)
    watchdog.info("WatchDog 监控已启动 (tick间隔=1.0s)")
    watchdog.warning("聊天流 'test_stream' 响应缓慢: 距离上次心跳 2.50s")
    watchdog.error("聊天流 'test_stream' 可能已卡死，尝试重启...")


if __name__ == "__main__":
    main()
