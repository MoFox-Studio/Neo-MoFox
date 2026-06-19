"""OpenAI客户端测试。

使用mock来模拟OpenAI SDK，避免依赖真实的API调用。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from typing import cast, Any

import pytest

from src.kernel.llm import (
    Image,
    LLMPayload,
    ReasoningText,
    ROLE,
    Text,
    ToolCall,
    ToolResult,
)


class TestIsDataUrl:
    """测试_is_data_url函数。"""

    def test_data_url_with_png(self):
        """测试PNG data URL。"""
        from src.kernel.llm.model_client.openai_client import _is_data_url

        assert _is_data_url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA")

    def test_data_url_with_jpeg(self):
        """测试JPEG data URL。"""
        from src.kernel.llm.model_client.openai_client import _is_data_url

        assert _is_data_url("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD")

    def test_not_data_url(self):
        """测试非data URL。"""
        from src.kernel.llm.model_client.openai_client import _is_data_url

        assert not _is_data_url("https://example.com/image.png")
        assert not _is_data_url("/path/to/image.png")
        assert not _is_data_url("image.png")


class TestImageToDataUrl:
    """测试_image_to_data_url函数。"""

    def test_base64_format(self):
        """测试base64|格式转换。"""
        from src.kernel.llm.model_client.openai_client import _image_to_data_url

        b64_data = "iVBORw0KGgoAAAANSUhEUgAAAAUA"
        result = _image_to_data_url(f"base64|{b64_data}")

        assert result.startswith("data:image/png;base64,")
        assert b64_data in result

    def test_already_data_url(self):
        """测试已经是data URL格式。"""
        from src.kernel.llm.model_client.openai_client import _image_to_data_url

        url = "data:image/png;base64,iVBORw0KGgo"
        result = _image_to_data_url(url)

        assert result == url

    def test_file_not_found(self):
        """测试文件不存在。"""
        from src.kernel.llm.model_client.openai_client import _image_to_data_url

        with pytest.raises(FileNotFoundError, match="Image file not found"):
            _image_to_data_url("/nonexistent/file.png")

    @patch("src.kernel.llm.model_client.openai_client.Path")
    def test_valid_file_path(self, mock_path):
        """测试有效的文件路径。"""
        from src.kernel.llm.model_client.openai_client import _image_to_data_url

        # 创建mock文件
        mock_file = Mock()
        mock_file.exists.return_value = True
        mock_file.is_file.return_value = True
        mock_file.read_bytes.return_value = b"fake_image_data"
        mock_path.return_value = mock_file

        result = _image_to_data_url("/fake/path/image.png")

        assert result.startswith("data:image/png;base64,")
        # 验证base64编码
        assert "fake_image_data" not in result  # 应该被编码


class TestPayloadsToOpenAIMessages:
    """测试_payloads_to_openai_messages函数。"""

    def test_user_text_message(self):
        """测试用户文本消息。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [LLMPayload(ROLE.USER, Text("Hello"))]
        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert len(tools) == 0

    def test_assistant_message(self):
        """测试助手消息。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [LLMPayload(ROLE.ASSISTANT, Text("Hi there"))]
        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "Hi there"

    def test_assistant_message_with_reasoning_content(self):
        """测试助手消息会保留 reasoning_content。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [
            LLMPayload(ROLE.ASSISTANT, [ReasoningText("先想一下"), Text("再回答")])
        ]
        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert len(tools) == 0
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "再回答"
        assert messages[0]["reasoning_content"] == "先想一下"

    def test_multimodal_content(self):
        """测试多模态内容（文本+图片）。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [
            LLMPayload(
                ROLE.USER,
                [Text("What's in this image?"), Image("base64|aGVsbG8=")],
            )
        ]
        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"

    def test_tool_payload(self):
        """测试工具定义payload。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        class MockTool:
            @classmethod
            def to_schema(cls):
                return {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object"},
                }

        payloads = [LLMPayload(ROLE.TOOL, MockTool)]
        messages, tools = _payloads_to_openai_messages(payloads)

        # TOOL payload不应该进入messages
        assert len(messages) == 0
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "test_tool"

    def test_tool_result_payload(self):
        """测试工具结果payload。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        result = ToolResult(value="42", call_id="call_123", name="calculator")
        payloads = [LLMPayload(ROLE.TOOL_RESULT, [result])]

        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["content"] == "42"
        assert messages[0]["tool_call_id"] == "call_123"

    def test_tool_result_with_text(self):
        """测试工具结果包含Text。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [
            LLMPayload(
                ROLE.TOOL_RESULT,
                [ToolResult(value="result", call_id="call_123"), Text("extra")],
            )
        ]

        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        # 应该使用ToolResult的内容
        assert messages[0]["content"] == "result"

    def test_multiple_tool_results_in_one_payload(self):
        """同一个 TOOL_RESULT payload 内包含多个 ToolResult 时应拆分为多条 tool message。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [
            LLMPayload(
                ROLE.TOOL_RESULT,
                [
                    ToolResult(value="a", call_id="call_a"),
                    ToolResult(value="b", call_id="call_b"),
                ],
            )
        ]

        messages, tools = _payloads_to_openai_messages(payloads)

        assert tools == []
        assert len(messages) == 2
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "call_a"
        assert messages[0]["content"] == "a"
        assert messages[1]["role"] == "tool"
        assert messages[1]["tool_call_id"] == "call_b"
        assert messages[1]["content"] == "b"

    def test_custom_object_with_to_text(self):
        """测试自定义对象带to_text方法。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages
        from src.kernel.llm.payload.content import Content

        class CustomResult(Content):
            call_id = "custom_call"

            def to_text(self):
                return "custom result"

        payloads = [LLMPayload(ROLE.TOOL_RESULT, [CustomResult()])]

        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert messages[0]["content"] == "custom result"
        assert messages[0]["tool_call_id"] == "custom_call"

    def test_empty_tool_result_content(self):
        """测试空的工具结果内容。"""
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages
        from src.kernel.llm.payload.content import Content

        class BrokenObject(Content):
            def to_text(self):
                raise Exception("Cannot convert")

        payloads = [LLMPayload(ROLE.TOOL_RESULT, [BrokenObject()])]

        messages, tools = _payloads_to_openai_messages(payloads)

        assert len(messages) == 1
        assert messages[0]["content"] == ""

    def test_history_tool_call_ids_are_canonicalized_for_cache_stability(self):
        from src.kernel.llm.model_client.openai_client import _payloads_to_openai_messages

        payloads = [
            LLMPayload(
                ROLE.ASSISTANT,
                [
                    Text("I'll call a tool"),
                    ToolCall(
                        id="provider-random-call-id",
                        name="calculator",
                        args={"a": 1},
                    ),
                ],
            ),
            LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult(
                    value="42",
                    call_id="provider-random-call-id",
                    name="calculator",
                ),
            ),
        ]

        messages, tools = _payloads_to_openai_messages(payloads)

        assert tools == []
        assert messages[0]["tool_calls"][0]["id"] == "call_0_0"
        assert messages[1]["tool_call_id"] == "call_0_0"
        assert messages[1]["tool_call_id"] != "provider-random-call-id"


