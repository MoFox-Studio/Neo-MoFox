"""验证 Gemini 应用编排的任务回收、有效配置预检与同步恢复边界。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.runtime.bot import Bot
from src.app.runtime.exceptions import BotShutdownError
from src.core.config.model_config import APIProviderSection, ModelConfig, ModelInfoSection
from src.core.managers.llm_runtime import preflight_provider
from src.kernel.concurrency import TaskManager
from src.kernel.llm.model_client import gemini_client
from src.kernel.llm.types import ModelSet


async def _preflight_provider(
    bot: Bot, provider: APIProviderSection, models: list[Any], timeout: float,
) -> None:
    """通过领域入口验证原预检行为，不恢复 Bot 的品牌私有方法。

    Args:
        bot: 只提供代理策略与结果日志的测试运行时。
        provider: 本次检查的领域提供商配置。
        models: 原始配置或有效模型条目。
        timeout: 技术探测预算秒数。
    """
    config = ModelConfig(api_providers=[provider], models=[
        model for model in models if isinstance(model, ModelInfoSection)
    ])
    results = await preflight_provider(
        config, provider, models=models, timeout=timeout,
        trust_env=bot.config.advanced.trust_env,
    )
    for result in results:
        if result.status == "unverified":
            bot.logger.warning("LLM 预检: %s", result.detail)
        else:
            bot.logger.info("LLM 预检: %s", result.detail)


@pytest.fixture
def runtime_bot(monkeypatch: pytest.MonkeyPatch) -> Bot:
    """隔离终端及遥测，保留真实 Bot 关闭控制流。"""
    monkeypatch.setattr("src.app.runtime.bot.ConsoleUIManager", MagicMock())
    bot = Bot()
    bot.logger = MagicMock()
    bot._record_telemetry_event = AsyncMock()
    bot._record_runtime_snapshot = AsyncMock()
    bot.event_bus = SimpleNamespace(publish=AsyncMock())
    bot._command_parser = MagicMock()
    bot._signal_handler = MagicMock()
    bot.plugin_manager = None
    bot.load_order = []
    bot.load_results = {}
    bot.media_manager = None
    bot._stop_logger_cleanup = None
    manager = SimpleNamespace(is_running=False, stop=AsyncMock())
    bot._stream_loop_manager = manager
    return bot


@pytest.mark.parametrize("stop_fails", [False, True])
async def test_shutdown_waits_daemon_lease_before_final_pool_close(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot, stop_fails: bool,
) -> None:
    """仅 daemon 存活且取消清理被事件阻塞时，最终池回收必须等待 lease。"""
    manager = TaskManager()
    runtime_bot.task_manager = manager
    acquired, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def handle(request: httpx.Request) -> httpx.Response:
        """为本地池提供不访问网络的借用传输。"""
        return httpx.Response(200)

    client = gemini_client.GeminiChatClient(transport=httpx.MockTransport(handle))
    lease = await client._get_client({
        "api_key": "offline-daemon-key", "base_url": "https://offline.invalid",
        "timeout": 2, "extra_params": {"trust_env": False},
    })
    http = lease.http
    order: list[str] = []

    async def daemon_request() -> None:
        """以事件固定请求取消后尚未释放 lease 的窗口。"""
        acquired.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            await release.wait()
            lease.active -= 1
            order.append("released")

    async def stop_source() -> None:
        """模拟停止来源已返回，但旧请求仍在退出清理中。"""
        order.append("stopped")
        if stop_fails:
            raise RuntimeError("offline stop failure")

    async def close_pool() -> None:
        """真实回收本地 SDK 池并检查调用瞬间的活动计数。"""
        order.append("closed")
        assert lease.active == 0
        await client.aclose()

    source = SimpleNamespace(is_running=True, stop=stop_source)
    runtime_bot._stream_loop_manager = source
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close_pool)
    monkeypatch.setattr("src.kernel.db.close_engine", AsyncMock())
    runtime_bot._owned_resources.add("database")
    task = manager.create_task(daemon_request(), name="chat_stream_offline", daemon=True).task
    assert task is not None
    await acquired.wait()
    supervisor = TaskManager()
    supervisor.shutdown_process_pool(wait=False)
    runner = supervisor.create_task(runtime_bot.shutdown()).task
    assert runner is not None
    try:
        await asyncio.wait_for(cancelling.wait(), 2)
        assert "closed" not in order
        assert lease.active == 1
        release.set()
        if stop_fails:
            with pytest.raises(BotShutdownError, match="聊天流: RuntimeError"):
                await runner
            # stop失败时有pending任务，不会关闭池
            assert lease in client._pools[asyncio.get_running_loop()].values()
            assert order == ["stopped", "released"]
        else:
            await runner
            assert not client._pools.get(asyncio.get_running_loop())
            assert order.index("stopped") < order.index("released") < order.index("closed")
        assert task.done()
        if not stop_fails:
            assert http.is_closed
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await client.aclose()
        manager.shutdown_process_pool(wait=False)


@pytest.mark.parametrize("blocked_phase", ["chat", "tasks", "fresh_tasks", "source"])
async def test_shutdown_per_step_timeout_leaves_active_pool_and_restores_terminal(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot, blocked_phase: str,
) -> None:
    """各步骤服从独立超时，超时不强关活动池且恢复终端。"""
    manager = TaskManager()
    runtime_bot.task_manager = manager
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    parser, signals = runtime_bot._command_parser, runtime_bot._signal_handler

    def handle(request: httpx.Request) -> httpx.Response:
        """为真实活动 lease 提供离线传输，禁止访问网络。"""
        return httpx.Response(200)

    client = gemini_client.GeminiChatClient(transport=httpx.MockTransport(handle))
    lease = await client._get_client({
        "api_key": "offline-timeout-key", "base_url": "https://offline.invalid",
        "timeout": 2, "extra_params": {"trust_env": False},
    })
    close = AsyncMock(wraps=client.aclose)
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close)
    monkeypatch.setattr("src.kernel.db.close_engine", AsyncMock(side_effect=RuntimeError("unexpected database")))

    async def blocked() -> None:
        """用事件固定取消已被交付但资源清理仍未完成的状态。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            lease.active -= 1

    source_task: asyncio.Task[Any] | None = None
    if blocked_phase == "source":
        source = SimpleNamespace(is_running=True, stop=blocked)
        runtime_bot._stream_loop_manager = source
    else:
        source_task = manager.create_task(
            blocked(), name="chat_stream_stale" if blocked_phase == "chat" else "other_daemon", daemon=True,
        ).task
        assert source_task is not None
        await entered.wait()
        if blocked_phase != "fresh_tasks":
            source_task.cancel()
            await cleaning.wait()
    supervisor = TaskManager()
    supervisor.shutdown_process_pool(wait=False)
    runner = supervisor.create_task(runtime_bot.shutdown(timeout=0.05)).task
    assert runner is not None
    started = asyncio.get_running_loop().time()
    try:
        done, _ = await asyncio.wait({runner}, timeout=1)
        assert runner in done, "shutdown 超过总预算仍在等待取消清理"
        with pytest.raises(BotShutdownError, match="TimeoutError"):
            await runner
        assert asyncio.get_running_loop().time() - started < 1
        close.assert_not_awaited()
        assert lease.active == 1
        assert not lease.http.is_closed
        assert lease in client._pools[asyncio.get_running_loop()].values()
        runtime_bot.ui.display_success.assert_not_called()
        parser.close.assert_called_once_with()
        signals.restore_handlers.assert_called_once_with()
        if source_task is not None:
            assert not source_task.done()
        else:
            await cleaning.wait()
            source_steps = [info.task for info in manager.get_all_tasks() if info.name and "聊天流" in info.name]
            assert len(source_steps) == 1
            source_task = source_steps[0]
            assert source_task is not None and not source_task.done()
    finally:
        release.set()
        if source_task is None:
            steps = [info.task for info in manager.get_all_tasks() if info.name and "聊天流" in info.name]
            source_task = steps[0] if steps else None
        if source_task is not None:
            await asyncio.gather(source_task, return_exceptions=True)
            assert source_task.done()
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        assert lease.active == 0
        await client.aclose()
        assert lease.http.is_closed
        assert not client._pools.get(asyncio.get_running_loop())
        manager.shutdown_process_pool(wait=False)


