"""验证消费仅签名响应后插入用户消息的 Gemini 出站顺序。"""
from __future__ import annotations

import json

from src.kernel.llm import LLMPayload, LLMResponse, ROLE, Text
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from test.kernel.llm import test_gemini_continuations as continuations


OfflineCase = tuple[continuations._Wire, GeminiChatClient]
offline = continuations.offline


async def _assert_signature_order(case: OfflineCase, *, stream: bool, events: bool = False) -> None:
    """在消费与续发之间插入 USER，检查 SDK 实际发送的原生 Part。"""
    wire, client = case
    signature = {"thoughtSignature": "c2ln"}
    wire.replies.extend([[signature], [{"text": "done"}]])
    first = await continuations._request(client).send(stream=stream)
    if events:
        assert [event async for event in first.stream_events()] == []
    else:
        assert await continuations._consume(first, stream) == ""
    assert first.stop_reason == "STOP"
    first.add_payload(LLMPayload(ROLE.USER, Text("next")))
    second = await first.send(stream=stream)
    assert await continuations._consume(second, stream) == "done"

    assert len(wire.requests) == 2
    history = json.loads(wire.requests[1].content)["contents"]
    assert sum("thoughtSignature" in part for item in history for part in item["parts"]) == 1
    assert history == [
        {"role": "user", "parts": [{"text": "hello"}]},
        {"role": "model", "parts": [signature]},
        {"role": "user", "parts": [{"text": "next"}]},
    ], history


async def test_nonstream_signature_precedes_next_user(offline: OfflineCase) -> None:
    """非流式 STOP 在 await 后即保留模型轮的位置。"""
    await _assert_signature_order(offline, stream=False)


async def test_stream_signature_precedes_next_user(offline: OfflineCase) -> None:
    """SSE 签名事件即使没有文本增量，也要在迭代完成时占据模型轮。"""
    await _assert_signature_order(offline, stream=True)


async def test_nonstream_signature_preserved_by_event_iteration(offline: OfflineCase) -> None:
    """公开事件迭代器消费缓冲响应时不产出伪事件，但同样要保存签名。"""
    await _assert_signature_order(offline, stream=False, events=True)


async def test_signature_respects_disabled_auto_append(offline: OfflineCase) -> None:
    """显式禁用自动写回时，消费不改历史；直接续发仍可回放签名。"""
    wire, client = offline
    signature = {"thoughtSignature": "c2ln"}
    wire.replies.extend([[signature], [{"text": "done"}]])
    first = await continuations._request(client).send(stream=False, auto_append_response=False)
    assert await first == ""
    assert [payload.role for payload in first.payloads] == [ROLE.USER]
    assert await (await first.send(stream=False)) == "done"
    history = json.loads(wire.requests[1].content)["contents"]
    assert history == [
        {"role": "user", "parts": [{"text": "hello"}]},
        {"role": "model", "parts": [signature]},
    ], history


async def test_stateless_empty_response_skips_assistant(offline: OfflineCase) -> None:
    """没有正文也没有回放状态时，消费和续发均不应产生空模型轮。"""
    wire, client = offline
    request = continuations._request(client)
    # Gemini 本身拒绝无签名的空 STOP；从公共响应对象开始验证这一通用边界。
    empty = LLMResponse(
        _stream=None, _upper=request, _auto_append_response=True,
        payloads=list(request.payloads), model_set=request.model_set,
    )
    assert await empty == ""
    assert empty.payloads == request.payloads
    wire.replies.append([{"text": "done"}])
    empty.add_payload(LLMPayload(ROLE.USER, Text("next")))
    assert await (await empty.send(stream=False)) == "done"
    history = json.loads(wire.requests[0].content)["contents"]
    assert all(item["role"] == "user" for item in history), history
    roles = [payload.role for payload in empty.payloads]
    assert roles == [ROLE.USER], roles
