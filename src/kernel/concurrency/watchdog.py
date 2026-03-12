"""
WatchDog 监控系统

提供独立线程的异步任务监控，包括心跳检测和任务超时管理。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .task_manager import TaskManager
from .exceptions import WatchDogError

from src.kernel.logger import get_logger, COLOR

logger = get_logger("WatchDog", display="WatchDog", color=COLOR.YELLOW)


@dataclass
class StreamHeartbeat:
    """聊天流心跳信息

    Attributes:
        stream_id: 聊天流 ID
        last_tick: 最后一次心跳时间
        tick_interval: 正常 tick 间隔（秒）
        warning_threshold: 警告阈值（倍数），超过此值输出警告
        restart_threshold: 重启阈值（倍数），超过此值尝试重启
        restart_callback: 重启回调函数
    """

    stream_id: str
    last_tick: datetime = field(default_factory=datetime.now)
    tick_interval: float = 1.0  # 默认 1 秒
    warning_threshold: float = 2.0  # 超过 2 倍警告
    restart_threshold: float = 5.0  # 超过 5 倍重启
    restart_callback: Callable[[], Any] | None = None


class WatchDog:
    """WatchDog 监控系统

    在独立线程中运行，提供以下功能：
    1. 监控聊天流驱动器健康状态（心跳机制）
    2. 清理已完成的任务
    3. 监控非守护任务超时并尝试取消
    4. 自身 tick 健康检查

    Attributes:
        _tick_interval: WatchDog 自身 tick 间隔（秒）
        _running: 是否运行中
        _monitor_thread: 监控线程
        _stream_registry: 聊天流心跳注册表 {stream_id: StreamHeartbeat}
        _last_tick_time: 上次 tick 时间
        _task_manager: TaskManager 实例
    """

    def __init__(self, tick_interval: float = 1.0) -> None:
        """初始化 WatchDog

        Args:
            tick_interval: WatchDog 自身 tick 间隔（秒），默认 1.0
        """
        self._tick_interval = tick_interval
        self._running = False
        self._monitor_thread: threading.Thread | None = None
        self._stream_registry: dict[str, StreamHeartbeat] = {}
        self._last_tick_time: datetime | None = None
        self._task_manager: TaskManager | None = None

    def start(self) -> None:
        """启动 WatchDog 监控线程"""
        if self._running:
            raise WatchDogError("WatchDog is already running")

        self._running = True
        self._monitor_thread = threading.Thread(target=self._run_loop, daemon=True, name="WatchDog")
        self._monitor_thread.start()

        logger.info(f"WatchDog 监控已启动 (tick间隔={self._tick_interval}s)")

    def stop(self) -> None:
        """停止 WatchDog 监控线程"""
        if not self._running:
            return

        self._running = False

        # 等待线程结束
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3.0)

        logger.info("WatchDog 监控已停止")

    def _run_loop(self) -> None:
        """WatchDog 主循环（在独立线程中运行）"""
        self._last_tick_time = datetime.now()

        while self._running:
            try:
                # 记录当前时间
                now = datetime.now()

                # 计算 tick 间隔
                if self._last_tick_time:
                    tick_delta = (now - self._last_tick_time).total_seconds()
                    expected_interval = self._tick_interval

                    # 检查 tick 间隔是否异常
                    if tick_delta > expected_interval * 2:
                        logger.warning(
                            f"WatchDog tick 间隔异常: {tick_delta:.2f}s "
                            f"(预期 {expected_interval}s)",
                        )

                self._last_tick_time = now

                # 执行检查
                self._check_streams()
                self._check_tasks()

                # 等待下一个 tick
                time.sleep(self._tick_interval)

            except Exception as e:
                logger.error(f"WatchDog 循环异常: {e}")
                # 继续运行，不退出

    def _check_streams(self) -> None:
        """检查聊天流健康状态"""
        if not self._stream_registry:
            return

        now = datetime.now()

        # 遍历所有注册的流
        for stream_id, heartbeat in list(self._stream_registry.items()):
            # 计算距离上次心跳的时间
            delta = (now - heartbeat.last_tick).total_seconds()

            # 检查是否超过警告阈值
            if delta > heartbeat.tick_interval * heartbeat.warning_threshold:
                logger.warning(
                    f"聊天流 '{stream_id}' 响应缓慢: "
                    f"距离上次心跳 {delta:.2f}s "
                    f"(正常间隔 {heartbeat.tick_interval}s)",
                )

            # 检查是否超过重启阈值
            if delta > heartbeat.tick_interval * heartbeat.restart_threshold:
                logger.warning(
                    f"聊天流 '{stream_id}' 可能已卡死: "
                    f"距离上次心跳 {delta:.2f}s，尝试重启...",
                )

                # 尝试重启
                if heartbeat.restart_callback:
                    try:
                        heartbeat.restart_callback()
                        logger.info(f"聊天流 '{stream_id}' 重启回调已执行")
                    except Exception as e:
                        logger.error(f"聊天流 '{stream_id}' 重启失败: {e}")

    def _check_tasks(self) -> None:
        """检查任务状态"""
        # 获取 TaskManager 实例
        if self._task_manager is None:
            from .task_manager import get_task_manager

            self._task_manager = get_task_manager()

        # 清理已完成的任务
        self._task_manager.cleanup_tasks()
        # 检查非守护任务超时
        now = datetime.now()
        for task_info in self._task_manager.get_active_tasks():
            # 跳过守护任务
            if task_info.daemon:
                continue

            # 跳过没有超时设置的任务
            if task_info.timeout is None:
                continue

            # 计算任务运行时间
            delta = (now - task_info.created_at).total_seconds()

            # 检查是否超时
            if delta > task_info.timeout:
                logger.warning(
                    f"任务 '{task_info.name}' (id={task_info.task_id[:8]}) "
                    f"超时 ({delta:.2f}s > {task_info.timeout}s)，尝试取消...",
                )

                # 取消任务
                if task_info.cancel():
                    logger.info(f"任务 '{task_info.name}' 已取消")
                else:
                    logger.warning(f"任务 '{task_info.name}' 取消失败")

    def register_stream(
        self,
        stream_id: str,
        tick_interval: float = 1.0,
        warning_threshold: float = 2.0,
        restart_threshold: float = 5.0,
        restart_callback: Callable[[], Any] | None = None,
    ) -> StreamHeartbeat:
        """注册聊天流心跳

        Args:
            stream_id: 聊天流 ID
            tick_interval: 正常 tick 间隔（秒）
            warning_threshold: 警告阈值（倍数）
            restart_threshold: 重启阈值（倍数）
            restart_callback: 重启回调函数

        Returns:
            StreamHeartbeat: 心跳信息对象
        """
        heartbeat = StreamHeartbeat(
            stream_id=stream_id,
            tick_interval=tick_interval,
            warning_threshold=warning_threshold,
            restart_threshold=restart_threshold,
            restart_callback=restart_callback,
        )

        self._stream_registry[stream_id] = heartbeat
        logger.info(f"聊天流 '{stream_id}' 已注册到 WatchDog")

        return heartbeat

    def unregister_stream(self, stream_id: str) -> None:
        """注销聊天流心跳

        Args:
            stream_id: 聊天流 ID
        """
        if stream_id in self._stream_registry:
            del self._stream_registry[stream_id]
            logger.info(f"聊天流 '{stream_id}' 已从 WatchDog 注销")

    def feed_dog(self, stream_id: str) -> None:
        """喂狗（更新心跳时间）

        聊天流驱动器应在每个 tick 中调用此方法发送心跳。

        Args:
            stream_id: 聊天流 ID
        """
        if stream_id in self._stream_registry:
            self._stream_registry[stream_id].last_tick = datetime.now()

    def get_stream_heartbeat(self, stream_id: str) -> StreamHeartbeat | None:
        """获取聊天流心跳信息

        Args:
            stream_id: 聊天流 ID

        Returns:
            StreamHeartbeat | None: 心跳信息，如果未注册则返回 None
        """
        return self._stream_registry.get(stream_id)

    def get_stats(self) -> dict[str, Any]:
        """获取 WatchDog 统计信息

        Returns:
            dict: 统计信息字典
        """
        return {
            "running": self._running,
            "tick_interval": self._tick_interval,
            "registered_streams": len(self._stream_registry),
            "thread_alive": self._monitor_thread.is_alive() if self._monitor_thread else False,
        }

    def __repr__(self) -> str:
        """WatchDog 字符串表示"""
        stats = self.get_stats()
        status = "running" if stats["running"] else "stopped"
        return f"WatchDog(status={status}, streams={stats['registered_streams']})"


# 全局 WatchDog 实例
_watchdog: WatchDog | None = None


def get_watchdog() -> WatchDog:
    """获取全局 WatchDog 实例

    Returns:
        WatchDog: 全局 WatchDog 单例
    """
    global _watchdog
    if _watchdog is None:
        _watchdog = WatchDog()
    return _watchdog