async def test_managed_shutdown_excludes_its_own_task(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
) -> None:
    """同一真实管理器托管非 daemon shutdown 时，不进入全局自身等待。"""
    manager = TaskManager()
    runtime_bot.task_manager = manager
    close = AsyncMock()
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close)
    monkeypatch.setattr("src.kernel.db.close_engine", AsyncMock(side_effect=RuntimeError("managed boundary")))
    runtime_bot._owned_resources.add("database")
    runner = manager.create_task(runtime_bot.shutdown(timeout=0.1), name="managed_shutdown").task
    assert runner is not None
    try:
        done, _ = await asyncio.wait({runner}, timeout=1)
        assert runner in done, "托管关闭等待了自身"
        with pytest.raises(BotShutdownError, match="主数据库: RuntimeError"):
            await runner
        close.assert_awaited_once_with()
        assert not runner.cancelled()
    finally:
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        manager.shutdown_process_pool(wait=False)


async def test_fallback_cancellation_always_restores_terminal_and_signals(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
) -> None:
    """取消落在 fallback 网络 await 内时，同步恢复各一次且取消继续传播。"""
    entered = asyncio.Event()
    runtime_bot.event_bus.publish = AsyncMock(side_effect=RuntimeError("primary failure"))
    parser, signals = runtime_bot._command_parser, runtime_bot._signal_handler

    async def close_pool() -> None:
        """使外部取消准确命中池清理窗口。"""
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close_pool)
    supervisor = TaskManager()
    supervisor.shutdown_process_pool(wait=False)
    runner = supervisor.create_task(runtime_bot.shutdown()).task
    assert runner is not None
    await asyncio.wait_for(entered.wait(), 2)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    parser.close.assert_called_once_with()
    signals.restore_handlers.assert_called_once_with()
    assert runtime_bot._command_parser is None
    assert runtime_bot._signal_handler is None


