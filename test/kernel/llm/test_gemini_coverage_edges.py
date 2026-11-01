"""Offline regressions for Gemini configuration and client ownership edges."""
from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from PIL import Image as PILImage

from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.exceptions import LLMConfigurationError, LLMContentFilterError
from src.kernel.llm.model_client.base import ModelClientResult
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.gemini_codec import encode_payloads, target_identity
from src.kernel.llm.payload import Image
from test.kernel.llm.test_gemini_client import model, response


async def create(client: GeminiChatClient, entry: object, **overrides: object) -> ModelClientResult:
    """Exercise the adapter entry point with a valid minimal conversation."""
    kwargs = dict(model_name="test-model", payloads=[LLMPayload(ROLE.USER, Text("hi"))],
                  tools=[], request_name="edges", model_set=entry, stream=False)
    kwargs.update(overrides)
    return await client.create(**kwargs)


@pytest.mark.parametrize("changes, message", [
    ({"extra_params": None}, "extra_params must be an object"),
    ({"extra_params": []}, "extra_params must be an object"),
    ({"extra_params": {"gemini": {"unknown": True}}}, "Unknown Gemini"),
    ({"extra_params": {"top_p": 0.5}}, "Use extra_params.gemini"),
    ({"extra_params": {"body": {"temperature": 1}}}, "Use extra_params.gemini"),
    ({"max_tokens": True}, "max_tokens"),
    ({"max_tokens": 0}, "max_tokens"),
    ({"max_tokens": -1}, "max_tokens"),
    ({"max_tokens": 1.5}, "max_tokens"),
    ({"temperature": True}, "temperature"),
    ({"temperature": "SECRET_INVALID"}, "temperature"),
    ({"temperature": float("nan")}, "temperature"),
    ({"temperature": float("inf")}, "temperature"),
])
async def test_invalid_model_settings_fail_before_http(changes: dict, message: str) -> None:
    """Configuration errors remain domain errors, not raw SDK/Pydantic failures."""
    entry = model()
    entry.update(changes)
    calls = []
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: calls.append(req)))
    try:
        with pytest.raises(LLMConfigurationError, match=message) as exc:
            await create(client, entry)
        assert "SECRET_INVALID" not in str(exc.value)
        assert not calls
        assert not client._pools
    finally:
        await client.aclose()


@pytest.mark.parametrize("field", [
    "http_options", "system_instruction", "tools", "automatic_function_calling",
    "automatic_continuation", "continuation_token", "candidate_count",
    "max_output_tokens", "temperature", "unknown_field",
])
async def test_generation_config_cannot_override_reserved_fields(field: str) -> None:
    """SDK ownership and MoFox top-level settings cannot be bypassed."""
    entry = model()
    entry["extra_params"] = {"gemini": {"generation_config": {field: "SECRET_INVALID"}}}
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected HTTP")))
    with pytest.raises(LLMConfigurationError, match="unknown or reserved") as exc:
        await create(client, entry)
    assert "SECRET_INVALID" not in str(exc.value)
    assert not client._pools


@pytest.mark.parametrize("timeout", [None, 0, -1, float("nan"), float("inf"), "5"])
async def test_invalid_timeout_is_rejected(timeout: object) -> None:
    """Invalid timeout budgets never reach SDK construction or HTTP."""
    entry = model()
    entry["timeout"] = timeout
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected HTTP")))
    with pytest.raises(LLMConfigurationError, match="positive finite"):
        await create(client, entry)
    assert not client._pools


@pytest.mark.parametrize("extra", [
    {"query": {"key": "dummy"}},
    {"headers": {"AUTHORIZATION": "dummy"}},
    {"headers": {"X-Goog-Api-Key": "dummy"}},
    {"headers": {"Host": "offline.invalid"}},
    {"headers": {"Content-Type": "text/plain"}},
])
async def test_transport_overrides_cannot_change_auth_or_routing(extra: dict) -> None:
    """Header checks are case insensitive and query overrides are forbidden."""
    entry = model()
    entry["extra_params"] = extra
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected HTTP")))
    with pytest.raises(LLMConfigurationError, match="overrid"):
        await create(client, entry)
    assert not client._pools


async def test_custom_header_and_timeout_reach_real_sdk_request() -> None:
    """Safe custom headers remain usable alongside the SDK API key header."""
    observed = []
    def handler(req: httpx.Request) -> httpx.Response:
        """记录 SDK 实际请求，以核对自定义请求头和超时配置。"""
        observed.append(req)
        return httpx.Response(200, json=response())
    entry = model()
    entry["timeout"] = 2.5
    entry["extra_params"] = {"headers": {"X-Mofox-Test": "offline"}}
    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    try:
        assert (await create(client, entry)).message == "ok"
        assert observed[0].headers["x-mofox-test"] == "offline"
        assert observed[0].headers["x-goog-api-key"] == "dummy"
        assert observed[0].extensions["timeout"]["read"] == 2.5
    finally:
        await client.aclose()


@pytest.mark.parametrize("name", ["", "models/", "a/b", "a:b", "a?b", "a&b", "a#b", "a\\b", ".", ".."])
async def test_invalid_model_identifier_is_rejected(name: str) -> None:
    """非法模型标识必须在发送 HTTP 请求前被拒绝。"""
    client = GeminiChatClient()
    with pytest.raises(LLMConfigurationError, match="single model ID"):
        await create(client, model(), model_name=name)


