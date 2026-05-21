"""LLM 请求执行主流程。

这个模块承载 ``LLMRequest.send`` 背后的完整生命周期：
payload 归一化、策略驱动的模型选择、上下文预处理、provider 调用、
响应归一化，以及观测与重试处理。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from src.kernel.logger import get_logger
from src.kernel.llm.payload.tooling import LLMUsable

from .exceptions import (
    LLMAPIError,
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
    classify_exception,
)
from .payload import LLMPayload, ReasoningText, Text, ToolResult
from .roles import ROLE
from .response import LLMResponse
from .types import ModelEntry, ModelSet

if TYPE_CHECKING:
    from .request import LLMRequest

logger = get_logger("kernel.llm.request", display="LLM 请求")


def normalize_tool_result_payload(payload: LLMPayload) -> LLMPayload:
    """把 ``TOOL_RESULT`` payload 规整成稳定、可序列化的内容形态。"""
    if payload.role != ROLE.TOOL_RESULT:
        return payload

    out_content: list[Any] = []
    for part in payload.content:
        if isinstance(part, ToolResult):
            out_content.append(part)
        elif isinstance(part, Text):
            out_content.append(part)
        else:
            out_content.append(Text(str(part)))

    return LLMPayload(ROLE.TOOL_RESULT, out_content)  # type: ignore[arg-type]


def extract_tools(payloads: list[LLMPayload]) -> list[LLMUsable]:
    """按顺序从请求 payload 中提取工具声明。"""
    tools: list[LLMUsable] = []
    for payload in payloads:
        if payload.role != ROLE.TOOL:
            continue
        for part in payload.content:
            if isinstance(part, type):
                if issubclass(part, LLMUsable):
                    tools.append(part)
                continue
            if isinstance(part, LLMUsable):
                tools.append(part)
    return tools


def normalize_client_create_result(
    result: tuple[Any, ...],
) -> tuple[
    str | None,
    list[dict[str, Any]] | None,
    Any,
    str | list[ReasoningText] | None,
    dict[str, Any] | None,
]:
    """把历史上的 3/4/5 元组 provider 返回值统一成一种形态。"""
    if len(result) == 5:
        message, tool_calls, stream_iter, reasoning_content, usage = result
        return message, tool_calls, stream_iter, reasoning_content, usage
    if len(result) == 4:
        message, tool_calls, stream_iter, reasoning_content = result
        return message, tool_calls, stream_iter, reasoning_content, None
    if len(result) == 3:
        message, tool_calls, stream_iter = result
        return message, tool_calls, stream_iter, None, None
    raise ValueError(
        "client.create must return a 3/4/5-tuple: "
        "(message, tool_calls, stream_iter[, reasoning_content[, usage]])"
    )


def split_reasoning_result(
    reasoning_result: str | list[ReasoningText] | None,
) -> tuple[str | None, list[ReasoningText] | None]:
    """把 reasoning 拆成纯文本和结构化片段两部分。"""
    if isinstance(reasoning_result, list):
        text = (
            "".join(
                part.text for part in reasoning_result if isinstance(part.text, str)
            )
            or None
        )
        return text, reasoning_result
    return reasoning_result, None


async def execute_request(
    request: LLMRequest,
    *,
    auto_append_response: bool,
    stream: bool,
    validate_model_set: Callable[[Any], ModelSet],
    validate_model_entry: Callable[[dict[str, Any]], ModelEntry],
    stats_recorder: Callable[..., None],
) -> LLMResponse:
    """执行一次逻辑上的 LLM 请求，并带上策略驱动的重试行为。"""
    model_set = validate_model_set(request.model_set)
    request_started_at = time.perf_counter()

    # 先统一一次 payload 形态，确保每次重试看到的输入结构完全一致。
    payloads = [normalize_tool_result_payload(p) for p in request.payloads]
    tools = extract_tools(payloads)

    assert request.policy is not None
    session = request.policy.new_session(
        model_set=model_set, request_name=request.request_name
    )

    last_error: BaseException | None = None
    retry_count = 0
    step = session.first()

    while step.model is not None:
        # 每次循环都是一次具体的 provider 调用尝试，由当前重试策略会话决定。
        model = validate_model_entry(step.model)
        model_identifier = model.get("model_identifier")
        if not isinstance(model_identifier, str) or not model_identifier:
            raise LLMConfigurationError("model.model_identifier must be a non-empty string")

        if step.delay_seconds and step.delay_seconds > 0:
            await asyncio.sleep(step.delay_seconds)

        trimmed_payloads = list(payloads)
        if request.context_manager is not None:
            # 上下文预处理依赖具体模型，因为不同模型的预算和压缩阈值不同。
            trimmed_payloads = await request.context_manager.prepare_payloads_for_model(
                trimmed_payloads,
                model,
                request=request,
            )
            request.context_manager.validate_for_send(list(trimmed_payloads))

        assert request.clients is not None
        client = request.clients.get_client_for_model(model)

        from .monitor import RequestTimer

        timer = RequestTimer()

        try:
            with timer:
                timeout_seconds = model.get("timeout")
                create_task = client.create(
                    model_name=model_identifier,
                    payloads=trimmed_payloads,
                    tools=tools,
                    request_name=request.request_name,
                    model_set=model,
                    stream=stream,
                )
                if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
                    result = await asyncio.wait_for(
                        create_task,
                        timeout=float(timeout_seconds),
                    )
                else:
                    result = await create_task
                (
                    message,
                    tool_calls,
                    stream_iter,
                    reasoning_content,
                    usage,
                ) = normalize_client_create_result(result)

            reasoning_text, reasoning_parts = split_reasoning_result(reasoning_content)
            request.payloads = list(trimmed_payloads)

            resp = LLMResponse(
                _stream=stream_iter,
                _upper=request,
                _auto_append_response=auto_append_response,
                payloads=list(trimmed_payloads),
                model_set=model_set,
                context_manager=request.context_manager,
                tool_call_compat=bool(model.get("tool_call_compat", False)),
                message=message,
                reasoning_content=reasoning_text,
                reasoning_parts=reasoning_parts,
                call_list=[],
            )

            if tool_calls:
                from .payload import ToolCall

                # Provider 适配层返回的是普通 dict，这里再收口成强类型响应对象。
                resp.call_list = [
                    ToolCall(
                        id=tc.get("id"),
                        name=tc.get("name", ""),
                        args=tc.get("args", {}),
                    )
                    for tc in tool_calls
                ]

            if request.enable_metrics and not stream:
                model_index = step.meta.get("model_index", 0) if step.meta else 0
                stats_recorder(
                    model=model,
                    model_identifier=model_identifier,
                    request_name=request.request_name,
                    meta_data=request.meta_data,
                    latency=timer.elapsed,
                    usage=usage,
                    success=True,
                    stream=False,
                    retry_count=retry_count,
                    model_index=model_index,
                )
            elif request.enable_metrics and stream:
                resp._stream_stats_recorder = (
                    lambda final_usage, final_latency: stats_recorder(
                        model=model,
                        model_identifier=model_identifier,
                        request_name=request.request_name,
                        meta_data=request.meta_data,
                        latency=final_latency,
                        usage=final_usage,
                        success=True,
                        stream=True,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                )
                resp._stream_started_at = request_started_at

            session.record_success(latency=timer.elapsed)
            return resp
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                logger.debug(
                    "LLM request cancelled: model=%s, request=%s",
                    model_identifier,
                    request.request_name or "__default__",
                    exc_info=True,
                )
                raise

            classified_error = classify_exception(exc, model=model_identifier)
            last_error = classified_error
            _log_request_error(
                model_identifier=model_identifier,
                request_name=request.request_name,
                error=classified_error,
            )

            if request.enable_metrics:
                stats_recorder(
                    model=model,
                    model_identifier=model_identifier,
                    request_name=request.request_name,
                    meta_data=request.meta_data,
                    latency=timer.elapsed,
                    usage=None,
                    success=False,
                    stream=stream,
                    retry_count=retry_count,
                    model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    error=classified_error,
                )

            retry_count += 1
            next_step = session.next_after_error(classified_error)
            if next_step.model is None:
                logger.error(
                    "LLM retries exhausted: request=%s, retry_count=%s, last_error=%s: %s",
                    request.request_name or "__default__",
                    retry_count,
                    type(classified_error).__name__,
                    classified_error,
                )
            else:
                next_model_identifier = next_step.model.get("model_identifier")
                next_model_name = (
                    next_model_identifier
                    if isinstance(next_model_identifier, str) and next_model_identifier
                    else "<unknown>"
                )
                logger.warning(
                    "LLM request will retry: request=%s, retry_count=%s, next_model=%s, delay_seconds=%.2f",
                    request.request_name or "__default__",
                    retry_count,
                    next_model_name,
                    float(next_step.delay_seconds),
                )
            step = next_step

    assert last_error is not None
    raise last_error


def _log_request_error(
    *,
    model_identifier: str,
    request_name: str,
    error: BaseException,
) -> None:
    """根据错误是否可能重试，按不同严重级别记录日志。"""
    error_type = type(error).__name__
    status_code: int | None = (
        error.status_code
        if isinstance(error, LLMAPIError)
        and isinstance(error.status_code, int)
        and error.status_code >= 500
        else None
    )
    if (
        isinstance(error, (LLMTimeoutError, LLMRateLimitError, TimeoutError))
        or status_code is not None
        or (isinstance(error, LLMAPIError) and error.status_code is None)
    ):
        status_hint = f", status_code={status_code}" if status_code is not None else ""
        logger.warning(
            "LLM request temporarily failed: model=%s, request=%s, error_type=%s%s",
            model_identifier,
            request_name or "__default__",
            error_type,
            status_hint,
        )
        logger.debug(
            "LLM request temporarily failed (detail): model=%s, request=%s, reason=%s",
            model_identifier,
            request_name or "__default__",
            error,
            exc_info=True,
        )
        return

    logger.error(
        "LLM request failed: model=%s, request=%s, error_type=%s, reason=%s",
        model_identifier,
        request_name or "__default__",
        error_type,
        error,
        exc_info=True,
    )
