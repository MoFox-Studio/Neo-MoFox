"""MediaManager 媒体缓存清理逻辑单元测试。

测试 MediaManager 的 _cleanup_category_folder 和 _cleanup_all_media 方法，包括：
- 按天数删除过期媒体文件
- 按总容量裁剪大文件
- 空目录不报错
- 不存在的目录不报错
- _safe_delete_media 静态方法
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.managers.media_manager import MediaManager


def _create_media_file(folder: Path, name: str, age_seconds: float, content: bytes = b"test") -> Path:
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
    # 修改文件修改时间
    mtime = time.time() - age_seconds
    os.utime(file_path, (mtime, mtime))
    return file_path


@pytest.fixture
def mock_media_manager(tmp_path: Path) -> MediaManager:
    """创建一个使用临时目录的 MediaManager 实例（跳过真实初始化）。

    Args:
        tmp_path: pytest 提供的临时目录

    Returns:
        配置好临时目录的 MediaManager 实例
    """
    # 使用 MagicMock 绕过 __init__ 的真实初始化
    manager = MediaManager.__new__(MediaManager)

    # 手动设置必要的文件夹路径
    manager.media_root = tmp_path / "media_cache"
    manager.pending_folder = manager.media_root / "pending"
    manager.images_folder = manager.media_root / "images"
    manager.emojis_folder = manager.media_root / "emojis"

    # 创建文件夹
    for folder in [manager.pending_folder, manager.images_folder, manager.emojis_folder]:
        folder.mkdir(parents=True, exist_ok=True)

    # 设置清理配置
    manager._media_cleanup_enabled = True
    manager._media_max_age_days = 7
    manager._media_max_total_size_mb = 500
    manager._media_cleanup_interval_hours = 1.0
    manager._cleanup_task_id = None

    return manager


class TestCleanupCategoryFolder:
    """测试 _cleanup_category_folder 方法。"""

    @pytest.mark.asyncio
    async def test_cleanup_by_age(self, mock_media_manager: MediaManager) -> None:
        """测试按天数删除过期文件。"""
        folder = mock_media_manager.images_folder

        # 创建过期文件（10天前）
        old_file1 = _create_media_file(folder, "old1.jpg", age_seconds=10 * 86400, content=b"x" * 100)
        old_file2 = _create_media_file(folder, "old2.jpg", age_seconds=15 * 86400, content=b"x" * 100)
        # 创建新文件（1天前）
        new_file = _create_media_file(folder, "new.jpg", age_seconds=1 * 86400, content=b"x" * 100)

        mock_media_manager._media_max_age_days = 7
        mock_media_manager._media_max_total_size_mb = 0  # 禁用容量清理

        await mock_media_manager._cleanup_category_folder(folder)

        assert not old_file1.exists()
        assert not old_file2.exists()
        assert new_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_by_total_size(self, mock_media_manager: MediaManager) -> None:
        """测试按总容量裁剪大文件。"""
        folder = mock_media_manager.emojis_folder

        # 创建 4 个文件，每个 1MB，age 递增确保 mtime 不同（i=0 最新，i=3 最旧）
        files = []
        for i in range(4):
            f = _create_media_file(
                folder,
                f"emoji_{i}.png",
                age_seconds=i * 3600,
                content=b"x" * (1 * 1024 * 1024),  # 1MB
            )
            files.append(f)

        # 总容量 4MB，上限设为 2MB
        mock_media_manager._media_max_age_days = 0  # 禁用天数清理
        mock_media_manager._media_max_total_size_mb = 2  # 2MB 上限

        await mock_media_manager._cleanup_category_folder(folder)

        # 按 mtime 排序（旧->新）：files[3](3h), files[2](2h), files[1](1h), files[0](0h)
        # 从最旧开始删除，删除 2 个后总容量降到 2MB
        assert not files[3].exists()
        assert not files[2].exists()
        assert files[1].exists()
        assert files[0].exists()

    @pytest.mark.asyncio
    async def test_cleanup_empty_folder(self, mock_media_manager: MediaManager) -> None:
        """测试空文件夹不报错。"""
        folder = mock_media_manager.images_folder

        mock_media_manager._media_max_age_days = 7
        mock_media_manager._media_max_total_size_mb = 500

        await mock_media_manager._cleanup_category_folder(folder)
        # 不报错即通过

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_folder(self, mock_media_manager: MediaManager) -> None:
        """测试不存在的文件夹不报错。"""
        folder = mock_media_manager.media_root / "nonexistent"

        mock_media_manager._media_max_age_days = 7
        mock_media_manager._media_max_total_size_mb = 500

        await mock_media_manager._cleanup_category_folder(folder)
        # 不报错即通过

    @pytest.mark.asyncio
    async def test_cleanup_max_age_zero_no_deletion(self, mock_media_manager: MediaManager) -> None:
        """测试 max_age_days=0 时不按天数清理。"""
        folder = mock_media_manager.images_folder

        old_file = _create_media_file(folder, "old.jpg", age_seconds=365 * 86400, content=b"x" * 100)

        mock_media_manager._media_max_age_days = 0
        mock_media_manager._media_max_total_size_mb = 0

        await mock_media_manager._cleanup_category_folder(folder)

        assert old_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_max_size_zero_no_deletion(self, mock_media_manager: MediaManager) -> None:
        """测试 max_total_size_mb=0 时不按容量清理。"""
        folder = mock_media_manager.emojis_folder

        # 创建多个大文件
        for i in range(5):
            _create_media_file(
                folder,
                f"emoji_{i}.png",
                age_seconds=i * 100,
                content=b"x" * (1 * 1024 * 1024),
            )

        mock_media_manager._media_max_age_days = 0
        mock_media_manager._media_max_total_size_mb = 0

        await mock_media_manager._cleanup_category_folder(folder)

        # 所有文件都应保留
        assert len(list(folder.iterdir())) == 5

    @pytest.mark.asyncio
    async def test_cleanup_combined_age_and_size(self, mock_media_manager: MediaManager) -> None:
        """测试同时按天数和容量清理。"""
        folder = mock_media_manager.images_folder

        # 创建 3 个过期文件（10天前），每个 1MB，age 递增确保 mtime 不同
        old_files = []
        for i in range(3):
            f = _create_media_file(
                folder,
                f"old_{i}.jpg",
                age_seconds=10 * 86400 + i * 3600,
                content=b"x" * (1 * 1024 * 1024),
            )
            old_files.append(f)

        # 创建 2 个新文件（1天内），每个 1MB，i=0 最新，i=1 最旧
        new_files = []
        for i in range(2):
            f = _create_media_file(
                folder,
                f"new_{i}.jpg",
                age_seconds=i * 3600,
                content=b"x" * (1 * 1024 * 1024),
            )
            new_files.append(f)

        mock_media_manager._media_max_age_days = 7
        mock_media_manager._media_max_total_size_mb = 1  # 只保留 1MB

        await mock_media_manager._cleanup_category_folder(folder)

        # 所有旧文件应被删除（按天数）
        for f in old_files:
            assert not f.exists()

        # 新文件按 mtime 排序（旧->新）：new_files[1](1h), new_files[0](0h)
        # 只保留 1MB，所以从最旧开始删除，删除 new_files[1] 后剩 1MB
        assert not new_files[1].exists()
        assert new_files[0].exists()

    @pytest.mark.asyncio
    async def test_cleanup_skips_subdirectories(self, mock_media_manager: MediaManager) -> None:
        """测试清理时跳过子目录。"""
        folder = mock_media_manager.images_folder

        # 创建一个子目录
        subfolder = folder / "subfolder"
        subfolder.mkdir()

        # 创建一个过期文件
        old_file = _create_media_file(folder, "old.jpg", age_seconds=30 * 86400, content=b"x" * 100)

        mock_media_manager._media_max_age_days = 7
        mock_media_manager._media_max_total_size_mb = 0

        await mock_media_manager._cleanup_category_folder(folder)

        assert not old_file.exists()
        assert subfolder.exists()  # 子目录不应被删除

    @pytest.mark.asyncio
    async def test_cleanup_delete_failure_logged(self, mock_media_manager: MediaManager) -> None:
        """测试删除失败时记录警告但不中断。"""
        folder = mock_media_manager.images_folder

        old_file = _create_media_file(folder, "old.jpg", age_seconds=30 * 86400, content=b"x" * 100)

        mock_media_manager._media_max_age_days = 7
        mock_media_manager._media_max_total_size_mb = 0

        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("Permission denied")

        Path.unlink = failing_unlink  # type: ignore[method-assign]
        try:
            await mock_media_manager._cleanup_category_folder(folder)
            # 不抛出异常即通过
        finally:
            Path.unlink = original_unlink  # type: ignore[method-assign]


class TestCleanupAllMedia:
    """测试 _cleanup_all_media 方法。"""

    @pytest.mark.asyncio
    async def test_cleanup_all_calls_pending_and_categories(
        self, mock_media_manager: MediaManager
    ) -> None:
        """测试 _cleanup_all_media 调用 pending 和分类目录清理。"""
        # 使用 AsyncMock 避免协程复用问题
        mock_pending = AsyncMock()
        mock_category = AsyncMock()

        with patch.object(
            mock_media_manager, "_cleanup_pending_folder", mock_pending
        ), patch.object(
            mock_media_manager, "_cleanup_category_folder", mock_category
        ):
            await mock_media_manager._cleanup_all_media()

            mock_pending.assert_called_once()
            # 当 enabled=True 时应调用分类清理 2 次（images + emojis）
            assert mock_category.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_all_disabled_skips_categories(
        self, mock_media_manager: MediaManager
    ) -> None:
        """测试 _media_cleanup_enabled=False 时跳过分类清理但保留 pending 清理。"""
        mock_media_manager._media_cleanup_enabled = False

        mock_pending = AsyncMock()
        mock_category = AsyncMock()

        with patch.object(
            mock_media_manager, "_cleanup_pending_folder", mock_pending
        ), patch.object(
            mock_media_manager, "_cleanup_category_folder", mock_category
        ):
            await mock_media_manager._cleanup_all_media()

            mock_pending.assert_called_once()
            # 当 enabled=False 时不应调用分类清理
            mock_category.assert_not_called()


class TestSafeDeleteMedia:
    """测试 _safe_delete_media 静态方法。"""

    def test_safe_delete_success(self, tmp_path: Path) -> None:
        """测试成功删除文件返回 1。"""
        file_path = tmp_path / "test.png"
        file_path.write_bytes(b"content")

        result = MediaManager._safe_delete_media(file_path)

        assert result == 1
        assert not file_path.exists()

    def test_safe_delete_failure(self, tmp_path: Path) -> None:
        """测试删除失败返回 0。"""
        file_path = tmp_path / "nonexistent.png"

        result = MediaManager._safe_delete_media(file_path)

        assert result == 0