@pytest.mark.parametrize("parser_fails,signal_fails", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("network_fails", [False, True])
async def test_fallback_restoration_errors_do_not_replace_primary_failure(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
    parser_fails: bool, signal_fails: bool, network_fails: bool,
) -> None:
    """同步恢复彼此隔离，普通次要清理错误不能取代原始关闭失败。"""
    primary = RuntimeError("primary failure")
    runtime_bot.event_bus.publish = AsyncMock(side_effect=primary)
    parser, signals = runtime_bot._command_parser, runtime_bot._signal_handler
    parser.close.side_effect = ValueError("parser secret") if parser_fails else None
    signals.restore_handlers.side_effect = ValueError("signal secret") if signal_fails else None
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", AsyncMock(
        side_effect=ValueError("network secret") if network_fails else None,
    ))
    with pytest.raises(BotShutdownError) as caught:
        await runtime_bot.shutdown()
    assert "ON_STOP: RuntimeError" in str(caught.value)
    parser.close.assert_called_once_with()
    signals.restore_handlers.assert_called_once_with()
    assert runtime_bot._command_parser is None
    assert runtime_bot._signal_handler is None
    assert "secret" not in str(runtime_bot.logger.mock_calls)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("first_status", [200, 404, 401])
async def test_preflight_groups_effective_transport_and_covers_every_model(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot, first_status: int,
) -> None:
    """真实配置合并和 SDK 请求验证每组头、版本与网络策略，不生成内容。"""
    import src.core.config as core_config

    runtime_bot.config = SimpleNamespace(
        bot=SimpleNamespace(llm_preflight_check=True, llm_preflight_timeout=3),
        advanced=SimpleNamespace(trust_env=False, force_sync_http=False),
    )
    provider = APIProviderSection(
        name="offline", base_url="https://offline.invalid/proxy", api_key="offline-api-secret",
        client_type="gemini", timeout=2,
    )
    models = [
        ModelInfoSection(name=name, model_identifier=name, api_provider="offline", extra_params=extras)
        for name, extras in [
            ("first", {"headers": {"x-tenant": "tenant-secret"}}),
            ("same", {"headers": {"x-tenant": "tenant-secret"}}),
            ("version", {"gemini": {"api_version": "v1"}, "headers": {"x-tenant": "other-secret"}}),
            ("network", {"headers": {"x-tenant": "tenant-secret"}, "trust_env": True, "force_ipv4": True}),
        ]
    ]
    config = ModelConfig(api_providers=[provider], models=models)
    monkeypatch.setattr(core_config, "get_model_config", lambda: config)
    monkeypatch.setattr(core_config, "get_core_config", lambda: runtime_bot.config)
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []
    options: list[dict[str, Any]] = []
    ipv4_options: list[bool] = []
    original_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """只允许非生成模型枚举，并模拟首组失败而其余继续。"""
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/models")
        status = first_status if len(requests) == 1 else 200
        return httpx.Response(status, json={"models": []} if status == 200 else {
            "error": {"code": status, "message": "upstream-secret"},
        })

    def transport(*, force_ipv4: bool = False) -> httpx.MockTransport:
        """记录真实传输构建参数，并禁止任何 TCP 调用。"""
        ipv4_options.append(force_ipv4)
        return httpx.MockTransport(handle)

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """保留实际 HTTPX 客户端生命周期并捕获策略。"""
        options.append(dict(kwargs))
        kwargs["transport"] = httpx.MockTransport(handle)
        client = original_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(gemini_client, "build_httpx_transport", transport)
    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    await runtime_bot._preflight_llm_providers()
    assert len(requests) == 3
    assert [request.url.path for request in requests] == [
        "/proxy/v1beta/models", "/proxy/v1/models", "/proxy/v1beta/models",
    ]
    assert [request.headers.get("x-tenant") for request in requests] == [
        "tenant-secret", "other-secret", "tenant-secret",
    ]
    assert all(request.headers["x-goog-api-key"] == "offline-api-secret" for request in requests)
    assert ipv4_options == [False, False, True]
    assert [item["trust_env"] for item in options] == [False, False, True]
    assert all(client.is_closed for client in clients)
    logged = str(runtime_bot.logger.mock_calls)
    assert "secret" not in logged
    assert "4" in logged and "3" in logged
    runtime_bot.ui.update_phase_status.assert_called_with("LLM 预检", "已完成")


