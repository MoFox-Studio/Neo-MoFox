"""
模型客户端注册表，负责根据模型配置返回对应的 client 实例。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..exceptions import LLMConfigurationError, LLMProviderUnavailableError
from ..types import ModelEntry
from .anthropic_client import AnthropicChatClient
from .base import ASRModelClient, ChatModelClient, EmbeddingModelClient, RerankModelClient
from .gemini_client import get_default_gemini_client
from .openai_client import OpenAIChatClient
from .responses_client import OpenAIResponsesClient

# 只识别已声明的类型；每个别名映射到对应的注册表字段。
_CLIENT_TYPES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "openai_response": "responses",
    "responses": "responses",
    "openai.responses": "responses",
    "gemini": "gemini",
    "aiohttp_gemini": "gemini",
    "gemini_v2": "gemini",
    "google": "gemini",
    "bedrock": "bedrock",
    "amazon_bedrock": "bedrock",
    "bedrock_converse": "bedrock",
}


def _normalize_client_type(client_type: object) -> str:
    """把类型归一化为小写下划线形式，非字符串视同空值。"""
    if not isinstance(client_type, str):
        return ""
    return client_type.strip().casefold().replace("-", "_")


def resolve_client_type(client_type: object) -> str:
    """把已识别的客户端类型解析为请求分发与预检共用的类型名。

    Args:
        client_type: 原始类型值，可以来自未经配置层校验的技术条目。

    Returns:
        注册表中对应的客户端字段名。

    Raises:
        LLMProviderUnavailableError: 类型未知、无效，或是尚不支持的 Vertex AI。
    """
    normalized_type = _normalize_client_type(client_type)
    if normalized_type == "vertex_ai":
        raise LLMProviderUnavailableError("Vertex AI requires a separate authentication/route adapter")
    resolved_type = _CLIENT_TYPES.get(normalized_type)
    if resolved_type is None:
        raise LLMProviderUnavailableError("不支持的 client_type")
    return resolved_type


def is_gemini_client_type(client_type: object) -> bool:
    """判断原始类型是否为 Gemini Developer API，不验证具体签名来源。"""
    return _CLIENT_TYPES.get(_normalize_client_type(client_type)) == "gemini"


@dataclass(slots=True)
class ModelClientRegistry:
    """provider -> client 的注册表。

    默认装配 OpenAI、Anthropic、Responses 与 Gemini；Bedrock 仍需显式实现。
    """

    openai: ChatModelClient | None = None
    anthropic: ChatModelClient | None = None
    responses: ChatModelClient | None = None
    gemini: ChatModelClient | None = None
    bedrock: ChatModelClient | None = None

    def __post_init__(self) -> None:
        """保留显式注入的客户端，并补齐默认适配器；不在此处创建网络连接。"""
        if self.gemini is None:
            self.gemini = get_default_gemini_client()
        if self.openai is None:
            self.openai = OpenAIChatClient()
        if self.anthropic is None:
            self.anthropic = AnthropicChatClient()
        if self.responses is None:
            self.responses = OpenAIResponsesClient()

    def matches_replay_target(self, model: ModelEntry, target: tuple[str, str, str]) -> bool:
        """检查签名续轮来源，别名共用技术匹配器且不放宽协议隔离。

        Args:
            model: 本次候选模型技术配置，不修改调用方条目。
            target: 已锁定的 provider、端点版本和模型标识。

        Returns:
            只有受支持协议与完整来源完全一致时返回 True。
        """
        if not is_gemini_client_type(model.get("client_type")):
            return False
        from .gemini_codec import target_identity

        return target_identity(model) == target

    def get_client_for_model(self, model: ModelEntry) -> ChatModelClient:
        """根据单个模型配置返回已装配的聊天客户端，不发起网络请求。

        Args:
            model: 原始单模型配置；按 client_type 归一化大小写、首尾空白及
                连字符后分发，不修改调用方的配置。

        Returns:
            类型对应的聊天客户端，不回退到其他协议。

        Raises:
            LLMProviderUnavailableError: 类型无效、不受支持或对应客户端未注册。
            LLMConfigurationError: 显式选择的 OpenAI 客户端未配置。
        """
        client_type = resolve_client_type(model.get("client_type"))
        client: ChatModelClient | None = getattr(self, client_type)
        if client is not None:
            return client
        if client_type == "openai":
            raise LLMConfigurationError("OpenAI client 未配置")
        raise LLMProviderUnavailableError(f"{client_type.capitalize()} client 未注册")

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
