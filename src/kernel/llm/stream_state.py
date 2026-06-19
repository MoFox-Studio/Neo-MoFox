"""流式 LLM 响应的状态归并逻辑。

同一条 provider 流会被不同消费方式使用：
既有等待完整消息的场景，也有逐段迭代文本的场景，还有提取工具调用和 reasoning
结构化信息的场景。这个模块用一个统一 reducer 把这些视图收口到一起。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .model_client import StreamEvent
from .payload import ReasoningText, ToolCall


@dataclass(slots=True)
class StreamReductionResult:
    """由 provider 流归并得到的最终状态。"""
    message: str
    reasoning_content: str | None
    reasoning_parts: list[ReasoningText]
    call_list: list[ToolCall]
    usage: dict[str, Any] | None
    stop_reason: str | None = None
    error: Exception | None = None


class LLMStreamReducer:
    """增量收集文本、reasoning 片段、工具调用和 usage。"""

    def __init__(self) -> None:
        self._full_content: list[str] = []
        self._full_reasoning: list[str] = []
        self._tool_acc = _ToolCallAccumulator()
        self._reasoning_acc = _ReasoningBlockAccumulator()
        self._usage: dict[str, Any] | None = None
        self._stop_reason: str | None = None

    def apply(self, event: StreamEvent) -> str | None:
        """应用一个流事件，并返回其中产生的文本增量。"""
        text_delta = event.text_delta
        if text_delta:
            self._full_content.append(text_delta)
        if (
            event.reasoning_block_type
            or event.reasoning_delta
            or event.reasoning_signature_delta
        ):
            self._reasoning_acc.apply(event)
        if event.reasoning_delta:
            self._full_reasoning.append(event.reasoning_delta)
        if event.tool_name or event.tool_args_delta or event.tool_call_id:
            self._tool_acc.apply(event)
        if event.usage:
            self._usage = dict(event.usage)
        if event.stop_reason:
            self._stop_reason = event.stop_reason
        if event.finish_reason:
            self._stop_reason = event.finish_reason
        return text_delta

    def finalize(self, error: Exception | None = None) -> StreamReductionResult:
        """把当前 reducer 状态冻结成最终响应快照。"""
        reasoning_parts = self._reasoning_acc.snapshot()
        reasoning_content = "".join(self._full_reasoning) or None
        if reasoning_parts and reasoning_content is None:
            reasoning_content = (
                "".join(part.text for part in reasoning_parts if isinstance(part.text, str))
                or None
            )
        return StreamReductionResult(
            message="".join(self._full_content),
            reasoning_content=reasoning_content,
            reasoning_parts=reasoning_parts,
            call_list=self._tool_acc.finalize(),
            usage=self._usage,
            stop_reason=self._stop_reason,
            error=error,
        )


async def drain_stream(
    stream: Any,
    *,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
) -> StreamReductionResult:
    """使用共享 reducer 消费完整条 provider 流。"""
    reducer = LLMStreamReducer()
    stream_error: Exception | None = None
    try:
        async for event in stream:
            text_delta = reducer.apply(event)
            if text_delta and on_text_delta is not None:
                await on_text_delta(text_delta)
    except Exception as exc:
        stream_error = exc
    return reducer.finalize(stream_error)


class _ToolCallAccumulator:
    """按 tool-call id 重组流式到达的工具调用片段。"""

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._current_id: str | None = None

    def apply(self, event: StreamEvent) -> None:
        """把一个事件合并进当前工具调用缓冲区。"""
        effective_id = event.tool_call_id or self._current_id
        if not effective_id:
            return

        if effective_id not in self._by_id:
            self._by_id[effective_id] = {"id": effective_id, "name": None, "args": ""}
            self._order.append(effective_id)

        if event.tool_call_id:
            self._current_id = event.tool_call_id

        rec = self._by_id[effective_id]
        if event.tool_name:
            rec["name"] = event.tool_name
        if event.tool_args_delta:
            rec["args"] = (rec.get("args") or "") + event.tool_args_delta

    def finalize(self) -> list[ToolCall]:
        """生成最终有序的 ``ToolCall`` 列表。"""
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


class _ReasoningBlockAccumulator:
    """收集 provider 的 reasoning block，并保留其元数据。"""

    def __init__(self) -> None:
        self._current_type: str | None = None
        self._current_text: list[str] = []
        self._current_signature: str | None = None
        self._current_redacted_data: str | None = None
        self._blocks: list[ReasoningText] = []

    def apply(self, event: StreamEvent) -> None:
        """应用单个流事件中的 reasoning 相关增量。"""
        if event.reasoning_block_type:
            self._flush_current()
            self._current_type = event.reasoning_block_type
            self._current_text = []
            self._current_signature = None
            self._current_redacted_data = event.reasoning_redacted_data

        if event.reasoning_delta:
            if self._current_type is None:
                self._current_type = "thinking"
            self._current_text.append(event.reasoning_delta)

        if event.reasoning_signature_delta:
            if self._current_type is None:
                self._current_type = "thinking"
            self._current_signature = (
                (self._current_signature or "") + event.reasoning_signature_delta
            )

    def finalize(self) -> list[ReasoningText]:
        """刷出当前 block，并返回所有收集到的 reasoning 片段。"""
        self._flush_current()
        return list(self._blocks)

    def snapshot(self) -> list[ReasoningText]:
        """返回当前 reasoning 片段快照，不消费内部状态。"""
        blocks = list(self._blocks)
        if self._current_type == "thinking" and (
            self._current_text or self._current_signature
        ):
            blocks.append(
                ReasoningText(
                    "".join(self._current_text),
                    signature=self._current_signature,
                )
            )
        elif self._current_type == "redacted_thinking" and self._current_redacted_data:
            blocks.append(ReasoningText("", redacted_data=self._current_redacted_data))
        return blocks

    def _flush_current(self) -> None:
        """如果当前 reasoning block 含有有效数据，则把它落到结果中。"""
        if self._current_type == "thinking":
            if self._current_text or self._current_signature:
                self._blocks.append(
                    ReasoningText(
                        "".join(self._current_text),
                        signature=self._current_signature,
                    )
                )
        elif self._current_type == "redacted_thinking" and self._current_redacted_data:
            self._blocks.append(ReasoningText("", redacted_data=self._current_redacted_data))

        self._current_type = None
        self._current_text = []
        self._current_signature = None
        self._current_redacted_data = None
