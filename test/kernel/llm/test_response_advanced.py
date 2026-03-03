"""LLMResponse 高级功能测试。

测试覆盖：
1. stream_with_callback 方法
2. stream_with_buffer 方法
3. _ToolCallAccumulator 类
4. 各种边缘情况
5. 流式响应不完整问题修复的回归测试：
   - Bug 1/2：_ToolCallAccumulator 处理后续 chunk tool_call_id 为 None 的情况
   - Bug 3：__aiter__ 流异常时已收集内容不丢失
   - Bug 4：_collect_full_response 流异常时已收集内容不丢失
"""

import pytest

from src.kernel.llm.exceptions import LLMError
from src.kernel.llm.context import LLMContextManager
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import LLMPayload, Text, ToolResult
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.response import LLMResponse, _ToolCallAccumulator
from src.kernel.llm.roles import ROLE


def dummy_model_set():
    return [
        {
            "api_provider": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "dummy",
            "api_key": "dummy-key",
            "client_type": "openai",
            "max_retry": 0,
            "timeout": 1,
            "retry_interval": 0,
            "price_in": 0.0,
            "price_out": 0.0,
            "temperature": 0.1,
            "max_tokens": 10,
            "extra_params": {},
        }
    ]


async def _stream_text_events():
    """生成文本流事件。"""
    yield StreamEvent(text_delta="hel")
    yield StreamEvent(text_delta="lo")
    yield StreamEvent(text_delta=" world")


async def _stream_tool_events():
    """生成工具调用流事件。"""
    yield StreamEvent(tool_call_id="call_123", tool_name="calculator", tool_args_delta='{"a":')
    yield StreamEvent(tool_call_id="call_123", tool_args_delta=' 1, "b":')
    yield StreamEvent(tool_call_id="call_123", tool_args_delta=' 2}')


async def _stream_mixed_events():
    """生成混合流事件。"""
    yield StreamEvent(text_delta="Thinking")
    yield StreamEvent(tool_call_id="call_456", tool_name="search", tool_args_delta='{"query":')
    yield StreamEvent(text_delta="...")
    yield StreamEvent(tool_call_id="call_456", tool_args_delta=' "test"}')


@pytest.mark.asyncio
async def test_stream_with_callback():
    """测试流式响应 + 实时回调。"""
    chunks = []

    async def callback(chunk: str):
        chunks.append(chunk)

    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=_stream_text_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    result = await resp.stream_with_callback(callback)

    assert result == "hello world"
    assert chunks == ["hel", "lo", " world"]
    assert resp.message == "hello world"


@pytest.mark.asyncio
async def test_stream_with_callback_no_stream():
    """测试stream_with_callback处理非流式响应。"""
    chunks = []

    async def callback(chunk: str):
        chunks.append(chunk)

    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="static message",
    )

    result = await resp.stream_with_callback(callback)

    assert result == "static message"
    assert chunks == ["static message"]


@pytest.mark.asyncio
async def test_stream_with_callback_consumed_error():
    """测试stream_with_callback在已消费响应时抛出异常。"""
    async def callback(chunk: str):
        pass

    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=_stream_text_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    await resp.stream_with_callback(callback)

    with pytest.raises(Exception):  # LLMResponseConsumedError
        await resp.stream_with_callback(callback)


@pytest.mark.asyncio
async def test_stream_with_buffer():
    """测试带缓冲的流式响应。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=_stream_text_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    buffers = []
    async for chunk in resp.stream_with_buffer(buffer_size=5):
        buffers.append(chunk)

    # "hel" (3) + "lo" (2) = 5，应该先yield
    # " world" (6) 单独yield
    assert len(buffers) == 2
    assert buffers[0] == "hello"
    assert buffers[1] == " world"
    assert resp.message == "hello world"


@pytest.mark.asyncio
async def test_stream_with_buffer_no_stream():
    """测试stream_with_buffer处理非流式响应。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="static message",
    )

    buffers = []
    async for chunk in resp.stream_with_buffer(buffer_size=5):
        buffers.append(chunk)

    assert len(buffers) == 1
    assert buffers[0] == "static message"


