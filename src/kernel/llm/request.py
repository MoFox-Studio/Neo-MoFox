"""LLM 请求模块

提供 LLMRequest 类，用于构建和执行 LLM 请求。

LLMRequest 支持：
- 构建 LLMPayload 列表
- 负载均衡和重试策略
- 指标收集
- 流式和非流式响应
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Self

from src.kernel.llm.payload.tooling import LLMUsable

from .context import LLMContextManager
from .exceptions import LLMConfigurationError, classify_exception
from .model_client import ModelClientRegistry
from .monitor import RequestMetrics, RequestTimer, get_global_collector
from .payload import LLMPayload, Text, ToolResult
from .policy import RoundRobinPolicy
from .policy.base import Policy
from .response import LLMResponse
from .roles import ROLE
from .types import ModelEntry, ModelSet
from src.kernel.logger import get_logger


logger = get_logger("llm_request")


def _normalize_tool_result_payload(payload: LLMPayload) -> LLMPayload:
    if payload.role != ROLE.TOOL_RESULT:
        return payload

    # 允许 ToolResult 或任意对象。
    # 重要：ToolResult 需要保留 call_id（用于 OpenAI tool message 的 tool_call_id）。
    out_content: list[Any] = []
    for part in payload.content:
        if isinstance(part, ToolResult):
            out_content.append(part)
        elif isinstance(part, Text):
            out_content.append(part)
        else:
            out_content.append(Text(str(part)))

    return LLMPayload(ROLE.TOOL_RESULT, out_content)  # type: ignore[arg-type]


def _extract_tools(payloads: list[LLMPayload]) -> list[LLMUsable]:
    tools: list[LLMUsable] = []
    for payload in payloads:
        if payload.role != ROLE.TOOL:
            continue
        for part in payload.content:
            if isinstance(part, LLMUsable):
                tools.append(part)
    return tools


@dataclass(slots=True)
class LLMRequest:
    """LLMRequest：构建 payload 并执行请求。"""

    model_set: ModelSet
    request_name: str = ""

    payloads: list[LLMPayload] = field(default_factory=list)
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    context_manager: LLMContextManager | None = None
    enable_metrics: bool = True  # 是否启用指标收集

    def __post_init__(self) -> None:
        if self.payloads is None:
            object.__setattr__(self, "payloads", [])
        if self.policy is None:
            object.__setattr__(self, "policy", RoundRobinPolicy())
        if self.clients is None:
            object.__setattr__(self, "clients", ModelClientRegistry())
        if self.context_manager is None:
            object.__setattr__(self, "context_manager", LLMContextManager())

    def add_payload(self, payload: LLMPayload, position=None) -> Self:
        if position is not None:
            self.payloads.insert(int(position), payload)
        else:
            self.payloads.append(payload)
        self._maybe_trim_payloads()
        return self

    async def send(self, auto_append_response: bool = True, *, stream: bool = True) -> LLMResponse:
        self._maybe_trim_payloads()
        model_set = _validate_model_set(self.model_set)

        # TOOL_RESULT payload 规范化（确保 provider 端可读）
        payloads = [_normalize_tool_result_payload(p) for p in self.payloads]
        tools = _extract_tools(payloads)

        assert self.policy is not None
        session = self.policy.new_session(model_set=model_set, request_name=self.request_name)

        last_error: BaseException | None = None
        retry_count = 0
        step = session.first()
        
        while step.model is not None:
            model = _validate_model_entry(step.model)

            model_identifier = model.get("model_identifier")
            if not isinstance(model_identifier, str) or not model_identifier:
                raise LLMConfigurationError("model.model_identifier 必须是非空字符串")

            if step.delay_seconds and step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)

            assert self.clients is not None
            client = self.clients.get_client_for_model(model)

            # 开始计时
            timer = RequestTimer()

            try:
                with timer:
                    timeout_seconds = model.get("timeout")
                    create_task = client.create(
                        model_name=model_identifier,
                        payloads=payloads,
                        tools=tools,
                        request_name=self.request_name,
                        model_set=model,
                        stream=stream,
                    )

                    if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
                        message, tool_calls, stream_iter = await asyncio.wait_for(
                            create_task,
                            timeout=float(timeout_seconds),
                        )
                    else:
                        message, tool_calls, stream_iter = await create_task

                resp = LLMResponse(
                    _stream=stream_iter,
                    _upper=self,
                    _auto_append_response=auto_append_response,
                    payloads=list(self.payloads),
                    model_set=model_set,
                    context_manager=self.context_manager,
                    message=message,
                    call_list=[],
                )

                # 非流：立即解析 tool_calls
                if tool_calls:
                    from .payload import ToolCall

                    resp.call_list = [
                        ToolCall(id=tc.get("id"), name=tc.get("name", ""), args=tc.get("args", {})) for tc in tool_calls
                    ]

                # 记录成功指标
                if self.enable_metrics:
                    metrics = RequestMetrics(
                        model_name=model_identifier,
                        request_name=self.request_name,
                        latency=timer.elapsed,
                        success=True,
                        stream=stream,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                    get_global_collector().record_request(metrics)

                return resp
                
            except BaseException as e:
                # 将原始异常转换为标准化 LLM 异常
                classified_error = classify_exception(e, model=model_identifier)
                last_error = classified_error

                # 记录失败指标
                if self.enable_metrics:
                    metrics = RequestMetrics(
                        model_name=model_identifier,
                        request_name=self.request_name,
                        latency=timer.elapsed,
                        success=False,
                        error=str(classified_error),
                        error_type=type(classified_error).__name__,
                        stream=stream,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                    get_global_collector().record_request(metrics)
                
                retry_count += 1
                step = session.next_after_error(classified_error)

        assert last_error is not None
        raise last_error

    def _maybe_trim_payloads(self) -> None:
        if not self.context_manager:
            return
        self.payloads = self.context_manager.maybe_trim(self.payloads)


def _validate_model_entry(model: dict[str, Any]) -> ModelEntry:
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

    missing = [k for k in required if k not in model]
    if missing:
        raise LLMConfigurationError(f"model_set 元素缺少字段: {missing}")

    if not isinstance(model.get("extra_params"), dict):
        raise LLMConfigurationError("model.extra_params 必须是 dict")

    return model  # type: ignore[return-value]


def _validate_model_set(model_set: Any) -> ModelSet:
    if not isinstance(model_set, list) or not model_set:
        raise LLMConfigurationError("model_set 必须是非空 list[dict]")
    if not all(isinstance(x, dict) for x in model_set):
        raise LLMConfigurationError("model_set 必须是 list[dict]")
    return [
        _validate_model_entry(x) for x in model_set
    ]