@pytest.mark.parametrize("entry", [None, [], "model"])
async def test_model_set_requires_one_mapping(entry: object) -> None:
    """模型配置必须是单个映射，不能使用空值、列表或字符串。"""
    with pytest.raises(LLMConfigurationError, match="one model entry"):
        await create(GeminiChatClient(), entry)


@pytest.mark.parametrize("payloads", [[], [LLMPayload(ROLE.SYSTEM, Text("system only"))]])
async def test_empty_conversation_is_rejected(payloads: list) -> None:
    """空对话或只有系统指令时必须拒绝生成。"""
    with pytest.raises(LLMConfigurationError, match="no conversation contents"):
        await create(GeminiChatClient(), model(), payloads=payloads)


async def test_sdk_constructor_failure_closes_injected_http_client() -> None:
    """Failed construction cannot orphan the MoFox-owned HTTP client."""
    clients = []
    original = httpx.AsyncClient
    def track_http(**kwargs: Any) -> httpx.AsyncClient:
        """透传 HTTPX 构造参数，并保留客户端以检查异常后的关闭状态。"""
        client = original(**kwargs)
        clients.append(client)
        return client
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected HTTP")))
    with patch("src.kernel.llm.model_client.gemini_client.httpx.AsyncClient", side_effect=track_http), \
         patch("src.kernel.llm.model_client.gemini_client.genai.Client", side_effect=RuntimeError("constructor failed")):
        with pytest.raises(RuntimeError, match="constructor failed"):
            await create(client, model())
    assert len(clients) == 1
    assert clients[0].is_closed
    assert not client._pools[asyncio.get_running_loop()]


async def test_pool_evicts_oldest_idle_but_preserves_active_leases() -> None:
    """Capacity is bounded and only an idle entry may be evicted."""
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected HTTP")))
    leases = []
    try:
        for index in range(16):
            leases.append(await client._get_client(dict(model(), api_key=f"dummy-{index}")))
        leases[1].active -= 1
        replacement = await client._get_client(dict(model(), api_key="dummy-new"))
        leases.append(replacement)
        pool = client._pools[asyncio.get_running_loop()]
        assert len(pool) == 16
        assert leases[1].http.is_closed
        assert not leases[0].http.is_closed
        assert all(entry is not leases[1] for entry in pool.values())
        with pytest.raises(LLMConfigurationError, match="pool is busy"):
            await client._get_client(dict(model(), api_key="dummy-overflow"))
        assert len(pool) == 16
        await client.aclose()
        assert len(pool) == 16  # Shutdown cannot close active leases either.
    finally:
        for lease in leases:
            lease.active = 0
        await client.aclose()
    assert not pool
    assert all(lease.http.is_closed for lease in leases)


async def test_concurrent_same_key_shares_one_client_and_counts_borrows() -> None:
    """同配置的并发借用必须共享客户端并准确累计占用。"""
    client = GeminiChatClient(transport=httpx.MockTransport(lambda req: pytest.fail("unexpected HTTP")))
    leases = await asyncio.gather(*(client._get_client(model()) for _ in range(8)))
    try:
        assert all(lease is leases[0] for lease in leases)
        assert leases[0].active == 8
        assert len(client._pools[asyncio.get_running_loop()]) == 1
    finally:
        for lease in leases:
            lease.active -= 1
        await client.aclose()
    assert leases[0].http.is_closed


async def test_stream_prompt_block_is_terminal_and_releases_lease() -> None:
    """A blocked prefetched chunk is a content filter error, never an empty success."""
    def handler(req: httpx.Request) -> httpx.Response:
        """返回提示词安全拦截事件，检查预取失败时的租约释放。"""
        body = {"promptFeedback": {"blockReason": "SAFETY"}}
        return httpx.Response(200, text="data: " + json.dumps(body) + "\n\n",
                              headers={"content-type": "text/event-stream"})
    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    try:
        result = await create(client, model(), stream=True)
        assert result.stream_iter.__aiter__() is result.stream_iter
        with pytest.raises(LLMContentFilterError, match="blocked the prompt"):
            await anext(result.stream_iter)
        pool = client._pools[asyncio.get_running_loop()]
        assert all(lease.active == 0 for lease in pool.values())
        await result.stream_iter.aclose()
        assert all(lease.active == 0 for lease in pool.values())
    finally:
        await client.aclose()


@pytest.mark.parametrize("name", [None, "", 123])
def test_tool_declaration_requires_nonempty_string_name(name: object) -> None:
    """工具声明必须使用非空字符串名称。"""
    tool = SimpleNamespace(to_schema=lambda: {"function": {"name": name}})
    with pytest.raises(LLMConfigurationError, match="tool requires a name"):
        encode_payloads([], target_identity(model()), [tool])


def test_valid_but_unsupported_image_mime_is_rejected() -> None:
    """A genuine BMP exercises MIME rejection rather than base64/decode failure."""
    data = BytesIO()
    with PILImage.new("RGB", (1, 1)) as image:
        image.save(data, format="BMP")
    payload = LLMPayload(ROLE.USER, Image(base64.b64encode(data.getvalue()).decode("ascii")))
    with pytest.raises(LLMConfigurationError, match="MIME could not be validated"):
        encode_payloads([payload], target_identity(model()), [])
