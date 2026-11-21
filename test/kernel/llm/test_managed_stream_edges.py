"""Managed-stream ownership, cancellation and nested retry edge contracts."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Never
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.kernel.llm.exceptions import LLMAPIError, LLMTimeoutError, LLMContextError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import ToolCall
from src.kernel.llm.policy.base import ModelStep
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream
from src.kernel.llm.response import LLMResponse


class Source:
    """A provider iterator with directly observable reads and close calls."""

    def __init__(self, *items: StreamEvent | BaseException) -> None:
        """按顺序保存待产出的事件或待抛出的异常。"""
        self.items = iter(items)
        self.reads = 0
        self.closed = 0

    def __aiter__(self) -> Source:
        """返回持有读取和关闭计数的同一迭代器。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """记录读取次数，并产出事件、抛出指定异常或结束迭代。"""
        self.reads += 1
        item = next(self.items, None)
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        """记录提供方迭代器被关闭的次数。"""
        self.closed += 1


class Session:
    """Use each configured model once, without backoff or hidden retries."""

    def __init__(self, models: list[dict[str, Any]]) -> None:
        """保存候选模型，并初始化错误和成功记录。"""
        self.models = models
        self.index = 0
        self.errors = []
        self.successes = 0

    def first(self) -> ModelStep:
        """返回首个候选模型对应的尝试步骤。"""
        return ModelStep(model=self.models[0])

    def next_after_error(self, error: BaseException) -> ModelStep:
        """记录传入错误，并推进到下一模型或耗尽状态。"""
        self.errors.append(error)
        self.index += 1
        model = self.models[self.index] if self.index < len(self.models) else None
        return ModelStep(model=model)

    def record_success(self, **kwargs: float | int) -> None:
        """接收耗时和令牌数等成功指标，并累计成功次数。"""
        self.successes += 1


def managed(
    source: AsyncIterator[StreamEvent], *, timeout: float | None = None,
    started_at: float | None = None, models: list[dict[str, Any]] | None = None,
    resume: AsyncMock | None = None,
) -> tuple[ManagedStream, AsyncMock, AsyncMock]:
    """Return the wrapper and observable terminal/resume callbacks."""
    session = Session(models if models is not None else [{}])
    terminal = AsyncMock()
    resume = resume or AsyncMock(side_effect=AssertionError("unexpected retry"))
    stream = ManagedStream(
        source,
        timeout=timeout,
        started_at=time.perf_counter() if started_at is None else started_at,
        context=AttemptContext(session, session.first()),
        terminal=terminal,
        resume=resume,
    )
    return stream, terminal, resume


def response_for(stream: ManagedStream) -> LLMResponse:
    """将受管流包装为不自动追加上下文的测试响应。"""
    return LLMResponse(
        _stream=stream, _upper=SimpleNamespace(context_manager=None),
        _auto_append_response=False, payloads=[], model_set=[],
    )


@pytest.mark.parametrize("consume", ["stream_with_callback", "stream_events_with_callback"])
async def test_callback_cancellation_records_cancelled_and_closes_provider(consume: str) -> None:
    """Cancellation while inside caller code still belongs to this attempt."""
    source = Source(StreamEvent(text_delta="partial"), StreamEvent(text_delta="unused"))
    stream, terminal, resume = managed(source)
    response = response_for(stream)
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def callback(value: str | StreamEvent) -> None:
        """收到文本或事件后固定等待窗口，供调用方取消消费任务。"""
        entered.set()
        await blocker.wait()

    task = asyncio.create_task(getattr(response, consume)(callback))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await response.aclose()

    assert source.closed == 1
    assert source.reads == 1
    assert stream.phase == "cancelled"
    terminal.assert_awaited_once()
    assert isinstance(terminal.await_args.args[0], asyncio.CancelledError)
    assert terminal.await_args.args[3] == "cancelled"
    assert response.call_list == []
    resume.assert_not_awaited()


@pytest.mark.parametrize("close_via", ["response", "events", "context"])
async def test_early_close_discards_partial_tool_calls(close_via: str) -> None:
    """No public close path may leave a half-generated tool executable."""
    source = Source(
        StreamEvent(text_delta="partial", tool_call_id="call_partial", tool_name="danger", tool_args_delta='{"x":'),
        StreamEvent(tool_call_id="call_partial", tool_args_delta="1}"),
    )
    stream, terminal, resume = managed(source)
    response = response_for(stream)
    events = response.stream_events()
    try:
        if close_via == "context":
            async with response:
                await anext(events)
                assert response.call_list
        else:
            await anext(events)
            assert response.call_list
            if close_via == "response":
                await response.aclose()
            else:
                await events.aclose()
        assert response.call_list == []
        assert response.provider_state is None
        assert not any(isinstance(part, ToolCall) for part in response.to_payload().content)
        assert stream.phase == "abandoned"
        with pytest.raises(LLMContextError, match="incomplete or abandoned"):
            await response.send()
        assert source.closed == 1
        assert source.reads == 1
        terminal.assert_awaited_once()
        resume.assert_not_awaited()
    finally:
        await events.aclose()
        await response.aclose()


