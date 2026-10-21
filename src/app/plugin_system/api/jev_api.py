"""插件侧 Jev System One 公共 API。

Jev 保持独立的 typed-evaluation 协议，不伪装成 ``LLMRequest``；插件通过本模块
获取客户端并调用 Noul/Choice/Score 判断。客户端实例由调用方持有，避免全局单例
把不同插件的 API key、endpoint 和重试策略混在一起。
"""

from __future__ import annotations

from typing import Any

from src.kernel.jev import (
    JevClient,
    JevClientConfig,
    JevQuestion,
    JevResponse,
    JevState,
)

API_VERSION = "1.0.0"


def create_jev_client(
    config: JevClientConfig | None = None,
    *,
    provider_name: str | None = None,
    configured_model_name: str | None = None,
    **overrides: Any,
) -> JevClient:
    """创建一个独立的 Jev 客户端。

    ``provider_name``/``configured_model_name`` 只复用 Core 的提供商配置字段
    （api_key、base_url、timeout、trust_env、force_ipv4），不会把 Jev 注册成
    传统 ``ModelClientRegistry`` 的聊天 provider。没有指定配置名称时，客户端
    仍从 ``TYPESAFE_API_KEY`` 和显式 overrides 构造。

    Args:
        config: 已构造的 JevClientConfig。
        provider_name: Core ``api_providers`` 中的提供商名称。
        configured_model_name: Core ``models`` 中的模型名称；用于同时定位提供商，
            并以其 ``model_identifier`` 作为 Jev 默认模型。
        **overrides: JevClientConfig 字段覆盖，优先级高于 Core 配置。

    Returns:
        配置完成的 JevClient。
    """
    if config is not None:
        if provider_name or configured_model_name or overrides:
            raise ValueError("config 不能与 Core 配置名称或 Jev overrides 同时传入")
        return JevClient(config)

    if provider_name and configured_model_name:
        raise ValueError("provider_name 不能与 configured_model_name 同时传入")

    if provider_name or configured_model_name:
        from src.core.config import get_model_config

        model_config = get_model_config()
        configured_model = None
        if configured_model_name:
            configured_model = model_config.get_model(configured_model_name)
            provider_name = configured_model.api_provider
        assert provider_name is not None
        provider = model_config.get_provider(provider_name)
        if str(getattr(provider, "client_type", "")).casefold() not in {
            "jev",
            "typesafe",
            "type_safe",
        }:
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
        derived.update(overrides)
        return JevClient(JevClientConfig(**derived))

    return JevClient.from_env(**overrides)


async def evaluate_jev(
    *,
    state: JevState,
    questions: dict[str, JevQuestion | dict[str, Any]],
    client: JevClient | None = None,
    model: str | None = None,
    request_name: str = "jev",
    meta_data: dict[str, Any] | None = None,
    **client_overrides: Any,
) -> JevResponse:
    """通过插件公共 API 执行一次 Jev typed evaluation。

    传入 ``client`` 时复用调用方客户端；否则按 ``client_overrides`` 和
    ``TYPESAFE_API_KEY`` 创建一次短生命周期客户端。

    ``request_name`` 与 ``meta_data`` 会随 Jev 生命周期事件透传，便于插件
    在事件处理器中按场景聚合。
    """
    if client is not None and client_overrides:
        raise ValueError("client 不能与 Jev 客户端 overrides 同时传入")
    active_client = client or create_jev_client(**client_overrides)
    return await active_client.evaluate(
        state=state,
        questions=questions,
        model=model,
        request_name=request_name,
        meta_data=meta_data,
    )


__all__ = ["API_VERSION", "create_jev_client", "evaluate_jev"]
