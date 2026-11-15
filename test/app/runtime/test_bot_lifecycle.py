"""Bot 分阶段启动的顺序、失败和退出边界测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotRuntimeError
from src.core.components.types import EventType
from src.kernel.scheduler import UnifiedScheduler


@pytest.fixture
def bot(monkeypatch):
    """隔离网络、终端和存储，保留真实 Bot 编排与调度器。"""
    instance = Bot()
    instance.ui = MagicMock()
    for name in (
        "config", "logger", "task_manager", "message_receiver",
        "sink_manager", "plugin_manager",
    ):
        setattr(instance, name, MagicMock())
    instance.event_bus = AsyncMock()
    instance.media_manager = AsyncMock()
    instance.scheduler = UnifiedScheduler()
    instance._initialized = True
    instance._record_telemetry_event = AsyncMock()
    instance._record_runtime_snapshot = AsyncMock()
    monkeypatch.setattr(instance, "_install_signal_handler", MagicMock())
    monkeypatch.setattr(instance, "_create_command_parser", MagicMock())
    instance._command_parser = MagicMock()
    instance._command_parser.read_and_execute = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "src.app.cloud_telemetry.get_cloud_telemetry_client", lambda: None
    )
    return instance


@pytest.mark.asyncio
async def test_run_services_ready_before_start_event(bot):
    """ON_START 看到运行中的调度器和完成的媒体、日志启动。"""
    order = []

    async def media_start():
        assert bot.scheduler.is_started
        order.append("media")

    async def logs_start():
        assert bot.scheduler.is_started
        order.append("logs")

    async def publish(event, params):
        assert event == EventType.ON_START
        assert order == ["media", "logs"]
        order.append("event")

    bot.media_manager.start.side_effect = media_start
    bot._start_logger_cleanup = logs_start
    bot.event_bus.publish.side_effect = publish
    try:
        await bot.run()
        assert order == ["media", "logs", "event"]
        bot._command_parser.read_and_execute.assert_awaited_once()
        assert bot._running is False
        bot._command_parser.close.assert_not_called()
    finally:
        await bot.scheduler.stop()


@pytest.mark.asyncio
async def test_failed_service_does_not_publish_start(bot):
    """必需服务失败时不进入命令循环，并恢复运行标记。"""
    bot.media_manager.start.side_effect = RuntimeError("media failed")
    try:
        with pytest.raises(RuntimeError, match="media failed"):
            await bot.run()
        bot.event_bus.publish.assert_not_awaited()
        bot._command_parser.read_and_execute.assert_not_awaited()
        assert bot._running is False
    finally:
        await bot.scheduler.stop()


@pytest.mark.asyncio
async def test_missing_dependency_prevents_scheduler_start(bot):
    """初始化标志不能掩盖核心资源缺失。"""
    bot.media_manager = None
    with pytest.raises(BotRuntimeError, match="media_manager"):
        await bot.run()
    assert not bot.scheduler.is_started


@pytest.mark.asyncio
async def test_start_handler_can_request_stop(bot):
    """ON_START 请求停止后不能被主循环重新设为运行。"""
    async def request_stop(*args):
        bot._running = False

    bot.event_bus.publish.side_effect = request_stop
    try:
        await bot.run()
        bot._command_parser.read_and_execute.assert_not_awaited()
    finally:
        await bot.scheduler.stop()


@pytest.mark.asyncio
async def test_media_start_requires_running_scheduler(bot):
    """独立调用媒体启动阶段也不能跳过调度器依赖。"""
    with pytest.raises(BotRuntimeError, match="调度器"):
        await bot._start_media_manager()
    bot.media_manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_cleans_up_after_initialization_failure(bot):
    """完整入口在初始化中断后仍调用关闭流程。"""
    bot.initialize = AsyncMock(side_effect=RuntimeError("database failed"))
    bot.shutdown = AsyncMock()
    with pytest.raises(RuntimeError, match="database failed"):
        await bot.start()
    bot.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_core_media_ready_before_plugin_load(bot, monkeypatch):
    """通过真实 initialize() 编排验证媒体先于插件就绪。"""
    bot._initialized = False
    bot.media_manager = None
    monkeypatch.setattr(bot, "_optimize_async_network_runtime", AsyncMock())
    monkeypatch.setattr(bot, "_initialize_kernel", AsyncMock())
    for name in ("_initialize_transport", "_initialize_builtin_managers"):
        monkeypatch.setattr(bot, name, MagicMock())
    for name in ("_initialize_mcp_manager", "_initialize_http_server"):
        monkeypatch.setattr(bot, name, AsyncMock())
    manager = AsyncMock()
    monkeypatch.setattr("src.core.managers.initialize_media_manager", lambda: manager)

    async def load_plugins():
        assert bot.media_manager is manager
        manager.initialize.assert_awaited_once()
        manager.start.assert_not_awaited()
        assert not bot.scheduler.is_started

    monkeypatch.setattr(bot, "_initialize_plugins", load_plugins)
    await bot.initialize()
    assert bot._initialized
