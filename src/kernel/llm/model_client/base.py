from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from ..payload import LLMPayload, LLMUsable, ReasoningText


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """provider-agnostic 的流事件。"""

    text_delta: str | None = None
    reasoning_delta: str | None = None
    reasoning_signature_delta: str | None = None
    reasoning_block_type: str | None = None
    reasoning_redacted_data: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args_delta: str | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    stop_reason: str | None = None


class ChatModelClient(Protocol):
    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[
        str | None,
        list[dict[str, Any]] | None,
        AsyncIterator[StreamEvent] | None,
        str | list[ReasoningText] | None,
        dict[str, Any] | None,
    ]:
        """发起一次聊天请求。

        返回五元组：
        - message: 非流时的完整文本；流式则为 None
        - tool_calls: 非流时解析出的工具调用列表；流式则为 None（将通过 StreamEvent 解析）
        - stream_iter: 流式迭代器；非流则为 None
        - reasoning_content: 非流时的推理内容；流式则为 None（将通过 StreamEvent 解析）
        - usage: API 返回的 usage 字典（含 prompt_tokens/completion_tokens/cache 等）；
          流式且未启用 usage 上报时为 None
        """
        ...


class EmbeddingModelClient(Protocol):
    async def create_embedding(
        self,
        *,
        model_name: str,
        inputs: list[str],
        request_name: str,
        model_set: Any,
    ) -> list[list[float]]:
        """发起 embedding 请求并返回向量列表。"""
        ...


class RerankModelClient(Protocol):
    async def create_rerank(
        self,
        *,
        model_name: str,
        query: str,
        documents: list[Any],
        top_n: int | None,
        request_name: str,
        model_set: Any,
    ) -> list[dict[str, Any]]:
        """发起 rerank 请求并返回排序结果。"""
        ...


class ASRModelClient(Protocol):
    async def create_transcription(
        self,
        *,
        model_name: str,
        audio_bytes: bytes,
        request_name: str,
        model_set: Any,
    ) -> str:
        """发起语音转文字请求并返回识别结果。"""
        ...
