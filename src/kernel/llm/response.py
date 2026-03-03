"""LLM 响应模块

提供 LLMResponse 类，统一处理流式和非流式响应。

LLMResponse 支持：
- await 模式：收集完整响应
- async for 模式：流式处理响应
- 自动追加响应到上下文
- 工具调用处理
"""

from __future__ import annotations

import json
from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import Any, AsyncIterator, Self, TYPE_CHECKING

from .exceptions import LLMResponseConsumedError
from .model_client import StreamEvent
from .payload import LLMPayload, Text, ToolCall
from .roles import ROLE
from .tool_call_compat import parse_tool_call_compat_response

if TYPE_CHECKING:
    from .request import LLMRequest
    from .types import ModelSet
    from .context import LLMContextManager


@dataclass(slots=True)
class LLMResponse:
    """LLMResponse：既可 await（收集全量）也可 async for（流式吐出）。"""

    _stream: AsyncIterator[StreamEvent] | None
    _upper: "LLMRequest | LLMResponse"
    _auto_append_response: bool

    payloads: list[LLMPayload]
    model_set: "ModelSet"
    context_manager: LLMContextManager | None = None

    message: str | None = None
    call_list: list[ToolCall] | None = None
    tool_call_compat: bool = False

    _consumed: bool = False

    def __post_init__(self) -> None:
        """确保 message 和 call_list 不为 None，方便后续处理；如果 context_manager 为空且上层存在，则继承上层的 context_manager。"""
        if self.call_list is None:
            self.call_list = []
        if self.context_manager is None:
            ctx = getattr(self._upper, "context_manager", None)
            if ctx:
                self.context_manager = ctx

    def _maybe_apply_tool_call_compat(self) -> None:
        """如果启用了 tool_call_compat 模式且尚未解析过工具调用，则尝试从 message 中解析工具调用信息。"""
        if not self.tool_call_compat:
            return
        if self.call_list:
            return
        if not self.message:
            return

        parsed_message, parsed_calls = parse_tool_call_compat_response(self.message)
        self.message = parsed_message
        self.call_list = [
            ToolCall(id=call.get("id"), name=call.get("name", ""), args=call.get("args", {}))
            for call in parsed_calls
        ]

    def __await__(self):
        """await 模式：收集完整响应，适用于需要一次性获取完整结果的场景。"""
        return self._collect_full_response().__await__()

    async def __aiter__(self):
        """async for 模式：流式处理响应，适用于需要边接收边处理的场景（如 UI 更新）。"""
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            # 兼容非流式直接返回完整响应的情况，尝试解析工具调用信息后再返回文本内容
            self._maybe_apply_tool_call_compat()
            content = self.message or ""
            if content:
                yield content
            return

        full_content: list[str] = []
        tool_acc = _ToolCallAccumulator()
        stream_error: Exception | None = None
        try:
            async for event in self._stream:
                if event.text_delta:
                    full_content.append(event.text_delta)
                    yield event.text_delta
                if event.tool_name or event.tool_args_delta or event.tool_call_id:
                    tool_acc.apply(event)
        except Exception as e:
            # 部分 provider/SDK 会在流尾抛出"连接关闭"等异常。
            # 先记录异常，确保已收集的内容能正确落库，再重新抛出。
            stream_error = e

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()

        if stream_error is not None:
            raise stream_error

    async def _collect_full_response(self) -> str:
        """收集完整响应，适用于需要一次性获取完整结果的场景。"""
        if self._consumed:
            raise LLMResponseConsumedError("Response has already been consumed.")
        self._consumed = True

        if self._stream is None:
            # 非流式直接返回完整响应的情况，尝试解析工具调用信息后再返回文本内容
            self._maybe_apply_tool_call_compat()
            self._maybe_append_response_to_context()
            return self.message or ""

        # 流式处理的情况，收集完整文本并解析工具调用信息
        full_content: list[str] = []
        tool_acc = _ToolCallAccumulator()
        stream_error: Exception | None = None
        try:
            async for event in self._stream:
                if event.text_delta:
                    full_content.append(event.text_delta)
                if event.tool_name or event.tool_args_delta or event.tool_call_id:
                    tool_acc.apply(event)
        except Exception as e:
            # 部分 provider/SDK 会在流尾抛出"连接关闭"等异常。
            # 先记录异常，确保已收集的内容能正确落库，再重新抛出。
            stream_error = e

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()

        if stream_error is not None:
            raise stream_error

        return self.message


    def _maybe_append_response_to_context(self) -> None:
        """如果启用了自动追加响应到上下文，并且当前响应有内容，则将其作为新的 ASSISTANT 消息追加到 payloads 中，供后续请求使用。"""
        if not self._auto_append_response:
            return

        content_parts: list[object] = []
        if self.message:
            content_parts.append(Text(self.message))
        if self.call_list:
            content_parts.extend(self.call_list)

        if not content_parts:
            return

        assistant_payload = LLMPayload(ROLE.ASSISTANT, content_parts)  # type: ignore[arg-type]
        if self.context_manager is not None:
            self.payloads = self.context_manager.add_payload(self.payloads, assistant_payload)
            return

        self.payloads.append(assistant_payload)
        self._maybe_apply_context_manager()

    def _maybe_apply_context_manager(self) -> None:
        """如果启用了上下文管理器，则尝试裁剪 payloads，以适应上下文限制。"""
        if not self.context_manager:
            return
        self.payloads = self.context_manager.maybe_trim(self.payloads)

    def to_payload(self) -> LLMPayload:
        """将当前响应转换为一个 LLMPayload 对象，适用于需要将响应作为消息追加到上下文中的场景。"""
        content_parts: list[object] = []
        if self.message:
            content_parts.append(Text(self.message))
        if self.call_list:
            content_parts.extend(self.call_list)
        if not content_parts:
            content_parts.append(Text(""))
        return LLMPayload(ROLE.ASSISTANT, content_parts)  # type: ignore[arg-type]

    def add_payload(self, payload: "LLMPayload | LLMResponse", position=None) -> Self:
        """在当前响应的 payloads 中追加一个新的 payload，可以是一个 LLMPayload 对象，也可以是另一个 LLMResponse 对象（会被转换为 LLMPayload）。"""
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
        """在当前响应的 payloads 中追加一个新的工具调用结果列表，适用于工具调用完成后需要将结果写回上下文的场景。"""
        if self.context_manager is not None:
            for payload in results:
                self.payloads = self.context_manager.add_payload(self.payloads, payload)
            return self

        for payload in results:
            self.payloads.append(payload)
        self._maybe_apply_context_manager()
        return self

    async def send(self, auto_append_response: bool = True, *, stream: bool = True) -> "LLMResponse":
        """
        将当前响应作为新的请求发送，适用于需要基于当前响应继续对话的场景。

        Args:
            auto_append_response: 是否自动将当前响应追加到上下文中，默认为 True。
            stream: 是否以流式方式发送请求，默认为 True。

        Returns:
            LLMResponse: 新的请求的响应对象。
        """
        # 延迟导入，避免循环依赖
        from .request import LLMRequest

        # 创建一个新的 LLMRequest 对象，继承当前响应的 model_set 和 context_manager
        # 并将当前响应的 payloads 作为新的请求的初始 payloads，然后发送请求并返回响应对象
        req = LLMRequest(
            self.model_set,
            request_name=getattr(self._upper, "request_name", ""),
            context_manager=self.context_manager,
        )
        req.payloads = list(self.payloads)
        return await req.send(auto_append_response=auto_append_response, stream=stream)

    async def stream_with_callback(self, on_chunk: Callable[[str], Awaitable[None]]) -> str:
        """流式响应 + 实时回调。

        适用场景：需要在接收到每个 chunk 时立即执行某些操作（如 UI 更新）。

        Args:
            on_chunk: 异步回调函数，接收每个文本 chunk。

        Returns:
            完整的响应文本。

        Raises:
            LLMResponseConsumedError: 如果响应已被消费。
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

        full_content: list[str] = []
        tool_acc = _ToolCallAccumulator()
        async for event in self._stream:
            if event.text_delta:
                full_content.append(event.text_delta)
                await on_chunk(event.text_delta)
            if event.tool_name or event.tool_args_delta or event.tool_call_id:
                tool_acc.apply(event)

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()
        return self.message

    async def stream_with_buffer(self, buffer_size: int = 10) -> AsyncIterator[str]:
        """带缓冲的流式响应。

        适用场景：减少回调次数，累积多个 chunk 后再 yield。

        Args:
            buffer_size: 缓冲区大小（字符数），达到此大小后才 yield。

        Yields:
            缓冲后的文本块。

        Raises:
            LLMResponseConsumedError: 如果响应已被消费。
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

        full_content: list[str] = []
        buffer: list[str] = []
        buffer_len = 0
        tool_acc = _ToolCallAccumulator()

        stream_error: Exception | None = None
        try:
            async for event in self._stream:
                if event.text_delta:
                    full_content.append(event.text_delta)
                    buffer.append(event.text_delta)
                    buffer_len += len(event.text_delta)

                    if buffer_len >= buffer_size:
                        buffered = "".join(buffer)
                        yield buffered
                        buffer.clear()
                        buffer_len = 0

                if event.tool_name or event.tool_args_delta or event.tool_call_id:
                    tool_acc.apply(event)
        except Exception as e:
            # 有些 provider/SDK 会在流尾抛出“连接关闭”等异常。
            # 对于带 buffer 的消费方式，这会导致最后未 flush 的片段丢失。
            # 这里先记录异常，确保尾段 flush，再把异常抛出。
            stream_error = e

        # 剩余内容（无论正常结束还是异常结束，都尽量 flush）
        if buffer:
            yield "".join(buffer)

        self.message = "".join(full_content)
        self.call_list = tool_acc.finalize()
        self._maybe_apply_tool_call_compat()
        self._maybe_append_response_to_context()

        if stream_error is not None:
            raise stream_error


