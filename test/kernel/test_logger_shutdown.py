"""停机期间停止日志派生任务，同时保留日志输出并允许重新初始化。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.kernel.logger import logger as logger_module


async def test_stop_broadcast_keeps_logging_and_reinitialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停机开关与用户配置分离，重新初始化后已有 logger 也能广播。"""
    monkeypatch.setattr(logger_module, "_event_broadcast_stopped", False)
    monkeypatch.setattr(logger_module, "_global_config", dict(logger_module._global_config))
    publish = AsyncMock()
    monkeypatch.setattr(logger_module, "_get_event_bus", lambda: SimpleNamespace(publish=publish))
    logger = logger_module.Logger("shutdown_test", enable_file=False, enable_event_broadcast=True)
    logger.info("before shutdown")
    await asyncio.sleep(0)
    assert publish.await_count == 1
    logger_module.stop_logger_event_broadcast()
    logger.info("during shutdown")
    await asyncio.sleep(0)
    assert publish.await_count == 1
    logger_module.initialize_logger_system(
        log_dir=tmp_path, enable_file=False, log_cleanup_enabled=False,
        enable_event_broadcast=False,
    )
    logger.info("after reinitialization")
    await asyncio.sleep(0)
    assert publish.await_count == 2
