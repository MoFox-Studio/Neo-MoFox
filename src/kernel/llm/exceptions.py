from __future__ import annotations



class LLMError(RuntimeError):
    """LLM 操作基础异常。"""


class LLMContextError(LLMError):
    """上下文结构错误。

    用于在严格模式下，当上下文 messages 不满足协议约束时直接抛出，
    以避免“自动修复”掩盖上游链路问题。
    """


class LLMConfigurationError(LLMError):
    """配置错误。"""


class LLMResponseConsumedError(LLMError):
    """响应已被消费。"""


class LLMRateLimitError(LLMError):
    """速率限制错误。"""

    def __init__(self, message: str, retry_after: float | None = None, model: str | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.model = model


class LLMTimeoutError(LLMError):
    """超时错误。"""

    def __init__(self, message: str, timeout: float | None = None, model: str | None = None):
        super().__init__(message)
        self.timeout = timeout
        self.model = model


class LLMContentFilterError(LLMError):
    """内容过滤错误（内容违反安全策略）。"""

    def __init__(self, message: str, filter_type: str | None = None, model: str | None = None):
        super().__init__(message)
        self.filter_type = filter_type
        self.model = model


class LLMTokenLimitError(LLMError):
    """Token 超限错误。"""

    def __init__(self, message: str, max_tokens: int | None = None, requested_tokens: int | None = None, model: str | None = None):
        super().__init__(message)
        self.max_tokens = max_tokens
        self.requested_tokens = requested_tokens
        self.model = model


class LLMAuthenticationError(LLMError):
    """认证错误（API key 无效等）。"""

    def __init__(self, message: str, model: str | None = None):
        super().__init__(message)
        self.model = model


class LLMAPIError(LLMError):
    """API 调用错误（通用）。"""

    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None, model: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.model = model


def classify_exception(error: BaseException, model: str | None = None) -> BaseException:
    """将第三方 SDK 异常转换为标准化的 LLM 异常。

    这个函数尝试识别常见的 API 错误类型并转换为更具体的异常类型。
    如果无法识别，返回原始异常。
    """
    error_msg = str(error).lower()

    # OpenAI SDK 异常处理
    try:
        from openai import (
            APITimeoutError,
            RateLimitError,
            AuthenticationError,
            BadRequestError,
            APIError,
        )

        if isinstance(error, RateLimitError):
            # 尝试从错误中提取 retry_after
            retry_after = getattr(error, "retry_after", None)
            return LLMRateLimitError(str(error), retry_after=retry_after, model=model)

        if isinstance(error, APITimeoutError):
            return LLMTimeoutError(str(error), model=model)

        if isinstance(error, AuthenticationError):
            return LLMAuthenticationError(str(error), model=model)

        if isinstance(error, BadRequestError):
            # 检查是否是 token 限制
            if "maximum context length" in error_msg or "token" in error_msg and "limit" in error_msg:
                return LLMTokenLimitError(str(error), model=model)
            # 检查是否是内容过滤
            if "content_filter" in error_msg or "content policy" in error_msg:
                return LLMContentFilterError(str(error), model=model)

        if isinstance(error, APIError):
            status_code = getattr(error, "status_code", None)
            error_code = getattr(error, "code", None)
            return LLMAPIError(str(error), status_code=status_code, error_code=error_code, model=model)

    except ImportError:
        pass

    # 通用错误检查（基于错误消息）
    if "rate" in error_msg and "limit" in error_msg:
        return LLMRateLimitError(str(error), model=model)

    if "timeout" in error_msg or "timed out" in error_msg:
        return LLMTimeoutError(str(error), model=model)

    if "authentication" in error_msg or "unauthorized" in error_msg or "api key" in error_msg:
        return LLMAuthenticationError(str(error), model=model)

    if "token" in error_msg and "limit" in error_msg:
        return LLMTokenLimitError(str(error), model=model)

    if "content" in error_msg and ("filter" in error_msg or "policy" in error_msg):
        return LLMContentFilterError(str(error), model=model)

    return error
