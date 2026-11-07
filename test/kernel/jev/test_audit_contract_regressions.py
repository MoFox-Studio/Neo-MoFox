"""Jev 审计 F3—F6 的正确性回归：出站 query、题型、失败事件与退避。

全部请求使用假密钥、不可路由域名和内存传输；重试等待只记录，不真实休眠。
断言锁定应有契约，不把当前缺陷存在当成测试成功。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone, tzinfo
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any, TypeAlias

import httpx
import pytest

import src.kernel.jev.client as jev_client_module
from src.kernel.event import EventDecision, get_event_bus
from src.kernel.jev import (
    JevClient,
    JevClientConfig,
    JevConfigurationError,
    JevQuestion,
    JevRateLimitError,
    JevResponseError,
    JevState,
    choice,
    noul,
    score,
)
from src.kernel.jev.events import (
    AFTER_JEV_REQUEST,
    BEFORE_JEV_REQUEST,
    ON_JEV_REQUEST_FAILED,
)

EventLog: TypeAlias = list[tuple[str, dict[str, Any]]]

_QUESTIONS: dict[str, JevQuestion] = {
    "noul": noul("是否成立？"),
    "choice": choice("选择一项", {"a": "甲", "b": "乙"}),
    "score": score("程度如何？", ["低", "高"]),
}
_ANSWERS: dict[str, dict[str, Any]] = {
    "noul": {"type": "noul", "noul": 0.5},
    "choice": {"type": "choice", "choice": "a", "confidence": 0.8},
    "score": {"type": "score", "score": 1.0, "confidence": 0.7},
}
_BASE_QUERY: dict[str, list[str]] = {
    "region": ["eu"],
    "tag": ["one", "two"],
    "empty": [""],
}


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **overrides: Any,
) -> JevClient:
    """使用内存处理器构造客户端，可显式覆盖本用例所需配置。"""
    settings: dict[str, Any] = {
        "api_key": "audit-dummy-key",
        "base_url": "https://offline.invalid/v1/systemone",
        "max_retries": 0,
        "retry_backoff_seconds": 0,
        "trust_env": False,
    }
    settings.update(overrides)
    return JevClient(JevClientConfig(**settings), transport=httpx.MockTransport(handler))


def _response_body(
    answers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """为指定答案生成完整响应体，默认返回单个合法 Noul 答案。"""
    return {
        "model": "audit-model",
        "answers": {"q": _ANSWERS["noul"]} if answers is None else answers,
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }


def _ok_handler(request: httpx.Request) -> httpx.Response:
    """为不关注响应内容的请求返回合法答案，不接触网络。"""
    return httpx.Response(200, json=_response_body())


@pytest.fixture
def terminal_events() -> Iterator[EventLog]:
    """订阅真实成功与失败事件，记录参数并在用例结束时解除订阅。"""
    seen: EventLog = []

    async def record(
        event_name: str, params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """保留异常对象身份与事件参数，不改写业务结果。"""
        seen.append((event_name, dict(params)))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    names = (AFTER_JEV_REQUEST, ON_JEV_REQUEST_FAILED)
    for name in names:
        bus.subscribe(name, record)
    try:
        yield seen
    finally:
        for name in names:
            bus.unsubscribe(name, record)


@pytest.fixture
def retry_waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """只替换 Jev 模块使用的休眠入口，记录等待而不影响全局事件循环。"""
    waits: list[float] = []

    async def record_wait(delay: float) -> None:
        """记录本次退避时长，不执行真实休眠。"""
        waits.append(delay)

    monkeypatch.setattr(jev_client_module, "asyncio", SimpleNamespace(sleep=record_wait))
    return waits


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """固定 Jev 的 UTC 时钟，使日期格式等待秒数可精确比较。"""
    now = datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc)

    def frozen_now(tz: tzinfo | None = None) -> datetime:
        """按调用方时区返回固定时间，不修改系统时钟。"""
        return now.replace(tzinfo=None) if tz is None else now.astimezone(tz)

    monkeypatch.setattr(jev_client_module, "datetime", SimpleNamespace(now=frozen_now))
    return now


@pytest.mark.parametrize(
    ("extra_query", "expected_query"),
    [
        pytest.param(None, _BASE_QUERY, id="default-query"),
        pytest.param({}, _BASE_QUERY, id="empty-query"),
        pytest.param(
            {"region": "us", "added": "yes"},
            {**_BASE_QUERY, "region": ["us"], "added": ["yes"]},
            id="override-and-add",
        ),
        pytest.param(
            {"tag": "override"},
            {**_BASE_QUERY, "tag": ["override"]},
            id="override-repeated-key",
        ),
    ],
)
async def test_outbound_url_preserves_endpoint_query_and_explicit_overrides(
    extra_query: dict[str, str] | None,
    expected_query: dict[str, list[str]],
) -> None:
    """实际出站 URL 保留原 query 和重复值，仅显式同名键被覆盖。"""
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """捕获实际 HTTPX URL，而非只检查配置的 endpoint 属性。"""
        seen.append(request.url)
        return _ok_handler(request)

    overrides: dict[str, Any] = {
        "base_url": (
            "https://offline.invalid/gateway/v1/systemone"
            "?region=eu&tag=one&tag=two&empty="
        ),
    }
    if extra_query is not None:
        overrides["extra_query"] = extra_query
    await _client(handler, **overrides).evaluate(
        state="audit", questions={"q": _QUESTIONS["noul"]},
    )

    assert len(seen) == 1
    assert seen[0].path == "/gateway/v1/systemone"
    assert set(seen[0].params) == set(expected_query)
    for key, values in expected_query.items():
        assert seen[0].params.get_list(key) == values


@pytest.mark.parametrize(
    ("requested_type", "returned_type"),
    [
        (requested, returned)
        for requested in _QUESTIONS
        for returned in _ANSWERS
        if requested != returned
    ],
)
async def test_evaluate_rejects_wrong_answer_type_for_same_question_id(
    requested_type: str, returned_type: str,
) -> None:
    """同一问题 ID 的答案必须匹配请求题型，不能只校验答案自身结构。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回结构合法但与请求题型不同的同 ID 答案。"""
        return httpx.Response(200, json=_response_body({"q": _ANSWERS[returned_type]}))

    with pytest.raises(JevResponseError):
        await _client(handler).evaluate(
            state="audit", questions={"q": _QUESTIONS[requested_type]},
        )


async def test_evaluate_accepts_matching_answers_for_all_three_types() -> None:
    """一批请求内的三个题型按各自 ID 匹配，保留正确字段和 usage。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回与三个问题分别匹配的合法答案。"""
        return httpx.Response(200, json=_response_body(dict(_ANSWERS)))

    result = await _client(handler).evaluate(state="audit", questions=dict(_QUESTIONS))

    assert {key: value.type for key, value in result.answers.items()} == {
        key: key for key in _QUESTIONS
    }
    assert result.answer("noul").noul == 0.5
    assert result.answer("choice").choice == "a"
    assert result.answer("score").score == 1.0
    assert result.usage.input_tokens == 2
    assert result.usage.output_tokens == 1


@pytest.mark.parametrize("returned_type", ["choice", "noul"])
async def test_answer_type_follows_normalized_body_after_before_event(
    returned_type: str,
) -> None:
    """发送前事件改写题型后，以实际序列化 body 而非原始参数为准。"""
    sent_questions: list[dict[str, Any]] = []

    async def rewrite(
        event_name: str, params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """把原始 Noul 改为原始字典形式的合法 Choice 问题。"""
        params["questions"] = {"q": _QUESTIONS["choice"].to_payload()}
        return EventDecision.SUCCESS, params

    def handler(request: httpx.Request) -> httpx.Response:
        """记录真实请求体，并按用例返回新题型或旧题型答案。"""
        sent_questions.append(json.loads(request.content)["questions"])
        return httpx.Response(200, json=_response_body({"q": _ANSWERS[returned_type]}))

    bus = get_event_bus()
    bus.subscribe(BEFORE_JEV_REQUEST, rewrite)
    try:
        client = _client(handler)
        if returned_type == "noul":
            with pytest.raises(JevResponseError):
                await client.evaluate(state="audit", questions={"q": _QUESTIONS["noul"]})
        else:
            result = await client.evaluate(
                state="audit", questions={"q": _QUESTIONS["noul"]},
            )
            assert result.answer("q").type == "choice"
    finally:
        bus.unsubscribe(BEFORE_JEV_REQUEST, rewrite)

    assert sent_questions == [{"q": _QUESTIONS["choice"].to_payload()}]


async def test_after_event_preserves_legal_typed_answer_rewrite() -> None:
    """题型约束只验证上游响应，不收紧成功事件已有的合法答案改写。"""
    async def rewrite(
        event_name: str, params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """在合法 Noul 响应之后把最终结果改成合法 Choice 答案。"""
        params["answers"] = {"q": dict(_ANSWERS["choice"])}
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(AFTER_JEV_REQUEST, rewrite)
    try:
        result = await _client(_ok_handler).evaluate(
            state="audit", questions={"q": _QUESTIONS["noul"]},
        )
    finally:
        bus.unsubscribe(AFTER_JEV_REQUEST, rewrite)

    assert result.answer("q").type == "choice"
    assert result.answer("q").choice == "a"
    assert result.answer("q").confidence == 0.8


@pytest.mark.parametrize(
    "response_body",
    [
        pytest.param("not-json", id="invalid-json"),
        pytest.param("[]", id="invalid-structure"),
        pytest.param(
            json.dumps(_response_body({"q": {"type": "noul", "noul": "high"}})),
            id="invalid-answer-field",
        ),
        pytest.param(
            json.dumps(_response_body({"q": {"type": "noul", "noul": 10**400}})),
            id="answer-number-overflow",
        ),
        pytest.param(
            json.dumps(_response_body({"q": _ANSWERS["choice"]})),
            id="wrong-answer-type",
        ),
    ],
)
@pytest.mark.parametrize("prior_retries", [0, 1], ids=["first-attempt", "after-retry"])
async def test_invalid_success_publishes_one_failure_and_does_not_retry(
    response_body: str,
    prior_retries: int,
    terminal_events: EventLog,
    retry_waits: list[float],
) -> None:
    """200 解析失败只通知一次后原样抛出，保留已发生重试的准确次数。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """可先制造一次可重试限流，再返回本用例的非法成功响应。"""
        requests.append(request)
        if len(requests) <= prior_retries:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text=response_body)

    with pytest.raises(JevResponseError) as info:
        await _client(handler, max_retries=prior_retries + 2).evaluate(
            state="audit",
            questions={"q": _QUESTIONS["noul"]},
            request_name="audit-parse",
            meta_data={"case": "response-contract"},
        )

    assert len(requests) == prior_retries + 1
    assert retry_waits == [0.0] * prior_retries
    assert [name for name, _ in terminal_events] == [
        ON_JEV_REQUEST_FAILED
    ] * (prior_retries + 1)
    failures = [params for _, params in terminal_events]
    assert [params["retry_count"] for params in failures] == list(range(prior_retries + 1))
    assert [params["error_type"] for params in failures] == (
        ["JevRateLimitError"] * prior_retries + ["JevResponseError"]
    )
    assert failures[-1]["error"] is info.value
    assert failures[-1]["error_message"] == str(info.value)
    assert failures[-1]["request_name"] == "audit-parse"
    assert failures[-1]["state"] == "audit"
    assert failures[-1]["meta_data"] == {"case": "response-contract"}
    if response_body == "not-json":
        assert isinstance(info.value.__cause__, json.JSONDecodeError)


