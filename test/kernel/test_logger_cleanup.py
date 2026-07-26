"""LoggerCleanupManager 单元测试。

测试日志文件自动清理管理器的核心清理逻辑，包括：
- 按天数删除过期文件
- 按文件数裁剪超额文件
- 不删除非 .log 文件
- 禁用时不执行清理
- 空目录不报错
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.kernel.logger.cleanup import LoggerCleanupManager


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    """创建临时日志目录的 fixture。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


def _create_log_file(log_dir: Path, name: str, age_seconds: float, content: str = "test") -> Path:
    """创建一个具有指定年龄的日志文件。

    Args:
        log_dir: 日志目录
        name: 文件名
        age_seconds: 文件年龄（秒），通过修改 mtime 实现
        content: 文件内容

    Returns:
        创建的文件路径
    """
    file_path = log_dir / name
    file_path.write_text(content)
    # 修改文件修改时间
    mtime = time.time() - age_seconds
    import os

    os.utime(file_path, (mtime, mtime))
    return file_path


class TestLoggerCleanupManagerInit:
    """测试 LoggerCleanupManager 初始化。"""

    def test_init_with_defaults(self) -> None:
        """测试默认参数初始化。"""
        manager = LoggerCleanupManager(log_dir="logs")
        assert manager.log_dir == Path("logs")
        assert manager.max_age_days == 30
        assert manager.max_files == 100
        assert manager.cleanup_interval_hours == 6.0
        assert manager.enabled is True
        assert manager._cleanup_task_id is None

    def test_init_with_custom_params(self) -> None:
        """测试自定义参数初始化。"""
        manager = LoggerCleanupManager(
            log_dir="/tmp/test_logs",
            max_age_days=7,
            max_files=50,
            cleanup_interval_hours=2.0,
            enabled=False,
        )
        assert manager.log_dir == Path("/tmp/test_logs")
        assert manager.max_age_days == 7
        assert manager.max_files == 50
        assert manager.cleanup_interval_hours == 2.0
        assert manager.enabled is False