async def test_preflight_without_models_states_provider_only_boundary(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
) -> None:
    """无模型时只验证 provider 默认配置，并明确未覆盖模型传输配置。"""
    provider = APIProviderSection(name="offline", base_url="https://offline.invalid", api_key="offline-key", client_type="gemini")
    runtime_bot.config = SimpleNamespace(advanced=SimpleNamespace(trust_env=False))
    requests: list[httpx.Request] = []
    original_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """捕获唯一 provider 级枚举。"""
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """禁止无模型分支进行真实网络访问。"""
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    await _preflight_provider(runtime_bot, provider, [], 2)
    assert len(requests) == 1
    assert "provider" in str(runtime_bot.logger.mock_calls)
    assert "未覆盖模型" in str(runtime_bot.logger.mock_calls)


@pytest.mark.parametrize("remaining_pending", [False, True])
async def test_normal_shutdown_restores_once_without_active_tasks(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot, remaining_pending: bool,
) -> None:
    """无活动请求的正常退出仍恰好关闭池并恢复同步资源。"""
    parser, signals = runtime_bot._command_parser, runtime_bot._signal_handler
    close = AsyncMock()
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close)
    monkeypatch.setattr("src.kernel.db.close_engine", AsyncMock())
    monkeypatch.setattr("src.kernel.vector_db.close_all_vector_db_services", AsyncMock())
    monkeypatch.setattr("src.app.cloud_telemetry.close_cloud_telemetry_runtime", AsyncMock())
    monkeypatch.setattr("src.kernel.llm.stats.close_llm_stats_db", AsyncMock())
    monkeypatch.setattr("src.kernel.telemetry.close_telemetry_db", AsyncMock())
    monkeypatch.setattr("src.kernel.logger.shutdown_logger_system", MagicMock())
    unrelated_manager = TaskManager()
    unrelated_manager.shutdown_process_pool(wait=False)
    remaining = unrelated_manager.create_task(asyncio.Event().wait(), name="unrelated-host-task").task if remaining_pending else None
    runtime_bot._owned_resources.update(["database", "vector_db", "llm_stats", "telemetry", "logger"])
    try:
        await runtime_bot.shutdown()
        close.assert_awaited_once_with()
        parser.close.assert_called_once_with()
        signals.restore_handlers.assert_called_once_with()
        runtime_bot.ui.display_success.assert_called_once_with("关闭完成")
        if remaining is not None:
            assert not remaining.done() and remaining.cancelling() == 0
    finally:
        if remaining is not None:
            remaining.cancel()
            await asyncio.gather(remaining, return_exceptions=True)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
