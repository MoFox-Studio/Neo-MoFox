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
from .gemini_client import get_default_gemini_client
from ..types import ModelEntry

# Developer API 与尚未实现的 Vertex/Bedrock 分别显式分发，禁止协议误回退。
_GEMINI_CLIENT_TYPES = frozenset(
    {"gemini", "aiohttp_gemini", "gemini_v2", "google"}
)
_BEDROCK_CLIENT_TYPES = frozenset(
    {"bedrock", "amazon_bedrock", "bedrock_converse"}
)
# 已移除类型只保留拒绝名单，防止旧配置落入其他协议的兼容回退。
_REMOVED_CLIENT_TYPES = frozenset(
    {
        "jev",
        "typesafe",
        "type_safe",
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
    return client_type.strip().casefold().replace("-", "_")


def reject_removed_client_type(client_type: object) -> None:
    """拒绝已移除的客户端类型，防止生成与预检误用其他协议。

    Args:
        client_type: 未经配置层处理的客户端类型，沿用注册表归一规则。

    Raises:
        LLMProviderUnavailableError: 旧类型已移除，不能回退到其他客户端。
    """
    if _normalize_client_type(client_type) in _REMOVED_CLIENT_TYPES:
        raise LLMProviderUnavailableError(
            "该客户端类型已从核心移除，不能回退到其他客户端"
        )


def is_gemini_client_type(client_type: Any) -> bool:
    """判断原始 client_type 是否为 Gemini Developer API 别名。

    参数接受未经配置层处理的值；复用注册表的小写、空白及连字符归一化规则，
    非字符串与其他协议均返回 False。注册分发和签名来源锁共用此入口，
    调用方无需维护另一套别名集合；此判断不验证模型的具体签名来源。
    """
    return _normalize_client_type(client_type) in _GEMINI_CLIENT_TYPES


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
            对应的聊天客户端。默认已装配 Gemini Developer API；真正未知的
            client_type 仍兼容回退到 OpenAI。

        Raises:
            LLMProviderUnavailableError: Vertex 需要独立认证/路由适配器，Bedrock
                尚未装配，或客户端类型已从核心移除；这些类型不会回退到
                OpenAI 或 Gemini。
            LLMConfigurationError: 需要使用的默认 OpenAI 客户端未配置。
        """

        reject_removed_client_type(model.get("client_type"))
        normalized_type = _normalize_client_type(model.get("client_type"))
        if normalized_type == "vertex_ai":
            raise LLMProviderUnavailableError("Vertex AI requires a separate authentication/route adapter")
        if normalized_type:
            if normalized_type == "openai" and self.openai is not None:
                return self.openai
            if normalized_type == "anthropic" and self.anthropic is not None:
                return self.anthropic
            if normalized_type in {"openai_response", "responses", "openai.responses"} and self.responses is not None:
                return self.responses
            if is_gemini_client_type(model.get("client_type")):
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
