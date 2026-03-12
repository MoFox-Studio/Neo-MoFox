"""
日志颜色定义

提供日志输出使用的颜色枚举。
"""

from __future__ import annotations

from enum import Enum


class COLOR(Enum):
    """日志颜色枚举

    使用 rich 库的颜色系统，支持 ANSI 颜色名称。
    """

    # 基础颜色
    BLACK = "black"
    RED = "red"
    GREEN = "green"
    YELLOW = "yellow"
    BLUE = "blue"
    MAGENTA = "magenta"
    CYAN = "cyan"
    WHITE = "white"

    # 明亮变体
    BRIGHT_BLACK = "bright_black"
    BRIGHT_RED = "bright_red"
    BRIGHT_GREEN = "bright_green"
    BRIGHT_YELLOW = "bright_yellow"
    BRIGHT_BLUE = "bright_blue"
    BRIGHT_MAGENTA = "bright_magenta"
    BRIGHT_CYAN = "bright_cyan"
    BRIGHT_WHITE = "bright_white"

    # 特殊颜色
    GRAY = "grey50"
    ORANGE = "orange"
    PURPLE = "purple"
    PINK = "deep_pink"

    # 日志级别推荐颜色
    DEBUG = "dim"
    INFO = "blue"
    WARNING = "yellow"
    ERROR = "red"
    CRITICAL = "bold red"


def get_rich_color(color: COLOR | str) -> str:
    """获取 rich 库支持的颜色字符串

    Args:
        color: COLOR 枚举或颜色字符串

    Returns:
        str: rich 支持的颜色字符串
    """
    if isinstance(color, COLOR):
        return color.value
    return str(color)


# 默认日志级别颜色映射
DEFAULT_LEVEL_COLORS = {
    "DEBUG": COLOR.DEBUG,
    "INFO": COLOR.INFO,
    "WARNING": COLOR.WARNING,
    "ERROR": COLOR.ERROR,
    "CRITICAL": COLOR.CRITICAL,
}
