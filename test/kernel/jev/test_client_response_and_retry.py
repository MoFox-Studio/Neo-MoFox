"""Jev 响应链路与重试边界的补充测试。

全部用例走 ``httpx.MockTransport``，不触碰真实网络与真实 API key。
本文件聚焦两处既有覆盖的空缺：

1. ``JevAnswer.from_payload`` 的字段级校验分支（answer 结构非法时的分类）；
2. ``Retry-After`` 的 HTTP 日期格式分支，以及服务端退避上限与非法改写的兜底。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from src.kernel.jev import (
    JevClient,
    JevClientConfig,
    JevConfigurationError,
    JevResponseError,
    choice,
    noul,
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


def _answer_response(answer: Any) -> httpx.Response:
    """构造只含单个 noul 问题的成功响应，便于替换 answer 结构。"""
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {"q": answer},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


# ==============================================================================
# 响应链路：answer 结构校验分支
# ==============================================================================


async def test_evaluate_rejects_answer_that_is_not_an_object() -> None:
    """answer 不是 object 时必须整体失败，不能把脏结构交给业务。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response(["not", "an", "object"])

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_unknown_answer_type() -> None:
    """answer.type 不属于三种 typed question 时必须失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "unknown", "noul": 0.5})

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"type": "noul", "noul": "high"}, "noul"),
        ({"type": "noul", "noul": True}, "noul"),
        ({"type": "noul"}, "noul"),
        ({"type": "score", "score": "low"}, "score"),
        ({"type": "choice", "choice": "a", "confidence": "high"}, "confidence"),
        ({"type": "choice", "choice": "a"}, "confidence"),
    ],
)
async def test_evaluate_rejects_invalid_numeric_answer_fields(
    answer: dict[str, Any], expected: str,
) -> None:
    """数值字段必须是 number，bool 不算数字，缺失必填数值也要失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response(answer)

    with pytest.raises(JevResponseError, match="answer 结构非法") as info:
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )
    assert expected in str(info.value)


async def test_evaluate_rejects_non_string_choice() -> None:
    """choice 必须是 string。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "choice", "choice": 42, "confidence": 0.5})

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_choice_without_choice_value() -> None:
    """choice 题型必须给出 choice 值。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "choice", "confidence": 0.5})

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_non_object_legend() -> None:
    """legend 必须是 object。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response(
            {"type": "score", "score": 1.0, "confidence": 0.5, "legend": ["low", "high"]},
        )

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_non_object_probabilities() -> None:
    """probabilities 必须是 object。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response(
            {"type": "choice", "choice": "a", "confidence": 0.5, "probabilities": [0.5]},
        )

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_non_numeric_probability_values() -> None:
    """probabilities 的值必须是 number。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response(
            {
                "type": "choice",
                "choice": "a",
                "confidence": 0.5,
                "probabilities": {"a": "high"},
            },
        )

    with pytest.raises(JevResponseError, match="answer 结构非法"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_reports_validation_error_for_bad_answer_fields() -> None:
    """答案字段非法属于 provider 契约问题，映射为响应错误而非静默降级。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "noul", "noul": "not-a-number"})

    with pytest.raises(JevResponseError) as info:
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )
    # 异常消息必须指明具体字段，避免只看到笼统的失败原因。
    assert "noul" in str(info.value)


# ==============================================================================
# 重试链路：Retry-After 的 HTTP 日期格式与退避上限
# ==============================================================================


async def test_retry_delay_honors_http_date_retry_after() -> None:
    """Retry-After 使用 HTTP 日期时也能解析出等待秒数。"""
    target = datetime.now(timezone.utc) + timedelta(seconds=30)
    response = httpx.Response(
        429,
        headers={"Retry-After": format_datetime(target, usegmt=True)},
    )
    client = _client(lambda request: httpx.Response(200), max_retry_backoff_seconds=120)
    delay = client._retry_delay(0, response)
    assert 25 <= delay <= 30


async def test_retry_delay_caps_http_date_by_max_backoff() -> None:
    """服务端给出的等待时间仍然受本地退避上限约束。"""
    target = datetime.now(timezone.utc) + timedelta(seconds=600)
    response = httpx.Response(
        429,
        headers={"Retry-After": format_datetime(target, usegmt=True)},
    )
    client = _client(lambda request: httpx.Response(200), max_retry_backoff_seconds=5)
    assert client._retry_delay(0, response) == 5


