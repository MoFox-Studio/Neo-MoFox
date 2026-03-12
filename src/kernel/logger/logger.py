"""
统一日志系统

基于 rich 库的日志输出，支持彩色渲染、元数据跟踪和文件输出。
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.traceback import install as install_rich_traceback

from .color import COLOR, get_rich_color
from .file_handler import FileHandler, RotationMode

if TYPE_CHECKING:
    from src.kernel.event import EventBus


@lru_cache(maxsize=1)
def _get_event_bus() -> EventBus:
    """获取全局事件总线实例（使用 lru_cache 实现单例）。"""
    from src.kernel.event import get_event_bus
    return get_event_bus()


# 日志广播事件名称
LOG_OUTPUT_EVENT = "log_output"

# get_logger 默认颜色池（仅当未显式传 color 时使用）
# 使用 16 个十六进制颜色，避免与 COLOR 枚举中的命名颜色重复。
_DEFAULT_NAME_COLOR_PALETTE: tuple[str, ...] = (
    "#5E81AC",
    "#88C0D0",
    "#81A1C1",
    "#8FBCBB",
    "#A3BE8C",
    "#EBCB8B",
    "#D08770",
    "#BF616A",
    "#B48EAD",
    "#7AA2F7",
    "#9ECE6A",
    "#E0AF68",
    "#F7768E",
    "#7DCFFF",
    "#C0CAF5",
    "#BB9AF7",
)


def _get_default_logger_color_by_name(name: str) -> str:
    """根据 logger 名称稳定映射默认颜色。"""
    normalized_name = (name or "").strip().lower() or "default"
    digest = hashlib.md5(normalized_name.encode("utf-8")).digest()
    color_index = digest[0] % len(_DEFAULT_NAME_COLOR_PALETTE)
    return _DEFAULT_NAME_COLOR_PALETTE[color_index]


def _strip_rich_markup(message: str) -> str:
    """移除 Rich markup 标签，返回纯文本。

    该函数仅用于文件日志输出，避免将控制台样式标签写入日志文件。

    Args:
        message: 可能包含 Rich markup 的日志消息

    Returns:
        str: 去除 markup 后的纯文本消息
    """
    try:
        return Text.from_markup(message).plain
    except Exception:
        return message

# 全局配置
_global_config: dict[str, Any] = {
    "log_dir": "logs",
    "log_level": "DEBUG",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    "enable_file": False,
    "file_rotation": RotationMode.DATE,
    "max_file_size": 10 * 1024 * 1024,  # 10MB
    "enable_event_broadcast": True,
}
_config_lock = threading.Lock()

# 全局共享的文件处理器（所有logger共享同一个）
_global_file_handler: FileHandler | None = None
_file_handler_lock = threading.Lock()

# 日志等级优先级映射
_LOG_LEVEL_PRIORITY = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "ERROR": 3,
    "CRITICAL": 4,
}


class Logger:
    """日志记录器

    提供彩色日志输出、元数据跟踪、rich 渲染支持和文件输出。

    Attributes:
        name: 日志记录器名称
        display: 显示名称（用于输出前缀）
        color: 日志颜色
        console: rich.Console 实例
        file_handler: 文件处理器（可选）
        metadata: 元数据字典
        _lock: 线程锁
        _enable_file: 是否启用文件输出
        _log_level: 日志等级
    """

    def __init__(
        self,
        name: str,
        display: str | None = None,
        color: COLOR | str = COLOR.WHITE,
        console: Console | None = None,
        enable_file: bool = False,
        enable_event_broadcast: bool = True,
        log_level: str | None = None,
    ) -> None:
        """初始化日志记录器

        Args:
            name: 日志记录器名称（唯一标识）
            display: 显示名称，如果为 None 则使用 name
            color: 日志颜色
            console: rich.Console 实例，如果为 None 则创建默认实例
            enable_file: 是否启用文件输出（使用全局共享的文件处理器）
            enable_event_broadcast: 是否启用事件广播（发布到 on_log_output 事件）
            log_level: 日志等级，如果为 None 则使用全局配置
        """
        self.name = name
        self.display = display or name
        self.color = get_rich_color(color)
        self.metadata: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._enable_file = enable_file
        self._enable_event_broadcast = enable_event_broadcast

        # 设置日志等级（优先使用传入的，否则使用全局配置）
        with _config_lock:
            self._log_level = (log_level or _global_config["log_level"]).upper()

        # 创建或使用提供的 Console
        if console is None:
            self.console = Console(
                stderr=True,
                highlight=False,
                force_terminal=True,
                legacy_windows=False,
            )
        else:
            self.console = console

    def debug(self, message: str, **kwargs: Any) -> None:
        """输出 DEBUG 级别日志

        Args:
            message: 日志消息
            **kwargs: 额外的元数据
        """
        self._log("DEBUG", message, COLOR.DEBUG, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """输出 INFO 级别日志

        Args:
            message: 日志消息
            **kwargs: 额外的元数据
        """
        self._log("INFO", message, COLOR.INFO, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """输出 WARNING 级别日志

        Args:
            message: 日志消息
            **kwargs: 额外的元数据
        """
        self._log("WARNING", message, COLOR.WARNING, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """输出 ERROR 级别日志

        Args:
            message: 日志消息
            **kwargs: 额外的元数据
        """
        self._log("ERROR", message, COLOR.ERROR, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """输出 CRITICAL 级别日志

        Args:
            message: 日志消息
            **kwargs: 额外的元数据
        """
        self._log("CRITICAL", message, COLOR.CRITICAL, **kwargs)

    def _log(
        self,
        level: str,
        message: str,
        color: COLOR | str,
        **metadata: Any,
    ) -> None:
        """内部日志输出方法

        Args:
            level: 日志级别
            message: 日志消息
            color: 日志颜色
            **metadata: 额外的元数据
        """
        should_output = self._should_log(level)

        with self._lock:
            # 合并元数据
            all_metadata = {**self.metadata, **metadata}
            exc_info = all_metadata.pop("exc_info", None)

            # 构建时间戳
            now = datetime.now()
            timestamp_short = now.strftime("%H:%M:%S")
            timestamp_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            level_color = get_rich_color(color)
            exc_lines: list[str] = []

            if exc_info:
                import traceback

                if exc_info is True:
                    exc_type, exc_val, exc_tb = sys.exc_info()
                    exc_lines = traceback.format_exception(exc_type, exc_val, exc_tb)
                elif isinstance(exc_info, BaseException):
                    exc_lines = traceback.format_exception(
                        type(exc_info),
                        exc_info,
                        exc_info.__traceback__,
                    )
                else:
                    exc_lines = [str(exc_info)]

            # 输出到控制台（按日志级别过滤）
            if should_output:
                # 使用 rich.Text 构建彩色输出
                text = Text()
                text.append(f"[{timestamp_short}] ", style="dim")
                text.append(f"{self.display}", style=self.color)
                text.append(" | ", style="dim")
                text.append(f"{level}", style=level_color)
                text.append(" | ", style="dim")
                try:
                    text.append(Text.from_markup(message))
                except Exception:
                    # 如果 markup 解析失败（例如含有未闭合并非意图作为 markup 的方括号），回退到普通文本
                    text.append(message)

                self.console.print(text)

                # 如果有元数据，显示在下方
                if all_metadata:
                    metadata_str = " | ".join([f"{k}={v}" for k, v in all_metadata.items()])
                    metadata_text = Text(metadata_str, style="dim")
                    self.console.print(metadata_text)

                if exc_lines:
                    exc_text = Text("".join(exc_lines), style="dim")
                    self.console.print(exc_text)

            # 输出到文件（如果启用，不受日志级别过滤）
            if self._enable_file:
                global _global_file_handler
                if _global_file_handler is not None:
                    # 构建纯文本日志（不带颜色代码）
                    plain_message = _strip_rich_markup(message)
                    log_line = f"[{timestamp_short}] {self.display} | {level} | {plain_message}"
                    if all_metadata:
                        metadata_str = " | ".join([f"{k}={v}" for k, v in all_metadata.items()])
                        log_line += f"\n  {metadata_str}"
                    if exc_lines:
                        log_line += "\n" + "".join(exc_lines)
                    log_line += "\n"

                    _global_file_handler.write(log_line)

            # 发布事件广播（如果启用）
            if self._enable_event_broadcast:
                self._emit_log_event(timestamp_iso, level, message, all_metadata)

    def _emit_log_event(
        self,
        timestamp: str,
        level: str,
        message: str,
        metadata: dict[str, Any],
    ) -> None:
        """发布日志事件到事件总线。

        Args:
            timestamp: ISO 格式时间戳
            level: 日志级别
            message: 日志消息
            metadata: 元数据字典
        """
        try:
            # 构建事件数据
            log_data: dict[str, Any] = {
                "timestamp": timestamp,
                "level": level,
                "logger_name": self.name,
                "display": self.display,
                "color": self.color,
                "message": message,
            }

            # 添加元数据（如果有）
            if metadata:
                log_data["metadata"] = dict(metadata)

            # 获取事件总线
            event_bus = _get_event_bus()

            # 尝试发布事件（即发即弃）
            try:
                asyncio.get_running_loop()
                # 有运行中的事件循环
                # 直接使用 ensure_future 安排任务
                asyncio.ensure_future(event_bus.publish(LOG_OUTPUT_EVENT, log_data))
            except RuntimeError:
                # 没有运行中的事件循环
                # 事件广播是可选功能，静默忽略
                pass

        except Exception:
            # 事件广播失败不应影响日志系统本身
            # 静默忽略错误
            pass

    def _should_log(self, level: str) -> bool:
        """检查是否应该输出该级别的日志

        Args:
            level: 日志级别

        Returns:
            bool: 是否应该输出
        """
        level_priority = _LOG_LEVEL_PRIORITY.get(level.upper(), 0)
        current_priority = _LOG_LEVEL_PRIORITY.get(self._log_level, 0)
        return level_priority >= current_priority

    def set_log_level(self, level: str) -> None:
        """设置日志等级

        Args:
            level: 日志等级（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        """
        with self._lock:
            self._log_level = level.upper()

    def get_log_level(self) -> str:
        """获取当前日志等级

        Returns:
            str: 当前日志等级
        """
        return self._log_level

    def set_metadata(self, key: str, value: Any) -> None:
        """设置元数据

        Args:
            key: 元数据键
            value: 元数据值
        """
        with self._lock:
            self.metadata[key] = value

    def get_metadata(self, key: str) -> Any:
        """获取元数据

        Args:
            key: 元数据键

        Returns:
            元数据值，如果不存在则返回 None
        """
        return self.metadata.get(key)

    def clear_metadata(self) -> None:
        """清除所有元数据"""
        with self._lock:
            self.metadata.clear()

    def remove_metadata(self, key: str) -> None:
        """移除指定的元数据

        Args:
            key: 元数据键
        """
        with self._lock:
            self.metadata.pop(key, None)

    def print_panel(
        self,
        message: str,
        title: str | None = None,
        border_style: str | None = None,
    ) -> None:
        """输出面板格式的日志

        Args:
            message: 日志消息
            title: 面板标题
            border_style: 边框样式
        """
        with self._lock:
            if border_style is None:
                border_style = self.color

            panel = Panel(
                message,
                title=title or self.display,
                border_style=border_style,
            )
            self.console.print(panel)

    def print_rich(self, *args: Any, **kwargs: Any) -> None:
        """直接使用 rich 打印

        Args:
            *args: 传递给 console.print 的参数
            **kwargs: 传递给 console.print 的关键字参数
        """
        with self._lock:
            self.console.print(*args, **kwargs)

    def __repr__(self) -> str:
        """日志记录器字符串表示"""
        file_status = "enabled" if self._enable_file else "disabled"
        return (
            f"Logger(name='{self.name}', display='{self.display}', "
            f"color='{self.color}', file={file_status})"
        )


# 全局 logger 注册表
_loggers: dict[str, Logger] = {}
_lock = threading.Lock()


def initialize_logger_system(
    log_dir: str | Path = "logs",
    log_level: str = "DEBUG",
    enable_file: bool = True,
    file_rotation: RotationMode = RotationMode.DATE,
    max_file_size: int = 10 * 1024 * 1024,
    enable_event_broadcast: bool = True,
    log_filename: str = "mofox",
) -> None:
    """初始化日志系统全局配置

    此方法应在核心启动时调用，用于设置全局的日志配置。
    之后创建的所有logger将默认使用这些配置（除非在创建时显式指定）。
    
    所有logger将共享同一个日志文件，不会为每个logger创建单独的文件。

    Args:
        log_dir: 日志文件目录路径
        log_level: 全局日志等级（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        enable_file: 是否默认启用文件输出
        file_rotation: 文件轮转模式
        max_file_size: 单个日志文件最大大小（字节）
        enable_event_broadcast: 是否默认启用事件广播
        log_filename: 日志文件基础名称（所有logger共享）

    Example:
        >>> from src.kernel.logger import initialize_logger_system
        >>> # 在核心启动时调用
        >>> initialize_logger_system(
        ...     log_dir="logs/app",
        ...     log_level="INFO",
        ...     enable_file=True,
        ...     log_filename="mofox",
        ... )
    """
    global _global_file_handler
    
    with _config_lock:
        _global_config["log_dir"] = log_dir
        _global_config["log_level"] = log_level.upper()
        _global_config["enable_file"] = enable_file
        _global_config["file_rotation"] = file_rotation
        _global_config["max_file_size"] = max_file_size
        _global_config["enable_event_broadcast"] = enable_event_broadcast
    
    # 创建或重新创建全局文件处理器
    with _file_handler_lock:
        # 关闭旧的文件处理器（如果存在）
        if _global_file_handler is not None:
            _global_file_handler.close()
        
        # 创建新的文件处理器
        if enable_file:
            _global_file_handler = FileHandler(
                log_dir=log_dir,
                base_filename=log_filename,
                rotation_mode=file_rotation,
                max_size=max_file_size,
            )
        else:
            _global_file_handler = None
    # 安装 rich traceback
    install_rich_traceback_formatter()

def get_global_log_config() -> dict[str, Any]:
    """获取全局日志配置

    Returns:
        dict[str, Any]: 全局日志配置字典
    """
    with _config_lock:
        return dict(_global_config)


def get_logger(
    name: str,
    display: str | None = None,
    color: COLOR | str | None = None,
    console: Console | None = None,
    enable_file: bool | None = None,
    enable_event_broadcast: bool | None = None,
    log_level: str | None = None,
) -> Logger:
    """获取或创建日志记录器
    
    所有logger共享同一个日志文件，文件配置通过 initialize_logger_system() 设置。

    Args:
        name: 日志记录器名称（唯一标识）
        display: 显示名称，如果为 None 则使用 name
        color: 日志颜色；为 None 时根据 name 自动映射默认颜色
        console: rich.Console 实例
        enable_file: 是否启用文件输出（None 则使用全局配置）
        enable_event_broadcast: 是否启用事件广播（None 则使用全局配置）
        log_level: 日志等级（None 则使用全局配置）

    Returns:
        Logger: 日志记录器实例

    Example:
        >>> from src.kernel.logger import get_logger, COLOR, initialize_logger_system
        >>> # 先初始化全局配置
        >>> initialize_logger_system(log_dir="logs", log_level="INFO", enable_file=True)
        >>> # 使用全局配置创建logger
        >>> logger = get_logger("my_logger", display="我的日志", color=COLOR.BLUE)
        >>> logger.info("Hello World!")
        >>> # 覆盖全局配置
        >>> logger2 = get_logger("my_logger2", log_level="DEBUG")
        >>> logger2.debug("这条debug日志会显示")
    """
    with _lock:
        if name not in _loggers:
            # 使用全局配置作为默认值
            with _config_lock:
                actual_enable_file = enable_file if enable_file is not None else _global_config["enable_file"]
                actual_enable_event_broadcast = (
                    enable_event_broadcast if enable_event_broadcast is not None 
                    else _global_config["enable_event_broadcast"]
                )
                actual_log_level = log_level if log_level is not None else _global_config["log_level"]
                actual_color = color if color is not None else _get_default_logger_color_by_name(name)
            
            _loggers[name] = Logger(
                name=name,
                display=display,
                color=actual_color,
                console=console,
                enable_file=actual_enable_file,
                enable_event_broadcast=actual_enable_event_broadcast,
                log_level=actual_log_level,
            )
        return _loggers[name]


def remove_logger(name: str) -> None:
    """移除日志记录器

    Args:
        name: 日志记录器名称
    """
    with _lock:
        _loggers.pop(name, None)


def clear_all_loggers() -> None:
    """清除所有日志记录器"""
    with _lock:
        _loggers.clear()


def get_all_loggers() -> dict[str, Logger]:
    """获取所有日志记录器

    Returns:
        dict[str, Logger]: 所有日志记录器的字典
    """
    with _lock:
        return dict(_loggers)

def shutdown_logger_system() -> None:
    """关闭日志系统，释放所有资源
    
    包括关闭全局文件处理器和清除所有logger。
    建议在程序退出时调用。
    """
    global _global_file_handler

    # 关闭全局文件处理器
    with _file_handler_lock:
        if _global_file_handler is not None:
            _global_file_handler.close()
            _global_file_handler = None


def install_rich_traceback_formatter():
    """安装 rich 的异常格式化

    使用 rich 格式化 Python 异常的回溯信息。
    """
    install_rich_traceback(
        console=Console(stderr=True),
        width=None,
        word_wrap=False,
        show_locals=True,
    )



