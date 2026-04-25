"""Anthropic 客户端测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.kernel.llm import Image, LLMPayload, ReasoningText, ROLE, Text, ToolCall, ToolResult
from src.kernel.llm.model_client.anthropic_client import (
    AnthropicChatClient,
    _parse_anthropic_message,
    _payloads_to_anthropic_messages,
    _to_anthropic_tool,
)


class MockTool:
    """用于测试的工具 schema。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                },
                "required": ["city"],
            },
        }


class _FakeAsyncStream:
    """最小异步流上下文。"""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> "_FakeAsyncStream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self._events:
            yield event


class _FakeMessagesAPI:
    """模拟 Anthropic messages API。"""

    def __init__(self, *, create_response: Any | None = None, stream_events: list[Any] | None = None) -> None:
        self.create_response = create_response
        self.stream_events = stream_events or []
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        return self.create_response

    def stream(self, **kwargs: Any) -> _FakeAsyncStream:
        self.stream_calls.append(kwargs)
        return _FakeAsyncStream(self.stream_events)


class _FakeClient:
    """模拟 Anthropic client。"""

    def __init__(self, messages_api: _FakeMessagesAPI) -> None:
        self.messages = messages_api


class TestPayloadsToAnthropicMessages:
    """测试 payload 到 Anthropic messages 的转换。"""

    def test_convert_system_tool_and_tool_result_payloads(self) -> None:
        """测试 system、tool 和 tool_result 的转换。"""
        payloads = [
            LLMPayload(ROLE.SYSTEM, Text("You are helpful.")),
            LLMPayload(ROLE.TOOL, MockTool),
            LLMPayload(ROLE.USER, [Text("Hello"), Image("base64|aGVsbG8=")]),
            LLMPayload(
                ROLE.ASSISTANT,
                [ReasoningText("think", signature="sig_1"), Text("Need tool"), ToolCall(id="toolu_1", name="get_weather", args={"city": "Paris"})],
            ),
            LLMPayload(
                ROLE.TOOL_RESULT,
                [ToolResult(value={"temp": 23}, call_id="toolu_1", name="get_weather")],
            ),
        ]

        messages, tools, system_blocks = _payloads_to_anthropic_messages(payloads)

        assert system_blocks == [{"type": "text", "text": "You are helpful."}]
        assert tools == [
            {
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "description": "说明你选择此动作/工具的原因",
                        },
                    },
                    "required": ["city", "reason"],
                },
            }
        ]
        assert messages[0]["role"] == "user"
        assert messages[0]["content"][0] == {"type": "text", "text": "Hello"}
        assert messages[0]["content"][1]["type"] == "image"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"][0] == {
            "type": "thinking",
            "thinking": "think",
            "signature": "sig_1",
        }
        assert messages[1]["content"][1] == {"type": "text", "text": "Need tool"}
        assert messages[1]["content"][2] == {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "get_weather",
            "input": {"city": "Paris"},
        }
        assert messages[2] == {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": '{"temp": 23}',
                    "tool_use_id": "toolu_1",
                    "tool_name": "get_weather",
                }
            ],
        }

    def test_convert_tools_with_openai_format(self) -> None:
        """测试 Anthropic client 可切换为 OpenAI 风格 tools schema。"""
        payloads = [LLMPayload(ROLE.TOOL, MockTool)]

        _, tools, _ = _payloads_to_anthropic_messages(payloads, tool_format="openai")

        assert tools == [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]


def test_parse_anthropic_message_extracts_text_tools_and_reasoning() -> None:
    """测试 Anthropic 响应解析。"""
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="analyze first", signature="sig_1"),
            SimpleNamespace(type="text", text="Here is the answer."),
            SimpleNamespace(type="tool_use", id="toolu_1", name="get_weather", input={"city": "Paris"}),
        ]
    )

    text, tool_calls, reasoning = _parse_anthropic_message(message)

    assert text == "Here is the answer."
    assert tool_calls == [{"id": "toolu_1", "name": "get_weather", "args": {"city": "Paris"}}]
    assert reasoning == "analyze first"


def test_convert_assistant_preserves_redacted_thinking_block() -> None:
    """Anthropic 的 redacted_thinking block 应原样回传。"""
    payloads = [
        LLMPayload(
            ROLE.ASSISTANT,
            [ReasoningText("", redacted_data="opaque-data"), Text("after think")],
        )
    ]

    messages, _, _ = _payloads_to_anthropic_messages(payloads)

    assert messages == [
        {
            "role": "assistant",
            "content": [
                {"type": "redacted_thinking", "data": "opaque-data"},
                {"type": "text", "text": "after think"},
            ],
        }
    ]


def test_convert_assistant_after_tool_result_synthesizes_missing_thinking() -> None:
    """紧随 TOOL_RESULT 的 assistant 若缺少 reasoning block，应自动用文本补 thinking。"""
    payloads = [
        LLMPayload(
            ROLE.ASSISTANT,
            [ReasoningText("think", signature="sig_1"), ToolCall(id="toolu_1", name="get_weather", args={"city": "Paris"})],
        ),
        LLMPayload(
            ROLE.TOOL_RESULT,
            [ToolResult(value={"temp": 23}, call_id="toolu_1", name="get_weather")],
        ),
        LLMPayload(ROLE.ASSISTANT, Text("__SUSPEND__")),
    ]

    messages, _, _ = _payloads_to_anthropic_messages(payloads)

    assert messages[2] == {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "__SUSPEND__"},
            {"type": "text", "text": "__SUSPEND__"},
        ],
    }


