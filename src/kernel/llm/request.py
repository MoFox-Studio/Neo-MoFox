"""LLM 子系统的公开请求门面。

``LLMRequest`` 是面向调用方的入口，用来组装 payload 并发起请求。更重的执行
流程放在 ``request_execution.py`` 中，这里主要聚焦默认值、校验和兼容层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

from .context import LLMContextManager
from .exceptions import LLMConfigurationError
from .model_client import ModelClientRegistry
from .observation import calculate_request_cost
from .payload import LLMPayload
from .policy import create_default_policy
from .policy.base import Policy
from .request_execution import (
    execute_request,
    extract_tools as _extract_tools,
    normalize_tool_result_payload as _normalize_tool_result_payload,
)
from .types import ModelEntry, ModelSet, RequestType


@dataclass(slots=True)
class LLMRequest:
    """一次逻辑 LLM 交互的可变请求构建器与发送器。"""
    model_set: ModelSet
    request_name: str = ""
    meta_data: dict[str, Any] = field(default_factory=dict)

    payloads: list[LLMPayload] = field(default_factory=list)
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    context_manager: LLMContextManager | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.COMPLETIONS

    def __post_init__(self) -> None:
        """在兼容旧构造方式的前提下补齐默认协作者。"""
        if self.payloads is None:
            self.payloads = []
        if self.policy is None:
            self.policy = create_default_policy()
        if self.clients is None:
            self.clients = ModelClientRegistry()
        if self.context_manager is None:
            self.context_manager = LLMContextManager()
        if self.meta_data is None:
            self.meta_data = {}
        elif not isinstance(self.meta_data, dict):
            self.meta_data = dict(self.meta_data)

    def add_payload(self, payload: LLMPayload, position=None) -> Self:
        """按当前 context manager 规则追加或插入 payload。"""
        if self.context_manager is not None:
            self.payloads = self.context_manager.add_payload(
                self.payloads,
                payload,
                position=int(position) if position is not None else None,
            )
            return self

        if position is not None:
            self.payloads.insert(int(position), payload)
            return self

        if self.payloads and self.payloads[-1].role == payload.role:
            self.payloads[-1].content.extend(payload.content)
        else:
            self.payloads.append(payload)
        return self

    async def send(
        self,
        auto_append_response: bool = True,
        *,
        stream: bool = True,
    ):
        """执行请求，并返回一个 ``LLMResponse`` 包装对象。"""
        return await execute_request(
            self,
            auto_append_response=auto_append_response,
            stream=stream,
            validate_model_set=_validate_model_set,
            validate_model_entry=_validate_model_entry,
            stats_recorder=_record_llm_stats,
        )


def _validate_model_entry(model: dict[str, Any]) -> ModelEntry:
    """校验单个模型配置项，并补齐历史默认值。"""
    required = [
        "api_provider",
        "base_url",
        "model_identifier",
        "api_key",
        "client_type",
        "max_retry",
        "timeout",
        "retry_interval",
        "price_in",
        "price_out",
        "temperature",
        "max_tokens",
        "extra_params",
    ]

    missing = [key for key in required if key not in model]
    if missing:
        raise LLMConfigurationError(f"model_set 元素缺少字段: {missing}")

    if not isinstance(model.get("extra_params"), dict):
        raise LLMConfigurationError("model.extra_params 必须是 dict")

    if "tool_call_compat" in model and not isinstance(
        model.get("tool_call_compat"), bool
    ):
        raise LLMConfigurationError("model.tool_call_compat 必须是 bool")
    if "max_context" in model and not isinstance(model.get("max_context"), int):
        raise LLMConfigurationError("model.max_context 必须是 int")

    extra_params = model.get("extra_params", {})
    if isinstance(extra_params, dict):
        if "context_reserve_ratio" in extra_params and not isinstance(
            extra_params.get("context_reserve_ratio"), (int, float)
        ):
            raise LLMConfigurationError(
                "model.extra_params.context_reserve_ratio 必须是 number"
            )
        if "context_reserve_tokens" in extra_params and not isinstance(
            extra_params.get("context_reserve_tokens"), int
        ):
            raise LLMConfigurationError(
                "model.extra_params.context_reserve_tokens 必须是 int"
            )

    model.setdefault("tool_call_compat", False)
    model.setdefault("max_context", 0)
    model.setdefault("cache_hit_price_in", model.get("price_in", 0.0))
    return model  # type: ignore[return-value]


def _validate_model_set(model_set: Any) -> ModelSet:
    """在执行开始前校验整组模型配置。"""
    if not isinstance(model_set, list) or not model_set:
        raise LLMConfigurationError("model_set 必须是非空 list[dict]")
    if not all(isinstance(item, dict) for item in model_set):
        raise LLMConfigurationError("model_set 必须是 list[dict]")
    return [_validate_model_entry(item) for item in model_set]


def _record_llm_stats(
    *,
    model: ModelEntry,
    model_identifier: str,
    request_name: str,
    meta_data: dict[str, Any],
    latency: float,
    usage: dict[str, Any] | None,
    success: bool,
    stream: bool,
    retry_count: int,
    model_index: int = 0,
    error: BaseException | None = None,
) -> None:
    """把旧的 stats 回调签名桥接到新的 observation 模块。"""
    from .observation import RequestObservation, record_request_observation

    record_request_observation(
        RequestObservation(
            model=model,
            model_identifier=model_identifier,
            request_name=request_name,
            meta_data=meta_data,
            latency=latency,
            usage=usage,
            success=success,
            stream=stream,
            retry_count=retry_count,
            model_index=model_index,
            error=error,
        )
    )