async def test_retry_delay_ignores_past_http_date() -> None:
    """已过期的 HTTP 日期不是合法等待时间，回退到本地退避。"""
    target = datetime.now(timezone.utc) - timedelta(seconds=60)
    response = httpx.Response(
        429,
        headers={"Retry-After": format_datetime(target, usegmt=True)},
    )
    client = _client(lambda request: httpx.Response(200), retry_backoff_seconds=2)
    delay = client._retry_delay(0, response)
    assert 0 <= delay <= 2


async def test_retry_delay_ignores_naive_http_date() -> None:
    """没有时区的 HTTP 日期按 UTC 解释，不会直接判定为非法。"""
    target = datetime.now(timezone.utc) + timedelta(seconds=20)
    naive = target.replace(tzinfo=None).strftime("%a, %d %b %Y %H:%M:%S")
    response = httpx.Response(429, headers={"Retry-After": naive})
    client = _client(lambda request: httpx.Response(200), max_retry_backoff_seconds=120)
    delay = client._retry_delay(0, response)
    assert 15 <= delay <= 20


async def test_evaluate_retries_after_http_date_retry_after() -> None:
    """带 HTTP 日期 Retry-After 的 429 会触发重试并最终成功。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """首次返回带 HTTP 日期的限流响应，重试时返回合法答案。"""
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            target = datetime.now(timezone.utc) + timedelta(seconds=1)
            return httpx.Response(
                429,
                headers={"Retry-After": format_datetime(target, usegmt=True)},
            )
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.3}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    response = await _client(handler, max_retries=1).evaluate(
        state="state",
        questions={"q": noul("ok?")},
    )
    assert len(attempts) == 2
    assert response.answer("q").noul == 0.3


async def test_retry_delay_ignores_unparsable_retry_after() -> None:
    """无法解析的 Retry-After 退回本地抖动退避，不让请求卡死。"""
    response = httpx.Response(429, headers={"Retry-After": "soon"})
    client = _client(lambda request: httpx.Response(200), retry_backoff_seconds=3)
    delay = client._retry_delay(0, response)
    assert 0 <= delay <= 3


# ==============================================================================
# 响应链路：事件改写与请求体校验的兜底
# ==============================================================================


async def test_invalid_event_rewritten_answers_keep_original_answers() -> None:
    """订阅者改写出非法 answers 时保留原始答案，不把脏数据交给业务。"""
    from src.kernel.event import EventDecision, get_event_bus
    from src.kernel.jev.events import AFTER_JEV_REQUEST

    async def rewrite_answers(event_name: str, params: dict[str, Any]) -> Any:
        """把 answers 改写成非法结构，验证客户端保留原值。"""
        params["answers"] = {"q": {"type": "noul", "noul": "not-a-number"}}
        return (EventDecision.SUCCESS, params)

    bus = get_event_bus()
    bus.subscribe(AFTER_JEV_REQUEST, rewrite_answers)

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "noul", "noul": 0.42})

    try:
        response = await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )
    finally:
        bus.unsubscribe(AFTER_JEV_REQUEST, rewrite_answers)
    # 改写被拒绝，原始答案必须完好无损。
    assert response.answer("q").noul == 0.42


async def test_invalid_event_rewritten_questions_keep_original_questions() -> None:
    """订阅者把 questions 改写成非 mapping 时保留原始问题定义。"""
    from src.kernel.event import EventDecision, get_event_bus
    from src.kernel.jev.events import BEFORE_JEV_REQUEST

    async def rewrite_questions(event_name: str, params: dict[str, Any]) -> Any:
        """把 questions 改写成非法类型，验证客户端忽略该改写。"""
        params["questions"] = "not-a-mapping"
        return (EventDecision.SUCCESS, params)

    bus = get_event_bus()
    bus.subscribe(BEFORE_JEV_REQUEST, rewrite_questions)

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "noul", "noul": 0.11})

    try:
        response = await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )
    finally:
        bus.unsubscribe(BEFORE_JEV_REQUEST, rewrite_questions)
    assert response.answer("q").noul == 0.11


async def test_event_bus_failure_does_not_break_request() -> None:
    """事件总线故障不能让主请求失败。"""
    from src.kernel.event import get_event_bus
    from src.kernel.jev.events import BEFORE_JEV_REQUEST

    async def broken_handler(event_name: str, params: dict[str, Any]) -> Any:
        """故意抛出异常，验证事件链路故障被隔离。"""
        raise RuntimeError("event bus is broken")

    bus = get_event_bus()
    bus.subscribe(BEFORE_JEV_REQUEST, broken_handler)

    def handler(request: httpx.Request) -> httpx.Response:
        """为当前答案校验用例返回指定结构的响应。"""
        return _answer_response({"type": "noul", "noul": 0.77})

    try:
        response = await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )
    finally:
        bus.unsubscribe(BEFORE_JEV_REQUEST, broken_handler)
    assert response.answer("q").noul == 0.77


async def test_long_upstream_error_detail_is_truncated() -> None:
    """上游 error 字符串过长时必须截断，避免污染日志与异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(
            500,
            json={"error": {"type": "x" * 500}},
        )

    with pytest.raises(Exception) as info:
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )
    message = str(info.value)
    assert "…" in message
    # 截断后长度受限，不把整段上游文本带进异常。
    assert len(message) < 300


