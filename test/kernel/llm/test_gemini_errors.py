"""SDK status classification and safe error logging at the provider boundary."""
from unittest.mock import patch

import httpx
import pytest
from google.genai import errors

from src.kernel.llm.exceptions import (
    LLMAPIError, LLMAuthenticationError, LLMProviderUnavailableError,
    LLMRateLimitError, LLMTimeoutError, classify_exception,
)
from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model


@pytest.mark.parametrize("status,kind", [(400, LLMAPIError),(401,LLMAuthenticationError),(403,LLMAuthenticationError),(404,LLMProviderUnavailableError),(429,LLMRateLimitError),(500,LLMAPIError),(503,LLMAPIError)])
def test_google_sdk_status_is_typed_without_upstream_details(status: int, kind: type[Exception]) -> None:
    """A provider error body must not become a diagnostic text classifier input."""
    response=httpx.Response(status,headers={"retry-after":"2"},request=httpx.Request("POST","https://offline.invalid"))
    error=errors.APIError(status,{"error":{"code":status,"message":"SECRET_UPSTREAM","status":"ERROR"}},response)
    classified=classify_exception(error,model="authentication-model")
    assert isinstance(classified,kind)
    assert "SECRET_UPSTREAM" not in str(classified)
    if status==429:assert classified.retry_after==2.0


def test_httpx_errors_are_normalized() -> None:
    """Direct transport failures have consistent provider-independent semantics."""
    assert isinstance(classify_exception(httpx.ReadTimeout("secret")),LLMTimeoutError)
    assert isinstance(classify_exception(httpx.ConnectError("secret")),ConnectionError)


async def test_failed_sdk_call_does_not_log_raw_response_trace() -> None:
    """The active SDK exception traceback contains details excluded from logging."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401,json={"error":{"code":401,"message":"SECRET_UPSTREAM"}})
    client=GeminiChatClient(transport=httpx.MockTransport(handler))
    req=LLMRequest(model_set=[model()],clients=ModelClientRegistry(gemini=client),policy=RoundRobinPolicy(),enable_metrics=False)
    req.context_manager=None;req.payloads=[LLMPayload(ROLE.USER,Text("hi"))]
    try:
        with patch("src.kernel.llm.request_execution.logger") as logger:
            with pytest.raises(LLMAuthenticationError):await req.send(stream=False)
            assert logger.error.call_args.kwargs["exc_info"] is False
            assert "SECRET_UPSTREAM" not in str(logger.error.call_args)
    finally:await client.aclose()