@pytest.mark.asyncio
async def test_stream_with_buffer_consumed_error():
    """测试stream_with_buffer在已消费响应时抛出异常。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=_stream_text_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    async for _ in resp.stream_with_buffer(buffer_size=5):
        pass

    with pytest.raises(Exception):  # LLMResponseConsumedError
        async for _ in resp.stream_with_buffer(buffer_size=5):
            pass


@pytest.mark.asyncio
async def test_tool_call_accumulator_single_call():
    """测试工具调用累加器（单个工具调用）。"""
    acc = _ToolCallAccumulator()

    async def events():
        yield StreamEvent(tool_call_id="call_1", tool_name="calc", tool_args_delta='{"a":')
        yield StreamEvent(tool_call_id="call_1", tool_args_delta='1}')

    async for event in events():
        acc.apply(event)

    calls = acc.finalize()
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "calc"
    assert calls[0].args == {"a": 1}


@pytest.mark.asyncio
async def test_tool_call_accumulator_multiple_calls():
    """测试工具调用累加器（多个工具调用）。"""
    acc = _ToolCallAccumulator()

    async def events():
        yield StreamEvent(tool_call_id="call_1", tool_name="tool1", tool_args_delta='{"x":1}')
        yield StreamEvent(tool_call_id="call_2", tool_name="tool2", tool_args_delta='{"y":2}')

    async for event in events():
        acc.apply(event)

    calls = acc.finalize()
    assert len(calls) == 2
    assert calls[0].id == "call_1"
    assert calls[0].name == "tool1"
    assert calls[1].id == "call_2"
    assert calls[1].name == "tool2"


@pytest.mark.asyncio
async def test_tool_call_accumulator_invalid_json():
    """测试工具调用累加器处理无效JSON。"""
    acc = _ToolCallAccumulator()

    async def events():
        yield StreamEvent(tool_call_id="call_1", tool_name="calc", tool_args_delta="not json")

    async for event in events():
        acc.apply(event)

    calls = acc.finalize()
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].args == "not json"  # 应该保持原始字符串


@pytest.mark.asyncio
async def test_tool_call_accumulator_empty_args():
    """测试工具调用累加器处理空参数。"""
    acc = _ToolCallAccumulator()

    async def events():
        yield StreamEvent(tool_call_id="call_1", tool_name="calc")

    async for event in events():
        acc.apply(event)

    calls = acc.finalize()
    assert len(calls) == 1
    assert calls[0].args == {}


@pytest.mark.asyncio
async def test_response_collects_tool_calls_from_stream():
    """测试响应从流中收集工具调用。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=_stream_tool_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    await resp

    assert len(resp.call_list) == 1
    assert resp.call_list[0].id == "call_123"
    assert resp.call_list[0].name == "calculator"
    assert resp.call_list[0].args == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_response_collects_mixed_stream():
    """测试响应收集混合流（文本+工具调用）。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=_stream_mixed_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    chunks = []
    async for chunk in resp:
        chunks.append(chunk)

    assert chunks == ["Thinking", "..."]
    assert resp.message == "Thinking..."
    assert len(resp.call_list) == 1
    assert resp.call_list[0].name == "search"


@pytest.mark.asyncio
async def test_response_tool_call_compat_stream_repair_success():
    """测试 tool_call_compat 在流式文本 JSON 下可修复并转为 tool call。"""

    async def compat_events():
        yield StreamEvent(text_delta="{'tool_calls':")
        yield StreamEvent(text_delta="[{'name':'search','args':{'query':'neo'}}]}")

    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=compat_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        tool_call_compat=True,
    )

    await resp

    assert resp.call_list is not None
    assert len(resp.call_list) == 1
    assert resp.call_list[0].name == "search"
    assert resp.call_list[0].args == {"query": "neo"}


@pytest.mark.asyncio
async def test_response_tool_call_compat_stream_repair_fail_raises():
    """测试 tool_call_compat JSON repair 失败时抛错。"""

    async def broken_events():
        yield StreamEvent(text_delta="<not-json>")

    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=broken_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        tool_call_compat=True,
    )

    with pytest.raises(LLMError):
        await resp


@pytest.mark.asyncio
async def test_response_to_payload():
    """测试to_payload方法。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="test message",
    )

    payload = resp.to_payload()
    assert payload.role == ROLE.ASSISTANT
    assert payload.content == [Text("test message")]


@pytest.mark.asyncio
async def test_response_add_payload():
    """测试add_payload方法。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("first"))],
        model_set=req.model_set,
    )

    new_payload = LLMPayload(ROLE.USER, Text("second"))
    resp.add_payload(new_payload)

    assert len(resp.payloads) == 1
    assert resp.payloads[0].role == ROLE.USER
    assert resp.payloads[0].content == [Text("first"), Text("second")]


@pytest.mark.asyncio
async def test_response_add_payload_with_position():
    """测试add_payload方法指定位置。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[
            LLMPayload(ROLE.USER, Text("first")),
            LLMPayload(ROLE.USER, Text("third")),
        ],
        model_set=req.model_set,
    )

    new_payload = LLMPayload(ROLE.USER, Text("second"))
    resp.add_payload(new_payload, position=1)

    assert len(resp.payloads) == 3
    assert resp.payloads[1] == new_payload