async def test_evaluate_rejects_empty_question_id() -> None:
    """question ID 必须是非空字符串。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """若本应在本地校验失败的请求进入传输层，立即使测试失败。"""
        raise AssertionError("请求不应发出")

    with pytest.raises(JevConfigurationError, match="question ID"):
        await _client(handler).evaluate(
            state="state",
            questions={"": noul("ok?")},
        )


async def test_evaluate_rejects_invalid_question_object() -> None:
    """question 既不是 JevQuestion 也不是 Mapping 时必须失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """若本应在本地校验失败的请求进入传输层，立即使测试失败。"""
        raise AssertionError("请求不应发出")

    with pytest.raises(JevConfigurationError, match="格式无效"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": "not-a-question"},
        )


async def test_evaluate_sends_question_criteria_for_choice() -> None:
    """choice 题型的 criteria 必须进入请求体，便于断言编解码一致。"""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """记录序列化问题结构，并返回合法 choice 答案。"""
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "choice", "choice": "a", "confidence": 0.6}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    await _client(handler).evaluate(
        state="state",
        questions={"q": choice("pick", {"a": "A", "b": "B"})},
    )
    assert seen["body"]["questions"]["q"]["criteria"] == {"a": "A", "b": "B"}


async def test_evaluate_rejects_response_with_mismatched_answer_ids() -> None:
    """answers 的 question ID 与请求不一致时整体失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"other": {"type": "noul", "noul": 0.1}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    with pytest.raises(JevResponseError, match="question ID"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_non_integer_usage() -> None:
    """usage 的 token 字段必须是整数。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.1}},
                "usage": {"input_tokens": "12", "output_tokens": 1},
            },
        )

    with pytest.raises(JevResponseError, match="usage"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_non_json_response() -> None:
    """响应不是合法 JSON 时明确失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(200, text="not json")

    with pytest.raises(JevResponseError, match="JSON"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_non_object_response() -> None:
    """响应必须是 JSON object。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(JevResponseError, match="JSON object"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_missing_model_or_answers() -> None:
    """缺少 model 或 answers 时明确失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(200, json={"usage": {"input_tokens": 1, "output_tokens": 1}})

    with pytest.raises(JevResponseError, match="model"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_missing_usage() -> None:
    """缺少 usage 时明确失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """返回当前用例指定的 HTTP 状态及响应体，验证响应边界。"""
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.1}},
            },
        )

    with pytest.raises(JevResponseError, match="usage"):
        await _client(handler).evaluate(
            state="state",
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_state_that_is_not_serializable() -> None:
    """state 无法序列化时在传输前失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """若本应在本地校验失败的请求进入传输层，立即使测试失败。"""
        raise AssertionError("请求不应发出")

    with pytest.raises(JevConfigurationError, match="JSON"):
        await _client(handler).evaluate(
            state={"bad": object()},
            questions={"q": noul("ok?")},
        )


async def test_evaluate_rejects_empty_questions() -> None:
    """questions 必须是非空 mapping。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """若本应在本地校验失败的请求进入传输层，立即使测试失败。"""
        raise AssertionError("请求不应发出")

    with pytest.raises(JevConfigurationError, match="questions"):
        await _client(handler).evaluate(
            state="state",
            questions={},
        )


async def test_evaluate_rejects_invalid_state_type() -> None:
    """state 必须是 string、object 或 array。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """若本应在本地校验失败的请求进入传输层，立即使测试失败。"""
        raise AssertionError("请求不应发出")

    with pytest.raises(JevConfigurationError, match="state"):
        await _client(handler).evaluate(
            state=123,
            questions={"q": noul("ok?")},
        )
