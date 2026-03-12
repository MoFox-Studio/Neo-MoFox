"""
模型客户端注册表，负责根据模型配置返回对应的 client 实例。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..exceptions import LLMConfigurationError
from .base import ChatModelClient, EmbeddingModelClient, RerankModelClient
from .openai_client import OpenAIChatClient
from ..types import ModelEntry

@dataclass(slots=True)
class ModelClientRegistry:
    """provider -> client 的注册表。

    当前默认提供 openai client；gemini/bedrock 后续可注册。
    """

    openai: ChatModelClient | None = None
    gemini: ChatModelClient | None = None
    bedrock: ChatModelClient | None = None

    def __post_init__(self) -> None:
        if self.openai is None:
            self.openai = OpenAIChatClient()

    def get_client_for_model(self, model: ModelEntry) -> ChatModelClient:
        """根据单个模型配置决定使用哪个 provider。

        当前阶段以 `client_type` 为准：openai/gemini/bedrock。
        """

        client_type = model.get("client_type")
        if isinstance(client_type, str):
            if client_type == "openai" and self.openai is not None:
                return self.openai
            if client_type in {"gemini", "aiohttp_gemini"} and self.gemini is not None:
                return self.gemini
            if client_type == "bedrock" and self.bedrock is not None:
                return self.bedrock

        if self.openai is None:
            raise LLMConfigurationError("OpenAI client 未配置")
        return self.openai

    def get_embedding_client_for_model(self, model: ModelEntry) -> EmbeddingModelClient:
        """根据单个模型配置获取 embedding client。"""

        client = self.get_client_for_model(model)
        if not hasattr(client, "create_embedding"):
            raise LLMConfigurationError("当前 client 不支持 embeddings 请求")
        return client  # type: ignore[return-value]

    def get_rerank_client_for_model(self, model: ModelEntry) -> RerankModelClient:
        """根据单个模型配置获取 rerank client。"""

        client = self.get_client_for_model(model)
        if not hasattr(client, "create_rerank"):
            raise LLMConfigurationError("当前 client 不支持 rerank 请求")
        return client  # type: ignore[return-value]
