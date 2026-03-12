"""
Logger 模块

基于 rich 库的统一日志系统，支持彩色渲染、元数据跟踪、文件输出和异常格式化。

用法示例:
    from src.kernel.logger import (
        initialize_logger_system, get_logger, COLOR, RotationMode, LOG_OUTPUT_EVENT
    )
    from src.kernel.event import event_bus

    # 初始化全局日志配置（在核心启动时调用）
    initialize_logger_system(
        log_dir="logs/app",
        log_level="INFO",
        enable_file=True,
        file_rotation=RotationMode.DATE,
    )

    # 创建日志记录器（将使用全局配置）
    logger = get_logger("my_logger", display="我的日志", color=COLOR.BLUE)
    logger.info("Hello World!")

    # 覆盖全局配置
    logger2 = get_logger("debug_logger", log_level="DEBUG")
    logger2.debug("这条debug日志会显示")

    # 使用元数据
    logger.set_metadata("user_id", "12345")
    logger.info("用户登录", ip="192.168.1.1")

    # 使用面板输出
    logger.print_panel("重要消息", title="通知")

    # 启用事件广播
    async def on_log(event_name, params):
        print(f"[{params['level']}] {params['message']}")
        from src.kernel.event import EventDecision
        return (EventDecision.SUCCESS, params)

    logger = get_logger("my_logger", enable_event_broadcast=True)
    event_bus.subscribe(LOG_OUTPUT_EVENT, on_log)
    logger.info("这条日志会被广播到事件系统")
"""

from .logger import (
    Logger,
    initialize_logger_system,
    get_global_log_config,
    get_logger,
    remove_logger,
    get_all_loggers,
    clear_all_loggers,
    shutdown_logger_system,
    install_rich_traceback_formatter,
    LOG_OUTPUT_EVENT,
)
from .color import COLOR, get_rich_color, DEFAULT_LEVEL_COLORS
from .file_handler import FileHandler, RotationMode

__all__ = [
    # 全局初始化
    "initialize_logger_system",
    "get_global_log_config",
    "shutdown_logger_system",
    # 主要接口
    "get_logger",
    "Logger",
    "COLOR",
    # 文件输出相关
    "FileHandler",
    "RotationMode",
    # 辅助函数
    "remove_logger",
    "get_all_loggers",
    "clear_all_loggers",
    "get_rich_color",
    "install_rich_traceback_formatter",
    "DEFAULT_LEVEL_COLORS",
    # 事件广播相关
    "LOG_OUTPUT_EVENT",
]

# 版本信息
__version__ = "1.0.0"