@pytest.mark.parametrize("invalid_input", ["question", "serialization"])
async def test_local_configuration_error_is_not_an_http_retry_or_failure(
    invalid_input: str,
    terminal_events: EventLog,
    retry_waits: list[float],
) -> None:
    """本地问题定义及 JSON 序列化失败不冒充已发出请求的失败或重试。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """本地错误不应到达传输层；若发生则保留请求供断言。"""
        requests.append(request)
        return _ok_handler(request)

    state: JevState = "audit"
    questions: dict[str, JevQuestion | dict[str, Any]] = {"q": _QUESTIONS["noul"]}
    if invalid_input == "question":
        questions = {"q": {"type": "unknown", "instructions": "非法题型"}}
    else:
        state = {"not_serializable": object()}

    with pytest.raises(JevConfigurationError):
        await _client(handler, max_retries=2).evaluate(state=state, questions=questions)

    assert requests == []
    assert retry_waits == []
    assert terminal_events == []


async def test_cancellation_propagates_without_http_retry_or_failure(
    terminal_events: EventLog,
    retry_waits: list[float],
) -> None:
    """传输中的取消原样传播，不转成 HTTP 重试或失败通知。"""
    requests: list[httpx.Request] = []
    cancellation = asyncio.CancelledError("audit-cancel")

    def handler(request: httpx.Request) -> httpx.Response:
        """在内存传输中确定性抛出取消，无需启动后台任务。"""
        requests.append(request)
        raise cancellation

    with pytest.raises(asyncio.CancelledError) as info:
        await _client(handler, max_retries=2).evaluate(
            state="audit", questions={"q": _QUESTIONS["noul"]},
        )

    assert info.value is cancellation
    assert len(requests) == 1
    assert retry_waits == []
    assert terminal_events == []


def test_equivalent_retry_after_formats_both_override_local_cap(
    fixed_clock: datetime,
) -> None:
    """等价数字和 HTTP 日期都等待 600 秒，不受本地 8 秒上限截断。"""
    client = _client(_ok_handler, max_retry_backoff_seconds=8)
    numeric = httpx.Response(429, headers={"Retry-After": "600"})
    dated = httpx.Response(
        429,
        headers={
            "Retry-After": format_datetime(fixed_clock + timedelta(seconds=600), usegmt=True),
        },
    )

    assert client._retry_delay(0, numeric) == client._retry_delay(0, dated) == 600.0


@pytest.mark.parametrize("retry_after", ["past-http-date", "not-a-date"])
@pytest.mark.parametrize(("attempt", "ceiling"), [(0, 2.0), (3, 8.0)])
def test_expired_or_invalid_retry_after_keeps_bounded_jitter(
    retry_after: str,
    attempt: int,
    ceiling: float,
    fixed_clock: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """过期及非法响应头仍回退到原有有上限指数抖动，不采用服务端时长。"""
    bounds: list[tuple[float, float]] = []

    def midpoint(lower: float, upper: float) -> float:
        """记录抖动范围并返回中点，使本地退避可精确断言。"""
        bounds.append((lower, upper))
        return (lower + upper) / 2

    monkeypatch.setattr(jev_client_module, "random", SimpleNamespace(uniform=midpoint))
    if retry_after == "past-http-date":
        retry_after = format_datetime(fixed_clock - timedelta(seconds=60), usegmt=True)
    response = httpx.Response(429, headers={"Retry-After": retry_after})
    client = _client(
        _ok_handler, retry_backoff_seconds=2, max_retry_backoff_seconds=8,
    )

    assert client._retry_after_seconds(response) is None
    assert client._retry_delay(attempt, response) == ceiling / 2
    assert bounds == [(0.0, ceiling)]


@pytest.mark.parametrize("header_format", ["seconds", "http-date"])
async def test_retry_after_changes_delay_without_extending_retry_budget(
    header_format: str,
    fixed_clock: datetime,
    retry_waits: list[float],
    terminal_events: EventLog,
) -> None:
    """两种合法格式均只等待预算内的两次重试，失败尝试编号保持不变。"""
    retry_after = "600"
    if header_format == "http-date":
        retry_after = format_datetime(fixed_clock + timedelta(seconds=600), usegmt=True)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """持续返回相同限流响应，锁定实际尝试次数与等待入口。"""
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": retry_after})

    with pytest.raises(JevRateLimitError) as info:
        await _client(handler, max_retries=2, max_retry_backoff_seconds=8).evaluate(
            state="audit", questions={"q": _QUESTIONS["noul"]},
        )

    assert len(requests) == 3
    assert retry_waits == [600.0, 600.0]
    assert [name for name, _ in terminal_events] == [ON_JEV_REQUEST_FAILED] * 3
    assert [params["retry_count"] for _, params in terminal_events] == [0, 1, 2]
    assert terminal_events[-1][1]["error"] is info.value