class TestSchemaNormalization:
    """测试 schema 归一化逻辑。"""

    def test_normalize_array_and_remove_none_default(self):
        """测试补齐 array.items 并移除 default=None。"""
        from src.kernel.llm.model_client.shared import normalize_schema_for_grammar

        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "default": None,
                }
            },
        }

        normalize_schema_for_grammar(schema)
        tags = schema["properties"]["tags"]
        assert "default" not in tags
        assert tags["items"]["type"] == "string"

    def test_to_openai_tool_normalizes_function_schema(self):
        """测试 _to_openai_tool 会归一化函数参数 schema。"""
        from src.kernel.llm.model_client.openai_client import _to_openai_tool

        class MockTool:
            @classmethod
            def to_schema(cls):
                return {
                    "name": "test_tool",
                    "description": "desc",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tags": {
                                "type": "array",
                                "default": None,
                            }
                        },
                    },
                }

        tool = _to_openai_tool(MockTool)
        tags = tool["function"]["parameters"]["properties"]["tags"]
        assert tags["type"] == "array"
        assert tags["items"]["type"] == "string"
        assert "default" not in tags

    def test_to_openai_tool_keeps_existing_reason_schema(self):
        """测试已有 reason 参数时不重复注入。"""
        from src.kernel.llm.model_client.openai_client import _to_openai_tool

        class MockTool:
            @classmethod
            def to_schema(cls):
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

        tool = _to_openai_tool(MockTool)
        properties = tool["function"]["parameters"]["properties"]
        required = tool["function"]["parameters"]["required"]

        assert properties["reason"]["description"] == "original reason"
        assert required == ["query"]

    def test_to_openai_tool_does_not_inject_reason_when_execute_accepts_it(self):
        """测试 execute 已声明 reason 时不额外注入 schema 参数。"""
        from src.kernel.llm.model_client.openai_client import _to_openai_tool

        class MockTool:
            @classmethod
            def to_schema(cls):
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

        tool = _to_openai_tool(MockTool)
        properties = tool["function"]["parameters"]["properties"]
        required = tool["function"]["parameters"]["required"]

        assert "reason" not in properties
        assert "reason" not in required


