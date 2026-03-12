"""LLM 异常系统测试。

测试覆盖：
1. 各种异常类型的初始化
2. 异常属性访问
3. 异常分类功能（classify_exception）
"""


from src.kernel.llm import (
    LLMError,
    LLMConfigurationError,
    LLMResponseConsumedError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMContentFilterError,
    LLMTokenLimitError,
    LLMAuthenticationError,
    LLMAPIError,
    classify_exception,
)


def test_llm_error_base():
    """测试基础LLM异常。"""
    error = LLMError("Base error")
    assert str(error) == "Base error"
    assert isinstance(error, RuntimeError)


def test_llm_configuration_error():
    """测试配置错误异常。"""
    error = LLMConfigurationError("Invalid config")
    assert str(error) == "Invalid config"
    assert isinstance(error, LLMError)


def test_llm_response_consumed_error():
    """测试响应已消费异常。"""
    error = LLMResponseConsumedError("Response already consumed")
    assert str(error) == "Response already consumed"
    assert isinstance(error, LLMError)


def test_llm_rate_limit_error_with_attributes():
    """测试速率限制异常及其属性。"""
    error = LLMRateLimitError(
        "Rate limit exceeded",
        retry_after=60.0,
        model="gpt-4"
    )

    assert "Rate limit exceeded" in str(error)
    assert error.retry_after == 60.0
    assert error.model == "gpt-4"


def test_llm_rate_limit_error_without_attributes():
    """测试速率限制异常（无属性）。"""
    error = LLMRateLimitError("Rate limit exceeded")

    assert error.retry_after is None
    assert error.model is None


def test_llm_timeout_error_with_attributes():
    """测试超时异常及其属性。"""
    error = LLMTimeoutError(
        "Request timeout",
        timeout=30.0,
        model="gpt-4"
    )

    assert "Request timeout" in str(error)
    assert error.timeout == 30.0
    assert error.model == "gpt-4"


def test_llm_timeout_error_without_attributes():
    """测试超时异常（无属性）。"""
    error = LLMTimeoutError("Request timeout")

    assert error.timeout is None
    assert error.model is None


def test_llm_content_filter_error_with_attributes():
    """测试内容过滤异常及其属性。"""
    error = LLMContentFilterError(
        "Content filtered",
        filter_type="violence",
        model="gpt-4"
    )

    assert "Content filtered" in str(error)
    assert error.filter_type == "violence"
    assert error.model == "gpt-4"


def test_llm_content_filter_error_without_attributes():
    """测试内容过滤异常（无属性）。"""
    error = LLMContentFilterError("Content filtered")

    assert error.filter_type is None
    assert error.model is None


def test_llm_token_limit_error_with_attributes():
    """测试Token超限异常及其属性。"""
    error = LLMTokenLimitError(
        "Token limit exceeded",
        max_tokens=4096,
        requested_tokens=5000,
        model="gpt-4"
    )

    assert "Token limit exceeded" in str(error)
    assert error.max_tokens == 4096
    assert error.requested_tokens == 5000
    assert error.model == "gpt-4"


def test_llm_token_limit_error_without_attributes():
    """测试Token超限异常（无属性）。"""
    error = LLMTokenLimitError("Token limit exceeded")

    assert error.max_tokens is None
    assert error.requested_tokens is None
    assert error.model is None


def test_llm_authentication_error_with_model():
    """测试认证异常及模型属性。"""
    error = LLMAuthenticationError(
        "Invalid API key",
        model="gpt-4"
    )

    assert "Invalid API key" in str(error)
    assert error.model == "gpt-4"


def test_llm_authentication_error_without_model():
    """测试认证异常（无模型属性）。"""
    error = LLMAuthenticationError("Invalid API key")

    assert error.model is None


def test_llm_api_error_with_all_attributes():
    """测试API异常及所有属性。"""
    error = LLMAPIError(
        "API error occurred",
        status_code=500,
        error_code="server_error",
        model="gpt-4"
    )

    assert "API error occurred" in str(error)
    assert error.status_code == 500
    assert error.error_code == "server_error"
    assert error.model == "gpt-4"


def test_llm_api_error_without_attributes():
    """测试API异常（无属性）。"""
    error = LLMAPIError("API error occurred")

    assert error.status_code is None
    assert error.error_code is None
    assert error.model is None


def test_classify_exception_generic_rate_limit():
    """测试分类通用速率限制异常。"""
    error = Exception("rate limit exceeded")
    classified = classify_exception(error, model="test-model")

    assert isinstance(classified, LLMRateLimitError)
    assert classified.model == "test-model"


def test_classify_exception_generic_timeout():
    """测试分类通用超时异常。"""
    error = Exception("request timed out")
    classified = classify_exception(error)

    assert isinstance(classified, LLMTimeoutError)


def test_classify_exception_generic_auth_error():
    """测试分类通用认证异常。"""
    error = Exception("authentication failed")
    classified = classify_exception(error, model="gpt-4")

    assert isinstance(classified, LLMAuthenticationError)
    assert classified.model == "gpt-4"


def test_classify_exception_generic_unauthorized():
    """测试分类通用未授权异常。"""
    error = Exception("unauthorized access")
    classified = classify_exception(error)

    assert isinstance(classified, LLMAuthenticationError)


def test_classify_exception_generic_api_key_error():
    """测试分类通用API key异常。"""
    error = Exception("invalid api key")
    classified = classify_exception(error)

    assert isinstance(classified, LLMAuthenticationError)


def test_classify_exception_generic_token_limit():
    """测试分类通用Token超限异常。"""
    error = Exception("token limit exceeded")
    classified = classify_exception(error)

    assert isinstance(classified, LLMTokenLimitError)


def test_classify_exception_generic_content_filter():
    """测试分类通用内容过滤异常。"""
    error = Exception("content policy violation")
    classified = classify_exception(error)

    assert isinstance(classified, LLMContentFilterError)


def test_classify_exception_unknown_error():
    """测试分类未知异常。"""
    error = ValueError("some unknown error")
    classified = classify_exception(error)

    # 应该返回原始异常
    assert classified is error


def test_classify_exception_multiple_keywords_in_message():
    """测试包含多个关键词的错误消息。"""
    # 测试既有"rate"又有"limit"的情况
    error = Exception("rate limit reached for this api")
    classified = classify_exception(error, model="test-model")

    assert isinstance(classified, LLMRateLimitError)
    assert classified.model == "test-model"


def test_classify_exception_case_insensitive():
    """测试错误消息大小写不敏感。"""
    # 测试不同大小写的错误消息
    error1 = Exception("RATE LIMIT Exceeded")
    classified1 = classify_exception(error1)
    assert isinstance(classified1, LLMRateLimitError)

    error2 = Exception("Authentication FAILED")
    classified2 = classify_exception(error2)
    assert isinstance(classified2, LLMAuthenticationError)

    error3 = Exception("Request TIMED OUT")
    classified3 = classify_exception(error3)
    assert isinstance(classified3, LLMTimeoutError)


def test_classify_exception_token_limit_variations():
    """测试token限制的不同表述。"""
    error = Exception("token limit exceeded")
    classified = classify_exception(error)

    assert isinstance(classified, LLMTokenLimitError)


def test_classify_exception_content_filter_variations():
    """测试内容过滤的不同表述。"""
    error = Exception("content policy violation detected")
    classified = classify_exception(error)

    assert isinstance(classified, LLMContentFilterError)
