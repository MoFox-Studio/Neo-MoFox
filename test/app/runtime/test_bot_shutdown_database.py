"""停机应先等待流循环与守护任务收尾，再关闭数据库。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.runtime.bot import Bot


async def test_shutdown_waits_for_daemon_cleanup_before_database_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """慢守护任务在插件卸载前停止流循环，在关闭主数据库前完成清理。"""
    bot = Bot()
    bot.logger = MagicMock()
    bot.ui = MagicMock()
    bot.event_bus = SimpleNamespace(publish=AsyncMock())
    bot.watchdog = MagicMock()
    bot._record_telemetry_event = AsyncMock()
    bot._record_runtime_snapshot = AsyncMock()
    order: list[str] = []
    ready = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def daemon() -> None:
        """模拟收到取消后仍需异步完成数据库回滚的守护任务。"""
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await cleanup_release.wait()
            order.append("daemon_cleaned")

    worker = asyncio.create_task(daemon())
    await ready.wait()
    task_info = SimpleNamespace(task_id="daemon", task=worker, daemon=True)
    bot.task_manager = MagicMock()
    bot.task_manager.get_active_tasks.return_value = [task_info]
    bot.task_manager.cancel_task.side_effect = lambda _: worker.cancel()

    async def stop_streams() -> None:
        """确保重启监控已停止，再清理流循环。"""
        bot.watchdog.stop.assert_called_once()
        order.append("streams_stopped")

    manager = SimpleNamespace(is_running=True, stop=AsyncMock(side_effect=stop_streams))
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: manager,
    )

    async def unload_plugins() -> None:
        """插件数据库关闭前，流循环必须已经退出。"""
        assert order == ["streams_stopped"]
        order.append("plugins_unloaded")

    bot._unload_all_plugins = AsyncMock(side_effect=unload_plugins)

    async def close_database() -> None:
        """主数据库关闭前，守护任务也必须已完成取消清理。"""
        assert worker.done()
        assert order == ["streams_stopped", "plugins_unloaded", "daemon_cleaned"]
        order.append("database_closed")

    monkeypatch.setattr("src.kernel.db.close_engine", AsyncMock(side_effect=close_database))
    for module, name in [
        ("src.kernel.vector_db", "close_all_vector_db_services"),
        ("src.app.cloud_telemetry", "close_cloud_telemetry_runtime"),
        ("src.kernel.llm.stats", "close_llm_stats_db"),
        ("src.kernel.telemetry", "close_telemetry_db"),
    ]:
        monkeypatch.setattr(f"{module}.{name}", AsyncMock())
    monkeypatch.setattr("src.kernel.logger.shutdown_logger_system", MagicMock())
    monkeypatch.setattr("src.kernel.logger.stop_logger_event_broadcast", MagicMock())
    # 隔离 Bot 最后的全局任务清扫，避免取消 pytest 自身的运行任务。
    monkeypatch.setattr(asyncio, "all_tasks", lambda: {asyncio.current_task()})
    shutdown = asyncio.create_task(bot.shutdown())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=5)
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert "database_closed" not in order
        cleanup_release.set()
        await asyncio.wait_for(shutdown, timeout=5)
        assert order[-1] == "database_closed"
        assert bot._stats["db_connected"] is False
    finally:
        cleanup_release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if not shutdown.done():
            shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)
