"""LLM Provider 客户端共享辅助逻辑。

这个模块承载 OpenAI 与 Anthropic 适配层都会复用的无关 provider 的逻辑，
用于统一传输参数归一化、schema 清洗以及请求观测能力，避免各个客户端随时间
逐渐出现行为漂移。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from src.kernel.llm.payload.tooling import LLMUsable

from ..observation import capture_provider_request


def build_httpx_timeout(timeout: float | None) -> Any:
    """根据标量超时配置构造相对保守的 ``httpx.Timeout`` 对象。"""
    import httpx

    if not isinstance(timeout, (int, float)):
        return None

    total = float(timeout)
    if total <= 0:
        return None

    connect_timeout = min(total, 10.0)
    pool_timeout = min(total, 5.0)
    return httpx.Timeout(
        timeout=total,
        connect=connect_timeout,
        read=total,
        write=total,
        pool=pool_timeout,
    )


def callable_accepts_reason(callable_obj: Any) -> bool:
    """判断一个可调用对象是否已经接收注入的 ``reason`` 参数。"""
    if not callable(callable_obj):
        return False

    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False

    if "reason" in sig.parameters:
        return True

    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in sig.parameters.values()
    )


def normalize_schema_for_grammar(schema: Any) -> None:
    """原地规范化 JSON Schema 片段，适配 provider 的工具语法要求。"""
    if isinstance(schema, list):
        for item in schema:
            normalize_schema_for_grammar(item)
        return

    if not isinstance(schema, dict):
        return

    if schema.get("default") is None:
        schema.pop("default", None)

    if schema.get("type") == "array" and "items" not in schema:
        schema["items"] = {"type": "string"}

    if schema.get("type") == "object" and "properties" not in schema:
        schema.setdefault("additionalProperties", {"type": "string"})

    for key in (
        "properties",
        "items",
        "additionalProperties",
        "anyOf",
        "allOf",
        "oneOf",
    ):
        value = schema.get(key)
        if isinstance(value, dict):
            for child in value.values():
                normalize_schema_for_grammar(child)
        elif isinstance(value, list):
            for child in value:
                normalize_schema_for_grammar(child)


def inject_reason_parameter(
    tool: LLMUsable,
    input_schema: dict[str, object],
) -> dict[str, object]:
    """在需要时确保工具 schema 暴露必填的 ``reason`` 字段。"""
    properties_obj = input_schema.get("properties")
    properties: dict[str, object] = (
        properties_obj if isinstance(properties_obj, dict) else {}
    )
    schema_has_reason = "reason" in properties
    execute_has_reason = callable_accepts_reason(getattr(tool, "execute", None))

    if schema_has_reason or execute_has_reason:
        input_schema["properties"] = properties
        return input_schema

    properties["reason"] = {
        "type": "string",
        "description": "说明你选择此动作/工具的原因",
    }
    input_schema["properties"] = properties

    required_obj = input_schema.get("required")
    required = (
        [str(item) for item in required_obj]
        if isinstance(required_obj, list)
        else []
    )
    if "reason" not in required:
        required.append("reason")
    input_schema["required"] = required
    return input_schema


def extract_model_transport_params(
    model_set: Mapping[str, object],
) -> tuple[str, str | None, float | None, bool, bool, dict[str, object]]:
    """提取所有 provider 客户端都会使用的传输层配置。"""
    api_key = str(model_set.get("api_key") or "")
    if not api_key:
        raise ValueError("model.api_key cannot be empty")

    base_url = model_set.get("base_url")
    base_url = str(base_url) if base_url else None
    timeout = model_set.get("timeout")

    extra_params = model_set.get("extra_params")
    if extra_params is None:
        extra_params = {}
    if not isinstance(extra_params, dict):
        raise ValueError("model.extra_params must be a dict")

    normalized_extra_params = dict(extra_params)
    trust_env_raw = normalized_extra_params.pop("trust_env", None)
    trust_env = bool(trust_env_raw) if trust_env_raw is not None else True
    force_ipv4 = bool(normalized_extra_params.pop("force_ipv4", False))
    # 这些开关属于更高层的请求编排逻辑，不应继续透传给底层 SDK。
    normalized_extra_params.pop("context_reserve_ratio", None)
    normalized_extra_params.pop("context_reserve_tokens", None)
    normalized_extra_params.pop("force_sync_http", None)

    timeout_float = float(timeout) if isinstance(timeout, (int, float)) else None
    return (
        api_key,
        base_url,
        timeout_float,
        trust_env,
        force_ipv4,
        normalized_extra_params,
    )


def log_provider_request_body(
    api_name: str,
    params: dict[str, Any],
    *,
    model_set: Mapping[str, Any] | None = None,
    payloads: list[Any] | None = None,
    request_name: str | None = None,
    token_counter: Any = None,
) -> None:
    """把 provider 请求快照转发到统一的观测链路。"""
    capture_provider_request(
        api_name,
        params,
        model_set=model_set,
        payloads=payloads,
        request_name=request_name,
        token_counter=token_counter,
    )