class _ToolCallAccumulator:
    """把 OpenAI 风格的 tool_call 增量拼成最终 ToolCall 列表。

    OpenAI 流式协议中，工具调用分多个 chunk 传输：
    - 首个 chunk：携带 tool_call_id + tool_name（以及可能的首段 args）
    - 后续 chunk：tool_call_id 可能为 None，仅携带 tool_args_delta

    因此需要追踪"当前活跃 id"，将无 id 的增量归属到最近一次出现的工具调用。
    """

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._current_id: str | None = None  # 追踪最近一次有效的 tool_call_id

    def apply(self, event: StreamEvent) -> None:
        """处理一个新的 StreamEvent，更新工具调用的积累状态。"""
        # 优先使用事件携带的 id；若无则沿用上一次的 id（OpenAI 后续 chunk 不重复发送 id）
        effective_id = event.tool_call_id or self._current_id
        if not effective_id:
            # 既无新 id 又无历史 id，无法归属，丢弃
            return

        if effective_id not in self._by_id:
            self._by_id[effective_id] = {"id": effective_id, "name": None, "args": ""}
            self._order.append(effective_id)

        # 更新当前活跃 id
        if event.tool_call_id:
            self._current_id = event.tool_call_id

        rec = self._by_id[effective_id]
        if event.tool_name:
            rec["name"] = event.tool_name
        if event.tool_args_delta:
            rec["args"] = (rec.get("args") or "") + event.tool_args_delta

    def finalize(self) -> list[ToolCall]:
        """
        将当前积累的工具调用记录转换为 ToolCall 对象列表。
        """
        out: list[ToolCall] = []
        for tool_call_id in self._order:
            rec = self._by_id[tool_call_id]
            name = rec.get("name") or ""
            args_raw = rec.get("args") or ""
            args: dict[str, Any] | str
            if not args_raw:
                args = {}
            else:
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = args_raw

            out.append(ToolCall(id=tool_call_id, name=name, args=args))
        return out
