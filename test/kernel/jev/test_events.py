"""Jev 生命周期事件的单元测试。"""

from __future__ import annotations

import httpx
import pytest

from src.kernel.event import EventDecision, get_event_bus
from src.kernel.jev import JevClient, JevClientConfig, noul
from src.kernel.jev.events import (
    AFTER_JEV_REQUEST,
    BEFORE_JEV_REQUEST,
    ON_JEV_REQUEST_FAILED,
)


def _client(handler) -> JevClient:
    """构造一个使用内存 transport 的 Jev 客户端。"""
    return JevClient(
        JevClientConfig(api_key="test-key"),
        transport=httpx.MockTransport(handler),
    )


def _ok_handler(answers: dict | None = None):
    """返回固定的合法 System One 响应。"""
    payload = {
        "model": "jev-1.13.0",
        "answers": answers or {"q": {"type": "noul", "noul": 0.5}},
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


@pytest.mark.asyncio
async def test_before_event_can_rewrite_state_and_questions():
    """发送前订阅者可改写 state 与 questions。"""
    seen: dict = {}

    async def handler(event_name: str, params: dict):
        seen["params"] = dict(params)
        params["state"] = "rewritten-state"
        params["questions"] = {"q": {"type": "noul", "instructions": "rewritten?"}}
        return (EventDecision.SUCCESS, params)

    bus = get_event_bus()
    bus.subscribe(BEFORE_JEV_REQUEST, handler)
    try:
        client = _client(_ok_handler())
        await client.evaluate(
            state="original",
            questions={"q": noul("original?")},
        )
    finally:
        bus.unsubscribe(BEFORE_JEV_REQUEST, handler)

    assert seen["params"]["state"] == "original"
    assert seen["params"]["request_name"] == "jev"
    assert seen["params"]["meta_data"] == {}


@pytest.mark.asyncio
async def test_after_event_can_rewrite_answers_and_usage():
    """成功后订阅者可改写 answers 与 usage。"""
    async def handler(event_name: str, params: dict):
        params["answers"] = {"q": {"type": "noul", "noul": 0.99}}
        params["usage"] = {"input_tokens": 100, "output_tokens": 50}
        return (EventDecision.SUCCESS, params)

    bus = get_event_bus()
    bus.subscribe(AFTER_JEV_REQUEST, handler)
    try:
        client = _client(_ok_handler())
        response = await client.evaluate(
            state="s",
            questions={"q": noul("q?")},
        )
    finally:
        bus.unsubscribe(AFTER_JEV_REQUEST, handler)

    assert response.answer("q").noul == 0.99
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 50


@pytest.mark.asyncio
async def test_failed_event_is_published_on_http_error():
    """HTTP 错误会发布失败事件。"""
    seen: list[dict] = []

    async def handler(event_name: str, params: dict):
        seen.append(dict(params))
        return (EventDecision.SUCCESS, params)

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    bus = get_event_bus()
    bus.subscribe(ON_JEV_REQUEST_FAILED, handler)
    try:
        client = _client(error_handler)
        with pytest.raises(Exception):
            await client.evaluate(state="s", questions={"q": noul("q?")})
    finally:
        bus.unsubscribe(ON_JEV_REQUEST_FAILED, handler)

    assert len(seen) == 1
    assert seen[0]["error_type"] == "JevAuthenticationError"
    assert "retry_count" in seen[0]
    assert "meta_data" in seen[0]


@pytest.mark.asyncio
async def test_event_failure_does_not_break_request():
    """订阅者抛异常时，Jev 请求本身仍应正常完成。"""

    async def handler(event_name: str, params: dict):
        raise RuntimeError("handler boom")

    bus = get_event_bus()
    bus.subscribe(BEFORE_JEV_REQUEST, handler)
    try:
        client = _client(_ok_handler())
        response = await client.evaluate(
            state="s",
            questions={"q": noul("q?")},
        )
    finally:
        bus.unsubscribe(BEFORE_JEV_REQUEST, handler)

    assert response.answer("q").noul == 0.5


@pytest.mark.asyncio
async def test_retry_publishes_failed_event_per_attempt():
    """重试预算内的失败每次都发布失败事件。"""
    attempts = {"count": 0}
    seen: list[dict] = []

    async def handler(event_name: str, params: dict):
        seen.append(dict(params))
        return (EventDecision.SUCCESS, params)

    def flaky_handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow"})
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"q": {"type": "noul", "noul": 0.3}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    bus = get_event_bus()
    bus.subscribe(ON_JEV_REQUEST_FAILED, handler)
    try:
        client = JevClient(
            JevClientConfig(api_key="test-key", max_retries=1, retry_backoff_seconds=0),
            transport=httpx.MockTransport(flaky_handler),
        )
        response = await client.evaluate(state="s", questions={"q": noul("q?")})
    finally:
        bus.unsubscribe(ON_JEV_REQUEST_FAILED, handler)

    assert response.answer("q").noul == 0.3
    assert len(seen) == 1
    assert seen[0]["error_type"] == "JevRateLimitError"


def test_core_event_type_values_match_kernel_constants():
    """core 的 EventType 取值必须与 kernel 常量一致。"""
    from src.core.components.types import EventType

    assert EventType.BEFORE_JEV_REQUEST.value == BEFORE_JEV_REQUEST
    assert EventType.AFTER_JEV_REQUEST.value == AFTER_JEV_REQUEST
    assert EventType.ON_JEV_REQUEST_FAILED.value == ON_JEV_REQUEST_FAILED
