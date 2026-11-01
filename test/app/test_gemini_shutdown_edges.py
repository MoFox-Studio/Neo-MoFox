"""离线验证 Gemini 多版本分组预检及异常关闭边界。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest

from src.app.runtime import bot as bot_module
from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.core.config.model_config import APIProviderSection, ModelConfig, ModelInfoSection
from src.core.managers.llm_runtime import preflight_provider
from src.kernel import db
from src.kernel.llm.model_client import gemini_client


SECRET = "fixture-gemini-shutdown-secret"


@pytest.fixture
def isolated_bot(monkeypatch: pytest.MonkeyPatch) -> Bot:
    """隔离运行时服务、线程池、终端及信号，保留关闭流程。"""
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

    def create_task(coro: Any, **kwargs: Any) -> SimpleNamespace:
        """为有界关闭步骤提供真实 asyncio 句柄，同时保留任务管理器桩。"""
        return SimpleNamespace(task=asyncio.create_task(coro, name=kwargs.get("name")))

    bot.task_manager.create_task.side_effect = create_task
    bot._command_parser = MagicMock()
    bot._signal_handler = MagicMock()
    stream_manager = SimpleNamespace(is_running=False, stop=AsyncMock())
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: stream_manager,
    )
    return bot


@pytest.mark.parametrize("beta_options", [{}, {"api_version": "v1beta"}])
async def test_preflight_groups_multiple_versions_using_sdk_list_only(
    monkeypatch: pytest.MonkeyPatch, isolated_bot: Bot, beta_options: dict[str, str],
) -> None:
    """默认及显式 beta 均与 v1 分组枚举，不再将合法多版本配置整体拒绝。"""
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []
    original_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """仅允许本地非生成枚举，捕获每组 API 版本。"""
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """使用真实 HTTPX 生命周期并禁止实际网络访问。"""
        kwargs["transport"] = httpx.MockTransport(handle)
        client = original_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    isolated_bot.config = SimpleNamespace(advanced=SimpleNamespace(trust_env=False, force_sync_http=False))
    provider = APIProviderSection(
        name="native", base_url="https://gateway.invalid", timeout=2,
        api_key=SECRET, client_type="gemini",
    )
    models = [
        ModelInfoSection(name="beta", model_identifier="beta", api_provider="native", extra_params={"gemini": beta_options}),
        ModelInfoSection(name="stable", model_identifier="stable", api_provider="native", extra_params={"gemini": {"api_version": "v1"}}),
    ]
    config = ModelConfig(api_providers=[provider], models=models)
    monkeypatch.setattr("src.core.config.get_model_config", lambda: config)
    monkeypatch.setattr("src.core.config.get_core_config", lambda: isolated_bot.config)
    results = await preflight_provider(config, provider, models=models, timeout=3, trust_env=False)
    for result in results:
        if result.status == "unverified":
            isolated_bot.logger.warning("LLM 预检: %s", result.detail)
        else:
            isolated_bot.logger.info("LLM 预检: %s", result.detail)

    assert len(requests) == 2
    assert [request.url.path for request in requests] == ["/v1beta/models", "/v1/models"]
    assert all(request.method == "GET" for request in requests)
    assert all(request.headers["x-goog-api-key"] == SECRET for request in requests)
    assert all("authorization" not in request.headers for request in requests)
    assert all(client.is_closed for client in clients)
    assert not isolated_bot.logger.warning.called
    assert SECRET not in str(isolated_bot.logger.mock_calls)


@pytest.mark.parametrize("close_fails", [False, True], ids=["closed", "close-warning"])
async def test_shutdown_closes_gemini_before_database_and_never_retries(
    monkeypatch: pytest.MonkeyPatch, isolated_bot: Bot, close_fails: bool,
) -> None:
    """在数据库边界停止，验证任务等待、池关闭及恢复的既有顺序。"""
    close = AsyncMock(side_effect=RuntimeError(SECRET) if close_fails else None)
    stop_error = RuntimeError("database shutdown test boundary")
    close_engine = AsyncMock(side_effect=stop_error)
    monkeypatch.setattr(gemini_client, "close_gemini_clients", close)
    monkeypatch.setattr(db, "close_engine", close_engine)
    sequence = MagicMock()
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
    assert sequence.mock_calls == [call.close_gemini(), call.close_database()]
    isolated_bot.task_manager.wait_all_tasks.assert_not_awaited()
    close.assert_awaited_once_with()
    close_engine.assert_awaited_once_with()
    isolated_bot.event_bus.publish.assert_awaited_once()
    isolated_bot._unload_all_plugins.assert_awaited_once_with()
    isolated_bot.task_manager.cancel_task.assert_not_called()
    if close_fails:
        isolated_bot.logger.warning.assert_called_once_with(
            "关闭默认模型客户端失败 (RuntimeError)"
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
    """兜底关闭失败不能覆盖主异常，也不能在日志输出敏感错误文本。"""
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
        "关闭默认模型客户端失败 (ValueError)"
    )
    assert SECRET not in str(isolated_bot.logger.mock_calls)
    parser.close.assert_called_once_with()
    signals.restore_handlers.assert_called_once_with()
    assert loop._default_executor is executor
    assert not loop.is_closed()
    assert not asyncio.current_task().cancelling()
