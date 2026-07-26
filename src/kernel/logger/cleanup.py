"""日志文件自动清理管理器。

按保留天数和最大文件数自动清理旧日志文件，避免磁盘空间持续增长。

清理策略：
1. 按天数清理：删除修改时间超过 max_age_days 的日志文件
2. 按文件数裁剪：当日志文件数量超过 max_files 时，从最旧文件开始删除

清理任务通过 UnifiedScheduler 注册为周期性任务，与系统其他定时任务统一调度。
"""

from __future__ import annotations

import time
from pathlib import Path

from src.kernel.logger import get_logger
from src.kernel.scheduler import TriggerType, get_unified_scheduler

logger = get_logger("logger.cleanup", display="日志清理")


class LoggerCleanupManager:
    """日志文件自动清理管理器。

    根据配置定期扫描日志目录，删除超过保留天数的旧日志文件，
    并在文件数量超过上限时从最旧文件开始裁剪。

    Attributes:
        log_dir: 日志目录路径
        max_age_days: 最大保留天数（0 表示不按时间清理）
        max_files: 最大文件数（0 表示不限制）
        cleanup_interval_hours: 清理任务执行间隔（小时）
        enabled: 是否启用清理
        _cleanup_task_id: 调度器任务 ID
    """

    def __init__(
        self,
        log_dir: str | Path,
        max_age_days: int = 30,
        max_files: int = 100,
        cleanup_interval_hours: float = 6.0,
        enabled: bool = True,
    ) -> None:
        """初始化日志清理管理器。

        Args:
            log_dir: 日志目录路径
            max_age_days: 最大保留天数，0 表示不按时间清理
            max_files: 最大文件数，0 表示不限制
            cleanup_interval_hours: 清理任务执行间隔（小时）
            enabled: 是否启用清理
        """
        self.log_dir = Path(log_dir)
        self.max_age_days = max_age_days
        self.max_files = max_files
        self.cleanup_interval_hours = cleanup_interval_hours
        self.enabled = enabled
        self._cleanup_task_id: str | None = None

    async def start(self) -> None:
        """启动清理调度任务。

        通过 UnifiedScheduler 注册周期性清理任务。
        若 enabled 为 False 则跳过注册。
        """
        if not self.enabled:
            logger.info("日志自动清理已禁用")
            return

        scheduler = get_unified_scheduler()
        interval_seconds = self.cleanup_interval_hours * 3600

        self._cleanup_task_id = await scheduler.create_schedule(
            callback=self._cleanup,
            trigger_type=TriggerType.TIME,
            trigger_config={"delay_seconds": interval_seconds},
            is_recurring=True,
            task_name="log_file_cleanup",
            force_overwrite=True,
        )
        logger.info(
            f"日志自动清理已启动(每 {self.cleanup_interval_hours} 小时, "
            f"保留 {self.max_age_days} 天, 上限 {self.max_files} 文件)"
        )

    async def _cleanup(self) -> None:
        """执行日志清理。

        先按天数删除过期文件，再按文件数裁剪超额文件。
        清理过程中遇到的个别文件删除错误不会中断整体流程。
        """
        if not self.log_dir.exists():
            return

        now = time.time()
        deleted_count = 0

        # 收集所有 .log 文件及其修改时间
        log_files: list[tuple[Path, float]] = []
        for file_path in self.log_dir.iterdir():
            if file_path.is_file() and file_path.suffix == ".log":
                mtime = file_path.stat().st_mtime
                log_files.append((file_path, mtime))

        if not log_files:
            return

        # 按修改时间排序（旧 -> 新）
        log_files.sort(key=lambda x: x[1])

        # 阶段 1：按天数清理
        if self.max_age_days > 0:
            cutoff_time = now - self.max_age_days * 86400
            for file_path, mtime in log_files:
                if mtime < cutoff_time:
                    deleted_count += self._safe_delete(file_path)

        # 重新收集存活文件（阶段1可能已删除部分文件）
        if deleted_count > 0:
            log_files = [(fp, mt) for fp, mt in log_files if fp.exists()]

        # 阶段 2：按文件数裁剪
        if self.max_files > 0 and len(log_files) > self.max_files:
            excess = len(log_files) - self.max_files
            # 从最旧开始删除
            for file_path, _ in log_files[:excess]:
                deleted_count += self._safe_delete(file_path)

        if deleted_count > 0:
            logger.info(f"日志清理完成，删除了 {deleted_count} 个文件")

    @staticmethod
    def _safe_delete(file_path: Path) -> int:
        """安全删除文件，删除失败时记录警告但不抛出异常。

        Args:
            file_path: 要删除的文件路径

        Returns:
            成功删除返回 1，失败返回 0
        """
        try:
            file_path.unlink()
            return 1
        except Exception as e:
            logger.warning(f"删除日志文件失败 {file_path.name}: {e}")
            return 0

    async def stop(self) -> None:
        """停止清理调度任务。

        从调度器中移除已注册的清理任务。
        """
        if self._cleanup_task_id is None:
            return

        scheduler = get_unified_scheduler()
        await scheduler.remove_schedule(self._cleanup_task_id)
        self._cleanup_task_id = None
        logger.info("日志自动清理已停止")
