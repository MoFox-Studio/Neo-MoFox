"""LLM 请求执行主流程。

这个模块承载 ``LLMRequest.send`` 背后的完整生命周期：
payload 归一化、策略驱动的模型选择、上下文预处理、provider 调用、
响应归一化，以及观测与重试处理。

三个 LLM 生命周期事件（``BEFORE_LLM_REQUEST``、``AFTER_LLM_REQUEST``、
``ON_LLM_REQUEST_FAILED``）的发布会捕获事件管理器返回的 ``final_params``，
并将其中可变字段回写至本次请求执行流程，从而允许事件订阅者在调用前修改
请求体、流式开关与工具集，在调用后修改消息文本、思考块与工具调用。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from src.kernel.logger import get_logger
from src.kernel.llm.payload.tooling import LLMUsable

from .exceptions import (
    LLMConfigurationError,
    classify_exception,
    decide_retry,
)
from .payload import LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from .roles import ROLE
from .response import LLMResponse
from .request_trace import Attempt, attempt_scope, new_logical_request, retry_scope
from .types import ModelEntry, ModelSet
from .model_client.base import ModelClientResult
from .request_attempt import AttemptContext, ManagedStream

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
    result: tuple[Any, ...] | ModelClientResult,
) -> tuple[
    str | None,
    list[dict[str, Any]] | None,
    Any,
    str | list[ReasoningText] | None,
    dict[str, Any] | None,
]:
    """把结构化结果或历史 3/4/5 元组转换为标准五元组。

    Args:
        result: provider 返回的 ModelClientResult，或依次包含消息、工具调用、
            流迭代器及可选思考内容、用量的兼容元组。

    Returns:
        消息、工具调用、流迭代器、思考内容、用量组成的五元组；兼容元组缺少的
        可选项补 None。provider_state 和 stop_reason 不在此返回，执行流程会
        从原始 ModelClientResult 单独写入 LLMResponse，避免丢失签名回放状态。

    Raises:
        ValueError: 兼容元组的长度不是 3、4 或 5。
    """
    if isinstance(result, ModelClientResult):
        return result.message, result.tool_calls, result.stream_iter, result.reasoning_content, result.usage
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
        "client.create 必须返回 3/4/5 元组："
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
    logical = new_logical_request()

    # 先统一一次 payload 形态，确保每次重试看到的输入结构完全一致。
    payloads = [normalize_tool_result_payload(p) for p in request.payloads]
    tools = extract_tools(payloads)

    assert request.policy is not None
    if request._locked_target is not None:
        assert request.clients is not None
        model_set = [
            model for model in model_set
            if request.clients.matches_replay_target(model, request._locked_target)
        ]
        if not model_set:
            from .exceptions import LLMContextError
            raise LLMContextError("The active Gemini tool round has no matching configured target")
    context = request._attempt_context
    if context is None:
        session = request.policy.new_session(model_set=model_set, request_name=request.request_name)
        context = AttemptContext(session=session, step=session.first())
    else:
        session = context.session

    last_error: BaseException | None = None
    retry_count = context.retry_count
    step = context.step

    while step.model is not None:
        # 每次循环都是一次具体的 provider 调用尝试，由当前重试策略会话决定。
        model = validate_model_entry(step.model)
        actual_stream = stream or bool(model.get("force_stream_mode", False))
        model_identifier = model.get("model_identifier")
        if not isinstance(model_identifier, str) or not model_identifier:
            raise LLMConfigurationError(
                "model.model_identifier 必须是非空字符串"
            )

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

        from .monitor import RequestTimer

        timer = RequestTimer()
        attempt_started = time.perf_counter()
        tools = extract_tools(payloads)
        trace = Attempt(
            logical, model, request.request_name,
            stream=actual_stream, metadata=request.meta_data,
        )

        try:
            with timer:
                # Include registry resolution in both timing and failure accounting.
                client = request.clients.get_client_for_model(model)
                timeout_seconds = model.get("timeout")

                # 触发大模型请求前事件
                # 事件订阅者可以修改 payloads、stream、tools 三个字段，
                # 修改后的值会回写到本次 provider 调用。
                try:
                    from src.core.components.types import EventType
                    from src.kernel.event import get_event_bus

                    event_bus = get_event_bus()
                    if event_bus.get_subscribers(EventType.BEFORE_LLM_REQUEST):
                        _, final_params = await event_bus.publish(
                            EventType.BEFORE_LLM_REQUEST,
                            {
                                "request_name": request.request_name,
                                "model_identifier": model_identifier,
                                "stream": actual_stream,
                                "tools": list(tools),
                                "payloads": list(trimmed_payloads),
                                "meta_data": request.meta_data,
                            },
                        )
                        # 回写事件订阅者修改后的参数
                        final_payloads = final_params.get("payloads")
                        if isinstance(final_payloads, list):
                            trimmed_payloads = final_payloads  # type: ignore[assignment]
                        final_tools = final_params.get("tools")
                        if isinstance(final_tools, list):
                            tools = final_tools  # type: ignore[assignment]
                        final_stream = final_params.get("stream")
                        if isinstance(final_stream, bool):
                            actual_stream = final_stream
                except Exception:
                    # 事件触发失败不中断 LLM 请求，静默降级
                    pass

                with attempt_scope(trace):
                    create_task = client.create(
                        model_name=model_identifier,
                        payloads=trimmed_payloads,
                        tools=tools,
                        request_name=request.request_name,
                        model_set=model,
                        stream=actual_stream,
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
                _inspection=trace,
                message=message,
                reasoning_content=reasoning_text,
                reasoning_parts=reasoning_parts,
                call_list=[],
            )
            if isinstance(result, ModelClientResult):
                resp.provider_state = result.provider_state
                resp.stop_reason = result.stop_reason
            # 保存原始 payloads 副本，供流式消费重试时恢复使用
            resp._original_payloads = list(trimmed_payloads)

            if tool_calls:
                # Provider 适配层返回的是普通 dict，这里再收口成强类型响应对象。
                resp.call_list = [
                    ToolCall(
                        id=tc.get("id"),
                        name=tc.get("name", ""),
                        args=tc.get("args", {}),
                    )
                    for tc in tool_calls
                ]

            if request.enable_metrics and not actual_stream:
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
            if actual_stream and stream_iter is not None:
                async def terminal(
                    error: BaseException | None, final_usage: dict[str, Any] | None,
                    latency: float, phase: str,
                ) -> None:
                    """按流的实际终态记账并发布事件，而不是把创建成功当作完成。

                    error 为终态异常或 None，final_usage 为最终累计用量，latency
                    为整次尝试的秒数，phase 区分完成、失败及放弃；仅 completed
                    且无异常时记录策略成功。事件发布失败不覆盖原流结果。
                    """
                    from src.kernel.event import get_event_bus
                    success = error is None and phase == "completed"
                    trace.finish(
                        error,
                        cancelled=phase == "cancelled" or (
                            phase == "abandoned" and error is None
                        ),
                    )
                    if request.enable_metrics:
                        stats_recorder(model=model, model_identifier=model_identifier,
                            request_name=request.request_name, meta_data=request.meta_data,
                            latency=latency, usage=final_usage, success=success, stream=True,
                            retry_count=retry_count, model_index=step.meta.get("model_index", 0) if step.meta else 0,
                            error=error)
                    if success:
                        session.record_success(latency=latency)
                    params = {"request_name": request.request_name, "model_identifier": model_identifier,
                        "stream": True, "success": success, "phase": phase, "terminal": True,
                        "error": error, "error_type": type(error).__name__ if error else None,
                        "error_message": str(error) if error else "", "usage": final_usage,
                        "latency": latency, "retry_count": retry_count, "meta_data": request.meta_data}
                    try:
                        bus = get_event_bus()
                        if error is not None and phase == "failed":
                            await bus.publish("on_llm_request_failed", params)
                        await bus.publish("on_llm_stream_finished", params)
                    except Exception:
                        logger.debug("LLM stream terminal event failed", exc_info=True)

                async def resume(next_context: AttemptContext) -> LLMResponse:
                    """用 next_context 的同一策略会话和剩余重试预算续接流。

                    从原始消息重建请求，保留客户端、来源锁及上下文管理器；
                    返回新的流式响应，交由 ManagedStream 接管消费与关闭责任，
                    不在此函数消费替代流或重新建立独立重试预算。
                    """
                    from .request import LLMRequest
                    retry = LLMRequest(model_set=request.model_set, request_name=request.request_name,
                        meta_data=dict(request.meta_data), payloads=list(payloads), clients=request.clients,
                        policy=request.policy, context_manager=request.context_manager,
                        enable_metrics=request.enable_metrics, _attempt_context=next_context,
                        _locked_target=request._locked_target)
                    with retry_scope(logical):
                        replacement = await retry.send(
                            auto_append_response=auto_append_response, stream=True,
                        )
                    resp.tool_call_compat = replacement.tool_call_compat
                    resp._inspection = replacement._inspection
                    return replacement

                def adopt_replacement(final_response: LLMResponse) -> None:
                    """将最终替代响应的协议及观测句柄交接给原响应。"""
                    resp.tool_call_compat = final_response.tool_call_compat
                    resp._inspection = final_response._inspection

                context.step, context.retry_count = step, retry_count
                trace.handoff_stream()
                resp._stream = ManagedStream(
                    trace.observe_stream(stream_iter), timeout=model.get("timeout"),
                    started_at=attempt_started, context=context,
                    terminal=terminal, resume=resume,
                )
                resp._stream.on_replacement = adopt_replacement
            else:
                if not trace.bound:
                    trace.result(
                        message=message, tool_calls=tool_calls,
                        reasoning_content=reasoning_text,
                        reasoning_parts=reasoning_parts, usage=usage,
                        stop_reason=resp.stop_reason,
                    )
                trace.finish()
                session.record_success(latency=timer.elapsed)

            # 触发大模型请求后事件
            # 事件订阅者可以修改 message、reasoning_content、reasoning_parts、
            # tool_calls 四个字段，修改后的值会回写到 LLMResponse。
            try:
                from src.core.components.types import EventType
                from src.kernel.event import get_event_bus

                event_bus = get_event_bus()
                if event_bus.get_subscribers(EventType.AFTER_LLM_REQUEST):
                    _, final_params = await event_bus.publish(
                        EventType.AFTER_LLM_REQUEST,
                        {
                            "request_name": request.request_name,
                            "model_identifier": model_identifier,
                            "stream": actual_stream,
                            "success": True,  # Legacy opened-stage compatibility, not final success.
                            "phase": "opened" if actual_stream else "completed",
                            "terminal": not actual_stream,
                            "latency": timer.elapsed,
                            "retry_count": retry_count,
                            "message": message,
                            "reasoning_content": reasoning_text,
                            "reasoning_parts": reasoning_parts,
                            "tool_calls": list(resp.call_list or []),
                            "usage": usage,
                            "meta_data": request.meta_data,
                        },
                    )
                    # 回写事件订阅者修改后的响应内容
                    final_message = final_params.get("message")
                    if isinstance(final_message, str) or final_message is None:
                        resp.message = final_message
                    final_reasoning = final_params.get("reasoning_content")
                    if isinstance(final_reasoning, str) or final_reasoning is None:
                        resp.reasoning_content = final_reasoning
                    final_reasoning_parts = final_params.get("reasoning_parts")
                    if isinstance(final_reasoning_parts, list):
                        resp.reasoning_parts = final_reasoning_parts
                    final_tool_calls = final_params.get("tool_calls")
                    if isinstance(final_tool_calls, list):
                        resp.call_list = [
                            tc if isinstance(tc, ToolCall) else ToolCall(
                                id=tc.get("id") if isinstance(tc, dict) else None,
                                name=tc.get("name", "") if isinstance(tc, dict) else "",
                                args=tc.get("args", {}) if isinstance(tc, dict) else {},
                            )
                            for tc in final_tool_calls
                        ]
            except Exception:
                # 事件触发失败不中断 LLM 请求，静默降级
                pass

            return resp
        except BaseException as exc:
            trace.finish(exc)
            await trace.close_resource()
            if isinstance(exc, asyncio.CancelledError):
                logger.debug(
                    f"LLM 请求已取消：model={model_identifier}, "
                    f"request={request.request_name or '__default__'}",
                )
                raise

            classified_error = classify_exception(exc, model=model_identifier)
            last_error = classified_error
            retry_decision = decide_retry(classified_error)
            _log_request_error(
                model_identifier=model_identifier,
                request_name=request.request_name,
                error=classified_error,
                retryable=retry_decision.retryable,
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
                    stream=actual_stream,
                    retry_count=retry_count,
                    model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    error=classified_error,
                )

            # 分类完成后立即触发失败事件，供插件统一检测所有 LLM 错误
            try:
                from src.core.components.types import EventType
                from src.kernel.event import get_event_bus

                event_bus = get_event_bus()
                if event_bus.get_subscribers(EventType.ON_LLM_REQUEST_FAILED):
                    _, final_params = await event_bus.publish(
                        EventType.ON_LLM_REQUEST_FAILED,
                        {
                            "request_name": request.request_name,
                            "model_identifier": model_identifier,
                            "payloads": list(trimmed_payloads),
                            "tools": list(tools),
                            "stream": actual_stream,
                            "error": classified_error,
                            "error_type": type(classified_error).__name__,
                            "error_message": str(classified_error),
                            "retry_count": retry_count,
                            "latency": timer.elapsed,
                            "meta_data": request.meta_data,
                        },
                    )
                    # 回写事件订阅者修改后的错误信息
                    final_error = final_params.get("error")
                    if isinstance(final_error, BaseException):
                        last_error = final_error
            except Exception:
                # 事件触发失败不中断异常传播，静默降级
                pass

            if not retry_decision.retryable:
                raise last_error

            retry_count += 1
            next_step = session.next_after_error(classified_error)
            if next_step.model is None:
                logger.error(
                    f"LLM 重试已耗尽：request={request.request_name or '__default__'}, "
                    f"retry_count={retry_count}, "
                    f"last_error={type(classified_error).__name__}",
                )
            else:
                next_model_identifier = next_step.model.get("model_identifier")
                next_model_name = (
                    next_model_identifier
                    if isinstance(next_model_identifier, str) and next_model_identifier
                    else "<unknown>"
                )
                logger.warning(
                    f"LLM 请求将重试：request={request.request_name or '__default__'}, "
                    f"retry_count={retry_count}, next_model={next_model_name}, "
                    f"delay_seconds={float(next_step.delay_seconds):.2f}",
                )
            step = next_step

    assert last_error is not None
    raise last_error


def _log_request_error(
    *,
    model_identifier: str,
    request_name: str,
    error: BaseException,
    retryable: bool,
) -> None:
    """根据重试决策记录日志；已知配置错误附带安全的配置原因。"""
    error_type = type(error).__name__
    if isinstance(error, LLMConfigurationError):
        details = next(
            (arg for arg in error.args if isinstance(arg, str)), error_type,
        )
        logger.error(
            f"LLM 请求配置错误：model={model_identifier}, "
            f"request={request_name or '__default__'}, "
            f"error_type={error_type}, reason={details}",
            exc_info=False,
        )
        return
    if retryable:
        logger.warning(
            f"LLM 请求暂时失败：model={model_identifier}, "
            f"request={request_name or '__default__'}, "
            f"error_type={error_type}",
        )
        logger.debug(
            f"LLM 请求暂时失败（详情）：model={model_identifier}, "
            f"request={request_name or '__default__'}, error_type={error_type}",
            exc_info=False,
        )
        return

    logger.error(
        f"LLM 请求失败：model={model_identifier}, "
        f"request={request_name or '__default__'}, "
        f"error_type={error_type}",
        exc_info=False,
    )
