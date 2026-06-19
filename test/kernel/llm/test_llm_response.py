from typing import Any

import pytest

from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.response import LLMResponse
from src.kernel.llm.roles import ROLE
from src.kernel.llm.types import ModelEntry, ModelSet


def dummy_model_set() -> ModelSet:
    return [
        ModelEntry(
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
            "max_context": 4096,
            "tool_call_compat": False,
            "extra_params": {},
        }
        )
    ]


async def _stream_events():
    yield StreamEvent(text_delta="hel")
    yield StreamEvent(text_delta="lo")


async def _anthropic_reasoning_stream_events():
    yield StreamEvent(reasoning_block_type="thinking")
    yield StreamEvent(reasoning_delta="step ")
    yield StreamEvent(reasoning_signature_delta="sig_1")
    yield StreamEvent(text_delta="done")


@pytest.mark.asyncio
async def test_response_await_collects_full_message():
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_stream_events(),
        _upper=req,
        _auto_append_response=True,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    text = await resp
    assert text == "hello"
    assert resp.message == "hello"
    assert resp.payloads[-1].role == ROLE.ASSISTANT


@pytest.mark.asyncio
async def test_response_async_for_yields_chunks_and_sets_message():
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_stream_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    chunks = []
    async for c in resp:
        chunks.append(c)

    assert chunks == ["hel", "lo"]
    assert resp.message == "hello"


@pytest.mark.asyncio
async def test_response_cannot_be_consumed_twice():
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_stream_events(),
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    _ = await resp
    with pytest.raises(Exception):
        _ = await resp


@pytest.mark.asyncio
async def test_response_preserves_structured_reasoning_blocks_in_payload() -> None:
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=_anthropic_reasoning_stream_events(),
        _upper=req,
        _auto_append_response=True,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
    )

    text = await resp

    assert text == "done"
    assert resp.reasoning_parts == [ReasoningText("step ", signature="sig_1")]
    assistant_payload = resp.payloads[-1]
    assert assistant_payload.role == ROLE.ASSISTANT
    assert assistant_payload.content[0] == ReasoningText("step ", signature="sig_1")


def test_add_payload_appends_tool_result_after_response_payload() -> None:
    """测试把 tool_result 追加到已写回上下文的 assistant payload 之后。"""
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message=None,
        reasoning_parts=[ReasoningText("step ", signature="sig_1")],
        call_list=[ToolCall(id="toolu_1", name="demo_tool", args={})],
    )

    # 先把当前响应内容写回上下文，再追加 tool_result，避免上下文序列校验失败。
    resp._append_current_response_payload()

    resp.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(value="ok", call_id="toolu_1", name="demo_tool"),
        )
    )

    assert resp.payloads[1].role == ROLE.ASSISTANT
    assert resp.payloads[1].content[0] == ReasoningText("step ", signature="sig_1")
    assert resp.payloads[2].role == ROLE.TOOL_RESULT


@pytest.mark.asyncio
async def test_response_send_inherits_upper_stream_metadata() -> None:
    req = LLMRequest(
        dummy_model_set(),
        request_name="t",
        meta_data={"stream_id": "stream-789", "trace_id": "trace-1"},
    )
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="hello",
    )

    captured: dict[str, Any] = {}

    async def fake_send(
        self: LLMRequest,
        auto_append_response: bool = True,
        *,
        stream: bool = True,
    ) -> LLMResponse:
        captured["meta_data"] = dict(self.meta_data)
        return LLMResponse(
            _stream=None,
            _upper=self,
            _auto_append_response=auto_append_response,
            payloads=list(self.payloads),
            model_set=self.model_set,
            message="done",
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(LLMRequest, "send", fake_send)
        next_resp = await resp.send(stream=False)

    assert next_resp.message == "done"
    assert captured["meta_data"] == {"trace_id": "trace-1", "stream_id": "stream-789"}


def test_response_reasoning_only_state():
    """测试 LLMResponse 能正确识别 reasoning-only 状态。"""
    req = LLMRequest(dummy_model_set(), request_name="t")
    resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="",
        call_list=[],
        reasoning_content="thinking...",
    )

    assert resp.has_any_model_output()
    assert not resp.has_visible_or_actionable_output()
    assert resp.is_reasoning_only()

    resp_with_text = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="answer",
        call_list=[],
        reasoning_content="thinking...",
    )
    assert not resp_with_text.is_reasoning_only()
    assert resp_with_text.has_visible_or_actionable_output()

    empty_resp = LLMResponse(
        _stream=None,
        _upper=req,
        _auto_append_response=False,
        payloads=[LLMPayload(ROLE.USER, Text("hi"))],
        model_set=req.model_set,
        message="",
        call_list=[],
        reasoning_content=None,
        reasoning_parts=[],
    )
    assert not empty_resp.has_any_model_output()
    assert not empty_resp.is_reasoning_only()
