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

from src.kernel.logger import get_logger
from src.kernel.llm.payload.tooling import LLMUsable

from .context import LLMContextManager
from .exceptions import LLMAPIError, LLMConfigurationError, LLMRateLimitError, LLMTimeoutError, classify_exception
from .model_client import ModelClientRegistry
from .monitor import RequestMetrics, RequestTimer, get_global_collector
from .payload import LLMPayload, ReasoningText, Text, ToolResult
from .policy import create_default_policy
from .policy.base import Policy
from .response import LLMResponse
from .roles import ROLE
from .types import ModelEntry, ModelSet, RequestType


logger = get_logger("kernel.llm.request", display="LLM 请求")


def _normalize_tool_result_payload(payload: LLMPayload) -> LLMPayload:
    """
    规范化 TOOL_RESULT payload，确保内容中的 ToolResult 对象被保留，其他非 Text 对象被转换为 Text。
    """
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
    """从 payloads 中提取所有 TOOL 角色的 LLMUsable 对象，供 provider 端调用工具时使用。"""
    tools: list[LLMUsable] = []
    for payload in payloads:
        if payload.role != ROLE.TOOL:
            continue
        for part in payload.content:
            # TOOL payload 允许传入工具类（type）或工具实例。
            # 这里显式兼容两种形式，避免仅依赖 Protocol 的 isinstance 细节。
            if isinstance(part, type):
                if issubclass(part, LLMUsable):
                    tools.append(part)
                continue

            if isinstance(part, LLMUsable):
                tools.append(part)
    return tools


def _normalize_client_create_result(
    result: tuple[Any, ...],
) -> tuple[
    str | None,
    list[dict[str, Any]] | None,
    Any,
    str | list[ReasoningText] | None,
]:
    """兼容 provider client 的 3 元组与 4 元组返回格式。"""
    if len(result) == 4:
        message, tool_calls, stream_iter, reasoning_content = result
        return message, tool_calls, stream_iter, reasoning_content

    if len(result) == 3:
        message, tool_calls, stream_iter = result
        return message, tool_calls, stream_iter, None

    raise ValueError(
        "client.create 必须返回长度为 3 或 4 的元组："
        "(message, tool_calls, stream_iter[, reasoning_content])"
    )


