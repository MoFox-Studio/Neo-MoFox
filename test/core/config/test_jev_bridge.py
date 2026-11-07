"""Jev 配置桥接的真实领域配置回归，所有地址和密钥均为离线假值。"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.config import ModelConfig
from src.kernel.jev import JevClientConfig, JevConfigurationError


@pytest.fixture
def model_config(monkeypatch: pytest.MonkeyPatch) -> ModelConfig:
    """注入真实模型配置；参数为补丁器，返回可检查轮询状态的配置。"""
    config = ModelConfig.model_validate({
        "api_providers": [{
            "name": "typed", "client_type": "jev",
            "base_url": "https://offline.invalid/jev", "api_key": ["fake-1", "fake-2"],
            "timeout": 7, "max_retry": 3, "retry_interval": 2,
        }],
        "models": [{
            "name": "judge", "api_provider": "typed", "model_identifier": "jev-fixed",
            "extra_params": {"trust_env": False, "force_ipv4": True,
                             "headers": {"X-Tenant": "offline"}, "query": {"zone": "test"}},
        }],
    })
    def get_config() -> ModelConfig:
        """返回内存中的真实配置，避免读取用户本地文件。"""
        return config

    monkeypatch.setattr("src.core.config.get_model_config", get_config)
    return config


def _resolve(**kwargs: Any) -> JevClientConfig:
    """延迟加载新桥接以保留缺失模块的逐项红灯；返回解析结果。"""
    from src.core.config.jev import resolve_jev_config

    return resolve_jev_config(**kwargs)


def test_real_model_translation_and_override(model_config: ModelConfig) -> None:
    """真实模型字段经桥接转换，显式覆盖优先且不修改输入字典。"""
    overrides = {"model": "jev-override", "timeout_seconds": 11, "api_key": "fake-override"}
    result = _resolve(configured_model_name="judge", overrides=overrides)
    assert result.api_key == "fake-override"
    assert result.base_url == "https://offline.invalid/jev"
    assert result.model == "jev-override"
    assert result.timeout_seconds == 11
    assert result.max_retries == 3
    assert result.retry_backoff_seconds == 2.0
    assert result.max_retry_backoff_seconds == 8.0
    assert result.trust_env is False
    assert result.force_ipv4 is True
    assert result.extra_headers == {"X-Tenant": "offline"}
    assert result.extra_query == {"zone": "test"}
    assert overrides == {"model": "jev-override", "timeout_seconds": 11, "api_key": "fake-override"}
    assert model_config.get_provider("typed").get_api_key() == "fake-2"


@pytest.mark.parametrize("client_type", ["jev", "typesafe", "type_safe"])
def test_provider_defaults_ignore_global_trust_env(
    model_config: ModelConfig, monkeypatch: pytest.MonkeyPatch, client_type: str,
) -> None:
    """提供商别名均可用，默认网络字段不意外继承全局配置。"""
    model_config.get_provider("typed").client_type = client_type

    def forbidden_global() -> Any:
        """禁止读取全局配置；调用即表示桥接改变了旧默认。"""
        raise AssertionError("不得读取 global trust_env")

    monkeypatch.setattr("src.core.config.get_core_config", forbidden_global)
    result = _resolve(provider_name="typed")
    assert result.api_key == "fake-1"
    assert result.model == "jev-latest"
    assert result.timeout_seconds == 7.0
    assert result.trust_env is True
    assert result.force_ipv4 is False
    assert result.extra_headers == result.extra_query == {}


@pytest.mark.parametrize("field", ["headers", "query"])
@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_non_dict_extras_keep_empty_defaults(
    model_config: ModelConfig, field: str, value: Any,
) -> None:
    """历史非字典扩展仍忽略，不夹带额外配置校验。"""
    model_config.get_model("judge").extra_params[field] = value
    result = _resolve(configured_model_name="judge")
    assert getattr(result, "extra_headers" if field == "headers" else "extra_query") == {}


@pytest.mark.parametrize("base_url", ["https://offline.invalid/jev", "https://other.offline.invalid"])
def test_address_lock_preserves_key_rotation(
    model_config: ModelConfig, base_url: str,
) -> None:
    """即使覆盖相同地址也拒绝，且保留拒绝前读取一次轮询密钥的旧顺序。"""
    with pytest.raises(ValueError, match="不允许覆盖 base_url"):
        _resolve(provider_name="typed", overrides={"base_url": base_url, "api_key": "fake-new"})
    assert model_config.get_provider("typed").get_api_key() == "fake-2"


@pytest.mark.parametrize("kwargs", [
    {"provider_name": "missing"}, {"configured_model_name": "missing"},
])
def test_unknown_names_keep_key_error(model_config: ModelConfig, kwargs: dict[str, str]) -> None:
    """未找到模型或提供商仍传播原 KeyError。"""
    with pytest.raises(KeyError):
        _resolve(**kwargs)


def test_non_jev_provider_does_not_rotate_key(model_config: ModelConfig) -> None:
    """错误客户端类型在读取密钥之前拒绝。"""
    provider = model_config.get_provider("typed")
    provider.client_type = "openai"
    with pytest.raises(ValueError, match="client_type"):
        _resolve(provider_name="typed")
    assert provider.get_api_key() == "fake-1"


def test_invalid_override_keeps_configuration_error(model_config: ModelConfig) -> None:
    """字段校验沿用公开 JevConfigurationError，不重新包装异常。"""
    with pytest.raises(JevConfigurationError):
        _resolve(provider_name="typed", overrides={"max_retries": -1})


@pytest.mark.parametrize("kwargs", [{}, {"provider_name": "typed", "configured_model_name": "judge"}])
def test_bridge_requires_one_configuration_name(model_config: ModelConfig, kwargs: dict[str, str]) -> None:
    """领域桥接只接受一种配置名称，不承担环境变量路径。"""
    with pytest.raises(ValueError):
        _resolve(**kwargs)
