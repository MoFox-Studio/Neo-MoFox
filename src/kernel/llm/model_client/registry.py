from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..exceptions import LLMConfigurationError
from .base import ChatModelClient
from .openai_client import OpenAIChatClient


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

    def get_client_for_model(self, model: dict[str, Any]) -> ChatModelClient:
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