class TestExtractUsage:
    """测试 usage 统计字段提取。"""

    def test_extract_usage_supports_raw_response_dict(self):
        """测试兼容顶层返回为原始 JSON dict 的响应。"""
        from src.kernel.llm.model_client.openai_client import _extract_usage

        response = {
            "id": "019bdaa55225ef854b320e9b838f77ce",
            "object": "chat.completion",
            "model": "Pro/zai-org/GLM-4.7",
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 1540,
                "total_tokens": 1555,
                "completion_tokens_details": {"reasoning_tokens": 1190},
                "prompt_tokens_details": {"cached_tokens": 0},
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 15,
            },
        }

        result = _extract_usage(response)

        assert result["prompt_tokens"] == 15
        assert result["completion_tokens"] == 1540
        assert result["total_tokens"] == 1555
        assert result["cache_hit_tokens"] == 0
        assert result["cache_miss_tokens"] == 15

    def test_extract_usage_supports_dict_details(self):
        """测试兼容供应商返回 dict 形式的 prompt_tokens_details。"""
        from src.kernel.llm.model_client.openai_client import _extract_usage_from_obj

        usage = {
            "prompt_tokens": 300,
            "completion_tokens": 20,
            "total_tokens": 320,
            "prompt_tokens_details": {"cached_tokens": 120},
        }

        result = _extract_usage_from_obj(usage)

        assert result["prompt_tokens"] == 300
        assert result["completion_tokens"] == 20
        assert result["total_tokens"] == 320
        assert result["cache_hit_tokens"] == 120
        assert result["cache_miss_tokens"] == 180

    def test_extract_usage_supports_input_tokens_details_alias(self):
        """测试兼容 input_tokens_details.cached_tokens 别名。"""
        from src.kernel.llm.model_client.openai_client import _extract_usage_from_obj

        class Usage:
            prompt_tokens = 90
            completion_tokens = 10
            total_tokens = 100
            input_tokens_details = {"cached_tokens": 30}

        result = _extract_usage_from_obj(Usage())

        assert result["cache_hit_tokens"] == 30
        assert result["cache_miss_tokens"] == 60


