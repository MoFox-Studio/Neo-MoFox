"""Regression tests for finite shared attempt state and cleanup."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Never
import time
from types import SimpleNamespace

import pytest

from src.kernel.llm.exceptions import LLMAPIError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.policy.base import ModelStep
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream


class Session:
    """Small deterministic policy for stream tests."""
    def __init__(self) -> None:
        """初始化有限重试的调用计数。"""
        self.calls = 0
    def next_after_error(self, error: BaseException) -> ModelStep:
        """收到错误后仅允许一次模型替换，随后返回耗尽状态。"""
        self.calls += 1
        return ModelStep(model={"model_identifier": "next"}) if self.calls == 1 else ModelStep(model=None)


async def test_buffered_retry_discards_failed_text() -> None:
    """Await-only consumers must not receive a failed attempt's buffered prefix."""
    terminal = []
    async def failed() -> AsyncIterator[StreamEvent]:
        """先产出错误前缀，再抛出可重试的供应商异常。"""
        yield StreamEvent(text_delta="wrong")
        raise LLMAPIError("busy", status_code=503)
    async def good() -> AsyncIterator[StreamEvent]:
        """产出替换尝试的完整正确文本。"""
        yield StreamEvent(text_delta="correct")
    async def finish(error: BaseException | None, usage: dict[str, Any] | None, latency: float, phase: str) -> None:
        """接收尝试终态信息，并记录其阶段。"""
        terminal.append(phase)
    async def resume(context: AttemptContext) -> SimpleNamespace:
        """为传入尝试上下文返回携带成功事件流的替换响应桩。"""
        return SimpleNamespace(_stream=good())
    stream = ManagedStream(failed(), timeout=2, started_at=time.perf_counter(), context=AttemptContext(Session(), ModelStep(model={})), terminal=finish, resume=resume)
    stream.buffered = True
    assert [e.text_delta async for e in stream] == ["correct"]
    assert terminal == ["failed"]


async def test_exposed_text_never_replays() -> None:
    """Once streamed text is visible, a retry would duplicate external output."""
    async def failed() -> AsyncIterator[StreamEvent]:
        """产出已对外暴露的部分文本后，抛出供应商异常。"""
        yield StreamEvent(text_delta="partial")
        raise LLMAPIError("busy", status_code=503)
    phases=[]
    async def finish(error: BaseException | None, usage: dict[str, Any] | None, latency: float, phase: str) -> None:
        """接收错误、用量与耗时，并记录尝试终态阶段。"""
        phases.append(phase)
    async def resume(context: AttemptContext) -> Never:
        """若已暴露文本的上下文仍被要求重试，立即使测试失败。"""
        raise AssertionError("must not replay")
    stream=ManagedStream(failed(),timeout=2,started_at=time.perf_counter(),context=AttemptContext(Session(),ModelStep(model={})),terminal=finish,resume=resume)
    assert (await anext(stream)).text_delta == "partial"
    with pytest.raises(LLMAPIError):
        await anext(stream)
    assert phases == ["failed"]


async def test_unconsumed_close_is_idempotent() -> None:
    """A never-started wrapper still owns its source."""
    class Source:
        """通过延迟读取和关闭计数检查未消费流的所有权。"""
        closed=0
        async def __anext__(self) -> None:
            """模拟一次长时间等待；本用例不应开始读取。"""
            await asyncio.sleep(10)
        def __aiter__(self) -> Source:
            """返回同一个可观测的提供方迭代器。"""
            return self
        async def aclose(self) -> None:
            """累计关闭次数，以验证外层关闭的幂等性。"""
            self.closed += 1
    source = Source()
    phases = []
    async def finish(error: BaseException | None, usage: dict[str, Any] | None, latency: float, phase: str) -> None:
        """接收错误、用量与耗时，并记录尝试终态阶段。"""
        phases.append(phase)
    async def resume(context: AttemptContext) -> Never:
        """未开始消费的上下文不应触发恢复请求。"""
        raise AssertionError
    stream=ManagedStream(source,timeout=2,started_at=time.perf_counter(),context=AttemptContext(Session(),ModelStep(model={})),terminal=finish,resume=resume)
    await stream.aclose()
    await stream.aclose()
    assert source.closed == 1
    assert phases == ["abandoned"]
