"""Signed active rounds must survive preprocessing as one indivisible group."""

from src.kernel.llm import LLMPayload, ROLE, Text, ToolCall, ToolResult
from src.kernel.llm.context_budget import maybe_compress_payloads
from src.kernel.llm.provider_state import ProviderReplayState, payload_fingerprint
from test.kernel.llm.test_gemini_client import model


async def test_active_signed_round_never_enters_lossy_compression() -> None:
    """A compression callback may replace old rounds, not active signed steps."""
    content=[ToolCall(id="c",name="weather",args={})]
    state=ProviderReplayState(provider="gemini",target=("test","https://offline.invalid/v1beta","test-model"),parts_json="[]",content_fingerprint=payload_fingerprint(content),call_ids=(("c",None),))
    payloads=[LLMPayload(ROLE.USER,Text("question")),LLMPayload(ROLE.ASSISTANT,content,provider_state=state),LLMPayload(ROLE.TOOL_RESULT,ToolResult("ok",call_id="c"))]
    m = model()
    m["max_context"] = 1
    called=[]
    async def compress(request: object, history: list[LLMPayload], entry: dict[str, object]) -> list[LLMPayload]:
        """记录压缩回调调用，并将传入历史替换为有损摘要。"""
        called.append(True)
        return [LLMPayload(ROLE.USER,Text("lossy summary"))]
    result=await maybe_compress_payloads(payloads,m,request=object(),context_compression_handler=compress)
    assert result == payloads
    assert not called
