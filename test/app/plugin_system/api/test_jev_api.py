"""插件 Jev 公共 API 的单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.plugin_system.api import jev_api
from src.kernel.jev import JevClientConfig, JevResponse


def test_create_jev_client_from_explicit_config() -> None:
    """公共 API 能从显式配置创建独立客户端。"""
    config = JevClientConfig(api_key="test-key")
    client = jev_api.create_jev_client(config=config)

    assert client.config is config


def test_create_jev_client_uses_core_provider_configuration(monkeypatch) -> None:
    """Jev 可以复用 Core provider 的连接字段，但不进入 LLM registry。"""
    provider = MagicMock()
    provider.get_api_key.return_value = "provider-key"
    provider.base_url = "https://jev.example"
    provider.client_type = "jev"
    provider.timeout = 7
    provider.max_retry = 2
    provider.retry_interval = 3
    model = MagicMock()
    model.api_provider = "typesafe"
    model.model_identifier = "jev-1.13.0"
    model.extra_params = {
        "trust_env": False,
        "headers": {"X-Tenant": "tenant-a"},
        "query": {"region": "cn"},
    }
    config = MagicMock()
    config.get_provider.return_value = provider
    config.get_model.return_value = model
    monkeypatch.setattr("src.core.config.get_model_config", lambda: config)

    client = jev_api.create_jev_client(configured_model_name="jev-prod")

    assert client.config.api_key == "provider-key"
    assert client.config.base_url == "https://jev.example"
    assert client.config.model == "jev-1.13.0"
    assert client.config.trust_env is False
    assert client.config.max_retries == provider.max_retry
    assert client.config.extra_headers == {"X-Tenant": "tenant-a"}
    assert client.config.extra_query == {"region": "cn"}


def test_create_jev_client_from_core_model_rejects_non_jev_client_type(monkeypatch) -> None:
    """配置桥接只接受显式 Jev provider，避免误用聊天 provider。"""
    provider = MagicMock()
    provider.client_type = "openai"
    config = MagicMock()
    config.get_provider.return_value = provider
    config.get_model.return_value = MagicMock(
        api_provider="openai",
        model_identifier="gpt",
        extra_params={},
    )
    monkeypatch.setattr("src.core.config.get_model_config", lambda: config)

    with pytest.raises(ValueError, match="client_type"):
        jev_api.create_jev_client(configured_model_name="chat")


def test_create_jev_client_rejects_mixed_config_sources() -> None:
    """显式 config 与 overrides 不能混用。"""
    with pytest.raises(ValueError, match="不能与"):
        jev_api.create_jev_client(
            config=JevClientConfig(api_key="test-key"),
            model="jev-latest",
        )


@pytest.mark.asyncio
async def test_evaluate_jev_reuses_caller_client() -> None:
    """插件侧可以复用自己的 JevClient，保持 key/endpoint 隔离。"""
    client = MagicMock()
    client.evaluate = AsyncMock(return_value=MagicMock(spec=JevResponse))

    result = await jev_api.evaluate_jev(
        state="test",
        questions={"q": {"type": "noul", "instructions": "true?"}},
        client=client,
    )

    assert result is client.evaluate.return_value
    client.evaluate.assert_awaited_once_with(
        state="test",
        questions={"q": {"type": "noul", "instructions": "true?"}},
        model=None,
    )
