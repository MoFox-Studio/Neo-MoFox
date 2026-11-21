"""PR181 的 Gemini 公开续轮回归：保留签名边界，不放宽工具与来源校验。"""

from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from src.kernel.llm import LLMPayload, LLMRequest, LLMResponse, ROLE, Text, ToolResult
from src.kernel.llm.exceptions import LLMContextError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.gemini_codec import target_identity
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_gemini_coverage_edges import _close_idle_client


SIGNATURE_PARTS = [
    {"thoughtSignature": "c2lnLW9uZQ=="},
    {"thoughtSignature": "c2lnLXR3bw=="},
]


def _native_response(parts: list[dict[str, Any]], *, finished: bool = True) -> dict[str, Any]:
    """构造 SDK 接收的原生响应；流中间块不携带终态。"""
    candidate: dict[str, Any] = {
        "index": 0,
        "content": {"role": "model", "parts": parts},
    }
    if finished:
        candidate["finishReason"] = "STOP"
    return {"candidates": [candidate]}


@dataclass
class _Wire:
    """只替换 HTTP 传输，保留真实 SDK、编解码和公开请求链。"""

    replies: list[list[dict[str, Any]]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        """记录请求；流式场景逐 Part 返回，避免单块掩盖签名合并问题。"""
        self.requests.append(request)
        assert self.replies, "Unexpected Gemini HTTP request"
        parts = self.replies.pop(0)
        if request.url.path.endswith(":streamGenerateContent"):
            chunks = [
                _native_response([part], finished=index == len(parts) - 1)
                for index, part in enumerate(parts)
            ]
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks),
                headers={"content-type": "text/event-stream"},
            )
        assert request.url.path.endswith(":generateContent")
        return httpx.Response(200, json=_native_response(parts))


@pytest.fixture
async def offline() -> AsyncIterator[tuple[_Wire, GeminiChatClient]]:
    """为每个用例提供隔离的离线客户端，并关闭所有 SDK 资源。"""
    wire = _Wire()
    client = GeminiChatClient(transport=httpx.MockTransport(wire.handle))
    primary: BaseException | None = None
    try:
        yield wire, client
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await _close_idle_client(client)
        except BaseException as error:
            if primary is not None:
                primary.add_note("Gemini fixture cleanup failed:\n" + "".join(traceback.format_exception(error)))
            elif isinstance(error, Exception):
                raise error
            else:
                # pytest 仅会为普通 teardown 错误继续其余 finalizer；cause 保留控制异常。
                raise RuntimeError("Gemini fixture 清理收到控制异常") from error


def _request(client: GeminiChatClient, entry: dict[str, Any] | None = None) -> LLMRequest:
    """保留默认上下文管理器，仅注入离线传输与确定性选模策略。"""
    request = LLMRequest(
        model_set=[model() if entry is None else entry],
        clients=ModelClientRegistry(gemini=client),
        policy=RoundRobinPolicy(),
        enable_metrics=False,
    )
    assert request.context_manager is not None
    return request.add_payload(LLMPayload(ROLE.USER, Text("hello")))


async def _consume(response: LLMResponse, stream: bool) -> str:
    """分别通过公开流式迭代与 await 完整消费响应。"""
    if stream:
        return "".join([chunk async for chunk in response])
    return await response


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("signed", [False, True])
async def test_text_response_send_preserves_assistant_boundaries(
    offline: tuple[_Wire, GeminiChatClient], stream: bool, signed: bool,
) -> None:
    """无未决工具的普通续轮允许相邻 assistant，且不合并原生签名 Part。"""
    wire, client = offline
    first_parts = [{"text": "first "}, {"text": "answer"}]
    if signed:
        for part, signature in zip(first_parts, SIGNATURE_PARTS, strict=True):
            part.update(signature)
    second_parts = [{"text": "second", "thoughtSignature": "c2Vjb25k"}]
    wire.replies.extend([first_parts, second_parts, [{"text": "third"}]])
    first = await _request(client).send(stream=stream)
    assert await _consume(first, stream) == "first answer"
    second = await first.send(stream=stream)
    assert await _consume(second, stream) == "second"
    assert [payload.role for payload in second.payloads] == [
        ROLE.USER, ROLE.ASSISTANT, ROLE.ASSISTANT,
    ]
    assert second.payloads[1].provider_state is first.provider_state
    assert second.payloads[2].provider_state is second.provider_state
    third = await second.send(stream=stream)
    assert await _consume(third, stream) == "third"
    history = json.loads(wire.requests[2].content)["contents"]
    assert [content["role"] for content in history] == ["user", "model", "model"]
    assert history[1]["parts"] == first_parts
    assert history[2]["parts"] == second_parts
    for result in (first, second, third):
        assert result.provider_state is not None
        assert result.provider_state.target == target_identity(model())
        assert result.provider_state.matches(result.to_payload().content)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reuse_payload", [False, True])
