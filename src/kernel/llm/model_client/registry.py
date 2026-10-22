"""
模型客户端注册表，负责根据模型配置返回对应的 client 实例。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..exceptions import LLMConfigurationError, LLMProviderUnavailableError
from .base import ASRModelClient, ChatModelClient, EmbeddingModelClient, RerankModelClient
from .anthropic_client import AnthropicChatClient
from .openai_client import OpenAIChatClient
from .responses_client import OpenAIResponsesClient
from ..types import ModelEntry
from src.kernel.jev import JEV_CLIENT_TYPES

# 已经识别、但本仓库尚未提供实现的 provider 别名集合。
# 这些别名一旦出现就必须显式报错，禁止静默回退到 OpenAI：
# 用 OpenAI 协议去请求 Gemini/Bedrock 只会产生难以定位的 404/400。
_GEMINI_CLIENT_TYPES = frozenset(
    {"gemini", "aiohttp_gemini", "gemini_v2", "google", "vertex_ai"}
)
_BEDROCK_CLIENT_TYPES = frozenset(
    {"bedrock", "amazon_bedrock", "bedrock_converse"}
)
_JEV_CLIENT_TYPES = JEV_CLIENT_TYPES

# JEv 相关别名的「宽松匹配」集合：这些名字都是 JEv 的常见拼写变体。
# 与 :data:`_JEV_CLIENT_TYPES` 的区别在于，宽松集合里还包含可能由拼写错误
# 产生的近似名（例如 ``jev_api`` / ``typesafe-ai``），命中后会给出明确的
# 提示，而不是静默回退到 OpenAI 把请求发到错误的 provider。
_JEV_LOOSE_CLIENT_TYPES = frozenset(
    {
        *_JEV_CLIENT_TYPES,
        "jev_api",
        "jev_client",
        "typesafe_ai",
        "typesafe_system_one",
    }
)


def _normalize_client_type(client_type: Any) -> str | None:
    """把 client_type 归一化为小写下划线形式，非字符串返回 None。"""
    if not isinstance(client_type, str):
        return None
    return client_type.casefold().replace("-", "_")


@dataclass(slots=True)
class ModelClientRegistry:
    """provider -> client 的注册表。

    当前默认提供 openai client；gemini/bedrock 后续可注册。
    """

    openai: ChatModelClient | None = None
    anthropic: ChatModelClient | None = None
    responses: ChatModelClient | None = None
    gemini: ChatModelClient | None = None
    bedrock: ChatModelClient | None = None

    def __post_init__(self) -> None:
        if self.openai is None:
            self.openai = OpenAIChatClient()
        if self.anthropic is None:
            self.anthropic = AnthropicChatClient()
        if self.responses is None:
            self.responses = OpenAIResponsesClient()

    def get_client_for_model(self, model: ModelEntry) -> ChatModelClient:
        """根据单个模型配置决定使用哪个 provider。

        当前阶段以 `client_type` 为准：openai/anthropic/gemini/bedrock。
        client_type 统一做大小写与连字符归一化；已识别但尚未注册的
        provider（gemini/bedrock 系列别名）直接抛出 LLMProviderUnavailableError，
        不会回退到 OpenAI；JEv 系列别名（含常见拼写变体）同样明确报错，
        引导使用 ``jev_api``；真正未知的 client_type 仍回退到 OpenAI。
        """

        normalized_type = _normalize_client_type(model.get("client_type"))
        if normalized_type:
            if normalized_type == "openai" and self.openai is not None:
                return self.openai
            if normalized_type == "anthropic" and self.anthropic is not None:
                return self.anthropic
            if normalized_type in {"openai_response", "responses", "openai.responses"} and self.responses is not None:
                return self.responses
            if normalized_type in _JEV_LOOSE_CLIENT_TYPES:
                raise LLMProviderUnavailableError(
                    "Jev 是独立 typed-evaluation provider，请通过 src.app.plugin_system.api.jev_api 调用，"
                    "不能作为传统 LLM ChatModelClient 使用"
                    f"（api_provider={model.get('api_provider')!r}, "
                    f"model_identifier={model.get('model_identifier')!r}）"
                )
            if normalized_type in _GEMINI_CLIENT_TYPES:
                if self.gemini is None:
                    raise LLMProviderUnavailableError(
                        f"Gemini client 未注册（client_type={normalized_type}），"
                        "不能将 Gemini 请求回退到 OpenAI"
                        f"（api_provider={model.get('api_provider')!r}, "
                        f"model_identifier={model.get('model_identifier')!r}）"
                    )
                return self.gemini
            if normalized_type in _BEDROCK_CLIENT_TYPES:
                if self.bedrock is None:
                    raise LLMProviderUnavailableError(
                        f"Bedrock client 未注册（client_type={normalized_type}），"
                        "不能将 Bedrock 请求回退到 OpenAI"
                        f"（api_provider={model.get('api_provider')!r}, "
                        f"model_identifier={model.get('model_identifier')!r}）"
                    )
                return self.bedrock

        if self.openai is None:
            raise LLMConfigurationError("OpenAI client 未配置")
        return self.openai

    def get_embedding_client_for_model(self, model: ModelEntry) -> EmbeddingModelClient:
        """根据单个模型配置获取 embedding client。"""

        client = self.get_client_for_model(model)
        if not hasattr(client, "create_embedding"):
            raise LLMProviderUnavailableError("当前 client 不支持 embeddings 请求")
        return client  # type: ignore[return-value]

    def get_rerank_client_for_model(self, model: ModelEntry) -> RerankModelClient:
        """根据单个模型配置获取 rerank client。"""

        client = self.get_client_for_model(model)
        if not hasattr(client, "create_rerank"):
            raise LLMProviderUnavailableError("当前 client 不支持 rerank 请求")
        return client  # type: ignore[return-value]

    def get_asr_client_for_model(self, model: ModelEntry) -> ASRModelClient:
        """根据单个模型配置获取 ASR client。"""

        client = self.get_client_for_model(model)
        if not hasattr(client, "create_transcription"):
            raise LLMProviderUnavailableError("当前 client 不支持 ASR 请求")
        return client  # type: ignore[return-value]
