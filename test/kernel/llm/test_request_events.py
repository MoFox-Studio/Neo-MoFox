"""LLM 请求事件触发测试。

测试 BEFORE_LLM_REQUEST、AFTER_LLM_REQUEST、ON_LLM_REQUEST_FAILED
三个事件的触发、参数完整性以及事件订阅者对参数的修改能力。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from src.core.components.types import EventType
from src.kernel.event import EventDecision, get_event_bus
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.payload import LLMPayload, Text, ToolCall
from src.kernel.llm.policy import RoundRobinPolicy
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.roles import ROLE


def _model(identifier: str, *, max_retry: int = 0) -> Any:
    """构造测试用模型配置。"""
    return {
        "api_provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": identifier,
        "api_key": "dummy-key",
        "client_type": "openai",
        "max_retry": max_retry,
        "timeout": 5,
        "retry_interval": 0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.1,
        "max_tokens": 10,
        "extra_params": {},
    }


class _SuccessClient:
    """成功返回文本的 mock client。"""

    def __init__(self) -> None:
        """初始化并准备参数捕获容器。"""
        self.received_payloads: list[LLMPayload] | None = None
        self.received_tools: list[Any] | None = None
        self.received_stream: bool | None = None

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Any],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> Any:
        self.received_payloads = list(payloads)
        self.received_tools = list(tools)
        self.received_stream = stream
        return "Hello world!", None, None


class _FailingClient:
    """总是失败的 mock client。"""

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Any],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> Any:
        raise RuntimeError("connection refused")


class _ToolCallClient:
    """返回带 tool_calls 的 mock client。"""

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Any],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> Any:
        return "I will use a tool", [
            {"id": "call_1", "name": "get_weather", "args": {"city": "Tokyo"}}
        ], None


class _ReasoningClient:
    """返回带 reasoning_content 的 mock client。"""

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Any],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> Any:
        return "Final answer", None, None, "Let me think about this.", None


@pytest.fixture(autouse=True)
async def _clean_event_bus() -> AsyncGenerator[None, None]:
    """每个测试前后清理事件总线订阅。"""
    bus = get_event_bus()
    for event in (
        EventType.BEFORE_LLM_REQUEST,
        EventType.AFTER_LLM_REQUEST,
        EventType.ON_LLM_REQUEST_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)
    yield
    for event in (
        EventType.BEFORE_LLM_REQUEST,
        EventType.AFTER_LLM_REQUEST,
        EventType.ON_LLM_REQUEST_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)


@pytest.mark.asyncio
async def test_before_llm_request_fires_with_payloads() -> None:
    """成功请求应触发 BEFORE_LLM_REQUEST 事件并携带 payloads。"""

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_LLM_REQUEST, handler)

    payloads = [
        LLMPayload(ROLE.SYSTEM, Text("You are a bot.")),
        LLMPayload(ROLE.USER, Text("Hi")),
    ]
    req = LLMRequest(
        [_model("gpt-4")],
        request_name="test_req",
        payloads=payloads,
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_SuccessClient()),
    )

    await req.send(stream=False)

    assert len(received) == 1
    params = received[0]
    assert params["request_name"] == "test_req"
    assert params["model_identifier"] == "gpt-4"
    assert params["stream"] is False
    assert params["payloads"] is not None
    assert len(params["payloads"]) == 2


@pytest.mark.asyncio
async def test_after_llm_request_fires_with_result() -> None:
    """成功请求应触发 AFTER_LLM_REQUEST 事件并携带详细结果。"""

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_LLM_REQUEST, handler)

    req = LLMRequest(
        [_model("gpt-4")],
        request_name="test_req",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_SuccessClient()),
    )

    await req.send(stream=False)

    assert len(received) == 1
    params = received[0]
    assert params["request_name"] == "test_req"
    assert params["model_identifier"] == "gpt-4"
    assert params["success"] is True
    assert params["message"] == "Hello world!"
    assert "latency" in params
    assert params["retry_count"] == 0


@pytest.mark.asyncio
async def test_after_llm_request_includes_tool_calls() -> None:
    """成功请求返回 tool_calls 时应在 AFTER_LLM_REQUEST 事件中携带。"""

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_LLM_REQUEST, handler)

    req = LLMRequest(
        [_model("gpt-4")],
        request_name="tool_req",
        payloads=[LLMPayload(ROLE.USER, Text("What's the weather?"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_ToolCallClient()),
    )

    resp = await req.send(stream=False)

    assert len(received) == 1
    params = received[0]
    assert params["success"] is True
    assert len(params["tool_calls"]) == 1
    assert params["tool_calls"][0].name == "get_weather"
    assert resp.call_list[0].name == "get_weather"  # type: ignore[index]


@pytest.mark.asyncio
async def test_on_llm_request_failed_fires_with_error() -> None:
    """请求失败且重试耗尽时应触发 ON_LLM_REQUEST_FAILED 事件。"""

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.ON_LLM_REQUEST_FAILED, handler)

    req = LLMRequest(
        [_model("gpt-4", max_retry=0)],
        request_name="fail_req",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_FailingClient()),
    )

    with pytest.raises(Exception):
        await req.send(stream=False)

    assert len(received) == 1
    params = received[0]
    assert params["request_name"] == "fail_req"
    assert params["model_identifier"] == "gpt-4"
    assert params["stream"] is False
    assert params["payloads"] is not None
    assert "error" in params
    assert "error_type" in params
    assert "error_message" in params
    assert "connection refused" in params["error_message"]
    assert "retry_count" in params


@pytest.mark.asyncio
async def test_no_subscriber_skips_llm_event() -> None:
    """无订阅者时应直接执行不触发事件调度。"""

    req = LLMRequest(
        [_model("gpt-4")],
        request_name="no_sub",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_SuccessClient()),
    )

    resp = await req.send(stream=False)
    assert resp.message == "Hello world!"


# ── 参数修改测试 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_before_llm_request_can_modify_payloads() -> None:
    """BEFORE_LLM_REQUEST 订阅者修改 payloads 应反映到实际请求。"""

    modified_payloads = [LLMPayload(ROLE.USER, Text("Modified prompt"))]

    async def handler(
        event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        params["payloads"] = list(modified_payloads)
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_LLM_REQUEST, handler)

    client = _SuccessClient()
    req = LLMRequest(
        [_model("gpt-4")],
        request_name="modify_payloads",
        payloads=[LLMPayload(ROLE.USER, Text("Original"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=client),
    )

    await req.send(stream=False)

    assert client.received_payloads is not None
    assert len(client.received_payloads) == 1
    content = client.received_payloads[0].content
    assert len(content) == 1
    assert isinstance(content[0], Text)
    assert content[0].text == "Modified prompt"


@pytest.mark.asyncio
async def test_before_llm_request_can_modify_stream() -> None:
    """BEFORE_LLM_REQUEST 订阅者修改 stream 应反映到实际请求。"""

    async def handler(
        event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        params["stream"] = True
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_LLM_REQUEST, handler)

    client = _SuccessClient()
    req = LLMRequest(
        [_model("gpt-4")],
        request_name="modify_stream",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=client),
    )

    await req.send(stream=False)

    assert client.received_stream is True


@pytest.mark.asyncio
async def test_after_llm_request_can_modify_message() -> None:
    """AFTER_LLM_REQUEST 订阅者修改 message 应反映到 LLMResponse。"""

    async def handler(
        event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        params["message"] = "Modified message"
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_LLM_REQUEST, handler)

    req = LLMRequest(
        [_model("gpt-4")],
        request_name="modify_message",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_SuccessClient()),
    )

    resp = await req.send(stream=False)
    assert resp.message == "Modified message"


@pytest.mark.asyncio
async def test_after_llm_request_can_modify_reasoning() -> None:
    """AFTER_LLM_REQUEST 订阅者修改 reasoning_content 应反映到 LLMResponse。"""

    async def handler(
        event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        params["reasoning_content"] = "Modified reasoning"
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_LLM_REQUEST, handler)

    req = LLMRequest(
        [_model("gpt-4")],
        request_name="modify_reasoning",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_ReasoningClient()),
    )

    resp = await req.send(stream=False)
    assert resp.reasoning_content == "Modified reasoning"


@pytest.mark.asyncio
async def test_after_llm_request_can_modify_tool_calls() -> None:
    """AFTER_LLM_REQUEST 订阅者修改 tool_calls 应反映到 LLMResponse。"""

    modified_calls = [
        ToolCall(id="call_2", name="get_time", args={"timezone": "UTC"})
    ]

    async def handler(
        event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        params["tool_calls"] = list(modified_calls)
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_LLM_REQUEST, handler)

    req = LLMRequest(
        [_model("gpt-4")],
        request_name="modify_tool_calls",
        payloads=[LLMPayload(ROLE.USER, Text("What's the weather?"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_ToolCallClient()),
    )

    resp = await req.send(stream=False)
    assert resp.call_list is not None
    assert len(resp.call_list) == 1
    assert resp.call_list[0].name == "get_time"
    assert resp.call_list[0].id == "call_2"


@pytest.mark.asyncio
async def test_on_llm_request_failed_can_modify_error() -> None:
    """ON_LLM_REQUEST_FAILED 订阅者修改 error 应作为最终抛出的异常。"""

    custom_error = RuntimeError("custom wrapped error")

    async def handler(
        event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        params["error"] = custom_error
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.ON_LLM_REQUEST_FAILED, handler)

    req = LLMRequest(
        [_model("gpt-4", max_retry=0)],
        request_name="modify_error",
        payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
        policy=RoundRobinPolicy(),
        clients=ModelClientRegistry(openai=_FailingClient()),
    )

    with pytest.raises(RuntimeError, match="custom wrapped error"):
        await req.send(stream=False)
