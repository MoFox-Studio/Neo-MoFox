"""离线验证官方 Gemini SDK 的非生成预检和异常关闭契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.runtime.bot import Bot
from src.core.config.model_config import APIProviderSection, ModelConfig, ModelInfoSection


@pytest.mark.parametrize("base_url,version,path", [
    ("https://gateway.invalid/v1beta", None, "/v1beta/models"),
    ("https://gateway.invalid/proxy", "v1", "/proxy/v1/models"),
])
@pytest.mark.parametrize("status", [200, 401, 429, 404, 503, 0])
async def test_preflight_uses_sdk_list_only(
    monkeypatch: pytest.MonkeyPatch, base_url: str, version: str | None,
    path: str, status: int,
) -> None:
    """经真实配置合并后仅枚举一次，保留各状态及资源与脱敏断言。"""
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []
    options: list[dict[str, Any]] = []
    original_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """用本地传输模拟状态码，并在错误体中放入脱敏哨兵。"""
        requests.append(request)
        if status == 0:
            raise httpx.ConnectError("fixture-secret-key", request=request)
        if status == 200:
            return httpx.Response(200, json={"models": []})
        return httpx.Response(status, json={"error": {"code": status, "message": "fixture-secret-key"}})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """注入离线传输，同时记录真实客户端的策略和生命周期。"""
        options.append(dict(kwargs))
        kwargs["transport"] = httpx.MockTransport(handle)
        client = original_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    provider = APIProviderSection(name="native", base_url=base_url,
                                  api_key="fixture-secret-key", client_type="gemini", timeout=2)
    extra = {"gemini": {"api_version": version}} if version else {}
    model = ModelInfoSection(name="fixture", model_identifier="gemini-fixture",
                            api_provider="native", extra_params=extra)
    model_config = ModelConfig(api_providers=[provider], models=[model])
    monkeypatch.setattr("src.core.config.get_model_config", lambda: model_config)
    bot = Bot()
    bot.config = SimpleNamespace(bot=SimpleNamespace(llm_preflight_check=True,
        llm_preflight_timeout=3), advanced=SimpleNamespace(trust_env=False, force_sync_http=False))
    monkeypatch.setattr("src.core.config.get_core_config", lambda: bot.config)
    bot.logger = MagicMock()
    bot.ui = MagicMock()
    await bot._preflight_llm_providers()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == path
    assert requests[0].headers.get("x-goog-api-key") == "fixture-secret-key"
    assert "authorization" not in requests[0].headers
    assert all(client.is_closed for client in clients)
    assert any(
        item.get("trust_env") is False
        and isinstance(item.get("timeout"), httpx.Timeout)
        and item["timeout"].as_dict() == httpx.Timeout(2.0).as_dict()
        for item in options
    )
    assert "fixture-secret-key" not in str(bot.logger.mock_calls)
    if status == 404:
        assert "跳过不支持 models.list" in str(bot.logger.info.mock_calls)
    elif status != 200:
        assert bot.logger.warning.called


async def test_shutdown_closes_gemini_even_when_shutdown_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """前序关闭失败时，仍在原事件循环等待 Gemini 资源清理。"""
    from src.app.runtime.exceptions import BotShutdownError
    from src.kernel.llm.model_client import runtime as model_runtime

    close = AsyncMock()
    monkeypatch.setattr(model_runtime, "close_default_model_clients", close)
    bot = Bot()
    bot.logger = MagicMock()
    bot.ui = MagicMock()
    bot._record_telemetry_event = AsyncMock()
    bot.event_bus = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("fixture failure")))
    bot.task_manager = MagicMock()
    bot.task_manager.get_active_tasks.return_value = []
    bot.plugin_manager = None
    bot.load_order = []
    bot.load_results = {}
    bot.media_manager = None
    bot._stop_logger_cleanup = None

    def create_task(coro: Any, **kwargs: Any) -> SimpleNamespace:
        """为有界关闭步骤提供真实 asyncio 句柄。"""
        return SimpleNamespace(task=asyncio.create_task(coro, name=kwargs.get("name")))

    bot.task_manager.create_task.side_effect = create_task
    bot.task_manager.shutdown_process_pool = MagicMock()
    bot.task_manager.cleanup_tasks = MagicMock()
    stream_manager = SimpleNamespace(is_running=False, stop=AsyncMock())
    bot._stream_loop_manager = stream_manager
    with pytest.raises(BotShutdownError):
        await bot.shutdown()
    close.assert_awaited_once()
    assert not asyncio.get_running_loop().is_closed()
