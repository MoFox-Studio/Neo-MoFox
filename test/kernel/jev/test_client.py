"""Jev System One 客户端测试。

全部用例走 ``httpx.MockTransport``，不触碰真实网络与真实 API key。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.kernel.jev import (
    JevAPIError,
    JevAuthenticationError,
    JevClient,
    JevClientConfig,
    JevConfigurationError,
    JevOverloadedError,
    JevPayloadTooLargeError,
    JevRateLimitError,
    JevResponseError,
    JevTimeoutError,
    JevTransportError,
    JevUnsupportedMediaTypeError,
    JevValidationError,
    choice,
    noul,
    score,
)


def _client(handler: Any, *, max_retries: int = 0, **config: Any) -> JevClient:
    """用内存传输层构造客户端，默认不做退避等待。"""
    settings: dict[str, Any] = {
        "api_key": "test-key",
        "max_retries": max_retries,
        "retry_backoff_seconds": 0,
        "max_retry_backoff_seconds": 0,
    }
    settings.update(config)
    return JevClient(JevClientConfig(**settings), transport=httpx.MockTransport(handler))


async def test_evaluate_sends_official_body_and_parses_all_answer_types():
    """官方请求体形状与三种 typed answer 都能完整往返。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["Authorization"]
        seen["content_type"] = request.headers["Content-Type"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "urgent": {"type": "noul", "noul": 0.95},
                    "team": {
                        "type": "choice",
                        "choice": "technical",
                        "probabilities": {"technical": 0.9, "billing": 0.1},
                        "confidence": 0.8,
                    },
                    "severity": {
                        "type": "score",
                        "score": 1.2,
                        "legend": {"0": "low", "1": "medium", "2": "high"},
                        "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3},
                        "confidence": 0.7,
                    },
                },
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        )

    response = await _client(handler).evaluate(
        state={"ticket": "service is down"},
        questions={
            "urgent": noul("Is this urgent?"),
            "team": choice("Which team?", {"technical": None, "billing": None}),
            "severity": score("How severe?", ["low", "medium", "high"]),
        },
    )

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["content_type"] == "application/json"
    assert seen["body"]["model"] == "jev-latest"
    assert seen["body"]["state"] == {"ticket": "service is down"}
    assert set(seen["body"]["questions"]) == {"urgent", "team", "severity"}
    assert response.model == "jev-1.13.0"
    assert response.answer("urgent").noul == 0.95
    assert response.answer("team").choice == "technical"
    assert response.answer("team").probabilities == {"technical": 0.9, "billing": 0.1}
    assert response.answer("severity").score == 1.2
    assert response.answer("severity").legend == {"0": "low", "1": "medium", "2": "high"}
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 8


async def test_evaluate_accepts_raw_question_dicts_and_model_override():
    """questions 也接受原始 dict，并可按调用覆盖 model。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.1}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    await _client(handler).evaluate(
        state="raw state",
        questions={"q": {"type": "noul", "instructions": "true?"}},
        model="jev-1.13.0",
    )

    assert seen["body"]["model"] == "jev-1.13.0"
    assert seen["body"]["questions"] == {"q": {"type": "noul", "instructions": "true?"}}


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, JevAuthenticationError),
        (403, JevAuthenticationError),
        (400, JevValidationError),
        (422, JevValidationError),
        (413, JevPayloadTooLargeError),
        (415, JevUnsupportedMediaTypeError),
        (500, JevAPIError),
        (302, JevAPIError),
    ],
)
async def test_evaluate_maps_http_errors(status_code, error_type):
    """provider 错误映射到稳定、可区分的异常类型。"""
    client = _client(lambda request: httpx.Response(status_code, json={"error": "failure"}))

    with pytest.raises(error_type):
        await client.evaluate(state="test", questions={"q": noul("true?")})


async def test_evaluate_retries_rate_limit_then_succeeds():
    """429 在重试预算内被重试。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.4}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    response = await _client(handler, max_retries=1).evaluate(
        state="test", questions={"q": noul("true?")}
    )

    assert attempts == 2
    assert response.answer("q").noul == 0.4


