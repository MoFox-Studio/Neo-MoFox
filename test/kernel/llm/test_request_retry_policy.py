import asyncio

import pytest

from src.kernel.llm import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMContentFilterError,
    LLMTimeoutError,
    LLMTokenLimitError,
)
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.policy import RoundRobinPolicy
from src.kernel.llm.request import LLMRequest


class DummyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._fail_once_for: set[str] = {"a"}

    async def create(
        self,
        *,
        model_name: str,
        payloads,
        tools,
        request_name: str,
        model_set,
        stream: bool,
    ):
        self.calls.append(model_name)
        if model_name in self._fail_once_for:
            self._fail_once_for.remove(model_name)
            raise LLMTimeoutError("boom")
        return "ok", [], None


class FailingClient:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls: list[str] = []

    async def create(
        self,
        *,
        model_name: str,
        payloads,
        tools,
        request_name: str,
        model_set,
        stream: bool,
    ):
        self.calls.append(model_name)
        raise self.error


class RecoveringClient:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls: list[str] = []

    async def create(
        self,
        *,
        model_name: str,
        payloads,
        tools,
        request_name: str,
        model_set,
        stream: bool,
    ):
        self.calls.append(model_name)
        if len(self.calls) == 1:
            raise self.error
        return "ok", [], None


class CancelClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create(
        self,
        *,
        model_name: str,
        payloads,
        tools,
        request_name: str,
        model_set,
        stream: bool,
    ):
        self.calls += 1
        raise asyncio.CancelledError


def _model(identifier: str, *, max_retry: int):
    return {
        "api_provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": identifier,
        "api_key": "dummy-key",
        "client_type": "openai",
        "max_retry": max_retry,
        "timeout": 1,
        "retry_interval": 0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.1,
        "max_tokens": 10,
        "extra_params": {},
    }


@pytest.mark.asyncio
async def test_retry_is_driven_by_policy_switch_or_retry():
    # a 会失败一次；max_retry=0 => policy 应立刻切换到 b
    model_set = [_model("a", max_retry=0), _model("b", max_retry=0)]

    dummy = DummyClient()
    req = LLMRequest(
        model_set,
        request_name="req",
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=dummy),
    )

    resp = await req.send(stream=False)
    assert resp.message == "ok"
    assert dummy.calls == ["a", "b"]


@pytest.mark.parametrize(
    "error",
    [
        LLMAuthenticationError("unauthorized"),
        LLMContentFilterError("filtered"),
        LLMTokenLimitError("too many tokens"),
        LLMConfigurationError("invalid config"),
        LLMAPIError("invalid request", status_code=400),
        ValueError("serialization failed"),
    ],
)
@pytest.mark.asyncio
async def test_non_retryable_failure_does_not_enter_policy(error):
    model_set = [_model("a", max_retry=1), _model("b", max_retry=1)]
    dummy = FailingClient(error)
    req = LLMRequest(
        model_set,
        request_name="req",
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=dummy),
    )

    with pytest.raises(type(error)):
        await req.send(stream=False)

    assert dummy.calls == ["a"]


@pytest.mark.parametrize(
    "error",
    [
        LLMTimeoutError("timeout"),
        ConnectionResetError("reset"),
        LLMAPIError("server error", status_code=503),
    ],
)
@pytest.mark.asyncio
async def test_retryable_failure_uses_existing_policy(error):
    model_set = [_model("a", max_retry=0), _model("b", max_retry=0)]
    dummy = RecoveringClient(error)
    req = LLMRequest(
        model_set,
        request_name="req",
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=dummy),
    )

    resp = await req.send(stream=False)

    assert resp.message == "ok"
    assert dummy.calls == ["a", "b"]


@pytest.mark.asyncio
async def test_cancelled_error_propagates_without_retry():
    model_set = [_model("a", max_retry=1), _model("b", max_retry=0)]

    dummy = CancelClient()
    req = LLMRequest(model_set, request_name="req", clients=ModelClientRegistry(openai=dummy))

    with pytest.raises(asyncio.CancelledError):
        await req.send(stream=False)

    assert dummy.calls == 1