async def test_pool_close_step_is_bounded_and_late_cleanup_is_joined(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
    fallback: bool, interruption: str,
) -> None:
    """正常及兜底池关闭均可超时或取消，恢复同步资源并追踪迟到清理。"""
    manager = TaskManager()
    runtime_bot.task_manager = manager
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    parser, signals = runtime_bot._command_parser, runtime_bot._signal_handler
    if fallback:
        runtime_bot.event_bus.publish = AsyncMock(side_effect=RuntimeError("primary boundary"))

    def handle(request: httpx.Request) -> httpx.Response:
        """为待关闭的真实 idle lease 提供本地传输。"""
        return httpx.Response(200)

    client = gemini_client.GeminiChatClient(transport=httpx.MockTransport(handle))
    lease = await client._get_client({
        "api_key": "offline-pool-key", "base_url": "https://offline.invalid",
        "timeout": 2, "extra_params": {"trust_env": False},
    })
    lease.active -= 1

    async def close_pool() -> None:
        """固定关闭中的可取消窗口，释放事件前不假装 HTTP 已关闭。"""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            await client.aclose()

    close = AsyncMock(side_effect=close_pool)
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close)
    supervisor = TaskManager()
    supervisor.shutdown_process_pool(wait=False)
    runner = supervisor.create_task(runtime_bot.shutdown(timeout=0.1 if interruption == "timeout" else 5)).task
    assert runner is not None
    step: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        steps = [info for info in manager.get_all_tasks() if info.name and info.name.startswith("shutdown_step_")]
        assert len(steps) >= 1 and all(info.daemon for info in steps)
        step = next((info.task for info in steps if info.name and "默认模型资源" in info.name), None)
        assert step is not None
        if interruption == "cancel":
            runner.cancel()
        done, _ = await asyncio.wait({runner}, timeout=1)
        assert runner in done
        if interruption == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await runner
        else:
            with pytest.raises(BotShutdownError, match="ON_STOP: RuntimeError" if fallback else "默认模型资源: TimeoutError"):
                await runner
        await cleaning.wait()
        assert not step.done()
        assert lease.active == 0
        assert not lease.http.is_closed
        assert lease in client._pools[asyncio.get_running_loop()].values()
        close.assert_awaited_once_with()
        parser.close.assert_called_once_with()
        signals.restore_handlers.assert_called_once_with()
        runtime_bot.ui.display_success.assert_not_called()
    finally:
        release.set()
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        if step is None:
            matches = [info.task for info in manager.get_all_tasks() if info.name and "默认模型资源" in info.name]
            step = matches[0] if matches else None
        if step is not None:
            await asyncio.gather(step, return_exceptions=True)
            assert step.done()
        await client.aclose()
        assert lease.http.is_closed
        assert not client._pools.get(asyncio.get_running_loop())
        manager.shutdown_process_pool(wait=False)


async def test_failure_before_chat_stop_collects_all_step_errors(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
) -> None:
    """前置遥测失败时，后续步骤仍继续，最终汇总所有错误。"""
    primary = RuntimeError("telemetry secret")
    secondary = RuntimeError("model close boundary")
    runtime_bot._owned_resources.add("telemetry")
    runtime_bot._record_telemetry_event = AsyncMock(side_effect=[primary, None])
    monkeypatch.setattr(runtime_bot, "_close_telemetry", AsyncMock())
    close = AsyncMock(side_effect=secondary)
    source = SimpleNamespace(is_running=True, stop=AsyncMock())
    runtime_bot._stream_loop_manager = source
    monkeypatch.setattr("src.kernel.llm.model_client.runtime.close_default_model_clients", close)
    with pytest.raises(BotShutdownError, match="关闭开始遥测: RuntimeError.*默认模型资源: RuntimeError") as caught:
        await runtime_bot.shutdown()
    source.stop.assert_awaited_once_with()
    close.assert_awaited_once_with()
    assert runtime_bot._record_telemetry_event.await_count == 2
    assert "secret" not in str(caught.value)
    runtime_bot.ui.display_success.assert_not_called()


