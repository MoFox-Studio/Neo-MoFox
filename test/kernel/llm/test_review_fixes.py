"""对抗性审查修复项的回归测试。

覆盖本次审查确认并修复的问题：

1. provider 不可用（未注册 / 已移除类型）必须能走 policy failover，
   而不是直接中断整条重试链。
2. 已移除的 ``client_type`` 及其别名必须明确拒绝，不得静默回退到 OpenAI。
3. 非法 ``client_type`` 不得被配置加载器静默改写成默认值。
4. Rerank 的 endpoint 必须是合法 http(s) URL，且 ``extra_headers``
   不能覆盖 ``Authorization``。
5. Rerank 429 必须携带服务端给出的 ``Retry-After``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from src.kernel.llm import (
    LLMConfigurationError,
    LLMProviderUnavailableError,
)
from src.kernel.llm.exceptions import decide_retry
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.model_client.openai_client import OpenAIChatClient
from src.kernel.llm.types import ModelEntry


def _model_entry(**overrides: Any) -> ModelEntry:
    """构造最小可用的 ModelEntry。"""
    base: dict[str, Any] = {
        "api_provider": "test",
        "base_url": "https://example.com/v1",
        "model_identifier": "test-model",
        "api_key": "sk-test",
        "client_type": "openai",
        "max_retry": 0,
        "timeout": 10.0,
        "retry_interval": 1.0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.0,
        "max_tokens": 256,
        "max_context": 8192,
        "tool_call_compat": False,
        "extra_params": {},
    }
    base.update(overrides)
    return cast(ModelEntry, base)


# ---------------------------------------------------------------------------
# 1. provider 不可用 -> 可重试（failover）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "client_type",
    ["gemini", "bedrock", "google", "vertex_ai", "amazon_bedrock"],
)
def test_unregistered_provider_is_retryable_for_failover(client_type: str) -> None:
    """未注册或不支持的 provider 必须允许 failover。"""
    registry = ModelClientRegistry()
    registry.gemini = None

    with pytest.raises(LLMProviderUnavailableError):
        registry.get_client_for_model(_model_entry(client_type=client_type))

    decision = decide_retry(LLMProviderUnavailableError("boom"))
    assert decision.retryable is True
    assert decision.reason == "provider_unavailable"


def test_provider_unavailable_error_is_configuration_error_subclass() -> None:
    """新异常保持 LLMConfigurationError 兼容性，老调用方不会因此崩溃。"""
    assert issubclass(LLMProviderUnavailableError, LLMConfigurationError)


def test_plain_configuration_error_stays_non_retryable() -> None:
    """普通配置错误（请求本身不合法）仍然不可重试。"""
    decision = decide_retry(LLMConfigurationError("bad payload"))
    assert decision.retryable is False
    assert decision.reason == "configuration_error"


def test_removed_client_type_in_llm_registry_is_retryable() -> None:
    """已移除类型的不可用错误仍允许 failover，不会硬失败整链。"""
    registry = ModelClientRegistry()

    with pytest.raises(LLMProviderUnavailableError, match="已从核心移除") as error:
        registry.get_client_for_model(_model_entry(client_type="jev"))

    assert "jev_api" not in str(error.value)
    assert decide_retry(error.value).retryable is True


# ---------------------------------------------------------------------------
# 2. 已移除 client_type 的别名拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "client_type",
    ["jev", "typesafe", "type_safe", " JEV ", " TypeSafe ", "type-safe"],
)
def test_removed_alias_variants_all_rejected_in_llm_registry(client_type: str) -> None:
    """已移除类型的别名必须明确拒绝，不再指向已删除的插件 API。"""
    registry = ModelClientRegistry()

    with pytest.raises(LLMProviderUnavailableError, match="已从核心移除") as error:
        registry.get_client_for_model(_model_entry(client_type=client_type))
    assert "jev_api" not in str(error.value)


@pytest.mark.parametrize(
    "client_type",
    ["jev-api", "jev-client", "typesafe-ai", "typesafe-system-one",
     " JEV_API ", " JEV_CLIENT ", " TYPESAFE_AI ", " TYPESAFE_SYSTEM_ONE "],
)
def test_removed_alias_variants_do_not_fall_back_to_openai(client_type: str) -> None:
    """已移除类型的连字符、大小写及下划线变体不得静默回退到 OpenAI。"""
    registry = ModelClientRegistry()

    with pytest.raises(LLMProviderUnavailableError, match="已从核心移除") as error:
        registry.get_client_for_model(_model_entry(client_type=client_type))
    assert "jev_api" not in str(error.value)


def test_unknown_client_type_still_falls_back_to_openai() -> None:
    """真正未知的 client_type 保持历史回退行为，避免无意扩大破坏性变更。"""
    registry = ModelClientRegistry()

    client = registry.get_client_for_model(_model_entry(client_type="unknown_provider"))

    assert client is registry.openai


@pytest.mark.parametrize("client_type", ["jev", "typesafe", "type_safe"])
def test_model_config_rejects_removed_aliases(client_type: str) -> None:
    """配置层拒绝曾经允许的已移除类型，不能生成可用 provider。"""
    from src.core.config.model_config import APIProviderSection

    with pytest.raises(ValueError, match="client_type"):
        APIProviderSection(name="p", client_type=client_type)


# ---------------------------------------------------------------------------
# 3. 非法 client_type 不得被配置加载器静默改写
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("client_type", ["gemimni", "jev", "typesafe", "type_safe"])
def test_invalid_client_type_in_config_raises_instead_of_silently_defaulting(
    tmp_path: Path, client_type: str,
) -> None:
    """写错或已移除的 client_type 必须报错，而不是被静默改成 openai。"""
    from src.core.config.model_config import ModelConfig

    toml_path = tmp_path / "models.toml"
    toml_path.write_text(
        "\n".join(
            [
                "[[api_providers]]",
                'name = "broken"',
                'base_url = "https://example.com/v1"',
                'api_key = "sk-test"',
                f'client_type = "{client_type}"',
                "",
                "[[models]]",
                'model_identifier = "m1"',
                'name = "m1"',
                'api_provider = "broken"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="client_type"):
        ModelConfig.load(str(toml_path))


def test_valid_client_type_in_config_is_preserved(tmp_path: Path) -> None:
    """合法 Gemini 取值照常保留。"""
    from src.core.config.model_config import ModelConfig

    toml_path = tmp_path / "models.toml"
    toml_path.write_text(
        "\n".join(
            [
                "[[api_providers]]",
                'name = "ok"',
                'base_url = "https://example.com/v1"',
                'api_key = "sk-test"',
                'client_type = "gemini"',
                "",
                "[[models]]",
                'model_identifier = "m1"',
                'name = "m1"',
                'api_provider = "ok"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = ModelConfig.load(str(toml_path))

    assert config.api_providers[0].client_type == "gemini"


# ---------------------------------------------------------------------------
# 4. Rerank endpoint 与 Authorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///etc/passwd",
        "not-a-url",
        "https://user:pw@rerank.example/rerank",
    ],
)
def test_rerank_endpoint_must_be_valid_http_url(endpoint: str) -> None:
    """rerank endpoint 必须是合法 http(s) URL，且不含 userinfo。"""
    client = OpenAIChatClient()

    with pytest.raises(LLMConfigurationError):
        client._resolve_rerank_endpoint(
            model_set={"rerank_url": endpoint},
            base_url=None,
            extra_params={},
        )


def test_rerank_endpoint_defaults_to_base_url_rerank() -> None:
    """未配置显式 endpoint 时由 base_url 拼接 /rerank。"""
    client = OpenAIChatClient()

    endpoint = client._resolve_rerank_endpoint(
        model_set={},
        base_url="https://provider.example/v1",
        extra_params={},
    )

    assert endpoint == "https://provider.example/v1/rerank"


@pytest.mark.asyncio
async def test_rerank_extra_headers_cannot_override_authorization(monkeypatch) -> None:
    """extra_headers 不能覆盖 Authorization，否则密钥可被发往攻击者主机。"""
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        is_success = True

        def json(self) -> dict[str, Any]:
            return {"results": []}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    client = OpenAIChatClient()
    try:
        await client.create_rerank(
            model_name="m",
            query="q",
            documents=["d"],
            top_n=None,
            request_name="r",
            model_set={
                "api_key": "sk-real",
                "base_url": "https://provider.example/v1",
                "extra_params": {
                    "headers": {
                        "Authorization": "Bearer attacker",
                        "Content-Type": "text/plain",
                        "X-Tenant": "tenant-a",
                    }
                },
            },
        )
    except Exception:
        # fake response 缺少部分属性；本用例只关心请求头是否被污染
        pass

    headers = captured.get("post_kwargs", {}).get("headers", {})
    assert headers.get("Authorization") == "Bearer sk-real"
    assert headers.get("Content-Type") == "application/json"
    assert headers.get("X-Tenant") == "tenant-a"


# ---------------------------------------------------------------------------
# 5. Rerank 429 Retry-After
# ---------------------------------------------------------------------------


def test_rerank_rate_limit_carries_retry_after() -> None:
    """429 必须带上服务端给出的 Retry-After，供 policy 计算退避。"""
    client = OpenAIChatClient()

    response = httpx.Response(
        429,
        headers={"Retry-After": "12"},
        request=httpx.Request("POST", "https://provider.example/v1/rerank"),
    )

    from src.kernel.llm import LLMRateLimitError

    with pytest.raises(LLMRateLimitError) as excinfo:
        client._raise_for_rerank_status(response, model_name="m")

    assert excinfo.value.retry_after == 12.0


def test_rerank_rate_limit_without_retry_after_is_none() -> None:
    """没有合法 Retry-After 时保持 None。"""
    client = OpenAIChatClient()

    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://provider.example/v1/rerank"),
    )

    from src.kernel.llm import LLMRateLimitError

    with pytest.raises(LLMRateLimitError) as excinfo:
        client._raise_for_rerank_status(response, model_name="m")

    assert excinfo.value.retry_after is None
