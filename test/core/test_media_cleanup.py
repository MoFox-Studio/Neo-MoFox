"""MediaCleanup 媒体清理逻辑单元测试（拆分为子组件后）。

测试 MediaCleanup 拆分后的两套独立清理逻辑：
- 缓存清理（cleanup_pending_folder）：清理 pending 目录中的陈旧文件
- 文件清理（_cleanup_category_folder / cleanup_media_files）：清理 images/emojis/voices/videos 目录

覆盖：
- 按天数删除过期媒体文件
- 按总容量裁剪大文件
- 空目录不报错
- 不存在的目录不报错
- _safe_delete_media 静态方法
- pending 目录按 5 分钟阈值清理
- start_scheduler 合并注册两个清理任务（缓存与文件）的调度逻辑
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.managers.media_manager.cleanup import MediaCleanup
from src.core.managers.media_manager.config import MediaConfig


def _create_media_file(
    folder: Path, name: str, age_seconds: float, content: bytes = b"test"
) -> Path:
    """创建一个具有指定年龄的媒体文件。

    Args:
        folder: 目标文件夹
        name: 文件名
        age_seconds: 文件年龄（秒），通过修改 mtime 实现
        content: 文件内容（字节数据）

    Returns:
        创建的文件路径
    """
    file_path = folder / name
    file_path.write_bytes(content)
    mtime = time.time() - age_seconds
    os.utime(file_path, (mtime, mtime))
    return file_path


@pytest.fixture
def mock_media_cleanup(tmp_path: Path) -> MediaCleanup:
    """创建一个使用临时目录的 MediaCleanup 实例（跳过真实初始化）。

    Args:
        tmp_path: pytest 提供的临时目录

    Returns:
        配置好临时目录的 MediaCleanup 实例
    """
    # 绕过 MediaConfig.__init__（避免触发真实 VLM/ASR/core_config 加载）
    config = MediaConfig.__new__(MediaConfig)

    # 文件夹路径
    config.media_root = tmp_path / "media_cache"
    config.pending_folder = config.media_root / "pending"
    config.images_folder = config.media_root / "images"
    config.emojis_folder = config.media_root / "emojis"
    config.voices_folder = config.media_root / "voices"
    config.videos_folder = config.media_root / "videos"

    for folder in [
        config.pending_folder,
        config.images_folder,
        config.emojis_folder,
    ]:
        folder.mkdir(parents=True, exist_ok=True)

    # 文件清理配置（images/emojis 目录）
    config.media_file_cleanup_enabled = True
    config.media_file_max_age_days = 7
    config.media_file_max_total_size_mb = 500
    config.media_file_cleanup_interval_hours = 1.0
    # 缓存清理配置（pending 目录）
    config.media_cache_cleanup_enabled = True
    config.media_cache_cleanup_interval_hours = 0.5

    cleanup = MediaCleanup(config)
    return cleanup


class TestCleanupCategoryFolder:
    """测试 _cleanup_category_folder 方法。"""

    @pytest.mark.asyncio
    async def test_cleanup_by_age(self, mock_media_cleanup: MediaCleanup) -> None:
        """测试按天数删除过期文件。"""
        folder = mock_media_cleanup._config.images_folder

        old_file1 = _create_media_file(
            folder, "old1.jpg", age_seconds=10 * 86400, content=b"x" * 100
        )
        old_file2 = _create_media_file(
            folder, "old2.jpg", age_seconds=15 * 86400, content=b"x" * 100
        )
        new_file = _create_media_file(
            folder, "new.jpg", age_seconds=1 * 86400, content=b"x" * 100
        )

        mock_media_cleanup._config.media_file_max_age_days = 7
        mock_media_cleanup._config.media_file_max_total_size_mb = 0

        await mock_media_cleanup._cleanup_category_folder(folder)

        assert not old_file1.exists()
        assert not old_file2.exists()
        assert new_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_by_total_size(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试按总容量裁剪大文件。"""
        folder = mock_media_cleanup._config.emojis_folder

        files = []
        for i in range(4):
            f = _create_media_file(
                folder,
                f"emoji_{i}.png",
                age_seconds=i * 3600,
                content=b"x" * (1 * 1024 * 1024),
            )
            files.append(f)

        mock_media_cleanup._config.media_file_max_age_days = 0
        mock_media_cleanup._config.media_file_max_total_size_mb = 2

        await mock_media_cleanup._cleanup_category_folder(folder)

        assert not files[3].exists()
        assert not files[2].exists()
        assert files[1].exists()
        assert files[0].exists()

    @pytest.mark.asyncio
    async def test_cleanup_empty_folder(self, mock_media_cleanup: MediaCleanup) -> None:
        """测试空文件夹不报错。"""
        folder = mock_media_cleanup._config.images_folder

        mock_media_cleanup._config.media_file_max_age_days = 7
        mock_media_cleanup._config.media_file_max_total_size_mb = 500

        await mock_media_cleanup._cleanup_category_folder(folder)

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_folder(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试不存在的文件夹不报错。"""
        folder = mock_media_cleanup._config.media_root / "nonexistent"

        mock_media_cleanup._config.media_file_max_age_days = 7
        mock_media_cleanup._config.media_file_max_total_size_mb = 500

        await mock_media_cleanup._cleanup_category_folder(folder)

    @pytest.mark.asyncio
    async def test_cleanup_max_age_zero_no_deletion(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试 max_age_days=0 时不按天数清理。"""
        folder = mock_media_cleanup._config.images_folder

        old_file = _create_media_file(
            folder, "old.jpg", age_seconds=365 * 86400, content=b"x" * 100
        )

        mock_media_cleanup._config.media_file_max_age_days = 0
        mock_media_cleanup._config.media_file_max_total_size_mb = 0

        await mock_media_cleanup._cleanup_category_folder(folder)

        assert old_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_max_size_zero_no_deletion(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试 max_total_size_mb=0 时不按容量清理。"""
        folder = mock_media_cleanup._config.emojis_folder

        for i in range(5):
            _create_media_file(
                folder,
                f"emoji_{i}.png",
                age_seconds=i * 100,
                content=b"x" * (1 * 1024 * 1024),
            )

        mock_media_cleanup._config.media_file_max_age_days = 0
        mock_media_cleanup._config.media_file_max_total_size_mb = 0

        await mock_media_cleanup._cleanup_category_folder(folder)

        assert len(list(folder.iterdir())) == 5

    @pytest.mark.asyncio
    async def test_cleanup_combined_age_and_size(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试同时按天数和容量清理。"""
        folder = mock_media_cleanup._config.images_folder

        old_files = []
        for i in range(3):
            f = _create_media_file(
                folder,
                f"old_{i}.jpg",
                age_seconds=10 * 86400 + i * 3600,
                content=b"x" * (1 * 1024 * 1024),
            )
            old_files.append(f)

        new_files = []
        for i in range(2):
            f = _create_media_file(
                folder,
                f"new_{i}.jpg",
                age_seconds=i * 3600,
                content=b"x" * (1 * 1024 * 1024),
            )
            new_files.append(f)

        mock_media_cleanup._config.media_file_max_age_days = 7
        mock_media_cleanup._config.media_file_max_total_size_mb = 1

        await mock_media_cleanup._cleanup_category_folder(folder)

        for f in old_files:
            assert not f.exists()

        assert not new_files[1].exists()
        assert new_files[0].exists()

    @pytest.mark.asyncio
    async def test_cleanup_skips_subdirectories(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试清理时跳过子目录。"""
        folder = mock_media_cleanup._config.images_folder

        subfolder = folder / "subfolder"
        subfolder.mkdir()

        old_file = _create_media_file(
            folder, "old.jpg", age_seconds=30 * 86400, content=b"x" * 100
        )

        mock_media_cleanup._config.media_file_max_age_days = 7
        mock_media_cleanup._config.media_file_max_total_size_mb = 0

        await mock_media_cleanup._cleanup_category_folder(folder)

        assert not old_file.exists()
        assert subfolder.exists()

    @pytest.mark.asyncio
    async def test_cleanup_delete_failure_logged(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试删除失败时记录警告但不中断。"""
        folder = mock_media_cleanup._config.images_folder

        _create_media_file(
            folder, "old.jpg", age_seconds=30 * 86400, content=b"x" * 100
        )

        mock_media_cleanup._config.media_file_max_age_days = 7
        mock_media_cleanup._config.media_file_max_total_size_mb = 0

        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("Permission denied")

        Path.unlink = failing_unlink  # type: ignore[method-assign]
        try:
            await mock_media_cleanup._cleanup_category_folder(folder)
        finally:
            Path.unlink = original_unlink  # type: ignore[method-assign]


class TestCleanupMediaFiles:
    """测试 cleanup_media_files 方法（整合 images 与 emojis 目录清理）。"""

    @pytest.mark.asyncio
    async def test_cleanup_media_files_calls_all_categories(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试 cleanup_media_files 调用全部四个分类目录清理。"""
        mock_category = AsyncMock()

        with patch.object(
            mock_media_cleanup, "_cleanup_category_folder", mock_category
        ):
            await mock_media_cleanup.cleanup_media_files()

            # images + emojis + voices + videos = 4 次
            assert mock_category.call_count == 4
            called_folders = [call.args[0] for call in mock_category.call_args_list]
            assert mock_media_cleanup._config.images_folder in called_folders
            assert mock_media_cleanup._config.emojis_folder in called_folders
            assert mock_media_cleanup._config.voices_folder in called_folders
            assert mock_media_cleanup._config.videos_folder in called_folders


class TestCleanupPendingFolder:
    """测试 cleanup_pending_folder 方法（独立调度，按 5 分钟阈值清理）。"""

    @pytest.mark.asyncio
    async def test_pending_cleanup_deletes_old_files(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试删除超过 5 分钟未处理的 pending 文件。"""
        folder = mock_media_cleanup._config.pending_folder

        old_file = _create_media_file(
            folder, "old_pending.jpg", age_seconds=600, content=b"x" * 100
        )
        new_file = _create_media_file(
            folder, "new_pending.jpg", age_seconds=60, content=b"x" * 100
        )

        await mock_media_cleanup.cleanup_pending_folder()

        assert not old_file.exists()
        assert new_file.exists()

    @pytest.mark.asyncio
    async def test_pending_cleanup_keeps_recent_files(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试保留不足 5 分钟的 pending 文件。"""
        folder = mock_media_cleanup._config.pending_folder

        recent_file = _create_media_file(
            folder, "recent.jpg", age_seconds=120, content=b"x" * 50
        )

        await mock_media_cleanup.cleanup_pending_folder()

        assert recent_file.exists()

    @pytest.mark.asyncio
    async def test_pending_cleanup_empty_folder(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试空 pending 文件夹不报错。"""
        await mock_media_cleanup.cleanup_pending_folder()

    @pytest.mark.asyncio
    async def test_pending_cleanup_nonexistent_folder(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试 pending 目录不存在时不报错。"""
        mock_media_cleanup._config.pending_folder = (
            mock_media_cleanup._config.media_root / "absent"
        )
        await mock_media_cleanup.cleanup_pending_folder()

    @pytest.mark.asyncio
    async def test_pending_cleanup_skips_subdirectories(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试 pending 清理时跳过子目录。"""
        folder = mock_media_cleanup._config.pending_folder

        subfolder = folder / "subfolder"
        subfolder.mkdir()

        await mock_media_cleanup.cleanup_pending_folder()

        assert subfolder.exists()


class TestStartCleanupScheduler:
    """测试 start_scheduler 方法。

    合并注册缓存清理（pending 目录）与文件清理（images/emojis 目录）
    两个任务到同一方法，分别按各自的 enabled 开关决定是否注册。
    """

    @pytest.mark.asyncio
    async def test_start_cleanup_when_both_enabled(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试两项均启用时同时注册两个清理任务。"""
        mock_scheduler = MagicMock()
        mock_scheduler.create_schedule = AsyncMock(
            side_effect=["cache_schedule_id", "file_schedule_id"],
        )

        with patch(
            "src.core.managers.media_manager.cleanup.get_unified_scheduler",
            return_value=mock_scheduler,
        ):
            await mock_media_cleanup.start_scheduler()

            assert mock_scheduler.create_schedule.call_count == 2
            cache_kwargs = mock_scheduler.create_schedule.call_args_list[0].kwargs
            file_kwargs = mock_scheduler.create_schedule.call_args_list[1].kwargs
            assert cache_kwargs["task_name"] == "media_cache_cleanup"
            assert cache_kwargs["is_recurring"] is True
            assert file_kwargs["task_name"] == "media_file_cleanup"
            assert file_kwargs["is_recurring"] is True
            assert mock_media_cleanup.cache_cleanup_task_id == "cache_schedule_id"
            assert mock_media_cleanup.file_cleanup_task_id == "file_schedule_id"

    @pytest.mark.asyncio
    async def test_start_cleanup_skips_cache_when_disabled(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试禁用缓存清理时只注册文件清理任务。"""
        mock_media_cleanup._config.media_cache_cleanup_enabled = False

        mock_scheduler = MagicMock()
        mock_scheduler.create_schedule = AsyncMock(return_value="file_schedule_id")

        with patch(
            "src.core.managers.media_manager.cleanup.get_unified_scheduler",
            return_value=mock_scheduler,
        ):
            await mock_media_cleanup.start_scheduler()

            assert mock_scheduler.create_schedule.call_count == 1
            call_kwargs = mock_scheduler.create_schedule.call_args.kwargs
            assert call_kwargs["task_name"] == "media_file_cleanup"
            assert mock_media_cleanup.cache_cleanup_task_id is None
            assert mock_media_cleanup.file_cleanup_task_id == "file_schedule_id"

    @pytest.mark.asyncio
    async def test_start_cleanup_skips_file_when_disabled(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试禁用文件清理时只注册缓存清理任务。"""
        mock_media_cleanup._config.media_file_cleanup_enabled = False

        mock_scheduler = MagicMock()
        mock_scheduler.create_schedule = AsyncMock(return_value="cache_schedule_id")

        with patch(
            "src.core.managers.media_manager.cleanup.get_unified_scheduler",
            return_value=mock_scheduler,
        ):
            await mock_media_cleanup.start_scheduler()

            assert mock_scheduler.create_schedule.call_count == 1
            call_kwargs = mock_scheduler.create_schedule.call_args.kwargs
            assert call_kwargs["task_name"] == "media_cache_cleanup"
            assert mock_media_cleanup.cache_cleanup_task_id == "cache_schedule_id"
            assert mock_media_cleanup.file_cleanup_task_id is None

    @pytest.mark.asyncio
    async def test_start_cleanup_skips_all_when_both_disabled(
        self, mock_media_cleanup: MediaCleanup
    ) -> None:
        """测试两项均禁用时不注册任何清理任务。"""
        mock_media_cleanup._config.media_cache_cleanup_enabled = False
        mock_media_cleanup._config.media_file_cleanup_enabled = False

        mock_scheduler = MagicMock()
        mock_scheduler.create_schedule = AsyncMock()

        with patch(
            "src.core.managers.media_manager.cleanup.get_unified_scheduler",
            return_value=mock_scheduler,
        ):
            await mock_media_cleanup.start_scheduler()

            mock_scheduler.create_schedule.assert_not_called()
            assert mock_media_cleanup.cache_cleanup_task_id is None
            assert mock_media_cleanup.file_cleanup_task_id is None


class TestSafeDeleteMedia:
    """测试 _safe_delete_media 静态方法。"""

    def test_safe_delete_success(self, tmp_path: Path) -> None:
        """测试成功删除文件返回 1。"""
        file_path = tmp_path / "test.png"
        file_path.write_bytes(b"content")

        result = MediaCleanup._safe_delete_media(file_path)

        assert result == 1
        assert not file_path.exists()

    def test_safe_delete_failure(self, tmp_path: Path) -> None:
        """测试删除失败返回 0。"""
        file_path = tmp_path / "nonexistent.png"

        result = MediaCleanup._safe_delete_media(file_path)

        assert result == 0
