"""定义模型客户端协议、兼容结果对象和供应商无关的流事件。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from ..payload import LLMPayload, LLMUsable, ReasoningText
from ..provider_state import ProviderReplayState


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
    provider_state: ProviderReplayState | None = field(default=None, repr=False)
    reset: bool = False


@dataclass(slots=True)
class ModelClientResult:
    """携带响应、用量及供应商续接状态的结果对象，同时保留旧元组实现兼容。

    ``stream_iter`` 由响应消费方负责耗尽或显式关闭；``provider_state`` 与
    ``stop_reason`` 是结构化结果的扩展字段，不应在旧元组归一化时丢弃。
    """

    message: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    stream_iter: AsyncIterator[StreamEvent] | None = None
    reasoning_content: str | list[ReasoningText] | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    provider_state: ProviderReplayState | None = field(default=None, repr=False)


class ChatModelClient(Protocol):
    """将单个模型配置与消息转换为缓冲结果或由调用方持有的响应流。"""

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> ModelClientResult | tuple[
        str | None,
        list[dict[str, Any]] | None,
        AsyncIterator[StreamEvent] | None,
        str | list[ReasoningText] | None,
        dict[str, Any] | None,
    ]:
        """发起一次聊天请求，返回结构化结果或兼容的历史五元组。

        Args:
            model_name: 发送给目标供应商的模型标识。
            payloads: 已由框架组织的会话消息，可能包含供应商续接状态。
            tools: 工具声明；本接口不接管工具执行。
            request_name: 用于关联请求观测的名称。
            model_set: 当前尝试选中的单条模型配置，而非整个候选集合。
            stream: 是否返回流事件迭代器。

        Returns:
            ``ModelClientResult``，或历史五元组
            ``(message, tool_calls, stream_iter, reasoning_content, usage)``。
            非流式结果直接携带文本、工具、推理与用量；流式结果通过
            ``stream_iter`` 逐步提供这些信息，调用方负责耗尽或显式关闭。
            结构化结果还可携带 ``stop_reason`` 与 ``provider_state``，执行层
            需从原对象保留它们，不能只消费兼容五字段后丢弃续接状态。
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