@pytest.mark.asyncio
async def test_response_add_call_reflex():
    """测试add_call_reflex方法。"""
    req = LLMRequest(dummy_model_set(), request_name="test")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    results = [
        LLMPayload(ROLE.TOOL_RESULT, Text("result1")),
        LLMPayload(ROLE.TOOL_RESULT, Text("result2")),
    ]

    resp.add_call_reflex(results)

    assert len(resp.payloads) == 1
    assert all(payload.role != ROLE.TOOL_RESULT for payload in resp.payloads)


@pytest.mark.asyncio
async def test_response_add_payload_uses_context_manager_add_payload() -> None:
    """测试 response.add_payload 委托给 context_manager.add_payload。"""

    class SpyManager(LLMContextManager):
        def __init__(self) -> None:
            super().__init__(max_payloads=20)
            self.called = False

        def add_payload(self, payloads, payload, position=None):
            self.called = True
            return super().add_payload(payloads, payload, position=position)

    manager = SpyManager()
    req = LLMRequest(dummy_model_set(), request_name="test", context_manager=manager)
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("first"))],
        model_set=req.model_set,
        context_manager=manager,
    )

    resp.add_payload(LLMPayload(ROLE.ASSISTANT, Text("second")))

    assert manager.called is True


@pytest.mark.asyncio
async def test_response_add_call_reflex_uses_context_manager_add_payload() -> None:
    """测试 response.add_call_reflex 逐条委托给 context_manager.add_payload。"""

    class SpyManager(LLMContextManager):
        def __init__(self) -> None:
            super().__init__(max_payloads=20)
            self.called_count = 0

        def add_payload(self, payloads, payload, position=None):
            self.called_count += 1
            return super().add_payload(payloads, payload, position=position)

    manager = SpyManager()
    req = LLMRequest(dummy_model_set(), request_name="test", context_manager=manager)
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("first"))],
        model_set=req.model_set,
        context_manager=manager,
    )

    resp.add_call_reflex(
        [
            LLMPayload(ROLE.TOOL_RESULT, ToolResult(value="ok", call_id="call_1")),
            LLMPayload(ROLE.TOOL_RESULT, ToolResult(value="ok", call_id="call_2")),
        ]
    )

    assert manager.called_count == 2


# =============================================================================
# 流式响应不完整问题修复 — 回归测试
# =============================================================================

# --- Bug 1/2：_ToolCallAccumulator 后续 chunk tool_call_id 为 None ---

@pytest.mark.asyncio
async def test_accumulator_subsequent_chunks_no_id():
    """Bug 1/2 回归：后续 chunk tool_call_id 为 None 时，args 应正确归属到上一个工具调用。

    OpenAI 流式协议：首包携带 id+name，后续包 id 为 None，只含 args 增量。
    修复前：后续包因 id 为 None 被直接丢弃，导致 args 不完整。
    """
    acc = _ToolCallAccumulator()

    # 模拟 OpenAI 真实流：首包有 id，后续包 id 为 None
    events = [
        StreamEvent(tool_call_id="call_abc", tool_name="get_weather", tool_args_delta='{"loc'),
        StreamEvent(tool_call_id=None, tool_args_delta='ation":'),   # id 为 None
        StreamEvent(tool_call_id=None, tool_args_delta='"Tokyo"}'),  # id 为 None
    ]
    for ev in events:
        acc.apply(ev)

    calls = acc.finalize()
    assert len(calls) == 1
    assert calls[0].id == "call_abc"
    assert calls[0].name == "get_weather"
    assert calls[0].args == {"location": "Tokyo"}


@pytest.mark.asyncio
async def test_accumulator_multiple_calls_subsequent_no_id():
    """Bug 1/2 回归：多工具调用时后续 chunk 无 id，应正确切换归属。

    首包切换到新工具时带新 id，后续包 id 为 None 应归属到当前工具。
    """
    acc = _ToolCallAccumulator()

    events = [
        # 工具 1
        StreamEvent(tool_call_id="call_1", tool_name="tool_a", tool_args_delta='{"x":'),
        StreamEvent(tool_call_id=None, tool_args_delta="1}"),
        # 工具 2（新 id，触发切换）
        StreamEvent(tool_call_id="call_2", tool_name="tool_b", tool_args_delta='{"y":'),
        StreamEvent(tool_call_id=None, tool_args_delta="2}"),
    ]
    for ev in events:
        acc.apply(ev)

    calls = acc.finalize()
    assert len(calls) == 2
    assert calls[0].id == "call_1"
    assert calls[0].args == {"x": 1}
    assert calls[1].id == "call_2"
    assert calls[1].args == {"y": 2}


