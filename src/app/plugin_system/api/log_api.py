from src.kernel.logger import get_logger as kernel_get_logger, COLOR, Logger

def get_logger(
    name: str,
    display: str | None = None,
    color: COLOR | str | None = None,
    enable_event_broadcast: bool = True,
) -> Logger:
    """获取或创建日志记录器

    Args:
        name: 日志记录器名称（唯一标识）
        display: 显示名称，如果为 None 则使用 name
        color: 日志颜色
        enable_event_broadcast: 是否启用事件广播（发布到 on_log_output 事件）

    Returns:
        日志记录器实例

    Example:
        >>> logger = get_logger("my_logger", display="我的日志", color=COLOR.BLUE)
        >>> logger.info("Hello World!")
        >>> # 禁用事件广播
        >>> logger = get_logger("my_logger", enable_event_broadcast=False)
        >>> logger.info("这条日志不会广播到事件系统")
    """
    return kernel_get_logger(
        name=name,
        display=display,
        color=color,
        enable_event_broadcast=enable_event_broadcast,
    )

__all__ = ["get_logger", "COLOR"]