class TestOpenAIChatClient:
    """测试OpenAIChatClient类。"""

    def test_init(self):
        """测试初始化。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        client = OpenAIChatClient()

        assert client._clients == {}
        assert hasattr(client, "_lock")

    def test_get_loop_key_with_running_loop(self):
        """测试获取事件循环key（有运行中的循环）。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        client = OpenAIChatClient()

        async def test():
            loop_key = client._get_loop_key()
            assert loop_key > 0
            return loop_key

        loop_key = asyncio.run(test())
        assert isinstance(loop_key, int)

    def test_get_loop_key_without_loop(self):
        """测试获取事件循环key（无运行中的循环）。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        client = OpenAIChatClient()

        # 在没有运行循环的情况下调用
        loop_key = client._get_loop_key()
        assert loop_key == 0

    @pytest.mark.asyncio
    async def test_create_non_streaming_response(self):
        """测试非流式响应。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        # 创建mock客户端
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Hello, world!"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}  # 清空缓存
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [LLMPayload(ROLE.USER, Text("Hi"))]
        model_set = {
            "api_key": "test-key",
            "base_url": "https://api.test.com",
            "timeout": 30.0,
            "max_tokens": 100,
            "temperature": 0.7,
            "extra_params": {},
        }

        message, tool_calls, stream_iter, reasoning_content, usage = await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        assert message == "Hello, world!"
        assert tool_calls == []
        assert stream_iter is None
        assert reasoning_content is None
        assert usage is not None

    @pytest.mark.asyncio
    async def test_create_with_tool_calls(self):
        """测试包含工具调用的响应。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        # 创建mock工具调用
        mock_tc1 = MagicMock()
        mock_tc1.id = "call_123"
        mock_tc1.function.name = "calculator"
        mock_tc1.function.arguments = '{"a": 1, "b": 2}'

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = None
        mock_completion.choices[0].message.tool_calls = [mock_tc1]

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [LLMPayload(ROLE.USER, Text("Calculate 1+2"))]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        }

        message, tool_calls, stream_iter, reasoning_content, usage = await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        assert message == ""
        assert tool_calls is not None
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call_123"
        assert tool_calls[0]["name"] == "calculator"
        assert tool_calls[0]["args"] == {"a": 1, "b": 2}
        assert reasoning_content is None
        assert usage is not None
        assert reasoning_content is None

    @pytest.mark.asyncio
    async def test_create_omits_tool_choice_without_tool_payloads(self):
        """测试未传工具 payload 时不会透传 tool_choice。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Hello, world!"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [LLMPayload(ROLE.USER, Text("Hi"))]
        model_set = {
            "api_key": "test-key",
            "base_url": "https://api.test.com",
            "timeout": 30.0,
            "extra_params": {"tool_choice": "auto"},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        await_args = mock_chat.completions.create.await_args
        assert await_args is not None
        create_kwargs = await_args.kwargs
        assert "tools" not in create_kwargs
        assert "tool_choice" not in create_kwargs

    @pytest.mark.asyncio
    async def test_create_keeps_configured_tool_choice_with_tool_payloads(self):
        """测试传入工具 payload 时保留配置的 tool_choice。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        class MockTool:
            @classmethod
            def to_schema(cls):
                return {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object"},
                }

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Hello, world!"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.TOOL, MockTool),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": "https://api.test.com",
            "timeout": 30.0,
            "extra_params": {"tool_choice": "auto"},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        await_args = mock_chat.completions.create.await_args
        assert await_args is not None
        create_kwargs = await_args.kwargs
        assert create_kwargs["tool_choice"] == "auto"
        assert create_kwargs["tools"][0]["function"]["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_create_invalid_model_set_type(self):
        """测试无效的model_set类型。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        client = OpenAIChatClient()

        with pytest.raises(TypeError, match="OpenAIChatClient 期望 model_set 为单个模型配置 dict"):
            await client.create(
                model_name="gpt-4",
                payloads=[],
                tools=[],
                request_name="test",
                model_set="not_a_dict",  # type: ignore
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_create_missing_api_key(self):
        """测试缺少api_key。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        client = OpenAIChatClient()
        model_set = {"api_key": ""}  # 空api_key

        with pytest.raises(ValueError, match="model.api_key 不能为空"):
            await client.create(
                model_name="gpt-4",
                payloads=[],
                tools=[],
                request_name="test",
                model_set=model_set,
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_create_invalid_extra_params(self):
        """测试无效的extra_params。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": "invalid",  # 不是dict
        }

        with pytest.raises(ValueError, match="model.extra_params 必须是 dict"):
            await client.create(
                model_name="gpt-4",
                payloads=[],
                tools=[],
                request_name="test",
                model_set=model_set,
                stream=False,
            )

    @pytest.mark.asyncio
    async def test_create_with_extra_params(self):
        """测试包含额外参数。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Response"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [LLMPayload(ROLE.USER, Text("Hi"))]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {"top_p": 0.9, "presence_penalty": 0.1},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        # 验证额外参数被传递
        call_kwargs = mock_chat.completions.create.call_args.kwargs
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["presence_penalty"] == 0.1

    @pytest.mark.asyncio
    async def test_create_inject_reasoning_content_for_thinking_tool_calls(self):
        """测试开启 thinking 时为 tool_calls 消息补齐 reasoning_content。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(
                ROLE.ASSISTANT,
                [
                    Text("I'll call a tool"),
                    ToolCall(id="call_1", name="calculator", args={"a": 1}),
                ],
            ),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {
                "enable_thinking": True,
                "reasoning_history_mode": "deepseek",
            },
        }

        await client.create(
            model_name="moonshotai/kimi-k2.5",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        tool_call_message = messages[1]
        assert tool_call_message["role"] == "assistant"
        assert "tool_calls" in tool_call_message
        assert "reasoning_content" in tool_call_message
        assert tool_call_message["reasoning_content"] == tool_call_message["content"]

    @pytest.mark.asyncio
    async def test_create_backfills_reasoning_content_when_history_already_contains_it(self):
        """测试当上下文中已有 reasoning_content 并开启 auto 模式时，会为其他 assistant 历史回填。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.ASSISTANT, [ReasoningText("先思考"), Text("已有思考回复")]),
            LLMPayload(
                ROLE.ASSISTANT,
                [
                    Text("I'll call a tool"),
                    ToolCall(id="call_1", name="calculator", args={"a": 1}),
                ],
            ),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {"reasoning_history_mode": "auto"},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        first_assistant_message = messages[1]
        tool_call_message = messages[2]
        assert first_assistant_message["reasoning_content"] == "先思考"
        assert tool_call_message["role"] == "assistant"
        assert tool_call_message["reasoning_content"] == tool_call_message["content"]

    @pytest.mark.asyncio
    async def test_create_does_not_backfill_reasoning_content_without_reasoning_history(self):
        """测试当历史中没有 reasoning_content 时，不会额外回填。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(
                ROLE.ASSISTANT,
                [
                    Text("I'll call a tool"),
                    ToolCall(id="call_1", name="calculator", args={"a": 1}),
                ],
            ),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        tool_call_message = messages[1]
        assert tool_call_message["role"] == "assistant"
        assert "reasoning_content" not in tool_call_message

    @pytest.mark.asyncio
    async def test_create_preserves_reasoning_content_from_completion_message(self):
        """测试非流式响应中的 reasoning_content 会被返回。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "最终回答"
        mock_completion.choices[0].message.reasoning_content = "中间思考"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        result = await client.create(
            model_name="moonshotai/kimi-k2.5",
            payloads=[LLMPayload(ROLE.USER, Text("Hi"))],
            tools=[],
            request_name="test",
            model_set={
                "api_key": "test-key",
                "base_url": None,
                "timeout": None,
                "max_tokens": None,
                "temperature": None,
                "extra_params": {"enable_thinking": True},
            },
            stream=False,
        )

        assert result[0] == "最终回答"
        assert result[3] == "中间思考"

    @pytest.mark.asyncio
    async def test_default_tool_choice_is_auto_for_deepseek(self):
        """测试 DeepSeek 场景下默认 tool_choice 为 auto（避免 required 触发 grammar 编译失败）。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        class MockTool:
            @classmethod
            def to_schema(cls):
                return {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "required": ["x"],
                    },
                }

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Response"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.TOOL, MockTool),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        }

        await client.create(
            model_name="deepseek-ai/deepseek-v3.1",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        assert call_kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_default_tool_choice_is_required_for_non_deepseek(self):
        """测试非 DeepSeek 提供商默认 tool_choice 也为 auto。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        class MockTool:
            @classmethod
            def to_schema(cls):
                return {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object"},
                }

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "Response"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.TOOL, MockTool),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        assert call_kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_create_with_tool_call_compat_prompt_and_parsing(self):
        """测试 tool_call_compat 模式注入提示词并解析 JSON 返回。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        class MockTool:
            @classmethod
            def to_schema(cls):
                return {
                    "name": "calculator",
                    "description": "calc",
                    "parameters": {"type": "object", "properties": {"a": {"type": "number"}}},
                }

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "{'tool_calls':[{'name':'calculator','args':{'a':1}}]}"
        mock_completion.choices[0].message.tool_calls = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("请计算")),
            LLMPayload(ROLE.TOOL, MockTool),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "tool_call_compat": True,
            "extra_params": {},
        }

        message, tool_calls, _, reasoning_content, usage = await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        assert "tools" not in call_kwargs
        assert isinstance(call_kwargs["messages"][-1]["content"], str)
        assert "请只返回一个 JSON 对象" in call_kwargs["messages"][-1]["content"]
        assert "可用工具 schema" in call_kwargs["messages"][-1]["content"]

        assert message == ""
        assert tool_calls is not None
        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "calculator"
        assert tool_calls[0]["args"] == {"a": 1}
        assert usage is not None
        assert reasoning_content is None

    @pytest.mark.asyncio
    async def test_stream_iterator_closes_underlying_stream_on_early_stop(self):
        """测试流式响应提前停止消费时会关闭底层流对象。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        class FakeStream:
            def __init__(self):
                self._index = 0
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._index > 0:
                    raise StopAsyncIteration
                self._index += 1

                delta = MagicMock()
                delta.content = "hello"
                delta.tool_calls = None
                delta.function_call = None

                choice = MagicMock()
                choice.delta = delta

                chunk = MagicMock()
                chunk.choices = [choice]
                return chunk

            async def aclose(self):
                self.closed = True

        fake_stream = FakeStream()

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=fake_stream)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [LLMPayload(ROLE.USER, Text("Hi"))]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": 30.0,
            "max_tokens": 100,
            "temperature": 0.7,
            "extra_params": {},
        }

        message, tool_calls, stream_iter, reasoning_content, usage = await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=True,
        )

        assert message is None
        assert tool_calls is None
        assert stream_iter is not None
        assert reasoning_content is None
        assert usage is None

        event = await anext(stream_iter)
        assert event.text_delta == "hello"

        await cast(Any, stream_iter).aclose()
        assert fake_stream.closed is True


class TestThinkingEnabled:
    """测试 _thinking_enabled 对 reasoning 相关参数的识别。"""

    def test_enable_thinking(self):
        """测试 enable_thinking=True 被识别。"""
        from src.kernel.llm.model_client.openai_client import _thinking_enabled

        assert _thinking_enabled({"extra_params": {"enable_thinking": True}}) is True

    def test_reasoning_effort_high(self):
        """测试 reasoning_effort=high 被识别。"""
        from src.kernel.llm.model_client.openai_client import _thinking_enabled

        assert _thinking_enabled({"extra_params": {"reasoning_effort": "high"}}) is True

    def test_reasoning_effort_medium(self):
        """测试 reasoning_effort=medium 被识别。"""
        from src.kernel.llm.model_client.openai_client import _thinking_enabled

        assert _thinking_enabled({"extra_params": {"reasoning_effort": "medium"}}) is True

    def test_reasoning_effort_none(self):
        """测试 reasoning_effort=none 不开启 thinking。"""
        from src.kernel.llm.model_client.openai_client import _thinking_enabled

        assert _thinking_enabled({"extra_params": {"reasoning_effort": "none"}}) is False

    def test_reasoning_dict_effort(self):
        """测试 reasoning.effort 被识别。"""
        from src.kernel.llm.model_client.openai_client import _thinking_enabled

        assert _thinking_enabled({"extra_params": {"reasoning": {"effort": "high"}}}) is True

    def test_no_extra_params(self):
        """测试无额外参数时默认不开启。"""
        from src.kernel.llm.model_client.openai_client import _thinking_enabled

        assert _thinking_enabled({}) is False


class TestReasoningHistoryMode:
    """测试 reasoning_history_mode 对 reasoning_content 历史字段的控制。"""

    @pytest.mark.asyncio
    async def test_default_mode_strips_reasoning_content(self):
        """默认 none 模式下应移除 assistant 历史中的 reasoning_content。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.ASSISTANT, [ReasoningText("think"), Text("answer")]),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "timeout": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {},
        }

        await client.create(
            model_name="gpt-4",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[1]["role"] == "assistant"
        assert "reasoning_content" not in messages[1]

    @pytest.mark.asyncio
    async def test_deepseek_mode_keeps_and_backfills_reasoning_content(self):
        """deepseek 模式下应保留并回填 assistant 历史的 reasoning_content。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.ASSISTANT, [ReasoningText("think"), Text("answer")]),
            LLMPayload(ROLE.ASSISTANT, [Text("plain")]),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {"reasoning_history_mode": "deepseek"},
        }

        await client.create(
            model_name="deepseek-chat",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[1]["role"] == "assistant"
        assert messages[1]["reasoning_content"] == "think"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["reasoning_content"] == "plain"

    @pytest.mark.asyncio
    async def test_kimi_mode_backfills_reasoning_content(self):
        """kimi 模式下同样会回填 assistant 历史的 reasoning_content。"""
        from src.kernel.llm.model_client.openai_client import OpenAIChatClient

        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = "OK"
        mock_completion.choices[0].message.tool_calls = None
        mock_completion.usage = None

        mock_chat = AsyncMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_openai_client = MagicMock()
        mock_openai_client.chat.completions.create = mock_chat.completions.create

        client = OpenAIChatClient()
        client._clients = {}
        client._get_client = MagicMock(return_value=mock_openai_client)

        payloads = [
            LLMPayload(ROLE.USER, Text("Hi")),
            LLMPayload(ROLE.ASSISTANT, [Text("plain")]),
        ]
        model_set = {
            "api_key": "test-key",
            "base_url": None,
            "max_tokens": None,
            "temperature": None,
            "extra_params": {"reasoning_history_mode": "kimi"},
        }

        await client.create(
            model_name="moonshotai/kimi-k2.5",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        call_kwargs = mock_chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[1]["role"] == "assistant"
        assert messages[1]["reasoning_content"] == "plain"


@pytest.mark.asyncio
async def test_create_keeps_stream_options_as_top_level_param():
    """测试 stream_options 作为顶层参数传递，不会被塞进 extra_body。"""
    from src.kernel.llm.model_client.openai_client import OpenAIChatClient

    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].message.content = "OK"
    mock_completion.choices[0].message.tool_calls = None
    mock_completion.usage = None

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)

    mock_openai_client = MagicMock()
    mock_openai_client.chat.completions.create = mock_chat.completions.create

    client = OpenAIChatClient()
    client._clients = {}
    client._get_client = MagicMock(return_value=mock_openai_client)

    payloads = [LLMPayload(ROLE.USER, Text("Hi"))]
    model_set = {
        "api_key": "test-key",
        "base_url": None,
        "timeout": None,
        "max_tokens": None,
        "temperature": None,
        "extra_params": {"stream_options": {"include_usage": False}},
    }

    await client.create(
        model_name="gpt-4",
        payloads=payloads,
        tools=[],
        request_name="test",
        model_set=model_set,
        stream=False,
    )

    call_kwargs = mock_chat.completions.create.call_args.kwargs
    assert call_kwargs["stream_options"] == {"include_usage": False}
    assert "stream_options" not in call_kwargs.get("extra_body", {})

