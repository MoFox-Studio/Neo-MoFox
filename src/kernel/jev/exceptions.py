"""Jev System One HTTP API 的异常层次。

Jev 客户端刻意不复用 ``kernel.llm`` 的异常类型：它不属于传统 LLM 请求链路，
也不参与 LLM 的重试/failover 统计，混用会让监控与排障产生歧义。
"""

from __future__ import annotations


class JevError(RuntimeError):
    """Jev 请求基础异常。"""


class JevConfigurationError(JevError):
    """客户端配置或请求定义错误（请求尚未发出）。"""


class JevAuthenticationError(JevError):
    """API key 缺失、无效或被拒绝（HTTP 401/403）。"""


class JevValidationError(JevError):
    """TypeSafe 拒绝了请求体（HTTP 400/422）。"""


class JevPayloadTooLargeError(JevError):
    """请求体超出限制（HTTP 413）。"""


class JevUnsupportedMediaTypeError(JevError):
    """请求媒体类型不被接受（HTTP 415）。"""


class JevRateLimitError(JevError):
    """触发限流（HTTP 429，可重试）。"""


class JevOverloadedError(JevError):
    """服务端过载（HTTP 529，可重试）。"""


class JevResponseError(JevError):
    """成功状态码返回了非法 JSON 或不符合响应契约的数据。"""


class JevAPIError(JevError):
    """无法归入更具体类别的 HTTP API 错误。"""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class JevTimeoutError(JevError):
    """请求超时（可重试）。"""


class JevTransportError(JevError):
    """连接、DNS、TLS 等传输层错误（可重试）。"""