def _split_reasoning_result(
    reasoning_result: str | list[ReasoningText] | None,
) -> tuple[str | None, list[ReasoningText] | None]:
    """将 provider 返回的 reasoning 结果拆分为文本摘要和结构化 block。"""
    if isinstance(reasoning_result, list):
        text = "".join(part.text for part in reasoning_result if isinstance(part.text, str)) or None
        return text, reasoning_result
    return reasoning_result, None


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
    request_type: RequestType = RequestType.COMPLETIONS

    def __post_init__(self) -> None:
        if self.payloads is None:
            self.payloads = []
        if self.policy is None:
            self.policy = create_default_policy()
        if self.clients is None:
            self.clients = ModelClientRegistry()
        if self.context_manager is None:
            self.context_manager = LLMContextManager()

    def add_payload(self, payload: LLMPayload, position=None) -> Self:
        """
        添加一个新的 LLMPayload 到请求中。

        Args:
            payload: 要添加的 LLMPayload 对象。
            position: 可选的插入位置，如果为 None，则添加到末尾。

        Returns:
            Self: 返回当前 LLMRequest 实例，支持链式调用。
        """
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
        self, auto_append_response: bool = True, *, stream: bool = True
    ) -> LLMResponse:
        """
        发送请求并返回响应。

        Args:
            auto_append_response: 是否自动将响应消息追加到 payloads 中，默认为 True。
            stream: 是否使用流式响应，默认为 True。
        Returns:
            LLMResponse: 包含响应消息和工具调用信息的对象。
        """
        model_set = _validate_model_set(self.model_set)

        # TOOL_RESULT payload 规范化（确保 provider 端可读）
        payloads = [_normalize_tool_result_payload(p) for p in self.payloads]
        tools = _extract_tools(payloads)

        # 创建策略会话
        assert self.policy is not None
        session = self.policy.new_session(
            model_set=model_set, request_name=self.request_name
        )

        last_error: BaseException | None = None
        retry_count = 0
        step = session.first()

        # 循环直到找到可用模型或耗尽重试机会
        while step.model is not None:
            model = _validate_model_entry(step.model)

            model_identifier = model.get("model_identifier")
            if not isinstance(model_identifier, str) or not model_identifier:
                raise LLMConfigurationError("model.model_identifier 必须是非空字符串")

            # 如果当前步骤配置了 delay_seconds，则在发送请求前等待指定的时间（用于实现请求节流或冷却机制）
            if step.delay_seconds and step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)

            # 根据当前模型的上下文限制和保留策略，裁剪 payloads 以适应当前模型
            # 注意：裁剪结果仅用于本次请求，不回写 self.payloads，避免重试时基于已裁剪的结果再裁剪
            trimmed_payloads = list(payloads)
            if self.context_manager is not None:
                trimmed_payloads = await self.context_manager.prepare_payloads_for_model(
                    trimmed_payloads,
                    model,
                    request=self,
                )

            # 严格上下文校验：不允许带着不完整/不合法的 tool 链路发起请求。
            # 该错误属于“本地逻辑错误”，不应进入重试链。
            if self.context_manager is not None:
                self.context_manager.validate_for_send(list(trimmed_payloads))

            assert self.clients is not None
            client = self.clients.get_client_for_model(model)

            # 开始计时
            timer = RequestTimer()

            try:
                with timer:
                    timeout_seconds = model.get("timeout")
                    create_task = client.create(
                        model_name=model_identifier,
                        payloads=trimmed_payloads,
                        tools=tools,
                        request_name=self.request_name,
                        model_set=model,
                        stream=stream,
                    )

                    if (
                        isinstance(timeout_seconds, (int, float))
                        and timeout_seconds > 0
                    ):
                        message, tool_calls, stream_iter, reasoning_content = _normalize_client_create_result(
                            await asyncio.wait_for(
                                create_task,
                                timeout=float(timeout_seconds),
                            )
                        )
                    else:
                        message, tool_calls, stream_iter, reasoning_content = _normalize_client_create_result(
                            await create_task
                        )

                reasoning_text, reasoning_parts = _split_reasoning_result(reasoning_content)

                resp = LLMResponse(
                    _stream=stream_iter,
                    _upper=self,
                    _auto_append_response=auto_append_response,
                    payloads=list(trimmed_payloads),
                    model_set=model_set,
                    context_manager=self.context_manager,
                    tool_call_compat=bool(model.get("tool_call_compat", False)),
                    message=message,
                    reasoning_content=reasoning_text,
                    reasoning_parts=reasoning_parts,
                    call_list=[],
                )

                # 非流：立即解析 tool_calls
                if tool_calls:
                    from .payload import ToolCall

                    resp.call_list = [
                        ToolCall(
                            id=tc.get("id"),
                            name=tc.get("name", ""),
                            args=tc.get("args", {}),
                        )
                        for tc in tool_calls
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

                session.record_success(latency=timer.elapsed)
                return resp

            except BaseException as e:
                if isinstance(e, asyncio.CancelledError):
                    logger.debug(
                        f"LLM 请求被取消: model={model_identifier}, request={self.request_name or '__default__'}",
                        exc_info=True,
                    )
                    raise

                # 将原始异常转换为标准化 LLM 异常
                classified_error = classify_exception(e, model=model_identifier)
                last_error = classified_error

                _err_type = type(classified_error).__name__
                _5xx_status_code: int | None = (
                    classified_error.status_code
                    if isinstance(classified_error, LLMAPIError)
                    and isinstance(classified_error.status_code, int)
                    and classified_error.status_code >= 500
                    else None
                )
                if (
                    isinstance(classified_error, (LLMTimeoutError, LLMRateLimitError, TimeoutError))
                    or _5xx_status_code is not None
                    or (isinstance(classified_error, LLMAPIError) and classified_error.status_code is None)
                ):
                    _status_hint = f", status_code={_5xx_status_code}" if _5xx_status_code is not None else ""
                    logger.warning(
                        f"LLM 请求暂时失败: model={model_identifier}, "
                        f"request={self.request_name or '__default__'}, error_type={_err_type}{_status_hint}"
                    )
                    logger.debug(
                        f"LLM 请求暂时失败（详情）: model={model_identifier}, "
                        f"request={self.request_name or '__default__'}, reason={classified_error}",
                        exc_info=True,
                    )
                else:
                    logger.error(
                        f"LLM 请求失败: model={model_identifier}, request={self.request_name or '__default__'}, "
                        f"error_type={_err_type}, reason={classified_error}",
                        exc_info=True,
                    )

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
                next_step = session.next_after_error(classified_error)

                if next_step.model is None:
                    logger.error(
                        f"LLM 请求重试已耗尽: request={self.request_name or '__default__'}, "
                        f"retry_count={retry_count}, last_error={type(classified_error).__name__}: {classified_error}"
                    )
                else:
                    next_model_identifier = next_step.model.get("model_identifier")
                    next_model_name = (
                        next_model_identifier
                        if isinstance(next_model_identifier, str) and next_model_identifier
                        else "<unknown>"
                    )
                    logger.warning(
                        f"LLM 请求将进行下一步重试: request={self.request_name or '__default__'}, "
                        f"retry_count={retry_count}, next_model={next_model_name}, "
                        f"delay_seconds={float(next_step.delay_seconds):.2f}"
                    )

                step = next_step

        assert last_error is not None
        raise last_error

def _validate_model_entry(model: dict[str, Any]) -> ModelEntry:
    """验证模型配置项的完整性和正确性，返回一个标准化的 ModelEntry 对象。"""
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

    return model  # type: ignore[return-value]


def _validate_model_set(model_set: Any) -> ModelSet:
    """
    验证模型配置集合的完整性和正确性，返回一个标准化的 ModelSet 对象。
    """
    if not isinstance(model_set, list) or not model_set:
        raise LLMConfigurationError("model_set 必须是非空 list[dict]")
    if not all(isinstance(x, dict) for x in model_set):
        raise LLMConfigurationError("model_set 必须是 list[dict]")
    return [_validate_model_entry(x) for x in model_set]
