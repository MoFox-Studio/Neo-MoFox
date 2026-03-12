"""信号处理器

处理系统信号（SIGINT, SIGTERM）以实现优雅关闭。
"""

from __future__ import annotations

import asyncio
import signal
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import Bot


class SignalHandler:
    """信号处理器

    监听系统信号并协调 Bot 的优雅关闭。

    行为：
    - 第一次 SIGINT (Ctrl+C)：请求优雅关闭
    - 3 秒内第二次 SIGINT：强制立即关闭

    Attributes:
        bot: Bot 实例
        shutdown_requested: 关闭请求事件
        last_signal_time: 上次信号时间戳
        signal_count: 信号计数
    """

    def __init__(self, bot: "Bot") -> None:
        """初始化信号处理器

        Args:
            bot: Bot 实例
        """
        self.bot = bot
        self.shutdown_requested = asyncio.Event()
        self.last_signal_time = 0.0
        self.signal_count = 0
        self._lock = threading.Lock()
        self._original_handlers: dict[int, Any] = {}

    def register_signals(self) -> None:
        """注册信号处理器

        注册 SIGINT 和 SIGTERM 处理器。
        """
        # 注册 SIGINT (Ctrl+C)
        self._original_handlers[signal.SIGINT] = signal.signal(
            signal.SIGINT, self._handle_signal
        )

        # 注册 SIGTERM
        try:
            self._original_handlers[signal.SIGTERM] = signal.signal(
                signal.SIGTERM, self._handle_signal
            )
        except ValueError:
            # SIGTERM 在某些平台可能不可用
            pass

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """处理信号

        Args:
            signum: 信号编号
            frame: 当前堆栈帧
        """
        with self._lock:
            current_time = time.time()

            # 检查是否在 3 秒内多次触发
            if current_time - self.last_signal_time < 3.0:
                self.signal_count += 1
            else:
                self.signal_count = 1

            self.last_signal_time = current_time

            # 第一次信号：请求优雅关闭
            assert self.bot.logger is not None
            if self.signal_count == 1:
                self.bot.logger.info(
                    "已收到关闭信号。再次按下Ctrl+C强制退出..."
                )
                self.shutdown_requested.set()

                # 设置 _running 标志，让主循环自然退出
                self.bot._running = False

            # 第二次信号（3 秒内）：强制立即关闭
            elif self.signal_count >= 2:
                self.bot.logger.warning("正在强制关闭...")
                # 强制退出（不执行清理）
                import sys

                sys.exit(1)

    def restore_handlers(self) -> None:
        """恢复原始信号处理器"""
        for sig, handler in self._original_handlers.items():
            signal.signal(sig, handler)
        self._original_handlers.clear()

    async def wait_for_shutdown_signal(self) -> None:
        """等待关闭信号

        阻塞直到收到关闭信号。
        """
        await self.shutdown_requested.wait()

    def is_shutdown_requested(self) -> bool:
        """检查是否已请求关闭

        Returns:
            bool: 是否已请求关闭
        """
        return self.shutdown_requested.is_set()

    def reset(self) -> None:
        """重置信号处理器状态

        清除关闭请求和计数器。
        """
        self.shutdown_requested.clear()
        self.signal_count = 0
        self.last_signal_time = 0.0

    def __del__(self) -> None:
        """析构函数，恢复原始信号处理器"""
        self.restore_handlers()


__all__ = ["SignalHandler"]