async def test_evaluate_raises_rate_limit_error_after_budget():
    """重试预算耗尽后抛出最后一次的可重试错误。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(529, json={"error": "overloaded"})

    with pytest.raises(JevOverloadedError):
        await _client(handler, max_retries=2).evaluate(state="test", questions={"q": noul("true?")})

    assert attempts == 3


async def test_evaluate_does_not_retry_client_errors():
    """401 属于请求本身有问题，重试没有意义。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(JevAuthenticationError):
        await _client(handler, max_retries=2).evaluate(state="test", questions={"q": noul("true?")})

    assert attempts == 1


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("read timeout"), httpx.ConnectError("connection refused")],
)
async def test_evaluate_retries_transport_failures(failure):
    """超时与传输错误可重试，耗尽预算后暴露为对应异常。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise failure
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.2}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    response = await _client(handler, max_retries=2).evaluate(
        state="test", questions={"q": noul("true?")}
    )

    assert attempts == 2
    assert response.answer("q").noul == 0.2


async def test_evaluate_raises_timeout_error_after_budget():
    """持续超时时抛出 JevTimeoutError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    with pytest.raises(JevTimeoutError):
        await _client(handler, max_retries=1).evaluate(state="test", questions={"q": noul("true?")})


async def test_evaluate_raises_transport_error_after_budget():
    """持续连接失败时抛出 JevTransportError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(JevTransportError):
        await _client(handler, max_retries=1).evaluate(state="test", questions={"q": noul("true?")})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not-json", "合法 JSON"),
        (["list"], "JSON object"),
        ({"answers": {}, "usage": {}}, "model"),
        ({"model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}}, "answers"),
        (
            {"model": "m", "answers": {"other": {"type": "noul", "noul": 1}}, "usage": {}},
            "question ID",
        ),
        ({"model": "m", "answers": {"q": {"type": "noul", "noul": 1}}}, "usage"),
        (
            {
                "model": "m",
                "answers": {"q": {"type": "noul", "noul": 1}},
                "usage": {"input_tokens": "1", "output_tokens": 1},
            },
            "整数",
        ),
        (
            {
                "model": "m",
                "answers": {"q": {"type": "score", "score": 1}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            "answer 结构非法",
        ),
    ],
)
async def test_evaluate_rejects_malformed_responses(body, message):
    """畸形响应一律失败关闭，不返回部分答案。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    with pytest.raises(JevResponseError, match=message):
        await _client(handler).evaluate(state="test", questions={"q": noul("true?")})


async def test_evaluate_rejects_unserializable_state_before_transport():
    """无法序列化的 state 在发请求前就报配置错误。"""
    with pytest.raises(JevConfigurationError, match="可序列化"):
        await _client(lambda request: httpx.Response(200)).evaluate(
            state={"value": object()}, questions={"q": noul("true?")}
        )


@pytest.mark.parametrize(
    ("state", "questions", "message"),
    [
        (123, {"q": noul("true?")}, "state"),
        ("test", {}, "questions"),
        ("test", {"q": "invalid"}, "格式无效"),
    ],
)
async def test_evaluate_validates_request_shape(state, questions, message):
    """非法 state/questions 在本地被拦截。"""
    with pytest.raises(JevConfigurationError, match=message):
        await _client(lambda request: httpx.Response(200)).evaluate(
            state=state, questions=questions
        )


def test_question_helpers_build_typed_questions():
    """三个 helper 产出符合契约的类型与字段。"""
    assert noul("true?").to_payload() == {"type": "noul", "instructions": "true?"}
    assert choice("pick", {"a": "A"}).to_payload() == {
        "type": "choice",
        "instructions": "pick",
        "criteria": {"a": "A"},
    }
    assert score("rate", ["low", "high"]).to_payload() == {
        "type": "score",
        "instructions": "rate",
        "criteria": ["low", "high"],
    }


