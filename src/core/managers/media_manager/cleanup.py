"""媒体清理组件。

提供两套独立的清理逻辑：
- **缓存清理**（pending 目录）：高频清理 5 分钟内未处理的待识别文件
- **文件清理**（images/emojis/voices/videos 目录）：按用户配置间隔，
  根据文件年龄和总容量两个维度清理已识别文件

两类清理各自拥有独立的配置项与调度任务，互不影响。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from src.core.managers.media_manager.config import MediaConfig
from src.kernel.logger import get_logger
from src.kernel.scheduler import TriggerType, get_unified_scheduler

logger = get_logger("media_manager")


class MediaCleanup:
    """媒体清理调度与执行。

    Args:
        config: 提供文件夹路径与清理配置的配置组件
    """

    def __init__(self, config: MediaConfig) -> None:
        self._config = config
        # 两个独立的清理任务：缓存清理（pending 目录）与文件清理（images/emojis 目录）
        self.cache_cleanup_task_id: str | None = None
        self.file_cleanup_task_id: str | None = None

    async def start_scheduler(self) -> None:
        """启动两个独立的定时清理任务。

        - 缓存清理任务：高频清理 pending 目录中的陈旧文件
        - 文件清理任务：按用户配置间隔清理 images/emojis 目录中的已识别文件
        """
        scheduler = get_unified_scheduler()

        if self._config.media_cache_cleanup_enabled:
            try:
                interval_seconds = (
                    self._config.media_cache_cleanup_interval_hours * 3600
                )
                schedule_id = await scheduler.create_schedule(
                    callback=self.cleanup_pending_folder,
                    trigger_type=TriggerType.TIME,
                    trigger_config={"delay_seconds": interval_seconds},
                    is_recurring=True,
                    task_name="media_cache_cleanup",
                    force_overwrite=True,
                )
                self.cache_cleanup_task_id = schedule_id
                logger.info(
                    f"媒体缓存清理任务已注册(间隔 {self._config.media_cache_cleanup_interval_hours}h): {schedule_id}"
                )
            except Exception as e:
                logger.error(f"注册缓存清理任务失败: {e}")
        else:
            logger.info("媒体缓存清理已禁用，跳过 pending 目录清理任务注册")

        if self._config.media_file_cleanup_enabled:
            try:
                interval_seconds = self._config.media_file_cleanup_interval_hours * 3600
                schedule_id = await scheduler.create_schedule(
                    callback=self.cleanup_media_files,
                    trigger_type=TriggerType.TIME,
                    trigger_config={"delay_seconds": interval_seconds},
                    is_recurring=True,
                    task_name="media_file_cleanup",
                    force_overwrite=True,
                )
                self.file_cleanup_task_id = schedule_id
                logger.info(
                    f"媒体文件清理任务已注册(间隔 {self._config.media_file_cleanup_interval_hours}h): {schedule_id}"
                )
            except Exception as e:
                logger.error(f"注册文件清理任务失败: {e}")
        else:
            logger.info("媒体文件清理已禁用，跳过 images/emojis 目录清理任务注册")

        logger.info(
            "媒体清理调度器已启动"
            f"(缓存清理每 {self._config.media_cache_cleanup_interval_hours}h，"
            f"文件清理每 {self._config.media_file_cleanup_interval_hours}h)"
        )

    async def cleanup_pending_folder(self) -> None:
        """清理待识别文件夹中的陈旧文件。

        删除 pending 目录中超过 5 分钟（300 秒）未被处理的文件。
        该任务独立调度，不受 images/emojis 目录清理配置影响。
        """
        try:
            if not self._config.pending_folder.exists():
                return

            current_time = time.time()
            cleanup_count = 0

            for file_path in self._config.pending_folder.iterdir():
                if not file_path.is_file():
                    continue

                file_mtime = file_path.stat().st_mtime

                if current_time - file_mtime >= 300:  # 5分钟 = 300秒
                    try:
                        file_path.unlink()
                        cleanup_count += 1
                    except Exception as e:
                        logger.warning(f"删除文件失败 {file_path.name}: {e}")

            if cleanup_count > 0:
                logger.info(f"媒体缓存清理完成，删除了 {cleanup_count} 个陈旧文件")
        except Exception as e:
            logger.error(f"清理待识别文件夹失败: {e}")

    async def cleanup_media_files(self) -> None:
        """清理 images/emojis/voices/videos 目录中的已识别媒体文件。

        整合四个分类目录的清理，各目录按文件年龄和总容量两个维度清理：
        1. 删除超过 ``media_file_max_age_days`` 的文件
        2. 若总容量超过 ``media_file_max_total_size_mb``，从最旧文件开始删除直到达标
        """
        await self._cleanup_category_folder(self._config.images_folder)
        await self._cleanup_category_folder(self._config.emojis_folder)
        await self._cleanup_category_folder(self._config.voices_folder)
        await self._cleanup_category_folder(self._config.videos_folder)

    async def _cleanup_category_folder(self, folder: Path) -> None:
        """清理已识别媒体分类文件夹中的陈旧文件。

        按文件年龄和总容量两个维度清理：
        1. 删除超过 ``media_file_max_age_days`` 的文件
        2. 若总容量超过 ``media_file_max_total_size_mb``，从最旧文件开始删除直到达标

        Args:
            folder: 要清理的文件夹路径
        """
        try:
            if not folder.exists():
                return

            now = time.time()
            deleted_count = 0

            files: list[tuple[Path, float, int]] = []
            for file_path in folder.iterdir():
                if not file_path.is_file():
                    continue
                stat = file_path.stat()
                files.append((file_path, stat.st_mtime, stat.st_size))

            if not files:
                return

            files.sort(key=lambda x: x[1])

            if self._config.media_file_max_age_days > 0:
                cutoff_time = now - self._config.media_file_max_age_days * 86400
                for file_path, mtime, _ in files:
                    if mtime < cutoff_time:
                        deleted_count += self._safe_delete_media(file_path)

            if deleted_count > 0:
                files = [(fp, mt, sz) for fp, mt, sz in files if fp.exists()]

            if self._config.media_file_max_total_size_mb > 0:
                max_bytes = self._config.media_file_max_total_size_mb * 1024 * 1024
                total_size = sum(sz for _, _, sz in files)
                if total_size > max_bytes:
                    for file_path, _, _ in files:
                        if total_size <= max_bytes:
                            break
                        try:
                            size = file_path.stat().st_size
                            file_path.unlink()
                            total_size -= size
                            deleted_count += 1
                        except Exception as e:
                            logger.warning(f"裁剪媒体文件失败 {file_path.name}: {e}")

            if deleted_count > 0:
                logger.info(
                    f"媒体文件清理完成 [{folder.name}]，删除了 {deleted_count} 个文件"
                )
        except Exception as e:
            logger.error(f"清理媒体分类文件夹失败 [{folder.name}]: {e}")

    @staticmethod
    def _safe_delete_media(file_path: Path) -> int:
        """安全删除媒体文件，删除失败时记录警告但不抛出异常。

        Args:
            file_path: 要删除的文件路径

        Returns:
            成功删除返回 1，失败返回 0
        """
        try:
            file_path.unlink()
            return 1
        except Exception as e:
            logger.warning(f"删除媒体文件失败 {file_path.name}: {e}")
            return 0


def start_cleanup_scheduler_async(cleanup: MediaCleanup) -> None:
    """在事件循环中异步启动清理调度器。

    包装 :meth:`MediaCleanup.start_scheduler` 为 asyncio task，
    供 :class:`~src.core.managers.media_manager.manager.MediaManager` 在
    构造时非阻塞启动使用。

    Args:
        cleanup: 清理组件实例
    """
    try:
        asyncio.create_task(cleanup.start_scheduler())
    except Exception as e:
        logger.error(f"启动清理调度器失败: {e}")