@pytest.mark.parametrize("bad_extra,bad_timeout", [
    ({"query": {"secret-query": "secret-value"}}, 2),
    ({"headers": {"Authorization": "secret-header"}}, 2),
    ({"gemini": {"api_version": "secret-version"}}, 2),
    ({}, 0),
])
@pytest.mark.parametrize("include_valid", [False, True])
async def test_invalid_transport_configuration_does_not_block_valid_group(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
    bad_extra: dict[str, Any], bad_timeout: int, include_valid: bool,
) -> None:
    """坏配置显式未验证，后续有效组继续；全部失败也不能改探测 provider 默认值。"""
    runtime_bot.config = SimpleNamespace(advanced=SimpleNamespace(trust_env=False))
    provider = APIProviderSection(
        name="offline", base_url="https://offline.invalid", api_key="unused-provider-secret",
        client_type="gemini",
    )
    bad = {
        "api_provider": "offline", "base_url": "https://offline.invalid",
        "api_key": "bad-api-secret", "timeout": bad_timeout, "extra_params": bad_extra,
    }
    valid = {
        "api_provider": "offline", "base_url": "https://offline.invalid",
        "api_key": "valid-api-secret", "timeout": 2,
        "extra_params": {"headers": {"x-tenant": "valid-tenant-secret"}, "trust_env": False},
    }
    requests: list[httpx.Request] = []
    original_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """仅接收有效组的 SDK 非生成请求。"""
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """阻止有效组走真实网络。"""
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    foreign = {"api_provider": "other-provider"}
    await _preflight_provider(runtime_bot,
        provider, [foreign, bad, valid] if include_valid else [foreign, bad], 2,
    )
    assert len(requests) == int(include_valid)
    if include_valid:
        assert requests[0].method == "GET"
        assert requests[0].url.path == "/v1beta/models"
        assert requests[0].headers["x-goog-api-key"] == "valid-api-secret"
        assert requests[0].headers["x-tenant"] == "valid-tenant-secret"
    logged = str(runtime_bot.logger.mock_calls)
    assert "未验证" in logged
    assert "secret" not in logged
    assert "仅 provider" not in logged


@pytest.mark.parametrize("timeout", [0.0, float("inf"), float("nan")])
async def test_preflight_invalid_budget_is_unverified_without_request(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot, timeout: float,
) -> None:
    """非法预检总上限明确未验证，不建客户端、不发送请求或降级探测。"""
    runtime_bot.config = SimpleNamespace(advanced=SimpleNamespace(trust_env=False))
    provider = APIProviderSection(name="offline", api_key="unused-secret", client_type="gemini")
    http = MagicMock(side_effect=AssertionError("不得建立客户端"))
    monkeypatch.setattr(httpx, "AsyncClient", http)
    await _preflight_provider(runtime_bot, provider, [{
        "api_provider": "offline", "api_key": "offline-secret", "timeout": 2,
        "base_url": "https://offline.invalid", "extra_params": {"trust_env": False},
    }], timeout)
    http.assert_not_called()
    assert "未验证" in str(runtime_bot.logger.warning.mock_calls)
    assert "secret" not in str(runtime_bot.logger.mock_calls)


async def test_model_merge_failure_leaves_other_models_covered(
    monkeypatch: pytest.MonkeyPatch, runtime_bot: Bot,
) -> None:
    """一项合并失败只标记该项未验证，其余项仍通过真实有效配置入口。"""
    runtime_bot.config = SimpleNamespace(advanced=SimpleNamespace(trust_env=False, force_sync_http=False))
    provider = APIProviderSection(
        name="offline", base_url="https://offline.invalid", api_key="merge-api-secret", client_type="gemini",
    )
    models = [
        ModelInfoSection(name=name, model_identifier=name, api_provider="offline")
        for name in ["broken", "valid"]
    ]
    config = ModelConfig(api_providers=[provider], models=models)
    original_merge = ModelConfig.get_model_set_by_name
    original_client = httpx.AsyncClient
    requests: list[httpx.Request] = []
    merged: list[str] = []

    def merge(
        self: ModelConfig, model_name: str, *,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> ModelSet:
        """在单项注入合并错误，其余保留生产实现。"""
        merged.append(model_name)
        if model_name == "broken":
            raise ValueError("merge-error-secret")
        return original_merge(self, model_name, temperature=temperature, max_tokens=max_tokens)

    def handle(request: httpx.Request) -> httpx.Response:
        """捕获有效模型唯一的非生成枚举。"""
        requests.append(request)
        return httpx.Response(200, json={"models": []})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """为真实 SDK 注入本地 HTTPX 传输。"""
        kwargs["transport"] = httpx.MockTransport(handle)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(ModelConfig, "get_model_set_by_name", merge)
    monkeypatch.setattr("src.core.config.get_model_config", lambda: config)
    monkeypatch.setattr("src.core.config.get_core_config", lambda: runtime_bot.config)
    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    await _preflight_provider(runtime_bot, provider, models, 2)
    assert merged == ["broken", "valid"]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1beta/models"
    assert "合并失败" in str(runtime_bot.logger.warning.mock_calls)
    assert "secret" not in str(runtime_bot.logger.mock_calls)
