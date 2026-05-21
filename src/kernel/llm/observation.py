"""LLM 请求观测辅助逻辑。

这个模块统一管理请求级别的观测与遥测逻辑，让请求执行层和 provider 适配层
只需要发出稳定的小事件，而不必把 metrics 逻辑散落到多个文件里。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .monitor import RequestMetrics, get_global_collector
from .payload import LLMPayload
from .types import ModelEntry


@dataclass(frozen=True, slots=True)
class RequestObservation:
    """一次 provider 调用尝试的标准化观测记录。"""
    model: ModelEntry
    model_identifier: str
    request_name: str
    meta_data: Mapping[str, Any]
    latency: float
    usage: Mapping[str, Any] | None
    success: bool
    stream: bool
    retry_count: int
    model_index: int = 0
    error: BaseException | None = None


def capture_provider_request(
    api_name: str,
    params: dict[str, Any],
    *,
    model_set: Mapping[str, Any] | None = None,
    payloads: list[LLMPayload] | None = None,
    request_name: str | None = None,
    token_counter: Callable[[list[LLMPayload], str], int] | None = None,
) -> None:
    """捕获 provider 请求输入，供调试和检查工具使用。"""
    metadata: dict[str, Any] = {}
    if isinstance(model_set, Mapping):
        provider = model_set.get("base_url")
        if provider is not None:
            metadata["api_provider"] = str(provider)
    if request_name:
        metadata["request_name"] = request_name
    if payloads and token_counter is not None:
        try:
            metadata["estimated_input_tokens"] = token_counter(
                payloads,
                str(params.get("model") or "cl100k_base"),
            )
        except Exception:
            pass
    try:
        from .request_inspector import capture

        capture(api_name, params, metadata)
    except Exception:
        pass


def record_request_observation(observation: RequestObservation) -> None:
    """把一次请求观测分发到已配置的多个 sink。"""
    _record_llm_stats(observation)
    _record_metrics(observation)


def make_stream_stats_recorder(
    *,
    model: ModelEntry,
    model_identifier: str,
    request_name: str,
    meta_data: Mapping[str, Any],
    retry_count: int,
    model_index: int = 0,
) -> Callable[[dict[str, Any] | None, float], None]:
    """构造一个在流式请求结束后记录最终统计信息的回调。"""
    def recorder(final_usage: dict[str, Any] | None, final_latency: float) -> None:
        record_request_observation(
            RequestObservation(
                model=model,
                model_identifier=model_identifier,
                request_name=request_name,
                meta_data=meta_data,
                latency=final_latency,
                usage=final_usage,
                success=True,
                stream=True,
                retry_count=retry_count,
                model_index=model_index,
            )
        )

    return recorder


def calculate_request_cost(*, model: ModelEntry, usage: Mapping[str, Any]) -> float:
    """根据模型定价和 usage 估算请求成本。"""
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    cache_hit_tokens = int(usage.get("cache_hit_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cache_miss_tokens = int(usage.get("cache_miss_tokens", 0) or 0)
    price_in = float(model.get("price_in", 0.0) or 0.0)
    cache_hit_price_raw = model.get("cache_hit_price_in", price_in)
    cache_hit_price_in = (
        price_in if cache_hit_price_raw is None else float(cache_hit_price_raw)
    )
    price_out = float(model.get("price_out", 0.0) or 0.0)
    if cache_hit_tokens > 0 or cache_miss_tokens > 0:
        billable_prompt_tokens = (
            cache_miss_tokens
            if cache_miss_tokens > 0
            else max(prompt_tokens - cache_hit_tokens, 0)
        )
    else:
        billable_prompt_tokens = prompt_tokens

    input_cost = (
        billable_prompt_tokens * price_in + cache_hit_tokens * cache_hit_price_in
    )
    output_cost = completion_tokens * price_out
    return round((input_cost + output_cost) / 1_000_000, 8)


def _record_metrics(observation: RequestObservation) -> None:
    """把观测记录转换成通用请求指标。"""
    error = observation.error
    metrics = RequestMetrics(
        model_name=observation.model_identifier,
        request_name=observation.request_name,
        latency=observation.latency,
        success=observation.success,
        error=str(error) if error is not None else None,
        error_type=type(error).__name__ if error is not None else None,
        stream=observation.stream,
        retry_count=observation.retry_count,
        model_index=observation.model_index,
    )
    get_global_collector().record_request(metrics)


def _record_llm_stats(observation: RequestObservation) -> None:
    """在异步收集器启用时持久化更丰富的 LLM 请求统计。"""
    try:
        from src.kernel.llm.stats import LLMRequestRecord, get_llm_stats_collector

        collector = get_llm_stats_collector()
        if not collector.enabled:
            return

        usage_data = dict(observation.usage or {})
        cost = calculate_request_cost(model=observation.model, usage=usage_data)
        record = LLMRequestRecord(
            model_name=observation.model.get(
                "model_name", observation.model_identifier
            ),
            model_identifier=observation.model_identifier,
            api_provider=str(observation.model.get("base_url") or ""),
            request_name=observation.request_name,
            stream_id=observation.meta_data.get("stream_id"),
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            cache_hit_tokens=usage_data.get("cache_hit_tokens", 0),
            cache_miss_tokens=usage_data.get("cache_miss_tokens", 0),
            cache_write_tokens=usage_data.get("cache_write_tokens", 0),
            cost=cost,
            latency=observation.latency,
            success=observation.success,
            error_type=(
                type(observation.error).__name__
                if observation.error is not None
                else None
            ),
            stream=observation.stream,
            retry_count=observation.retry_count,
        )
        # 统计记录保持 fire-and-forget，避免请求耗时被慢存储后端拖住。
        asyncio.ensure_future(collector.record(record))
    except Exception:
        pass