def test_invalid_question_object_is_rejected_as_configuration_error():
    """直接构造的非法 JevQuestion 也要报配置错误，而不是 ValueError。"""
    from src.kernel.jev import JevQuestion

    client = _client(lambda request: httpx.Response(200))

    with pytest.raises(JevConfigurationError, match="定义无效"):
        client._build_request_body(
            state="x", questions={"q": JevQuestion("nope", "instructions")}, model=None
        )


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (choice("pick", {}), "choice criteria"),
        (choice("pick", ["a"]), "choice criteria"),
        (score("rate", ["only-one"]), "2 到 10"),
        (score("rate", ["a"] * 11), "2 到 10"),
        (noul("true?", criteria=["bad"]), "noul criteria"),
        ({"type": "nope", "instructions": "x"}, "type 无效"),
        ({"type": "choice", "instructions": "x"}, "choice criteria"),
        ({"type": "noul", "instructions": 5}, "instructions"),
    ],
)
def test_question_validation_uses_official_primitives(question, message):
    """Choice/Score 的 criteria 形状与数量按官方约定校验。"""
    client = _client(lambda request: httpx.Response(200))

    with pytest.raises(JevConfigurationError, match=message):
        client._build_request_body(state="x", questions={"q": question}, model=None)


def test_config_rejects_missing_api_key():
    """API key 必须显式提供或来自环境变量。"""
    for invalid in ("", "   ", None, 123):
        with pytest.raises(JevConfigurationError, match="api_key"):
            JevClientConfig(api_key=invalid)  # type: ignore[arg-type]


def test_config_hides_api_key_from_repr():
    """密钥及其字段名都不出现在 repr 中，避免泄漏到日志。"""
    config = JevClientConfig(api_key="super-secret-key")

    assert "super-secret-key" not in repr(config)
    assert "api_key" not in repr(config)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.typesafe.ai",
        "https://user:pass@api.typesafe.ai",
        "https://",
        "",
    ],
)
def test_config_requires_https_endpoint_without_userinfo(base_url):
    """密钥只允许发往无 userinfo 的 HTTPS 地址。"""
    with pytest.raises(JevConfigurationError):
        JevClientConfig(api_key="key", base_url=base_url)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": "fast"}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
        ({"max_retries": 1.5}, "max_retries"),
        ({"retry_backoff_seconds": -0.1}, "retry_backoff_seconds"),
        ({"max_retry_backoff_seconds": -1}, "max_retry_backoff_seconds"),
        ({"model": " "}, "model"),
    ],
)
def test_config_rejects_invalid_numbers(kwargs, field):
    """边界值错误在建立连接前失败。"""
    with pytest.raises(JevConfigurationError, match=field):
        JevClientConfig(api_key="key", **kwargs)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.typesafe.ai", "https://api.typesafe.ai/v1/systemone"),
        ("https://api.typesafe.ai/", "https://api.typesafe.ai/v1/systemone"),
        ("https://gateway.test/prefix", "https://gateway.test/prefix/v1/systemone"),
        ("https://api.typesafe.ai/v1/systemone", "https://api.typesafe.ai/v1/systemone"),
        (
            "https://api.typesafe.ai/v1/systemone?region=eu",
            "https://api.typesafe.ai/v1/systemone?region=eu",
        ),
    ],
)
def test_endpoint_derives_system_one_route(base_url, expected):
    """base_url 可传 host 或完整路由，query 必须保留。"""
    config = JevClientConfig(api_key="key", base_url=base_url)

    assert config.endpoint == expected


def test_from_env_reads_api_key_and_allows_override(monkeypatch):
    """API key 走环境变量，显式覆盖优先。"""
    from src.kernel.jev import API_KEY_ENV

    monkeypatch.setenv(API_KEY_ENV, "env-key")

    assert JevClient.from_env().config.api_key == "env-key"
    assert JevClient.from_env(api_key="explicit").config.api_key == "explicit"
    assert JevClient.from_env(model="jev-1.13.0").config.model == "jev-1.13.0"


def test_retry_delay_honors_valid_retry_after_header():
    """合法的 Retry-After 优先于本地退避上限。"""
    client = _client(lambda request: httpx.Response(200))
    response = httpx.Response(429, headers={"Retry-After": "60"})

    assert client._retry_delay(0, response) == 60


def test_retry_delay_falls_back_to_jitter_for_invalid_retry_after():
    """无法解析的 Retry-After 回落到有上限的抖动退避。"""
    client = _client(lambda request: httpx.Response(200), retry_backoff_seconds=2)
    response = httpx.Response(429, headers={"Retry-After": "not-a-date"})

    delay = client._retry_delay(1, response)

    assert 0 <= delay <= min(2 * 2, 8)
