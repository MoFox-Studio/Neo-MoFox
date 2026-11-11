"""以真实来源停止链验证 Bot 不截断请求 owner 的异步资源清理。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager
from src.kernel.concurrency import TaskManager


@pytest.mark.parametrize("interrupt", ["none", "waiter", "deadline"])
async def test_real_stop_preserves_owner_cleanup(
    monkeypatch: pytest.MonkeyPatch, interrupt: str,
) -> None:
    """来源使用真实 stop；重复停止、取消等待者或预算到期均不能追加取消。"""
    tasks = TaskManager()
    tasks.shutdown_process_pool(wait=False)
    entered, cleaning, release, stopping = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    )
    state = {"cleanup_completed": False}

    async def owner() -> None:
        """把首次取消固定在尚未完成的资源清理阶段。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            state["cleanup_completed"] = True

    task = tasks.create_task(owner(), name="chat_stream_p1_real", daemon=True).task
    assert task is not None
    await entered.wait()
    task.cancel()
    await cleaning.wait()
    source = StreamLoopManager()
    source.is_running = True
    streams = SimpleNamespace(
        _streams={"offline": SimpleNamespace(context=SimpleNamespace(stream_loop_task=task))}
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: streams)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: source,
    )
    real_stop = source.stop

    async def observe_stop() -> None:
        """仅标记真实停止入口，不替换停止实现或取消行为。"""
        stopping.set()
        await real_stop()

    monkeypatch.setattr(source, "stop", observe_stop)
    bot = Bot()
    bot.task_manager = tasks
    bot.watchdog = None
    bot.logger = MagicMock()
    bot._stream_loop_manager = source
    budget = 0.02 if interrupt == "deadline" else 2.0

    async def stop_and_observe() -> None:
        """按新步骤边界限制来源观察；取消不能传播给真实 owner。"""
        async with asyncio.timeout(budget):
            await bot._stop_stream_loops()
            await bot._stop_chat_tasks()

    runner: asyncio.Task[Any] | None = None
    try:
        runner = tasks.create_task(
            stop_and_observe(), name="p1_bot_stop_observer", daemon=True,
        ).task
        assert runner is not None
        await stopping.wait()
        if interrupt == "waiter":
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
        elif interrupt == "deadline":
            with pytest.raises(TimeoutError):
                await runner
        assert task.cancelling() == 1
        assert not task.done()
        assert not state["cleanup_completed"]
        release.set()
        if interrupt == "none":
            await runner
        await asyncio.gather(task, return_exceptions=True)
        assert state["cleanup_completed"]
        assert task.cancelled()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if runner is not None:
            await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.parametrize("already_done", [False, True])
async def test_captured_owner_cleanup_error_is_not_shutdown_success(
    monkeypatch: pytest.MonkeyPatch, already_done: bool,
) -> None:
    """已捕获任务退出不等于清理成功；普通清理错误必须转为安全停机失败。"""
    tasks = TaskManager()
    tasks.shutdown_process_pool(wait=False)
    entered = asyncio.Event()

    async def owner() -> None:
        """在取消清理时产生不应进入停机错误文本的业务原文。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("secret-url-must-not-leak")

    task = tasks.create_task(owner(), name="chat_stream_p1_failed", daemon=True).task
    assert task is not None
    await entered.wait()
    if already_done:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    source = StreamLoopManager()
    source.is_running = True
    streams = SimpleNamespace(
        _streams={"offline": SimpleNamespace(context=SimpleNamespace(stream_loop_task=task))}
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: streams)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: source,
    )
    bot = Bot()
    bot.task_manager, bot.watchdog, bot.logger = tasks, None, MagicMock()
    bot._stream_loop_manager = source
    try:
        await bot._stop_stream_loops()
        with pytest.raises(BotShutdownError) as caught:
            await bot._stop_chat_tasks()
        assert "secret-url" not in str(caught.value)
        assert task.done()
        assert isinstance(task.exception(), RuntimeError)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("interrupt", ["deadline", "cancel"])
async def test_full_shutdown_real_source_preserves_active_resource_and_restores_ui(
    monkeypatch: pytest.MonkeyPatch, interrupt: str,
) -> None:
    """完整停机接入真实来源，预算或取消不能强关活动池，也不能跳过同步恢复。"""
    from src.kernel.llm.model_client import gemini_client, runtime

    monkeypatch.setattr("src.app.runtime.bot.ConsoleUIManager", MagicMock())
    bot = Bot()
    bot.logger = MagicMock()
    bot._record_telemetry_event = AsyncMock()
    bot._record_runtime_snapshot = AsyncMock()
    bot.event_bus = SimpleNamespace(publish=AsyncMock())
    bot._unload_all_plugins = AsyncMock()
    bot._command_parser, bot._signal_handler = MagicMock(), MagicMock()
    parser, signals = bot._command_parser, bot._signal_handler
    tasks = TaskManager()
    tasks.shutdown_process_pool(wait=False)
    bot.task_manager = tasks
    bot.watchdog = None
    def handle(request: httpx.Request) -> httpx.Response:
        """仅为本地 lease 提供内存响应，不访问服务。"""
        return httpx.Response(200)

    client = gemini_client.GeminiChatClient(transport=httpx.MockTransport(handle))
    lease = await client._get_client({
        "api_key": "offline-only", "base_url": "https://offline.invalid",
        "timeout": 2, "extra_params": {"trust_env": False},
    })
    close = AsyncMock(wraps=client.aclose)
    monkeypatch.setattr(runtime, "close_default_model_clients", close)
    entered, cleaning, release, stopping = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
    )

    async def owner() -> None:
        """持有真实本地 lease，在首次取消后阻塞释放。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            lease.active -= 1

    task = tasks.create_task(owner(), name="chat_stream_full_shutdown", daemon=True).task
    assert task is not None
    await entered.wait()
    task.cancel()
    await cleaning.wait()
    source = StreamLoopManager()
    source.is_running = True
    streams = SimpleNamespace(
        _streams={"offline": SimpleNamespace(context=SimpleNamespace(stream_loop_task=task))}
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: streams)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: source,
    )
    real_stop = source.stop

    async def observe_stop() -> None:
        """只标记真正 stop 开始，不替换停止协议。"""
        stopping.set()
        await real_stop()

    monkeypatch.setattr(source, "stop", observe_stop)
    bot._stream_loop_manager = source
    runner = tasks.create_task(bot.shutdown(timeout=0.05), name="p1_full_shutdown", daemon=True).task
    assert runner is not None
    try:
        await stopping.wait()
        if interrupt == "cancel":
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
        else:
            with pytest.raises(BotShutdownError, match="超时"):
                await runner
        assert not task.done()
        assert task.cancelling() == 1
        assert lease.active == 1
        assert not lease.http.is_closed
        close.assert_not_awaited()
        bot.ui.display_success.assert_not_called()
        parser.close.assert_called_once_with()
        signals.restore_handlers.assert_called_once_with()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert lease.active == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(runner, return_exceptions=True)
        await client.aclose()


