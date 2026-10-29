"""Regression tests for finite shared attempt state and cleanup."""
import asyncio
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
        self.calls = 0
    def next_after_error(self, error: BaseException) -> ModelStep:
        self.calls += 1
        return ModelStep(model={"model_identifier": "next"}) if self.calls == 1 else ModelStep(model=None)


async def test_buffered_retry_discards_failed_text() -> None:
    """Await-only consumers must not receive a failed attempt's buffered prefix."""
    terminal = []
    async def failed():
        yield StreamEvent(text_delta="wrong")
        raise LLMAPIError("busy", status_code=503)
    async def good():
        yield StreamEvent(text_delta="correct")
    async def finish(error, usage, latency, phase):
        terminal.append(phase)
    async def resume(context):
        return SimpleNamespace(_stream=good())
    stream = ManagedStream(failed(), timeout=2, started_at=time.perf_counter(), context=AttemptContext(Session(), ModelStep(model={})), terminal=finish, resume=resume)
    stream.buffered = True
    assert [e.text_delta async for e in stream] == ["correct"]
    assert terminal == ["failed"]


async def test_exposed_text_never_replays() -> None:
    """Once streamed text is visible, a retry would duplicate external output."""
    async def failed():
        yield StreamEvent(text_delta="partial")
        raise LLMAPIError("busy", status_code=503)
    phases=[]
    async def finish(error, usage, latency, phase): phases.append(phase)
    async def resume(context): raise AssertionError("must not replay")
    stream=ManagedStream(failed(),timeout=2,started_at=time.perf_counter(),context=AttemptContext(Session(),ModelStep(model={})),terminal=finish,resume=resume)
    assert (await anext(stream)).text_delta == "partial"
    with pytest.raises(LLMAPIError): await anext(stream)
    assert phases == ["failed"]


async def test_unconsumed_close_is_idempotent() -> None:
    """A never-started wrapper still owns its source."""
    class Source:
        closed=0
        async def __anext__(self): await asyncio.sleep(10)
        def __aiter__(self): return self
        async def aclose(self): self.closed += 1
    source=Source(); phases=[]
    async def finish(error, usage, latency, phase): phases.append(phase)
    async def resume(context): raise AssertionError
    stream=ManagedStream(source,timeout=2,started_at=time.perf_counter(),context=AttemptContext(Session(),ModelStep(model={})),terminal=finish,resume=resume)
    await stream.aclose(); await stream.aclose()
    assert source.closed == 1
    assert phases == ["abandoned"]
