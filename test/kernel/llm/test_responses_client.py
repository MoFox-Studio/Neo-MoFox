"""OpenAI Responses 客户端测试。

使用 mock 模拟 openai SDK 的 ``client.responses``，避免依赖真实 API 调用。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.kernel.llm.payload import Image, LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from src.kernel.llm.roles import ROLE


class MockFunctionTool:
    """模拟实现 LLMUsable 协议的工具类。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_weather",
            "description": "查询天气",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        }


class MockToolInstance:
    """模拟工具实例（execute 已声明 reason 时不应注入 reason）。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {
            "name": "search",
            "description": "搜索",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }

    async def execute(self, q: str, reason: str) -> tuple[bool, str]:
        return True, f"{q}:{reason}"


class TestPayloadsToResponseInput:
    """测试 _payloads_to_response_input。"""

    def test_user_text_message(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [LLMPayload(ROLE.USER, Text("Hello"))]
        items, tools = _payloads_to_response_input(payloads)

        assert tools == []
        assert len(items) == 1
        assert items[0]["type"] == "message"
        assert items[0]["role"] == "user"
        assert items[0]["content"] == "Hello"

    def test_system_message(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [LLMPayload(ROLE.SYSTEM, Text("Be helpful"))]
        items, _ = _payloads_to_response_input(payloads)

        assert items[0]["role"] == "system"
        assert items[0]["content"] == "Be helpful"

    def test_assistant_message_with_reasoning(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [
            LLMPayload(ROLE.ASSISTANT, [ReasoningText("先思考"), Text("回答")])
        ]
        items, _ = _payloads_to_response_input(payloads)

        assert items[0]["type"] == "reasoning"
        assert items[0]["content"][0]["type"] == "reasoning_text"
        assert items[0]["content"][0]["text"] == "先思考"
        assert items[1]["type"] == "message"
        assert items[1]["role"] == "assistant"
        assert items[1]["content"][0]["type"] == "output_text"
        assert items[1]["content"][0]["text"] == "回答"

    def test_multimodal_user_message(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [
            LLMPayload(
                ROLE.USER,
                [Text("看图"), Image("base64|aGVsbG8=")],
            )
        ]
        items, _ = _payloads_to_response_input(payloads)

        content = items[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "input_text"
        assert content[1]["type"] == "input_image"

    def test_function_tool_payload(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [LLMPayload(ROLE.TOOL, MockFunctionTool)]
        items, tools = _payloads_to_response_input(payloads)

        assert items == []
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "get_weather"

    def test_raw_builtin_tool_payload_passthrough(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [LLMPayload(ROLE.TOOL, {"type": "web_search"})]  # type: ignore[list-item]
        _, tools = _payloads_to_response_input(payloads)

        assert tools == [{"type": "web_search"}]

    def test_tool_call_and_result_canonicalized(self) -> None:
        from src.kernel.llm.model_client.responses_client import _payloads_to_response_input

        payloads = [
            LLMPayload(
                ROLE.ASSISTANT,
                [
                    Text("调用工具"),
                    ToolCall(id="provider-random-id", name="get_weather", args={"location": "SF"}),
                ],
            ),
            LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult(value="晴", call_id="provider-random-id", name="get_weather"),
            ),
        ]
        items, _ = _payloads_to_response_input(payloads)

        function_call = items[1]
        function_output = items[2]
        assert function_call["type"] == "function_call"
        assert function_call["call_id"] == "call_0_0"
        assert function_call["name"] == "get_weather"
        assert function_call["arguments"] == '{"location": "SF"}'
        assert function_output["type"] == "function_call_output"
        assert function_output["call_id"] == "call_0_0"
        assert function_output["output"] == "晴"


class TestToolConversion:
    """测试 _tool_to_response_tool。"""

    def test_llmusable_converted(self) -> None:
        from src.kernel.llm.model_client.responses_client import _tool_to_response_tool

        tool = _tool_to_response_tool(MockToolInstance)
        assert tool is not None
        assert tool["type"] == "function"
        func = tool["function"]
        props = func["parameters"]["properties"]
        assert "reason" not in props  # execute 已声明 reason，不再注入

    def test_raw_dict_passthrough(self) -> None:
        from src.kernel.llm.model_client.responses_client import _tool_to_response_tool

        assert _tool_to_response_tool({"type": "web_search"}) == {"type": "web_search"}
        assert _tool_to_response_tool({"type": "web_search_2025_08_26"}) == {
            "type": "web_search_2025_08_26"
        }

    def test_unknown_item_returns_none(self) -> None:
        from src.kernel.llm.model_client.responses_client import _tool_to_response_tool

        assert _tool_to_response_tool({"no": "type"}) is None
        assert _tool_to_response_tool("string") is None
        assert _tool_to_response_tool(123) is None


class TestMergeTools:
    """测试 _merge_tools 去重逻辑。"""

    def test_merge_dedup_by_type_and_name(self) -> None:
        from src.kernel.llm.model_client.responses_client import _merge_tools

        function_tools = [
            {"type": "function", "function": {"name": "f", "parameters": {}}}
        ]
        custom = [
            {"type": "web_search"},
            {"type": "web_search"},
            {"type": "function", "function": {"name": "f", "parameters": {}}},
        ]
        merged = _merge_tools(function_tools, custom)

        assert len(merged) == 2
        types = {tool.get("type") for tool in merged}
        assert types == {"function", "web_search"}

    def test_ignore_non_dict(self) -> None:
        from src.kernel.llm.model_client.responses_client import _merge_tools

        merged = _merge_tools([{"type": "function", "function": {"name": "f"}}], ["junk", 3])
        assert len(merged) == 1


class TestExtractCustomTools:
    """测试 _extract_custom_tools。"""

    def test_extract_from_body(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_custom_tools

        tools = _extract_custom_tools({"tools": [{"type": "web_search"}]})
        assert tools == [{"type": "web_search"}]

    def test_non_mapping_or_missing_returns_empty(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_custom_tools

        assert _extract_custom_tools(None) == []
        assert _extract_custom_tools({"tools": "junk"}) == []
        assert _extract_custom_tools({"other": 1}) == []


class TestExtractResponseOutput:
    """测试 _extract_response_output。"""

    def _item(self, type_: str, **fields: Any) -> Any:
        item = MagicMock()
        item.type = type_
        for key, value in fields.items():
            setattr(item, key, value)
        return item

    def _block(self, type_: str, text: str) -> Any:
        block = MagicMock()
        block.type = type_
        block.text = text
        return block

    def test_message_reasoning_function_call(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_response_output

        message_item = self._item(
            "message",
            role="assistant",
            content=[self._block("output_text", "最终回答")],
        )
        reasoning_item = self._item(
            "reasoning",
            content=[self._block("reasoning_text", "中间思考")],
        )
        function_item = self._item(
            "function_call",
            call_id="call_1",
            name="get_weather",
            arguments='{"location": "SF"}',
        )
        resp = MagicMock()
        resp.output = [reasoning_item, message_item, function_item]

        message, tool_calls, reasoning = _extract_response_output(resp)

        assert message == "最终回答"
        assert reasoning == "中间思考"
        assert tool_calls == [
            {"id": "call_1", "name": "get_weather", "args": {"location": "SF"}}
        ]

    def test_web_search_call_ignored(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_response_output

        web_item = self._item("web_search_call", action={"type": "search"})
        message_item = self._item(
            "message",
            content=[self._block("output_text", "搜索到的结果")],
        )
        resp = MagicMock()
        resp.output = [web_item, message_item]

        message, tool_calls, reasoning = _extract_response_output(resp)

        assert message == "搜索结果" or message == "搜索到的结果"
        assert tool_calls == []
        assert reasoning is None

    def test_raw_dict_output(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_response_output

        resp = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "你好"}],
                }
            ]
        }
        message, tool_calls, reasoning = _extract_response_output(resp)
        assert message == "你好"
        assert tool_calls == []
        assert reasoning is None

    def test_invalid_arguments_kept_as_raw(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_response_output

        function_item = self._item(
            "function_call",
            call_id="call_2",
            name="f",
            arguments="not-json",
        )
        resp = MagicMock()
        resp.output = [function_item]

        _, tool_calls, _ = _extract_response_output(resp)
        assert tool_calls[0]["args"] == "not-json"


class TestExtractResponsesUsage:
    """测试 _extract_responses_usage。"""

    def test_maps_input_output_tokens(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_responses_usage

        resp = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 30},
                "output_tokens_details": {"reasoning_tokens": 10},
            }
        }
        usage = _extract_responses_usage(resp)

        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 20
        assert usage["total_tokens"] == 120
        assert usage["cache_hit_tokens"] == 30
        assert usage["cache_miss_tokens"] == 70
        assert usage.get("reasoning_tokens") == 10

    def test_no_usage_returns_empty(self) -> None:
        from src.kernel.llm.model_client.responses_client import _extract_responses_usage

        resp = MagicMock()
        resp.usage = None
        assert _extract_responses_usage(resp) == {}


class TestOpenAIResponsesClient:
    """测试 OpenAIResponsesClient。"""

    def _make_client(self, mock_resp: Any) -> Any:
        from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient

        mock_openai = MagicMock()
        mock_openai.responses.create = AsyncMock(return_value=mock_resp)

        client = OpenAIResponsesClient()
        client._get_client = MagicMock(return_value=mock_openai)
        return client, mock_openai

    def _model_set(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "timeout": 30.0,
            "max_tokens": 256,
            "temperature": 0.7,
            "extra_params": {},
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_create_non_stream(self) -> None:
        from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient

        message_item = MagicMock()
        message_item.type = "message"
        content_block = MagicMock()
        content_block.type = "output_text"
        content_block.text = "Hello from responses"
        message_item.content = [content_block]

        mock_resp = MagicMock()
        mock_resp.output = [message_item]
        mock_resp.usage = None

        mock_openai = MagicMock()
        mock_openai.responses.create = AsyncMock(return_value=mock_resp)

        client = OpenAIResponsesClient()
        client._get_client = MagicMock(return_value=mock_openai)

        payloads = [LLMPayload(ROLE.USER, Text("Hi"))]
        message, tool_calls, stream_iter, reasoning, usage = await client.create(
            model_name="deepseek-v4-flash",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=self._model_set(),
            stream=False,
        )

        assert message == "Hello from responses"
        assert tool_calls == []
        assert stream_iter is None
        assert reasoning is None
        assert usage == {}

    @pytest.mark.asyncio
    async def test_create_with_function_call_and_reasoning(self) -> None:

        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_block = MagicMock()
        reasoning_block.type = "reasoning_text"
        reasoning_block.text = "我需要查天气"
        reasoning_item.content = [reasoning_block]

        function_item = MagicMock()
        function_item.type = "function_call"
        function_item.call_id = "call_abc"
        function_item.name = "get_weather"
        function_item.arguments = '{"location": "SF"}'

        mock_resp = MagicMock()
        mock_resp.output = [reasoning_item, function_item]
        mock_resp.usage = None

        client, mock_openai = self._make_client(mock_resp)
        payloads = [
            LLMPayload(ROLE.USER, Text("查一下旧金山天气")),
            LLMPayload(ROLE.TOOL, MockFunctionTool),
        ]
        message, tool_calls, stream_iter, reasoning, _ = await client.create(
            model_name="deepseek-v4-flash",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=self._model_set(),
            stream=False,
        )

        assert message == ""
        assert tool_calls == [
            {"id": "call_abc", "name": "get_weather", "args": {"location": "SF"}}
        ]
        assert reasoning == "我需要查天气"
        assert stream_iter is None

        call_kwargs = mock_openai.responses.create.await_args.kwargs
        assert call_kwargs["tools"][0]["function"]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_create_merges_builtin_tools_from_custom_body(self) -> None:

        message_item = MagicMock()
        message_item.type = "message"
        content_block = MagicMock()
        content_block.type = "output_text"
        content_block.text = "ok"
        message_item.content = [content_block]

        mock_resp = MagicMock()
        mock_resp.output = [message_item]
        mock_resp.usage = None

        client, mock_openai = self._make_client(mock_resp)
        payloads = [
            LLMPayload(ROLE.USER, Text("搜一下最近新闻")),
            LLMPayload(ROLE.TOOL, MockFunctionTool),
        ]
        model_set = self._model_set(
            extra_params={
                "body": {
                    "tools": [{"type": "web_search"}],
                    "reasoning": {"effort": "low"},
                }
            }
        )

        await client.create(
            model_name="deepseek-v4-flash",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_openai.responses.create.await_args.kwargs
        tools = call_kwargs["tools"]
        tool_types = {tool.get("type") for tool in tools}
        assert tool_types == {"function", "web_search"}
        assert call_kwargs["reasoning"] == {"effort": "low"}
        assert call_kwargs["model"] == "deepseek-v4-flash"
        assert call_kwargs["input"][0]["content"] == "搜一下最近新闻"

    @pytest.mark.asyncio
    async def test_create_uses_max_output_tokens_and_temperature(self) -> None:

        message_item = MagicMock()
        message_item.type = "message"
        content_block = MagicMock()
        content_block.type = "output_text"
        content_block.text = "ok"
        message_item.content = [content_block]

        client, mock_openai = self._make_client(MagicMock(output=[message_item]))
        await client.create(
            model_name="deepseek-v4-flash",
            payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
            tools=[],
            request_name="test",
            model_set=self._model_set(),
            stream=False,
        )

        call_kwargs = mock_openai.responses.create.await_args.kwargs
        assert call_kwargs["max_output_tokens"] == 256
        assert call_kwargs["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_create_empty_output_raises(self) -> None:

        client, _ = self._make_client(MagicMock(output=[]))
        with pytest.raises(Exception, match="空响应"):
            await client.create(
                model_name="deepseek-v4-flash",
                payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
                tools=[],
                request_name="test",
                model_set=self._model_set(),
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_create_missing_input_and_instructions_raises(self) -> None:

        client, mock_openai = self._make_client(MagicMock())
        model_set = self._model_set(extra_params={})
        # 没有 input（只有 TOOL payload），也没有 instructions
        with pytest.raises(Exception, match="input 或 instructions"):
            await client.create(
                model_name="deepseek-v4-flash",
                payloads=[LLMPayload(ROLE.TOOL, MockFunctionTool)],
                tools=[],
                request_name="test",
                model_set=model_set,
                stream=False,
            )
        mock_openai.responses.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_invalid_model_set_type(self) -> None:
        from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient

        client = OpenAIResponsesClient()
        with pytest.raises(TypeError, match="OpenAIResponsesClient"):
            await client.create(
                model_name="deepseek-v4-flash",
                payloads=[],
                tools=[],
                request_name="test",
                model_set="not-a-dict",  # type: ignore[arg-type]
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_create_missing_api_key(self) -> None:
        from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient

        client = OpenAIResponsesClient()
        with pytest.raises(ValueError, match="api_key 不能为空"):
            await client.create(
                model_name="deepseek-v4-flash",
                payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
                tools=[],
                request_name="test",
                model_set=self._model_set(api_key=""),
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_create_stream_text_and_tool(self) -> None:
        from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient

        async def fake_stream():
            yield {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "call_id": "call_s1",
                    "name": "get_weather",
                    "id": "item_1",
                },
            }
            yield {
                "type": "response.function_call_arguments.delta",
                "item_id": "item_1",
                "delta": '{"location": "SF"}',
            }
            yield {"type": "response.reasoning_text.delta", "delta": "思考中"}
            yield {"type": "response.output_text.delta", "delta": "天气"}
            yield {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 2},
                    },
                },
            }

        mock_openai = MagicMock()
        mock_openai.responses.create = AsyncMock(return_value=fake_stream())

        client = OpenAIResponsesClient()
        client._get_client = MagicMock(return_value=mock_openai)

        _, _, stream_iter, _, _ = await client.create(
            model_name="deepseek-v4-flash",
            payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
            tools=[],
            request_name="test",
            model_set=self._model_set(),
            stream=True,
        )

        assert stream_iter is not None
        events = [event async for event in stream_iter]

        text = "".join(e.text_delta for e in events if e.text_delta)
        reasoning = "".join(e.reasoning_delta for e in events if e.reasoning_delta)
        tool_events = [e for e in events if e.tool_name]
        usage_events = [e for e in events if e.usage]

        assert text == "天气"
        assert reasoning == "思考中"
        assert tool_events[0].tool_name == "get_weather"
        assert tool_events[0].tool_call_id == "call_s1"
        assert usage_events[-1].usage is not None
        assert usage_events[-1].usage["prompt_tokens"] == 10

    @pytest.mark.asyncio
    async def test_create_stream_failed_raises(self) -> None:
        from src.kernel.llm.model_client.responses_client import OpenAIResponsesClient

        async def fake_stream():
            yield {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {"message": "boom"},
                    "usage": None,
                },
            }

        mock_openai = MagicMock()
        mock_openai.responses.create = AsyncMock(return_value=fake_stream())

        client = OpenAIResponsesClient()
        client._get_client = MagicMock(return_value=mock_openai)

        _, _, stream_iter, _, _ = await client.create(
            model_name="deepseek-v4-flash",
            payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
            tools=[],
            request_name="test",
            model_set=self._model_set(),
            stream=True,
        )

        assert stream_iter is not None
        with pytest.raises(RuntimeError, match="boom"):
            async for _ in stream_iter:
                pass

    @pytest.mark.asyncio
    async def test_create_applies_extra_headers_and_query(self) -> None:

        message_item = MagicMock()
        message_item.type = "message"
        content_block = MagicMock()
        content_block.type = "output_text"
        content_block.text = "ok"
        message_item.content = [content_block]

        client, mock_openai = self._make_client(MagicMock(output=[message_item]))
        model_set = self._model_set(
            extra_params={
                "headers": {"X-Test": "1"},
                "query": {"foo": "bar"},
            }
        )
        await client.create(
            model_name="deepseek-v4-flash",
            payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_openai.responses.create.await_args.kwargs
        assert call_kwargs["extra_headers"] == {"X-Test": "1"}
        assert call_kwargs["extra_query"] == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_create_tool_call_compat_injects_prompt(self) -> None:

        message_item = MagicMock()
        message_item.type = "message"
        content_block = MagicMock()
        content_block.type = "output_text"
        content_block.text = "ok"
        message_item.content = [content_block]

        client, mock_openai = self._make_client(MagicMock(output=[message_item]))
        model_set = self._model_set(tool_call_compat=True)
        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.TOOL, MockFunctionTool),
        ]
        await client.create(
            model_name="deepseek-v4-flash",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_openai.responses.create.await_args.kwargs
        last_input = call_kwargs["input"][-1]
        assert last_input["type"] == "message"
        assert last_input["role"] == "user"
        assert "get_weather" in last_input["content"]