async def test_source_warning_uses_project_logger_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """来源失败告警采用真实 Logger 方法签名，不以日志 TypeError 替换停止结果。"""
    from src.kernel.logger import get_logger

    logger = get_logger("p1_stop_logger_contract")
    messages: list[str] = []

    def record(level: str, message: str, color: Any, **metadata: Any) -> None:
        """仅捕获消息，不启动日志文件或输出原始错误。"""
        messages.append(message)

    monkeypatch.setattr(logger, "_log", record)

    async def fail_stop() -> None:
        """模拟来源普通失败，原文不得进入日志。"""
        raise ValueError("secret-stop-url")

    source = SimpleNamespace(is_running=True, stop=fail_stop)
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: source,
    )
    tasks = TaskManager()
    tasks.shutdown_process_pool(wait=False)
    bot = Bot()
    bot.task_manager, bot.watchdog, bot.logger = tasks, None, logger
    bot._stream_loop_manager = source
    with pytest.raises(BotShutdownError) as caught:
        await bot.shutdown(timeout=0.2)
    assert "聊天流: ValueError" in str(caught.value)
    assert any("ValueError" in message for message in messages)
    assert all("secret-stop-url" not in message for message in messages)
    assert "secret-stop-url" not in str(caught.value)


async def test_preflight_results_use_project_logger_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预检各结果及总覆盖计数均经真实 Logger 签名输出安全字符串。"""
    from src.kernel.logger import get_logger
    from src.kernel.llm.model_client.probe_contract import ProviderProbeResult

    logger = get_logger("p1_preflight_logger_contract")
    messages: list[str] = []

    def record(level: str, message: str, color: Any, **metadata: Any) -> None:
        """只收集内存消息。"""
        messages.append(message)

    monkeypatch.setattr(logger, "_log", record)
    monkeypatch.setattr("src.core.config.get_model_config", lambda: object())
    monkeypatch.setattr(
        "src.core.managers.llm_runtime.preflight_llm_providers",
        AsyncMock(return_value=[
            ProviderProbeResult("unverified", "offline unverified"),
            ProviderProbeResult("available", "offline available", covered_models=1, groups=1),
        ]),
    )
    bot = Bot.__new__(Bot)
    bot.logger, bot.ui = logger, MagicMock()
    bot.config = SimpleNamespace(
        bot=SimpleNamespace(llm_preflight_check=True, llm_preflight_timeout=1),
        advanced=SimpleNamespace(trust_env=False),
    )
    await bot._preflight_llm_providers()
    assert any("offline unverified" in message for message in messages)
    assert any("offline available" in message for message in messages)
    assert any("1 项、1 组" in message for message in messages)
