"""初始化被取消时必须回收临时引擎，不能发布半初始化状态。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.util.concurrency import await_only

from src.app.plugin_system.api import storage_api
from src.kernel.db.core import engine as engine_module
from src.kernel.db.core import session as session_module


@pytest.mark.parametrize("target", ["main", "plugin"])
async def test_initialization_cancellation_disposes_unpublished_engine(
    target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中真实初始化 PRAGMA 的 await 后取消，清理完成后才传播取消。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    engines = []

    def create(*args, **kwargs):
        """对第一次初始化设置可控制的驱动等待点。"""
        engine = create_async_engine(*args, **kwargs)
        if not engines:
            original = engine.sync_engine.dialect.do_execute
            armed = True

            def execute(cursor, statement, parameters, context=None) -> None:
                """只挂起第一次 PRAGMA，后续重试正常运行。"""
                nonlocal armed
                if armed:
                    armed = False
                    entered.set()
                    await_only(release.wait())
                original(cursor, statement, parameters, context)

            monkeypatch.setattr(engine.sync_engine.dialect, "do_execute", execute)
        engines.append(engine)
        return engine

    db = storage_api.PluginDatabase(str(tmp_path / "init.db"), [])
    if target == "main":
        monkeypatch.setattr(engine_module, "_engine", None)
        monkeypatch.setattr(engine_module, "_engine_lock", None)
        monkeypatch.setattr(session_module, "_session_factory", None)
        monkeypatch.setattr(engine_module, "create_async_engine", create)
        monkeypatch.setattr(engine_module, "record_db_lifecycle_event", AsyncMock())
        monkeypatch.setattr(
            engine_module, "_engine_config",
            engine_module.EngineConfig(
                url=f"sqlite+aiosqlite:///{(tmp_path / 'main.db').as_posix()}",
                engine_kwargs={}, db_type="sqlite", apply_optimizations=True,
            ),
        )
        initialize = engine_module.get_engine
        close = engine_module.close_engine
    else:
        monkeypatch.setattr(storage_api, "create_async_engine", create)
        initialize = db.initialize
        close = db.close

    worker = asyncio.create_task(initialize())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert engine_module._engine is None if target == "main" else db._engine is None
        for _ in range(3):
            worker.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=5)
        assert engines[0].pool.checkedout() == 0
        assert engine_module._engine is None if target == "main" else db._engine is None
        await initialize()
        assert len(engines) == 2
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
        await close()
