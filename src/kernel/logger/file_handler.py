"""
日志文件处理器

提供日志文件输出和轮转功能。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import TextIO
from uuid import uuid4


class RotationMode(Enum):
    """日志轮转模式"""

    SIZE = "size"  # 按文件大小轮转
    DATE = "date"  # 按日期轮转
    NEVER = "never"  # 不轮转


class FileHandler:
    """日志文件处理器

    负责将日志写入文件，支持轮转功能。

    Attributes:
        log_dir: 日志目录
        base_filename: 基础文件名
        rotation_mode: 轮转模式
        max_size: 最大文件大小（字节），仅在 SIZE 模式下生效
        _current_file: 当前打开的文件
        _current_date: 当前日期（DATE 模式）
        _lock: 线程锁
    """

    def __init__(
        self,
        log_dir: str | Path = "logs",
        base_filename: str = "app",
        rotation_mode: RotationMode = RotationMode.DATE,
        max_size: int = 10 * 1024 * 1024,  # 默认 10MB
    ) -> None:
        """初始化文件处理器

        Args:
            log_dir: 日志目录路径
            base_filename: 基础文件名（不含扩展名）
            rotation_mode: 轮转模式
            max_size: 最大文件大小（字节），仅在 SIZE 模式下有效
        """
        self.log_dir = Path(log_dir)
        self.base_filename = base_filename
        self.rotation_mode = rotation_mode
        self.max_size = max_size
        self._current_file: TextIO | None = None
        self._current_date: str | None = None
        self._startup_session_id = (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:8]}"
        )
        self._lock = Lock()

        # 确保日志目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_path(self, suffix: str = "") -> Path:
        """获取日志文件路径

        Args:
            suffix: 文件名后缀（用于轮转）

        Returns:
            Path: 日志文件完整路径
        """
        if self.rotation_mode == RotationMode.DATE:
            # 按日期轮转（包含启动会话标识）：app_20250131_143000_123456_2025-01-31.log
            date_str = datetime.now().strftime("%Y-%m-%d")
            filename = (
                f"{self.base_filename}_{self._startup_session_id}_{date_str}{suffix}.log"
            )
        else:
            # 其他模式：app.log 或 app_1.log
            filename = f"{self.base_filename}{suffix}.log"

        return self.log_dir / filename

    def _should_rotate(self) -> bool:
        """检查是否需要轮转

        Returns:
            bool: 是否需要轮转
        """
        if self._current_file is None:
            return True

        if self.rotation_mode == RotationMode.DATE:
            # 检查日期是否变化
            current_date = datetime.now().strftime("%Y-%m-%d")
            return self._current_date != current_date

        elif self.rotation_mode == RotationMode.SIZE:
            # 检查文件大小
            file_path = self._get_log_path()
            if file_path.exists():
                return file_path.stat().st_size >= self.max_size
            return True

        return False

    def _open_file(self) -> TextIO:
        """打开日志文件

        Returns:
            TextIO: 文件对象
        """
        # 如果需要轮转，关闭旧文件
        if self._current_file is not None and self._should_rotate():
            self._close_file()

        # 如果文件未打开，则打开新文件
        if self._current_file is None:
            file_path = self._get_log_path()

            # 按大小轮转时，如果文件已存在且超过大小，添加后缀
            if (
                self.rotation_mode == RotationMode.SIZE
                and file_path.exists()
                and file_path.stat().st_size >= self.max_size
            ):
                # 查找可用的后缀数字
                counter = 1
                while self._get_log_path(f"_{counter}").exists():
                    counter += 1
                file_path = self._get_log_path(f"_{counter}")

            self._current_file = open(file_path, "a", encoding="utf-8")

            # 更新当前日期（DATE 模式）
            if self.rotation_mode == RotationMode.DATE:
                self._current_date = datetime.now().strftime("%Y-%m-%d")

        return self._current_file

    def _close_file(self) -> None:
        """关闭当前日志文件"""
        if self._current_file is not None:
            self._current_file.close()
            self._current_file = None
            self._current_date = None

    def write(self, message: str) -> None:
        """写入日志消息

        Args:
            message: 日志消息（已包含换行符）
        """
        with self._lock:
            try:
                file = self._open_file()
                file.write(message)
                file.flush()  # 立即刷新到磁盘
            except Exception:
                # 如果写入失败，忽略错误，避免影响程序运行
                pass

    def close(self) -> None:
        """关闭文件处理器"""
        with self._lock:
            self._close_file()

    def __repr__(self) -> str:
        """文件处理器字符串表示"""
        return (
            f"FileHandler(dir='{self.log_dir}', "
            f"file='{self.base_filename}', "
            f"rotation={self.rotation_mode.value})"
        )
