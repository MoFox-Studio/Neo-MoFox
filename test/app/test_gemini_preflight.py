"""Offline official-SDK Gemini preflight and shutdown contract tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.runtime.bot import Bot
from src.core.config.model_config import APIProviderSection, ModelInfoSection


@pytest.mark.parametrize("base_url,version,path", [
    ("https://gateway.invalid/v1beta", None, "/v1beta/models"),
    ("https://gateway.invalid/proxy", "v1", "/proxy/v1/models"),
])
@pytest.mark.parametrize("status", [200, 401, 429, 404, 503, 0])
async def test_preflight_uses_sdk_list_only(
    monkeypatch: pytest.MonkeyPatch, base_url: str, version: str | None,
    path: str, status: int,
) -> None:
    """Real SDK issues one non-generating request with owned HTTP transport."""
    requests: list[httpx.Request] = []
    clients: list[httpx.AsyncClient] = []
    options: list[dict] = []
    original_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        """Never use the network and expose a sensitive error body deliberately."""
        requests.append(request)
        if status == 0:
            raise httpx.ConnectError("fixture-secret-key", request=request)
        if status == 200:
            return httpx.Response(200, json={"models": []})
        return httpx.Response(status, json={"error": {"code": status, "message": "fixture-secret-key"}})

    def make_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        """Inject the offline transport while recording runtime policy."""
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
    monkeypatch.setattr("src.core.config.get_model_config", lambda: SimpleNamespace(
        api_providers=[provider], models=[model]
    ))
    bot = Bot()
    bot.config = SimpleNamespace(bot=SimpleNamespace(llm_preflight_check=True,
        llm_preflight_timeout=3), advanced=SimpleNamespace(trust_env=False))
    bot.logger = MagicMock()
    bot.ui = MagicMock()
    await bot._preflight_llm_providers()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == path
    assert requests[0].headers.get("x-goog-api-key") == "fixture-secret-key"
    assert "authorization" not in requests[0].headers
    assert all(client.is_closed for client in clients)
    assert any(item.get("trust_env") is False and item.get("timeout") == 2.0 for item in options)
    assert "fixture-secret-key" not in str(bot.logger.mock_calls)
    if status == 404:
        assert "跳过不支持 models.list" in str(bot.logger.info.mock_calls)
    elif status != 200:
        assert bot.logger.warning.called


async def test_shutdown_closes_gemini_even_when_shutdown_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resources are awaited on the runtime loop even after an earlier failure."""
    from src.app.runtime.exceptions import BotShutdownError
    from src.kernel.llm.model_client import gemini_client

    close = AsyncMock()
    monkeypatch.setattr(gemini_client, "close_gemini_clients", close)
    bot = Bot()
    bot.logger = MagicMock()
    bot.ui = MagicMock()
    bot._record_telemetry_event = AsyncMock()
    bot._unload_all_plugins = AsyncMock(side_effect=RuntimeError("fixture failure"))
    with pytest.raises(BotShutdownError):
        await bot.shutdown()
    close.assert_awaited_once()
    assert not asyncio.get_running_loop().is_closed()
