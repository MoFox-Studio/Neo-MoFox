"""Offline coverage for Gemini preflight rejection and shutdown failure edges."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest
from google import genai

from src.app.runtime import bot as bot_module
from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.kernel import db
from src.kernel.llm.model_client import gemini_client


SECRET = "fixture-gemini-shutdown-secret"


@pytest.fixture
def isolated_bot(monkeypatch: pytest.MonkeyPatch) -> Bot:
    """Construct no runtime services, executors, terminal UI or signal handlers."""
    monkeypatch.setattr(bot_module, "ConsoleUIManager", MagicMock())
    bot = Bot()
    bot.logger = MagicMock()
    bot._record_telemetry_event = AsyncMock()
    bot._unload_all_plugins = AsyncMock()
    bot.event_bus = SimpleNamespace(publish=AsyncMock())
    bot._stop_logger_cleanup = AsyncMock()
    bot.scheduler = SimpleNamespace(stop=AsyncMock())
    bot.http_server = SimpleNamespace(is_running=MagicMock(return_value=True), stop=AsyncMock())
    bot.mcp_manager = SimpleNamespace(cleanup=AsyncMock())
    bot.watchdog = MagicMock()
    bot.task_manager = MagicMock()
    bot.task_manager.get_active_tasks.return_value = []
    bot.task_manager.wait_all_tasks = AsyncMock()
    bot._command_parser = MagicMock()
    bot._signal_handler = MagicMock()
    return bot


@pytest.mark.parametrize("beta_options", [{}, {"api_version": "v1beta"}])
async def test_preflight_rejects_multiple_versions_before_creating_clients(
    monkeypatch: pytest.MonkeyPatch, isolated_bot: Bot, beta_options: dict,
) -> None:
    """Default and explicit beta versions both conflict with stable v1."""
    http_client = MagicMock(side_effect=AssertionError("HTTP client must not be created"))
    sdk_client = MagicMock(side_effect=AssertionError("SDK client must not be created"))
    monkeypatch.setattr(httpx, "AsyncClient", http_client)
    monkeypatch.setattr(genai, "Client", sdk_client)
    isolated_bot.config = SimpleNamespace(advanced=SimpleNamespace(trust_env=False))
    provider = SimpleNamespace(name="native", base_url="https://gateway.invalid", timeout=2,
                               get_api_key=MagicMock(return_value=SECRET))
    models = [
        SimpleNamespace(api_provider="native", extra_params={"gemini": beta_options}),
        SimpleNamespace(api_provider="native", extra_params={"gemini": {"api_version": "v1"}}),
    ]

    with pytest.raises(ValueError, match="one API version per provider"):
        await isolated_bot._preflight_gemini_provider(provider, models, timeout=3)

    http_client.assert_not_called()
    sdk_client.assert_not_called()
    provider.get_api_key.assert_not_called()
    assert SECRET not in str(isolated_bot.logger.mock_calls)


@pytest.mark.parametrize("close_fails", [False, True], ids=["closed", "close-warning"])
async def test_shutdown_closes_gemini_before_database_and_never_retries(
    monkeypatch: pytest.MonkeyPatch, isolated_bot: Bot, close_fails: bool,
) -> None:
    """Stop at the next step, before executor shutdown or global task cancellation."""
    close = AsyncMock(side_effect=RuntimeError(SECRET) if close_fails else None)
    stop_error = RuntimeError("database shutdown test boundary")
    close_engine = AsyncMock(side_effect=stop_error)
    monkeypatch.setattr(gemini_client, "close_gemini_clients", close)
    monkeypatch.setattr(db, "close_engine", close_engine)
    sequence = MagicMock()
    sequence.attach_mock(isolated_bot.task_manager.wait_all_tasks, "wait_tasks")
    sequence.attach_mock(close, "close_gemini")
    sequence.attach_mock(close_engine, "close_database")
    parser = isolated_bot._command_parser
    signals = isolated_bot._signal_handler
    loop = asyncio.get_running_loop()
    executor = loop._default_executor
    current_task = asyncio.current_task()

    with pytest.raises(BotShutdownError, match="database shutdown test boundary") as raised:
        await isolated_bot.shutdown()

    assert raised.value.__cause__ is stop_error
    assert sequence.mock_calls == [call.wait_tasks(), call.close_gemini(), call.close_database()]
    close.assert_awaited_once_with()
    close_engine.assert_awaited_once_with()
    isolated_bot.event_bus.publish.assert_awaited_once()
    isolated_bot._unload_all_plugins.assert_awaited_once_with()
    isolated_bot.task_manager.cancel_task.assert_not_called()
    if close_fails:
        isolated_bot.logger.warning.assert_called_once_with(
            "关闭 Gemini 客户端失败 (%s)", "RuntimeError"
        )
    else:
        isolated_bot.logger.warning.assert_not_called()
    assert SECRET not in str(isolated_bot.logger.mock_calls)
    parser.close.assert_called_once_with()
    signals.restore_handlers.assert_called_once_with()
    assert isolated_bot._command_parser is None
    assert isolated_bot._signal_handler is None
    assert loop._default_executor is executor
    assert not loop.is_closed()
    assert not current_task.cancelling()


async def test_shutdown_finally_warns_safely_when_unload_and_gemini_close_fail(
    monkeypatch: pytest.MonkeyPatch, isolated_bot: Bot,
) -> None:
    """A failing fallback close neither masks the original error nor logs secrets."""
    unload_error = RuntimeError("plugin unload test boundary")
    isolated_bot._unload_all_plugins.side_effect = unload_error
    close = AsyncMock(side_effect=ValueError(SECRET))
    close_engine = AsyncMock(side_effect=AssertionError("must stop before database cleanup"))
    monkeypatch.setattr(gemini_client, "close_gemini_clients", close)
    monkeypatch.setattr(db, "close_engine", close_engine)
    parser = isolated_bot._command_parser
    signals = isolated_bot._signal_handler
    loop = asyncio.get_running_loop()
    executor = loop._default_executor

    with pytest.raises(BotShutdownError, match="plugin unload test boundary") as raised:
        await isolated_bot.shutdown()

    assert raised.value.__cause__ is unload_error
    close.assert_awaited_once_with()
    close_engine.assert_not_awaited()
    isolated_bot.task_manager.wait_all_tasks.assert_not_awaited()
    isolated_bot.logger.warning.assert_called_once_with(
        "关闭 Gemini 客户端失败 (%s)", "ValueError"
    )
    assert SECRET not in str(isolated_bot.logger.mock_calls)
    parser.close.assert_called_once_with()
    signals.restore_handlers.assert_called_once_with()
    assert loop._default_executor is executor
    assert not loop.is_closed()
    assert not asyncio.current_task().cancelling()
