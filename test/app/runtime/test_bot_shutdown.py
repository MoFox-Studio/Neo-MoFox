"""关闭阶段的错误隔离、依赖顺序及资源归属回归。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError


@pytest.fixture
def bot():
    """创建未连接外部服务的 Bot，保留真实关闭编排。"""
    instance = Bot()
    instance.ui = MagicMock()
    return instance


@pytest.mark.asyncio
async def test_shutdown_continues_after_failures_in_dependency_order(bot, monkeypatch):
    """媒体关闭失败后仍停止调度器和存储，聊天流在插件卸载前停止。"""
    order = []

    def operation(name, fail=False):
        async def run(*args):
            order.append(name)
            if fail:
                raise RuntimeError(name)
        return AsyncMock(side_effect=run)

    bot._stream_loop_manager = SimpleNamespace(stop=operation("streams"))
    bot.plugin_manager = SimpleNamespace(unload_plugin=operation("plugin"))
    bot.load_order = ["one"]
    bot.media_manager = SimpleNamespace(shutdown=operation("media", fail=True))
    bot.scheduler = SimpleNamespace(stop=operation("scheduler"))
    bot._owned_resources.add("database")
    close_db = operation("database")
    monkeypatch.setattr("src.kernel.db.close_engine", close_db)
    parser = MagicMock()
    signals = MagicMock()
    bot._command_parser = parser
    bot._signal_handler = signals

    with pytest.raises(BotShutdownError, match="media"):
        await bot.shutdown()
    assert order == ["streams", "plugin", "media", "scheduler", "database"]
    parser.close.assert_called_once()
    signals.restore_handlers.assert_called_once()
    bot.ui.display_success.assert_not_called()
    await bot.shutdown()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_each_plugin_timeout_does_not_skip_other_cleanup(bot):
    """一个插件挂起后，后续插件及核心服务仍得到清理机会。"""
    seen = []

    async def unload(name):
        seen.append(name)
        if name == "stuck":
            await asyncio.Event().wait()
        return True

    bot.plugin_manager = SimpleNamespace(unload_plugin=unload)
    bot.load_order = ["ok", "stuck"]
    bot.scheduler = SimpleNamespace(stop=AsyncMock())
    with pytest.raises(BotShutdownError, match="TimeoutError"):
        await bot.shutdown(timeout=0.01)
    assert seen == ["stuck", "ok"]
    bot.scheduler.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_database_initialization_is_closed(bot, monkeypatch):
    """初始化调用创建资源后抛错，关闭仍能释放资源。"""
    bot.config = MagicMock()
    monkeypatch.setattr(
        "src.kernel.db.init_database_from_config",
        AsyncMock(side_effect=RuntimeError("connection failed")),
    )
    close_db = AsyncMock()
    monkeypatch.setattr("src.kernel.db.close_engine", close_db)
    with pytest.raises(RuntimeError, match="connection failed"):
        await bot._initialize_database()
    await bot.shutdown()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_uninitialized_shutdown_does_not_create_or_close_global_resources(bot, monkeypatch):
    """空 Bot 关闭不懒创建管理器、不释放别处初始化的数据库。"""
    close_db = AsyncMock()
    getter = MagicMock(side_effect=AssertionError("unexpected creation"))
    monkeypatch.setattr("src.kernel.db.close_engine", close_db)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager", getter
    )
    await bot.shutdown()
    getter.assert_not_called()
    close_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_parser_failure_still_restores_signals(bot):
    """终端关闭失败不能跳过信号恢复。"""
    bot._command_parser = MagicMock()
    bot._command_parser.close.side_effect = RuntimeError("terminal failed")
    signals = MagicMock()
    bot._signal_handler = signals
    with pytest.raises(BotShutdownError, match="terminal failed"):
        await bot.shutdown()
    signals.restore_handlers.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_shutdown_still_restores_terminal(bot):
    """外部取消关闭协程时，最终同步资源恢复仍执行。"""
    entered = asyncio.Event()

    async def stop_http():
        entered.set()
        await asyncio.Event().wait()

    bot.http_server = SimpleNamespace(stop=stop_http)
    parser, signals = MagicMock(), MagicMock()
    bot._command_parser, bot._signal_handler = parser, signals
    task = asyncio.create_task(bot.shutdown())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    parser.close.assert_called_once()
    signals.restore_handlers.assert_called_once()


@pytest.mark.asyncio
async def test_original_startup_error_survives_cleanup_error(bot):
    """清理报错作为附注保留，不覆盖启动失败的根因。"""
    bot.initialize = AsyncMock(side_effect=RuntimeError("startup failed"))
    bot.shutdown = AsyncMock(side_effect=BotShutdownError("cleanup failed"))
    bot._record_telemetry_event = AsyncMock()
    with pytest.raises(RuntimeError, match="startup failed") as caught:
        await bot.start()
    assert "cleanup failed" in caught.value.__notes__[0]


@pytest.mark.asyncio
async def test_shutdown_preserves_unrelated_tasks_and_executor(bot, monkeypatch):
    """只取消任务管理器持有的任务，默认线程池恢复原实例。"""
    loop = asyncio.get_running_loop()
    previous = loop._default_executor
    unrelated = asyncio.create_task(asyncio.Event().wait())
    owned = asyncio.create_task(asyncio.Event().wait())
    manager = MagicMock()
    manager.get_active_tasks.return_value = [
        SimpleNamespace(task=owned, task_id="owned"),
        SimpleNamespace(task=asyncio.current_task(), task_id="self"),
    ]
    manager.cancel_task.side_effect = lambda task_id: owned.cancel()
    bot.task_manager = manager
    original_dns = loop.getaddrinfo
    monkeypatch.setattr(bot, "_extract_provider_hosts_from_model_config", lambda _: [])
    try:
        await bot._optimize_async_network_runtime()
        assert isinstance(loop._default_executor, ThreadPoolExecutor)
        await bot.shutdown()
        assert owned.cancelled()
        assert not unrelated.done()
        manager.cancel_task.assert_called_once_with("owned")
        assert loop._default_executor is previous
        assert loop.getaddrinfo == original_dns
    finally:
        unrelated.cancel()
        owned.cancel()
        await asyncio.gather(unrelated, owned, return_exceptions=True)
        bot._restore_network_runtime()