@pytest.mark.parametrize("timeout", [None, 0, -1])
async def test_no_deadline_consumes_without_wait_for(monkeypatch: pytest.MonkeyPatch, timeout: float | None) -> None:
    """Disabled timeout must not accidentally install a deadline."""
    wait_for = AsyncMock(side_effect=AssertionError("no deadline expected"))
    monkeypatch.setattr("src.kernel.llm.request_attempt.asyncio.wait_for", wait_for)
    source = Source(StreamEvent(text_delta="ok", usage={"total_tokens": 3}))
    stream, terminal, resume = managed(source, timeout=timeout)
    assert [event.text_delta async for event in stream] == ["ok"]
    assert stream.deadline is None
    assert stream.phase == "completed"
    assert source.closed == 1
    assert terminal.await_args.args[0] is None
    assert terminal.await_args.args[1] == {"total_tokens": 3}
    terminal.assert_awaited_once()
    wait_for.assert_not_awaited()
    resume.assert_not_awaited()


async def test_expired_budget_never_reads_provider_or_resets_policy() -> None:
    """总预算已过期时不读取提供方，也不重置策略会话。"""
    source = Source(StreamEvent(text_delta="too late"))
    stream, terminal, resume = managed(source, timeout=1, started_at=time.perf_counter() - 10)
    with pytest.raises(LLMTimeoutError):
        await anext(stream)
    assert source.reads == 0
    assert source.closed == 1
    assert stream.phase == "failed"
    assert stream.context.retry_count == 1
    assert len(stream.context.session.errors) == 1
    assert isinstance(terminal.await_args.args[0], LLMTimeoutError)
    terminal.assert_awaited_once()
    resume.assert_not_awaited()


async def test_retry_replacement_without_stream_fails_explicitly() -> None:
    """重试返回缺少流的响应时，必须显式失败并释放原始流。"""
    source = Source(LLMAPIError("busy", status_code=503))
    resume = AsyncMock(return_value=SimpleNamespace(_stream=None))
    stream, terminal, _ = managed(source, models=[{}, {}], resume=resume)
    with pytest.raises(RuntimeError, match="Streaming retry returned no stream"):
        await anext(stream)
    await stream.aclose()
    assert source.closed == 1
    assert stream.phase == "failed"
    terminal.assert_awaited_once()
    resume.assert_awaited_once_with(stream.context)


async def test_source_without_aclose_can_complete_and_close_idempotently() -> None:
    """不提供关闭方法的空迭代器也能正常完成并容忍重复关闭。"""
    class NoClose:
        """仅实现异步迭代协议，不提供资源关闭接口的源。"""
        def __aiter__(self) -> NoClose:
            """返回同一个无关闭接口的迭代器。"""
            return self

        async def __anext__(self) -> Never:
            """立即结束迭代，不产出任何事件。"""
            raise StopAsyncIteration

    stream, terminal, resume = managed(NoClose())
    assert [event async for event in stream] == []
    await stream.aclose()
    await stream.aclose()
    assert stream.phase == "completed"
    terminal.assert_awaited_once()
    resume.assert_not_awaited()


async def test_close_releases_retry_replacement_once() -> None:
    """关闭外层流必须恰好一次释放重试替换流，且不预读其内容。"""
    source = Source(LLMAPIError("busy", status_code=503))
    replacement = Source(StreamEvent(text_delta="unused"))
    resume = AsyncMock(return_value=SimpleNamespace(_stream=replacement))
    stream, terminal, _ = managed(source, models=[{}, {}], resume=resume)
    assert (await anext(stream)).reset
    await stream.aclose()
    await stream.aclose()
    assert source.closed == 1
    assert replacement.closed == 1
    assert replacement.reads == 0
    terminal.assert_awaited_once()
    resume.assert_awaited_once()


@pytest.mark.parametrize("consume", ["await", "events", "text_callback", "event_callback"])
async def test_three_retries_propagate_final_tool_compat_to_outer_response(mock_model_config: dict[str, Any], consume: str) -> None:
    """The final model's metadata must cross every nested replacement wrapper."""
    models = [dict(mock_model_config, model_identifier=f"attempt-{index}", tool_call_compat=index == 3) for index in range(4)]
    session = Session(models)
    policy = SimpleNamespace(new_session=lambda **kwargs: session)
    final_text = '{"message":"done","tool_calls":[{"id":"final","name":"lookup","args":{"x":1}}]}'
    sources = [Source(LLMAPIError("busy", status_code=503)) for _ in range(3)]
    sources.append(Source(StreamEvent(text_delta=final_text)))
    calls = []

    async def create(**kwargs: object) -> tuple[None, None, Source]:
        """记录模型名，并按尝试顺序返回预设提供方流。"""
        calls.append(kwargs["model_name"])
        return None, None, sources[len(calls) - 1]

    client = SimpleNamespace(create=create)
    request = LLMRequest(model_set=models, clients=SimpleNamespace(get_client_for_model=lambda model: client), policy=policy, enable_metrics=False)
    request.context_manager = None
    response = await request.send(stream=True, auto_append_response=False)
    outer_stream = response._stream
    assert response.tool_call_compat is False

    async def ignore(value: str | StreamEvent) -> None:
        """接收文本或事件，不改变重试链路和结果。"""
        pass

    try:
        if consume == "await":
            assert await response == "done"
        elif consume == "events":
            assert [event async for event in response.stream_events()]
        elif consume == "text_callback":
            assert await response.stream_with_callback(ignore) == "done"
        else:
            assert await response.stream_events_with_callback(ignore) == "done"
        assert response.tool_call_compat is True
        assert response.message == "done"
        assert [(call.id, call.name, call.args) for call in response.call_list] == [("final", "lookup", {"x": 1})]
        assert calls == [model["model_identifier"] for model in models]
        assert len(session.errors) == 3
        assert session.successes == 1
        assert outer_stream.context.retry_count == 3
        assert outer_stream.result_response.tool_call_compat is True
        assert all(source.closed == 1 for source in sources)
    finally:
        await response.aclose()