@pytest.mark.asyncio
async def test_create_non_stream_returns_text_tool_calls_and_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试非流式 create 路径。"""
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="reasoning", signature="sig_1"),
            SimpleNamespace(type="text", text="done"),
            SimpleNamespace(type="tool_use", id="toolu_1", name="lookup", input={"keyword": "neo"}),
        ]
    )
    messages_api = _FakeMessagesAPI(create_response=response)
    fake_client = _FakeClient(messages_api)
    client = AnthropicChatClient()
    monkeypatch.setattr(client, "_get_client", lambda **_: fake_client)

    message, tool_calls, stream_iter, reasoning = await client.create(
        model_name="claude-sonnet-4-6",
        payloads=[LLMPayload(ROLE.USER, Text("hello")), LLMPayload(ROLE.TOOL, MockTool)],
        tools=[],
        request_name="test",
        model_set={
            "api_key": "sk-ant-test",
            "max_tokens": 256,
            "temperature": 0.2,
            "extra_params": {},
        },
        stream=False,
    )

    assert message == "done"
    assert tool_calls == [{"id": "toolu_1", "name": "lookup", "args": {"keyword": "neo"}}]
    assert reasoning == [ReasoningText("reasoning", signature="sig_1")]
    assert stream_iter is None
    create_params = messages_api.create_calls[0]
    assert create_params["tool_choice"] == {"type": "auto"}
    assert create_params["tools"][0]["name"] == "get_weather"
    assert create_params["tools"][0]["input_schema"]["required"] == ["city", "reason"]


@pytest.mark.asyncio
async def test_create_non_stream_supports_openai_tool_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试 Anthropic client 可按配置发送 OpenAI 风格 tools。"""
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="done")])
    messages_api = _FakeMessagesAPI(create_response=response)
    fake_client = _FakeClient(messages_api)
    client = AnthropicChatClient()
    monkeypatch.setattr(client, "_get_client", lambda **_: fake_client)

    await client.create(
        model_name="deepseek-v4-pro",
        payloads=[LLMPayload(ROLE.USER, Text("hello")), LLMPayload(ROLE.TOOL, MockTool)],
        tools=[],
        request_name="test",
        model_set={
            "api_key": "sk-ant-test",
            "max_tokens": 256,
            "extra_params": {"tool_format": "openai"},
        },
        stream=False,
    )

    create_params = messages_api.create_calls[0]
    assert create_params["tools"][0]["function"]["name"] == "get_weather"


def test_to_anthropic_tool_does_not_inject_reason_when_schema_already_has_it() -> None:
    """已有 reason 参数时不应重复注入。"""

    class MockToolWithReason:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {
                "name": "test_tool",
                "description": "desc",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "original reason",
                        },
                        "query": {
                            "type": "string",
                            "description": "query",
                        },
                    },
                    "required": ["query"],
                },
            }

    tool = _to_anthropic_tool(MockToolWithReason)
    input_schema = tool["input_schema"]
    assert isinstance(input_schema, dict)
    properties = input_schema["properties"]
    required = input_schema["required"]

    assert properties["reason"]["description"] == "original reason"
    assert required == ["query"]


def test_to_anthropic_tool_does_not_inject_reason_when_execute_accepts_it() -> None:
    """execute 已声明 reason 时不应额外注入 schema 参数。"""

    class MockToolWithExecuteReason:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {
                "name": "test_tool",
                "description": "desc",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "query",
                        }
                    },
                    "required": ["query"],
                },
            }

        async def execute(self, query: str, reason: str) -> tuple[bool, str]:
            return True, f"{query}:{reason}"

    tool = _to_anthropic_tool(MockToolWithExecuteReason)
    input_schema = tool["input_schema"]
    assert isinstance(input_schema, dict)
    properties = input_schema["properties"]
    required = input_schema["required"]

    assert "reason" not in properties
    assert "reason" not in required


@pytest.mark.asyncio
async def test_create_stream_emits_text_reasoning_and_tool_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试流式 create 路径。"""
    events = [
        SimpleNamespace(type="message_start"),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="thinking", thinking="", signature=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="step "),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="signature_delta", signature="sig_stream"),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="lookup", input={}),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"keyword": "n'),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='eo"}'),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=2,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=2,
            delta=SimpleNamespace(type="text_delta", text="answer"),
        ),
        SimpleNamespace(type="message_stop"),
    ]
    messages_api = _FakeMessagesAPI(stream_events=events)
    fake_client = _FakeClient(messages_api)
    client = AnthropicChatClient()
    monkeypatch.setattr(client, "_get_client", lambda **_: fake_client)

    message, tool_calls, stream_iter, reasoning = await client.create(
        model_name="claude-sonnet-4-6",
        payloads=[LLMPayload(ROLE.USER, Text("hello"))],
        tools=[],
        request_name="stream-test",
        model_set={
            "api_key": "sk-ant-test",
            "max_tokens": 256,
            "extra_params": {"thinking": {"type": "adaptive", "display": "summarized"}},
        },
        stream=True,
    )

    assert message is None
    assert tool_calls is None
    assert reasoning is None
    assert stream_iter is not None

    collected = [event async for event in stream_iter]

    assert collected[0].reasoning_block_type == "thinking"
    assert collected[1].reasoning_delta == "step "
    assert collected[2].reasoning_signature_delta == "sig_stream"
    assert collected[3].tool_call_id == "toolu_1"
    assert collected[3].tool_name == "lookup"
    assert collected[4].tool_args_delta == '{"keyword": "n'
    assert collected[5].tool_args_delta == 'eo"}'
    assert collected[6].text_delta == "answer"
    stream_params = messages_api.stream_calls[0]
    assert stream_params["thinking"] == {"type": "adaptive", "display": "summarized"}
