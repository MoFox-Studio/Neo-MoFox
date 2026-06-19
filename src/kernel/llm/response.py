"""LLM 响应对象的缓冲式与流式包装层。

同一个响应对象支持三种使用方式：
- ``await response`` 获取最终完整消息；
- ``async for chunk in response`` 获取文本增量；
- 转成 payload 并继续串联后续工具调用请求。

流状态的归并逻辑单独放在 ``stream_state.py``，确保所有消费路径共享同一套语义。

流式消费期间的自动重试也在这个模块内统一处理。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Self

from src.kernel.logger import get_logger

from .exceptions import (
    LLMAPIError,
    LLMRateLimitError,
    LLMResponseConsumedError,
    LLMTimeoutError,
)
from .model_client import StreamEvent
from .payload import LLMPayload, ReasoningText, Text, ToolCall
from .roles import ROLE
from .stream_state import (
    LLMStreamReducer,
    drain_stream,
)
from .tool_call_compat import parse_tool_call_compat_response

if TYPE_CHECKING:
    from .context import LLMContextManager
    from .request import LLMRequest
    from .types import ModelSet

logger = get_logger("kernel.llm.response", display="LLM 响应")


def _is_retryable_stream_error(exc: BaseException) -> bool:
    """判断流式消费期间的异常是否值得在 LLMResponse 层面自动重试。

    规则与 ``request_execution._log_request_error`` 保持一致：
    - 服务端临时故障（status_code is None 或 >=500）→ 可重试
    - 速率限制 → 可重试
    - 超时（包括 LLM 级别和通用 TimeoutError）→ 可重试
    - CancelledError → 不可重试（直接上抛）
    - 其他错误（400、认证失败等）→ 不可重试
    """
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, (LLMRateLimitError, LLMTimeoutError, TimeoutError)):
        return True
    if isinstance(exc, LLMAPIError):
        return exc.status_code is None or exc.status_code >= 500
    return False

@dataclass(slots=True)
class LLMResponse:
    """对缓冲式 provider 结果或实时流结果的统一包装。"""
    _stream: AsyncIterator[StreamEvent] | None
    _upper: "LLMRequest | LLMResponse"
    _auto_append_response: bool

    payloads: list[LLMPayload]
    model_set: "ModelSet"
    context_manager: LLMContextManager | None = None

    message: str | None = None
    reasoning_content: str | None = None
    reasoning_parts: list[ReasoningText] | None = None
    call_list: list[ToolCall] | None = None
    tool_call_compat: bool = False
    _stream_stats_recorder: Callable[[dict[str, object] | None, float], None] | None = (
        None
    )
    _stream_started_at: float | None = None
    _usage: dict[str, object] | None = None
    stop_reason: str | None = None

    _consumed: bool = False
    _appended_to_context: bool = False

    _original_payloads: list[LLMPayload] | None = None

    def __post_init__(self) -> None:
        """规范化可选状态，并继承上游的 context manager。"""
        if self.call_list is None:
            self.call_list = []
        if self.reasoning_parts is None:
            self.reasoning_parts = []
        elif self.reasoning_content is None:
            self.reasoning_content = (
                "".join(
                    part.text
                    for part in self.reasoning_parts
                    if isinstance(part.text, str)
                )
                or None
            )
        if self.context_manager is None:
            ctx = getattr(self._upper, "context_manager", None)
            if ctx:
                self.context_manager = ctx

    # ── 流式重试辅助方法 ──────────────────────────────────────────

    def _get_stream_retries(self) -> int:
        """从 model_set 读取流式重试次数上限，默认 2。"""
        if self.model_set:
            return self.model_set[0].get("max_retry", 2)
        return 2

    def _get_stream_retry_delay(self) -> float:
        """从 model_set 读取流式重试退避延迟（秒），默认 3.0。"""
        if self.model_set:
            return self.model_set[0].get("retry_interval", 3.0)
        return 3.0

    def _retry_reset(self) -> None:
        """将响应状态恢复到流消费之前，准备重试。"""
        if self._original_payloads is not None:
            self.payloads = list(self._original_payloads)
        self._appended_to_context = False
        self.message = None
        self.reasoning_content = None
        self.reasoning_parts = []
        self.call_list = []
        self._usage = None
        self.stop_reason = None

    async def _retry_send(self) -> "LLMResponse":
        """从当前 payloads / model_set / context_manager 重建请求并发送。

        与 ``send()`` 不同，本方法不会先 drain 当前流，也不追加当前响应内容。
        """
        from .request import LLMRequest

        req = LLMRequest(
            self.model_set,
            request_name=getattr(self._upper, "request_name", ""),
            meta_data=dict(getattr(self._upper, "meta_data", {})),
            context_manager=self.context_manager,
        )
        req.payloads = list(self.payloads)
        return await req.send(
            auto_append_response=self._auto_append_response, stream=True
        )

    def _maybe_apply_tool_call_compat(self) -> None:
        """在兼容模式启用时，从纯文本中回填工具调用。"""
        if not self.tool_call_compat or self.call_list or not self.message:
            return

        parsed_message, parsed_calls = parse_tool_call_compat_response(self.message)
        self.message = parsed_message
        self.call_list = [
            ToolCall(
                id=call.get("id"),
                name=call.get("name", ""),
                args=call.get("args", {}),
            )
            for call in parsed_calls
        ]

    def __await__(self):
        return self._collect_full_response().__await__()

    async def stream_events(self) -> AsyncIterator[StreamEvent]:
        """以严格单次消费的方式产出原始流事件，并实时更新响应状态。

        流式消费期间遇到可重试错误时自动重建请求并重试。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            if self.message:
                yield StreamEvent(text_delta=self.message)
            return

        retries = self._get_stream_retries()
        delay = self._get_stream_retry_delay()

        while True:
            reducer = LLMStreamReducer()
            stream_error: Exception | None = None
            try:
                async for event in self._stream:
                    reducer.apply(event)
                    snapshot = reducer.finalize()
                    self.message = snapshot.message or None
                    self.reasoning_parts = snapshot.reasoning_parts or []
                    self.reasoning_content = snapshot.reasoning_content
                    self.call_list = snapshot.call_list
                    yield event
            except Exception as exc:
                stream_error = exc

            self._apply_stream_result(reducer.finalize(stream_error))

            if stream_error is None:
                return  # 成功

            if isinstance(stream_error, asyncio.CancelledError):
                raise

            if not _is_retryable_stream_error(stream_error) or retries <= 0:
                raise stream_error

            retries -= 1
            logger.warning(
                "LLM 流式中断，正在重试（剩余 %d 次）: %s",
                retries,
                type(stream_error).__name__,
            )
            await asyncio.sleep(delay)
            self._retry_reset()
            new_resp = await self._retry_send()
            self._stream = new_resp._stream

    async def __aiter__(self):
        """以严格单次消费的方式产出流式文本增量。"""
        async for event in self.stream_events():
            if event.text_delta:
                yield event.text_delta

    async def _collect_full_response(self) -> str:
        """以严格单次消费的方式收集完整响应文本。

        流式消费期间遇到可重试错误时自动重建请求并重试。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            self._maybe_append_response_to_context()
            return self.message or ""

        retries = self._get_stream_retries()
        delay = self._get_stream_retry_delay()

        while True:
            result = await drain_stream(self._stream)
            self._apply_stream_result(result)

            if result.error is None:
                return self.message or ""

            error = result.error
            if isinstance(error, asyncio.CancelledError):
                raise

            if not _is_retryable_stream_error(error) or retries <= 0:
                raise error

            retries -= 1
            logger.warning(
                "LLM 流式中断，正在重试（剩余 %d 次）: %s",
                retries,
                type(error).__name__,
            )
            await asyncio.sleep(delay)
            self._retry_reset()
            new_resp = await self._retry_send()
            self._stream = new_resp._stream

    def _apply_stream_result(self, result) -> None:
        """把归并后的流状态应用到当前响应对象。"""
        self.message = result.message
        self.reasoning_parts = result.reasoning_parts or self.reasoning_parts
        self.reasoning_content = result.reasoning_content or self.reasoning_content
        self.call_list = result.call_list
        self._usage = result.usage
        self.stop_reason = result.stop_reason
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()
        self._maybe_record_stream_stats()

    def _maybe_append_response_to_context(self) -> None:
        """按需把 assistant payload 追加回上下文。"""
        if not self._auto_append_response:
            return
        self._append_current_response_payload()

    def _append_current_response_payload(self) -> None:
        """把当前响应实体化为一个 assistant payload。"""
        if self._appended_to_context or not self._has_response_payload_content():
            return

        content_parts: list[object] = []
        if self.reasoning_parts:
            content_parts.extend(self.reasoning_parts)
        elif self.reasoning_content:
            content_parts.append(ReasoningText(self.reasoning_content))
        if self.message:
            content_parts.append(Text(self.message))
        if self.call_list:
            content_parts.extend(self.call_list)
        if not content_parts:
            return

        assistant_payload = LLMPayload(ROLE.ASSISTANT, content_parts)  # type: ignore[arg-type]
        if self.context_manager is not None:
            self.payloads = self.context_manager.add_payload(
                self.payloads, assistant_payload
            )
            self._appended_to_context = True
            return

        self.payloads.append(assistant_payload)
        self._maybe_apply_context_manager()
        self._appended_to_context = True

    def _has_response_payload_content(self) -> bool:
        """判断当前响应是否已经具备可写回 payload 的内容。"""
        return bool(
            self.reasoning_parts or self.reasoning_content or self.message or self.call_list
        )

    def _maybe_apply_context_manager(self) -> None:
        """在存在 context manager 时执行追加后的裁剪。"""
        if not self.context_manager:
            return
        self.payloads = self.context_manager.maybe_trim(self.payloads)

    def to_payload(self) -> LLMPayload:
        """把响应转换成一个可复用的 assistant payload。"""
        content_parts: list[object] = []
        if self.reasoning_parts:
            content_parts.extend(self.reasoning_parts)
        elif self.reasoning_content:
            content_parts.append(ReasoningText(self.reasoning_content))
        if self.message:
            content_parts.append(Text(self.message))
        if self.call_list:
            content_parts.extend(self.call_list)
        if not content_parts:
            content_parts.append(Text(""))
        return LLMPayload(ROLE.ASSISTANT, content_parts)  # type: ignore[arg-type]

    def add_payload(self, payload: "LLMPayload | LLMResponse", position=None) -> Self:
        """把 payload 或另一个 response 追加进当前响应上下文。"""
        if isinstance(payload, LLMResponse):
            payload = payload.to_payload()

        if self.context_manager is not None:
            self.payloads = self.context_manager.add_payload(
                self.payloads,
                payload,
                position=int(position) if position is not None else None,
            )
            return self

        if position is not None:
            self.payloads.insert(int(position), payload)
        else:
            if self.payloads and self.payloads[-1].role == payload.role:
                self.payloads[-1].content.extend(payload.content)
            else:
                self.payloads.append(payload)
        self._maybe_apply_context_manager()
        return self

    def add_call_reflex(self, results: list[LLMPayload]) -> Self:
        """仅在当前响应确实请求了工具时，才追加工具执行结果。"""
        if not self.call_list:
            return self

        if self.context_manager is not None:
            for payload in results:
                self.payloads = self.context_manager.add_payload(self.payloads, payload)
            return self

        for payload in results:
            self.payloads.append(payload)
        self._maybe_apply_context_manager()
        return self

    async def send(
        self,
        auto_append_response: bool = True,
        *,
        stream: bool = True,
    ) -> "LLMResponse":
        """把当前响应里的 payload 作为下一次请求的输入继续发送。"""
        if not self._consumed:
            await self._collect_full_response()

        if not self._appended_to_context:
            self.add_payload(self.to_payload())
            self._appended_to_context = True

        from .request import LLMRequest

        req = LLMRequest(
            self.model_set,
            request_name=getattr(self._upper, "request_name", ""),
            meta_data=dict(getattr(self._upper, "meta_data", {})),
            context_manager=self.context_manager,
        )
        req.payloads = list(self.payloads)
        return await req.send(auto_append_response=auto_append_response, stream=stream)

    async def stream_with_callback(
        self, on_chunk: Callable[[str], Awaitable[None]]
    ) -> str:
        """以回调方式消费流式文本增量，返回最终完整文本。

        流式消费期间遇到可重试错误时自动重建请求并重试。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            content = self.message or ""
            if content:
                await on_chunk(content)
            self._maybe_append_response_to_context()
            return content

        retries = self._get_stream_retries()
        delay = self._get_stream_retry_delay()

        while True:
            result = await drain_stream(self._stream, on_text_delta=on_chunk)
            self._apply_stream_result(result)

            if result.error is None:
                return self.message or ""

            error = result.error
            if isinstance(error, asyncio.CancelledError):
                raise

            if not _is_retryable_stream_error(error) or retries <= 0:
                raise error

            retries -= 1
            logger.warning(
                "LLM 流式中断，正在重试（剩余 %d 次）: %s",
                retries,
                type(error).__name__,
            )
            await asyncio.sleep(delay)
            self._retry_reset()
            new_resp = await self._retry_send()
            self._stream = new_resp._stream

    async def stream_events_with_callback(
        self,
        on_event: Callable[[StreamEvent], Awaitable[None]],
    ) -> str:
        """以事件粒度消费流式响应，每个 ``StreamEvent`` 都会交给回调。

        与 ``stream_with_callback`` 的区别是回调接收完整的 ``StreamEvent``，
        包含 ``tool_call_id`` / ``tool_name`` / ``tool_args_delta`` 等字段，
        而不仅仅是文本增量。

        流式消费期间遇到可重试错误时自动重建请求并重试。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            self._maybe_append_response_to_context()
            return self.message or ""

        retries = self._get_stream_retries()
        delay = self._get_stream_retry_delay()

        while True:
            reducer = LLMStreamReducer()
            stream_error: Exception | None = None

            try:
                async for event in self._stream:
                    reducer.apply(event)
                    await on_event(event)
            except Exception as exc:
                stream_error = exc

            self._apply_stream_result(reducer.finalize(stream_error))

            if stream_error is None:
                return self.message or ""

            error = stream_error
            if isinstance(error, asyncio.CancelledError):
                raise

            if not _is_retryable_stream_error(error) or retries <= 0:
                raise error

            retries -= 1
            logger.warning(
                "LLM 流式中断，正在重试（剩余 %d 次）: %s",
                retries,
                type(error).__name__,
            )
            await asyncio.sleep(delay)
            self._retry_reset()
            new_resp = await self._retry_send()
            self._stream = new_resp._stream

    async def stream_with_buffer(self, buffer_size: int = 10) -> AsyncIterator[str]:
        """以缓冲方式产出流式文本块。

        流式消费期间遇到可重试错误时自动重建请求并重试。
        """
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            self._maybe_apply_tool_call_compat()
            content = self.message or ""
            if content:
                yield content
            self._maybe_append_response_to_context()
            return

        retries = self._get_stream_retries()
        delay = self._get_stream_retry_delay()

        while True:
            reducer = LLMStreamReducer()
            buffer: list[str] = []
            buffer_len = 0
            stream_error: Exception | None = None
            try:
                async for event in self._stream:
                    text_delta = reducer.apply(event)
                    if not text_delta:
                        continue
                    buffer.append(text_delta)
                    buffer_len += len(text_delta)
                    if buffer_len >= buffer_size:
                        yield "".join(buffer)
                        buffer.clear()
                        buffer_len = 0
            except Exception as exc:
                stream_error = exc

            if buffer:
                yield "".join(buffer)

            self._apply_stream_result(reducer.finalize(stream_error))

            if stream_error is None:
                return  # 成功

            if isinstance(stream_error, asyncio.CancelledError):
                raise

            if not _is_retryable_stream_error(stream_error) or retries <= 0:
                raise stream_error

            retries -= 1
            logger.warning(
                "LLM 流式中断，正在重试（剩余 %d 次）: %s",
                retries,
                type(stream_error).__name__,
            )
            await asyncio.sleep(delay)
            self._retry_reset()
            new_resp = await self._retry_send()
            self._stream = new_resp._stream

    def _maybe_record_stream_stats(self) -> None:
        if self._stream_stats_recorder is None:
            return

        started_at = self._stream_started_at
        latency = time.perf_counter() - started_at if started_at is not None else 0.0
        recorder = self._stream_stats_recorder
        self._stream_stats_recorder = None
        recorder(self._usage, latency)
