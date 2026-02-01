"""
统一日志系统

基于 rich 库的日志输出，支持彩色渲染、元数据跟踪和文件输出。
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.traceback import install as install_rich_traceback

from .color import COLOR, get_rich_color
from .file_handler import FileHandler, RotationMode

# 事件总线（延迟导入避免循环依赖）
_event_bus = None


def _get_event_bus():
    """获取全局事件总线实例。"""
    global _event_bus
    if _event_bus is None:
        from src.kernel.event import event_bus
        _event_bus = event_bus
    return _event_bus


# 日志广播事件名称
LOG_OUTPUT_EVENT = "log_output"


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
    """

    def __init__(
        self,
        name: str,
        display: str | None = None,
        color: COLOR | str = COLOR.WHITE,
        console: Console | None = None,
        enable_file: bool = False,
        log_dir: str | Path = "logs",
        file_rotation: RotationMode = RotationMode.DATE,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        enable_event_broadcast: bool = True,
    ) -> None:
        """初始化日志记录器

        Args:
            name: 日志记录器名称（唯一标识）
            display: 显示名称，如果为 None 则使用 name
            color: 日志颜色
            console: rich.Console 实例，如果为 None 则创建默认实例
            enable_file: 是否启用文件输出
            log_dir: 日志文件目录
            file_rotation: 文件轮转模式
            max_file_size: 单个日志文件最大大小（字节），仅在 SIZE 模式下生效
            enable_event_broadcast: 是否启用事件广播（发布到 on_log_output 事件）
        """
        self.name = name
        self.display = display or name
        self.color = get_rich_color(color)
        self.metadata: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._enable_file = enable_file
        self._enable_event_broadcast = enable_event_broadcast

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

        # 创建文件处理器（如果启用）
        self.file_handler: FileHandler | None = None
        if enable_file:
            self.file_handler = FileHandler(
                log_dir=log_dir,
                base_filename=name,
                rotation_mode=file_rotation,
                max_size=max_file_size,
            )

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
        with self._lock:
            # 合并元数据
            all_metadata = {**self.metadata, **metadata}

            # 构建时间戳
            now = datetime.now()
            timestamp_short = now.strftime("%H:%M:%S")
            timestamp_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            level_color = get_rich_color(color)

            # 使用 rich.Text 构建彩色输出
            text = Text()
            text.append(f"[{timestamp_short}] ", style="dim")
            text.append(f"{self.display}", style=self.color)
            text.append(" | ", style="dim")
            text.append(f"{level}", style=level_color)
            text.append(" | ", style="dim")
            text.append(message)

            # 输出到控制台
            self.console.print(text)

            # 如果有元数据，显示在下方
            if all_metadata:
                metadata_str = " | ".join([f"{k}={v}" for k, v in all_metadata.items()])
                metadata_text = Text(metadata_str, style="dim")
                self.console.print(metadata_text)

            # 输出到文件（如果启用）
            if self._enable_file and self.file_handler:
                # 构建纯文本日志（不带颜色代码）
                log_line = f"[{timestamp_short}] {self.display} | {level} | {message}"
                if all_metadata:
                    metadata_str = " | ".join([f"{k}={v}" for k, v in all_metadata.items()])
                    log_line += f"\n  {metadata_str}"
                log_line += "\n"

                self.file_handler.write(log_line)

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

            # 创建事件
            from src.kernel.event import Event
            event = Event(name=LOG_OUTPUT_EVENT, data=log_data, source=self.name)

            # 尝试发布事件（即发即弃）
            try:
                loop = asyncio.get_running_loop()
                # 有运行中的事件循环
                # 直接使用 ensure_future 安排任务
                asyncio.ensure_future(event_bus.publish(event))
            except RuntimeError:
                # 没有运行中的事件循环
                # 事件广播是可选功能，静默忽略
                pass

        except Exception:
            # 事件广播失败不应影响日志系统本身
            # 静默忽略错误
            pass

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

    def enable_file_output(
        self,
        log_dir: str | Path = "logs",
        file_rotation: RotationMode = RotationMode.DATE,
        max_file_size: int = 10 * 1024 * 1024,
    ) -> None:
        """启用文件输出

        Args:
            log_dir: 日志文件目录
            file_rotation: 文件轮转模式
            max_file_size: 单个日志文件最大大小（字节）
        """
        with self._lock:
            if self.file_handler is None:
                self.file_handler = FileHandler(
                    log_dir=log_dir,
                    base_filename=self.name,
                    rotation_mode=file_rotation,
                    max_size=max_file_size,
                )
                self._enable_file = True

    def disable_file_output(self) -> None:
        """禁用文件输出"""
        with self._lock:
            self._enable_file = False
            if self.file_handler:
                self.file_handler.close()
                self.file_handler = None

    def close(self) -> None:
        """关闭日志记录器，释放资源"""
        with self._lock:
            if self.file_handler:
                self.file_handler.close()
                self.file_handler = None

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


def get_logger(
    name: str,
    display: str | None = None,
    color: COLOR | str = COLOR.WHITE,
    console: Console | None = None,
    enable_file: bool = False,
    log_dir: str | Path = "logs",
    file_rotation: RotationMode = RotationMode.DATE,
    max_file_size: int = 10 * 1024 * 1024,
    enable_event_broadcast: bool = True,
) -> Logger:
    """获取或创建日志记录器

    Args:
        name: 日志记录器名称（唯一标识）
        display: 显示名称，如果为 None 则使用 name
        color: 日志颜色
        console: rich.Console 实例
        enable_file: 是否启用文件输出
        log_dir: 日志文件目录
        file_rotation: 文件轮转模式
        max_file_size: 单个日志文件最大大小（字节）
        enable_event_broadcast: 是否启用事件广播（发布到 on_log_output 事件）

    Returns:
        Logger: 日志记录器实例

    Example:
        >>> from src.kernel.logger import get_logger, COLOR, RotationMode
        >>> # 不启用文件输出
        >>> logger = get_logger("my_logger", display="我的日志", color=COLOR.BLUE)
        >>> logger.info("Hello World!")
        >>> # 启用文件输出
        >>> logger = get_logger("my_logger", enable_file=True, file_rotation=RotationMode.DATE)
        >>> logger.info("这条日志会同时输出到控制台和文件")
        >>> # 启用事件广播
        >>> logger = get_logger("my_logger", enable_event_broadcast=True)
        >>> logger.info("这条日志会广播到事件系统")
    """
    with _lock:
        if name not in _loggers:
            _loggers[name] = Logger(
                name=name,
                display=display,
                color=color,
                console=console,
                enable_file=enable_file,
                log_dir=log_dir,
                file_rotation=file_rotation,
                max_file_size=max_file_size,
                enable_event_broadcast=enable_event_broadcast,
            )
        return _loggers[name]


def remove_logger(name: str) -> None:
    """移除日志记录器

    Args:
        name: 日志记录器名称
    """
    with _lock:
        logger = _loggers.pop(name, None)
        if logger:
            logger.close()


def get_all_loggers() -> dict[str, Logger]:
    """获取所有日志记录器

    Returns:
        dict[str, Logger]: 所有日志记录器的字典
    """
    with _lock:
        return dict(_loggers)


def clear_all_loggers() -> None:
    """清除所有日志记录器"""
    with _lock:
        for logger in _loggers.values():
            logger.close()
        _loggers.clear()


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


# 自动安装 rich traceback
install_rich_traceback_formatter()