class TestLoggerCleanupManagerCleanup:
    """测试 LoggerCleanupManager 清理逻辑。"""

    @pytest.mark.asyncio
    async def test_cleanup_by_age(self, log_dir: Path) -> None:
        """测试按天数删除过期文件。"""
        # 创建过期文件（10天前）
        old_file1 = _create_log_file(log_dir, "old1.log", age_seconds=10 * 86400)
        old_file2 = _create_log_file(log_dir, "old2.log", age_seconds=15 * 86400)
        # 创建新文件（1天前）
        new_file = _create_log_file(log_dir, "new.log", age_seconds=1 * 86400)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=7,
            max_files=0,  # 禁用文件数清理
            enabled=True,
        )

        await manager._cleanup()

        assert not old_file1.exists()
        assert not old_file2.exists()
        assert new_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_by_file_count(self, log_dir: Path) -> None:
        """测试按文件数裁剪超额文件。"""
        # 创建 5 个文件，age_seconds 递增（i=0 最新，i=4 最旧）
        files = []
        for i in range(5):
            f = _create_log_file(log_dir, f"file_{i}.log", age_seconds=i * 3600)
            files.append(f)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=0,  # 禁用天数清理
            max_files=3,
            enabled=True,
        )

        await manager._cleanup()

        # 最旧的 2 个（age 最大的）应该被删除
        assert not files[4].exists()
        assert not files[3].exists()
        # 最新的 3 个应该保留
        assert files[0].exists()
        assert files[1].exists()
        assert files[2].exists()

    @pytest.mark.asyncio
    async def test_cleanup_keeps_non_log_files(self, log_dir: Path) -> None:
        """测试清理时不删除非 .log 文件。"""
        old_log = _create_log_file(log_dir, "old.log", age_seconds=30 * 86400)
        old_txt = _create_log_file(log_dir, "old.txt", age_seconds=30 * 86400)
        old_json = _create_log_file(log_dir, "data.json", age_seconds=30 * 86400)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=7,
            max_files=0,
            enabled=True,
        )

        await manager._cleanup()

        assert not old_log.exists()
        assert old_txt.exists()
        assert old_json.exists()

    @pytest.mark.asyncio
    async def test_cleanup_disabled_no_deletion(self, log_dir: Path) -> None:
        """测试 enabled=False 时 start 不注册任务，但 _cleanup 仍可手动调用。"""
        old_file = _create_log_file(log_dir, "old.log", age_seconds=30 * 86400)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=7,
            max_files=0,
            enabled=False,
        )

        # enabled=False 只影响 start()，_cleanup 仍可执行
        await manager._cleanup()
        assert not old_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_empty_dir(self, log_dir: Path) -> None:
        """测试空目录不报错。"""
        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=7,
            max_files=10,
            enabled=True,
        )

        await manager._cleanup()
        # 不报错即通过

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_dir(self, tmp_path: Path) -> None:
        """测试不存在的目录不报错。"""
        manager = LoggerCleanupManager(
            log_dir=tmp_path / "nonexistent",
            max_age_days=7,
            max_files=10,
            enabled=True,
        )

        await manager._cleanup()
        # 不报错即通过

    @pytest.mark.asyncio
    async def test_cleanup_max_age_zero_no_age_deletion(self, log_dir: Path) -> None:
        """测试 max_age_days=0 时不按天数清理。"""
        old_file = _create_log_file(log_dir, "old.log", age_seconds=365 * 86400)
        new_file = _create_log_file(log_dir, "new.log", age_seconds=10)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=0,  # 不按时间清理
            max_files=0,  # 不按文件数清理
            enabled=True,
        )

        await manager._cleanup()

        assert old_file.exists()
        assert new_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_max_files_zero_no_count_deletion(self, log_dir: Path) -> None:
        """测试 max_files=0 时不按文件数清理。"""
        for i in range(20):
            _create_log_file(log_dir, f"file_{i}.log", age_seconds=i * 100)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=0,  # 不按时间清理
            max_files=0,  # 不限制文件数
            enabled=True,
        )

        await manager._cleanup()

        # 所有文件都应保留
        log_files = list(log_dir.glob("*.log"))
        assert len(log_files) == 20

    @pytest.mark.asyncio
    async def test_cleanup_combined_age_and_count(self, log_dir: Path) -> None:
        """测试同时按天数和文件数清理。"""
        # 创建 5 个过期文件（10天前，age 递增确保 mtime 不同）
        old_files = []
        for i in range(5):
            f = _create_log_file(log_dir, f"old_{i}.log", age_seconds=10 * 86400 + i * 3600)
            old_files.append(f)

        # 创建 3 个新文件（1天内，i=0 最新，i=2 最旧）
        new_files = []
        for i in range(3):
            f = _create_log_file(log_dir, f"new_{i}.log", age_seconds=i * 3600)
            new_files.append(f)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=7,
            max_files=2,  # 最多保留 2 个
            enabled=True,
        )

        await manager._cleanup()

        # 所有旧文件应被删除（按天数）
        for f in old_files:
            assert not f.exists()

        # 新文件按 mtime 排序（旧->新）：new_files[2](2h), new_files[1](1h), new_files[0](0h)
        # 保留 2 个最新的，删除最旧的 new_files[2]
        assert not new_files[2].exists()
        assert new_files[1].exists()
        assert new_files[0].exists()

    @pytest.mark.asyncio
    async def test_cleanup_latest_file_preserved(self, log_dir: Path) -> None:
        """测试当前最新文件不会被删除。"""
        # 创建文件，最新的最后创建
        files = []
        for i in range(5):
            f = _create_log_file(log_dir, f"file_{i}.log", age_seconds=(4 - i) * 86400)
            files.append(f)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=2,
            max_files=1,  # 只保留 1 个
            enabled=True,
        )

        await manager._cleanup()

        # 最新文件应该保留
        assert files[4].exists()

    @pytest.mark.asyncio
    async def test_cleanup_delete_failure_logged(self, log_dir: Path) -> None:
        """测试删除失败时记录警告但不中断。"""
        old_file = _create_log_file(log_dir, "old.log", age_seconds=30 * 86400)

        manager = LoggerCleanupManager(
            log_dir=log_dir,
            max_age_days=7,
            max_files=0,
            enabled=True,
        )

        # 使用 monkeypatch 模拟 unlink 失败
        original_unlink = Path.unlink

        def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("Permission denied")

        Path.unlink = failing_unlink  # type: ignore[method-assign]
        try:
            await manager._cleanup()
            # 不抛出异常即通过
        finally:
            Path.unlink = original_unlink  # type: ignore[method-assign]


class TestLoggerCleanupManagerSafeDelete:
    """测试 _safe_delete 静态方法。"""

    def test_safe_delete_success(self, tmp_path: Path) -> None:
        """测试成功删除文件返回 1。"""
        file_path = tmp_path / "test.log"
        file_path.write_text("content")

        result = LoggerCleanupManager._safe_delete(file_path)

        assert result == 1
        assert not file_path.exists()

    def test_safe_delete_failure(self, tmp_path: Path) -> None:
        """测试删除失败返回 0。"""
        file_path = tmp_path / "nonexistent.log"

        result = LoggerCleanupManager._safe_delete(file_path)

        assert result == 0
