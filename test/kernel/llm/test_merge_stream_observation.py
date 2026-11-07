"""验证 dev 请求观测与托管流所有权整合，不连接真实提供商。"""
from __future__ import annotations

import pytest

from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.llm import request_inspector as inspector_module
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.request_inspector import RequestInspector
from src.kernel.llm.request_trace import Attempt, LogicalRequest
from test.kernel.llm.test_request_monitor import FakeStream, make_client, model, sdk_events
from test.kernel.llm.test_gemini_client import model as gemini_model
from test.kernel.llm.test_review_gemini_resources import _Wire, _adapter


@pytest.fixture
def inspector(monkeypatch: pytest.MonkeyPatch) -> RequestInspector:
    """隔离全局观测记录，防止同进程历史记录影响断言。"""
    instance = RequestInspector()
    monkeypatch.setattr(inspector_module, "_inspector", instance)
    return instance


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "responses"])
async def test_managed_native_events_are_recorded_once(
    monkeypatch: pytest.MonkeyPatch, inspector: RequestInspector, protocol: str,
) -> None:
    """原生适配器已经观测的文本和工具参数不被托管层重复归并。"""
    sdk = FakeStream(sdk_events(protocol))
    client = make_client(monkeypatch, protocol, sdk_stream=sdk)
    request = LLMRequest(
        [model(protocol)], clients=ModelClientRegistry(**{protocol: client}),
        enable_metrics=False,
    )
    request.context_manager = None
    request.add_payload(LLMPayload(ROLE.USER, Text("offline")))
    response = await request.send(stream=True)
    record = inspector.get_record(1)
    assert record is not None and record["status"] in {"pending", "running"}
    assert await response == "hello"
    record = inspector.get_record(1)
    assert record is not None and record["status"] == "succeeded"
    assert record["response"]["message"] == "hello"
    assert record["response"]["tool_calls"][0]["args"] == {"city": "上海"}
    assert len(inspector.snapshot()["records"]) == 1
    await response.aclose()
    assert sdk.closed == 1


async def test_gemini_standard_events_observed_without_replay_state(
    inspector: RequestInspector,
) -> None:
    """未接原生 trace 的 Gemini 只补标准结果，不把签名回放状态送入观测。"""
    wire = _Wire()
    client = _adapter([wire])
    request = LLMRequest(
        [gemini_model()], clients=ModelClientRegistry(gemini=client),
        enable_metrics=False,
    )
    request.context_manager = None
    request.add_payload(LLMPayload(ROLE.USER, Text("offline")))
    try:
        response = await request.send(stream=True)
        assert await response == "012"
        assert response.provider_state is not None
        record = inspector.get_record(1)
        assert record is not None and record["status"] == "succeeded"
        assert record["response"]["message"] == "012"
        assert "provider_state" not in record["response"]
        assert record["capture_info"]["raw_unavailable"] is True
        assert len(inspector.snapshot()["records"]) == 1
        assert wire.closed == 1
    finally:
        await client.aclose()


async def test_unconsumed_resource_close_error_is_visible_and_cached(
    inspector: RequestInspector,
) -> None:
    """未启动生成器的 SDK 回收失败仍可见，并只执行一次真实关闭。"""
    from collections.abc import AsyncIterator

    class Resource:
        """模拟预先打开的 SDK 资源。"""

        def __init__(self) -> None:
            """保存精确关闭次数和同一失败对象。"""
            self.calls = 0
            self.error = RuntimeError("offline close failure")

        async def aclose(self) -> None:
            """报告可见的关闭失败。"""
            self.calls += 1
            raise self.error

    async def source() -> AsyncIterator[StreamEvent]:
        """提供尚未开始的统一事件源。"""
        yield StreamEvent(text_delta="unused")

    resource = Resource()
    trace = Attempt(LogicalRequest(), model(), "unconsumed", stream=True)
    trace.opened(resource)
    trace.handoff_stream()
    stream = trace.observe_stream(source())
    for _ in range(2):
        with pytest.raises(RuntimeError) as raised:
            await stream.aclose()
        assert raised.value is resource.error
    assert resource.calls == 1


async def test_handoff_defers_native_success_until_owner_finishes(
    inspector: RequestInspector,
) -> None:
    """适配器读完不能先于托管 owner 的关闭结果提交成功终态。"""
    trace = Attempt(LogicalRequest(), model(), "handoff", stream=True)
    trace.start("offline.stream", {"model": "demo", "stream": True})
    trace.handoff_stream()
    with trace.consume():
        trace.event(StreamEvent(text_delta="ok"))
    record = inspector.get_record(trace.record_id)
    assert record is not None and record["status"] == "streaming"
    trace.finish(RuntimeError("owner close failed"))
    record = inspector.get_record(trace.record_id)
    assert record is not None and record["status"] == "failed"
    assert record["response"]["message"] == "ok"
