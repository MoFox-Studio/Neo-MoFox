"""将 Core 的具名领域配置转换为独立 Jev 客户端配置。

配置名称解析、字段默认与凭据地址绑定由本模块负责；不创建客户端或管理传输。
"""

from __future__ import annotations

from typing import Any

from src.kernel.jev import JEV_CLIENT_TYPES, JevClientConfig


def resolve_jev_config(
    *,
    provider_name: str | None = None,
    configured_model_name: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> JevClientConfig:
    """解析一种 Core 配置名称，并生成带凭据地址锁的 Jev 配置。

    与传统聊天模型合并路径不同，本桥接保留 Jev 的历史网络默认值：
    ``trust_env=True``、``force_ipv4=False``，仅模型 extra_params 或显式
    overrides 可以覆盖，不读取全局 advanced 配置。每次解析读取一次轮询密钥，
    包括显式覆盖 api_key 或随后因 base_url 覆盖被拒绝的情况。

    Args:
        provider_name: Core api_providers 中的提供商名称。
        configured_model_name: Core models 中的模型名称，同时用于定位提供商。
        overrides: 优先于派生字段的显式覆盖；不允许包含 base_url。

    Returns:
        独立 Jev 客户端的公开配置对象，不创建或持有传输资源。

    Raises:
        ValueError: 名称未指定、名称混用、非 Jev 提供商或覆盖绑定地址。
        KeyError: 名称查找失败时保留 Core 原异常。
        JevConfigurationError: 字段校验失败时保留 Jev 配置原异常。
    """
    if provider_name and configured_model_name:
        raise ValueError("provider_name 不能与 configured_model_name 同时传入")
    if not provider_name and not configured_model_name:
        raise ValueError("必须指定 provider_name 或 configured_model_name")

    from src.core.config import get_model_config

    model_config = get_model_config()
    configured_model = None
    if configured_model_name:
        configured_model = model_config.get_model(configured_model_name)
        provider_name = configured_model.api_provider
    assert provider_name is not None
    provider = model_config.get_provider(provider_name)
    if str(getattr(provider, "client_type", "")).casefold() not in JEV_CLIENT_TYPES:
        raise ValueError(
            "Core provider 的 client_type 必须是 jev/typesafe，不能用传统聊天 provider"
        )
    extra_params = dict(getattr(configured_model, "extra_params", {}) or {})
    extra_headers = extra_params.get("headers")
    extra_query = extra_params.get("query")
    derived: dict[str, Any] = {
        "api_key": provider.get_api_key(),
        "base_url": provider.base_url,
        "timeout_seconds": float(provider.timeout),
        "model": getattr(configured_model, "model_identifier", "jev-latest"),
        "max_retries": int(provider.max_retry),
        "retry_backoff_seconds": float(provider.retry_interval),
        "trust_env": extra_params.get("trust_env", True),
        "force_ipv4": extra_params.get("force_ipv4", False),
        "extra_headers": extra_headers if isinstance(extra_headers, dict) else {},
        "extra_query": extra_query if isinstance(extra_query, dict) else {},
    }
    # 配置中的地址与密钥绑定；即使覆盖为相同地址，也沿用原拒绝规则。
    overrides = overrides if overrides is not None else {}
    if "base_url" in overrides:
        raise ValueError(
            "复用 Core provider 配置时不允许覆盖 base_url："
            "API key 与该地址绑定，改写会把密钥发往非预期主机；"
            "请直接修改 Core 的 provider 配置"
        )
    derived.update(overrides)
    return JevClientConfig(**derived)