@pytest.mark.asyncio
async def test_accumulator_no_id_no_history_discarded():
    """Bug 1/2 回归：既无新 id 又无历史 id 时，事件应被安全丢弃，不抛异常。"""
    acc = _ToolCallAccumulator()
    # 直接发送无 id 事件（历史也为空）
    acc.apply(StreamEvent(tool_call_id=None, tool_args_delta='{"x":1}'))
    calls = acc.finalize()
    assert calls == []


# --- Bug 3：__aiter__ 流异常时已收集内容不丢失 ---

@pytest.mark.asyncio
async def test_aiter_stream_error_content_preserved():
    """Bug 3 回归：async for 消费流时流中途抛异常，已收集的文本应保存到 message。

    修复前：异常直接向上传播，message 和 call_list 未被赋值，响应内容丢失。
    """
    async def flaky_stream():
        yield StreamEvent(text_delta="Hello")
        yield StreamEvent(text_delta=" world")
        raise RuntimeError("connection reset by peer")

    req = LLMRequest(dummy_model_set(), request_name="test")  # type: ignore[arg-type]
    resp = LLMResponse(
        _stream=flaky_stream(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,  # type: ignore[arg-type]
    )

    collected: list[str] = []
    with pytest.raises(RuntimeError, match="connection reset by peer"):
        async for chunk in resp:
            collected.append(chunk)

    # 已收到的内容必须保存，不能丢失
    assert collected == ["Hello", " world"]
    assert resp.message == "Hello world"


@pytest.mark.asyncio
async def test_aiter_stream_error_tool_calls_preserved():
    """Bug 3 回归：async for 消费流时异常，已收集的工具调用应保存到 call_list。"""
    async def flaky_stream():
        yield StreamEvent(tool_call_id="call_x", tool_name="search", tool_args_delta='{"q":"hi"}')
        raise RuntimeError("eof")

    req = LLMRequest(dummy_model_set(), request_name="test")  # type: ignore[arg-type]
    resp = LLMResponse(
        _stream=flaky_stream(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        async for _ in resp:
            pass

    assert resp.call_list is not None
    assert len(resp.call_list) == 1
    assert resp.call_list[0].name == "search"
    assert resp.call_list[0].args == {"q": "hi"}


# --- Bug 4：_collect_full_response（await 模式）流异常时已收集内容不丢失 ---

@pytest.mark.asyncio
async def test_await_stream_error_content_preserved():
    """Bug 4 回归：await 消费流时流中途抛异常，已收集的文本应保存到 message。

    修复前：异常向上传播，message 未被赋值，响应内容丢失。
    """
    async def flaky_stream():
        yield StreamEvent(text_delta="Partial")
        yield StreamEvent(text_delta=" response")
        raise RuntimeError("stream closed")

    req = LLMRequest(dummy_model_set(), request_name="test")  # type: ignore[arg-type]
    resp = LLMResponse(
        _stream=flaky_stream(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="stream closed"):
        await resp

    # 即使异常，已收集内容必须落库
    assert resp.message == "Partial response"


@pytest.mark.asyncio
async def test_await_stream_error_tool_calls_preserved():
    """Bug 4 回归：await 消费流时异常，已收集的工具调用应保存到 call_list。"""
    async def flaky_stream():
        yield StreamEvent(tool_call_id="call_y", tool_name="calc", tool_args_delta='{"a":42}')
        raise RuntimeError("eof")

    req = LLMRequest(dummy_model_set(), request_name="test")  # type: ignore[arg-type]
    resp = LLMResponse(
        _stream=flaky_stream(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        await resp

    assert resp.call_list is not None
    assert len(resp.call_list) == 1
    assert resp.call_list[0].name == "calc"
    assert resp.call_list[0].args == {"a": 42}


@pytest.mark.asyncio
async def test_await_normal_stream_unaffected():
    """Bug 3/4 回归（无错误路径）：正常流在修复后仍能完整收集内容。"""
    async def good_stream():
        yield StreamEvent(text_delta="All")
        yield StreamEvent(text_delta=" good")

    req = LLMRequest(dummy_model_set(), request_name="test")  # type: ignore[arg-type]
    resp = LLMResponse(
        _stream=good_stream(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,  # type: ignore[arg-type]
    )

    result = await resp
    assert result == "All good"
    assert resp.message == "All good"