async def test_signature_only_response_continues_through_public_payload(
    offline: tuple[_Wire, GeminiChatClient], stream: bool, reuse_payload: bool,
) -> None:
    """仅签名 STOP 在公开转换后可同来源续轮，且两个签名保持原样和顺序。"""
    wire, client = offline
    wire.replies.extend([SIGNATURE_PARTS, [{"text": "continued"}]])
    first = await _request(client).send(stream=stream)
    assert await _consume(first, stream) == ""
    payload = first.to_payload()
    assert payload.content == [Text("")]
    assert payload.provider_state is first.provider_state
    if reuse_payload:
        followup = _request(client).add_payload(payload)
        second = await followup.send(stream=stream)
    else:
        second = await first.send(stream=stream)
    assert await _consume(second, stream) == "continued"
    assert first.provider_state is not None
    assert first.provider_state.target == target_identity(model())
    assert first.provider_state.matches(payload.content)
    assert json.loads(wire.requests[1].content)["contents"][1] == {
        "role": "model", "parts": SIGNATURE_PARTS,
    }
    assert len(wire.requests) == 2


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("resolved", [0, 1])
async def test_pending_tools_still_reject_send_and_adjacent_assistant(
    offline: tuple[_Wire, GeminiChatClient], stream: bool, resolved: int,
) -> None:
    """未回复或仅部分回复工具时，发送与插入 assistant 均须拒绝。"""
    wire, client = offline
    wire.replies.append([
        {"functionCall": {"id": call_id, "name": "weather", "args": {}}, **signature}
        for call_id, signature in zip(("call-one", "call-two"), SIGNATURE_PARTS, strict=True)
    ])
    response = await _request(client).send(stream=stream)
    assert await _consume(response, stream) == ""
    assert len(response.call_list) == 2
    response.add_call_reflex([
        LLMPayload(ROLE.TOOL_RESULT, ToolResult("sunny", call_id=call.id))
        for call in response.call_list[:resolved]
    ])
    previous = list(response.payloads)
    with pytest.raises(LLMContextError, match="tool_result"):
        await response.send(stream=stream)
    with pytest.raises(LLMContextError, match="tool_result"):
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text("cannot skip tools")))
    assert response.payloads == previous
    assert len(wire.requests) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_completed_tool_round_allows_later_text_continuation(
    offline: tuple[_Wire, GeminiChatClient], stream: bool,
) -> None:
    """工具全部闭合后恢复普通续轮；原生工具调用及签名仍可回放。"""
    wire, client = offline
    tool_parts = [{
        "functionCall": {"id": "weather-call", "name": "weather", "args": {}},
        "thoughtSignature": "dG9vbA==",
    }]
    wire.replies.extend([tool_parts, [{"text": "sunny"}], [{"text": "continued"}]])
    first = await _request(client).send(stream=stream)
    assert await _consume(first, stream) == ""
    first.add_call_reflex([
        LLMPayload(ROLE.TOOL_RESULT, ToolResult("sunny", call_id=first.call_list[0].id)),
    ])
    second = await first.send(stream=stream)
    assert await _consume(second, stream) == "sunny"
    third = await second.send(stream=stream)
    assert await _consume(third, stream) == "continued"
    history = json.loads(wire.requests[2].content)["contents"]
    assert history[1]["parts"] == tool_parts
    assert history[2]["parts"][0]["functionResponse"]["id"] == "weather-call"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("signature_only", [False, True])
@pytest.mark.parametrize("edit", ["replace", "append"])
async def test_public_signed_payload_rejects_real_edits_before_http(
    offline: tuple[_Wire, GeminiChatClient], stream: bool, signature_only: bool, edit: str,
) -> None:
    """空表示规范化不能让真正的替换或追加绕过签名内容指纹。"""
    wire, client = offline
    wire.replies.append(
        SIGNATURE_PARTS if signature_only else [{"text": "original", **SIGNATURE_PARTS[0]}]
    )
    first = await _request(client).send(stream=stream)
    await _consume(first, stream)
    payload = first.to_payload()
    if edit == "replace":
        payload.content[0] = Text("edited")
    else:
        payload.content.append(Text("edited"))
    request = _request(client).add_payload(payload)
    with pytest.raises(LLMContextError, match="signed content"):
        await request.send(stream=stream)
    assert len(wire.requests) == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("signature_only", [False, True])
@pytest.mark.parametrize("field,value", [
    ("api_provider", "other-provider"),
    ("base_url", "https://other.invalid"),
    ("model_identifier", "other-model"),
])
async def test_public_signed_payload_rejects_other_targets_before_http(
    offline: tuple[_Wire, GeminiChatClient], stream: bool, signature_only: bool,
    field: str, value: str,
) -> None:
    """原样 payload 也不能迁移到其他供应商、端点或模型。"""
    wire, client = offline
    wire.replies.append(
        SIGNATURE_PARTS if signature_only else [{"text": "original", **SIGNATURE_PARTS[0]}]
    )
    first = await _request(client).send(stream=stream)
    await _consume(first, stream)
    other = model()
    other[field] = value
    request = _request(client, other).add_payload(first.to_payload())
    with pytest.raises(LLMContextError, match="another target"):
        await request.send(stream=stream)
    assert len(wire.requests) == 1
