"""Jev System One 独立客户端。

本包提供对 TypeSafe Jev HTTP API 的直接封装，不参与 ``kernel.llm`` 的
ModelClientRegistry：Jev 返回 typed 判断（Noul/Choice/Score），不生成聊天文本。
"""

from .client import (
    API_KEY_ENV,
    DEFAULT_BASE_URL,
    ENDPOINT_PATH,
    JevClient,
    JevClientConfig,
    choice,
    noul,
    score,
)
from .exceptions import (
    JevAPIError,
    JevAuthenticationError,
    JevConfigurationError,
    JevError,
    JevOverloadedError,
    JevPayloadTooLargeError,
    JevRateLimitError,
    JevResponseError,
    JevTimeoutError,
    JevTransportError,
    JevUnsupportedMediaTypeError,
    JevValidationError,
)
from .types import (
    JevAnswer,
    JevInstruction,
    JevQuestion,
    JevResponse,
    JevState,
    JevUsage,
)

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "ENDPOINT_PATH",
    "JevAPIError",
    "JevAnswer",
    "JevAuthenticationError",
    "JevClient",
    "JevClientConfig",
    "JevConfigurationError",
    "JevError",
    "JevOverloadedError",
    "JevPayloadTooLargeError",
    "JevInstruction",
    "JevQuestion",
    "JevRateLimitError",
    "JevResponse",
    "JevResponseError",
    "JevTimeoutError",
    "JevState",
    "JevTransportError",
    "JevUnsupportedMediaTypeError",
    "JevUsage",
    "JevValidationError",
    "choice",
    "noul",
    "score",
]
