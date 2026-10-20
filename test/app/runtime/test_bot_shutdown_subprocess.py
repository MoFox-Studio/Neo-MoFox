"""在独立进程验证重复停机与 asyncio.run 最终清扫不会打断 SQLite 收尾。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_repeated_shutdown_with_untracked_sqlite_task(tmp_path: Path) -> None:
    """真实 Bot.shutdown + SQLite，包含未登记的任务及 Python 进程退出。"""
    script = textwrap.dedent('''
        import asyncio
        from contextlib import ExitStack
        from pathlib import Path
        import sqlite3
        import sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from sqlalchemy import text
        from sqlalchemy.util.concurrency import await_only
        from src.app.runtime.bot import Bot
        from src.app.plugin_system.api.storage_api import PluginDatabase

        async def main():
            for cycle in range(5):
                path = Path(sys.argv[1]) / f"shutdown-{cycle}.db"
                db = PluginDatabase(str(path), [])
                await db.initialize()
                pool = db._engine.pool
                async with db.session() as session:
                    await session.execute(text("CREATE TABLE entries (value INTEGER)"))
                entered = asyncio.Event()
                release = asyncio.Event()
                original = db._engine.sync_engine.dialect.do_rollback
                armed = False
                def rollback(connection):
                    nonlocal armed
                    if armed:
                        armed = False
                        entered.set()
                        await_only(release.wait())
                    original(connection)
                db._engine.sync_engine.dialect.do_rollback = rollback
                async def work():
                    nonlocal armed
                    async with db.session() as session:
                        await session.execute(text("INSERT INTO entries VALUES (1)"))
                        armed = True
                worker = asyncio.create_task(work(), name="untracked_database_writer")
                await asyncio.wait_for(entered.wait(), 5)
                worker.cancel()
                await asyncio.sleep(0)
                bot = Bot()
                bot.logger = MagicMock()
                bot.ui = MagicMock()
                bot.event_bus = SimpleNamespace(publish=AsyncMock())
                bot._record_telemetry_event = AsyncMock()
                bot._record_runtime_snapshot = AsyncMock()
                bot._unload_all_plugins = AsyncMock()
                async def close_database():
                    assert worker.done(), "database closed before its untracked user"
                    assert pool.checkedout() == 0, "checked-out connection leaked"
                    await db.close()
                with ExitStack() as stack:
                    stack.enter_context(patch(
                        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
                        return_value=SimpleNamespace(is_running=False),
                    ))
                    stack.enter_context(patch("src.kernel.db.close_engine", side_effect=close_database))
                    for target in [
                        "src.kernel.vector_db.close_all_vector_db_services",
                        "src.app.cloud_telemetry.close_cloud_telemetry_runtime",
                        "src.kernel.llm.stats.close_llm_stats_db",
                        "src.kernel.telemetry.close_telemetry_db",
                    ]:
                        stack.enter_context(patch(target, new=AsyncMock()))
                    stack.enter_context(patch("src.kernel.logger.shutdown_logger_system"))
                    asyncio.get_running_loop().call_later(0.03, release.set)
                    await bot.shutdown()
                assert worker.cancelled()
                assert db._engine is None
                assert asyncio.all_tasks() == {asyncio.current_task()}
                with sqlite3.connect(path) as connection:
                    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    assert connection.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
            print("five shutdown cycles completed")
        asyncio.run(main())
    ''')
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "five shutdown cycles completed" in result.stdout
    for message in [
        "Exception during reset", "Exception terminating", "non-checked-in",
        "Task was destroyed", "Event loop is closed", "exception was never retrieved",
    ]:
        assert message not in result.stderr, result.stderr
