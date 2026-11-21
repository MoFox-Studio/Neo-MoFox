"""验证响应单次消费权与托管流关闭责任，所有源均为离线可计数迭代器。"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast, get_type_hints
from unittest.mock import AsyncMock, Mock

import pytest

from src.kernel.concurrency import get_task_manager
from src.kernel.llm.exceptions import LLMResponseConsumedError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.payload import Text, ToolCall
from src.kernel.llm.policy.base import ModelStep
from src.kernel.llm.provider_state import ProviderReplayState
from src.kernel.llm.request import LLMRequest
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream
from src.kernel.llm.response import LLMResponse


ENTRIES = ("events", "text", "buffer", "await", "text_callback", "event_callback")
GENERATORS = ENTRIES[:3]
CALLBACKS = ENTRIES[4:]


class Source:
    """记录读取和关闭次数，并允许在读取第二块前执行确定性的嵌套探针。"""

    def __init__(self) -> None:
        """提供两个文本块及首块中的未完成工具参数。"""
        self.events = iter([
            StreamEvent(text_delta="one", tool_call_id="partial", tool_name="lookup", tool_args_delta='{"x":'),
            StreamEvent(text_delta="two", tool_call_id="partial", tool_args_delta="1}"),
        ])
        self.reads = 0
        self.closed = 0
        self.before_second: Callable[[], Awaitable[None]] | None = None

    def __aiter__(self) -> Source:
        """返回具有独立计数器的当前源。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """读取下一块，在第二次读取时等待可选探针。"""
        self.reads += 1
        if self.reads == 2 and self.before_second is not None:
            await self.before_second()
        event = next(self.events, None)
        if event is None:
            raise StopAsyncIteration
        return event

    async def aclose(self) -> None:
        """记录真实资源关闭，故意不替测试隐藏重复关闭。"""
        self.closed += 1


class Session:
    """禁止本测试意外重试，保持终态计数可归因到单个尝试。"""

    def first(self) -> ModelStep:
        """返回仅供尝试上下文使用的初始模型。"""
        return ModelStep(model={})

    def next_after_error(self, error: BaseException) -> ModelStep:
        """对任意错误停止重试，error 保留协议参数形态。"""
        return ModelStep(model=None)

    def record_success(self, *, latency: float = 0.0, tokens: int = 0) -> None:
        """接收协议中的 latency 秒数和 tokens 数；终态断言由独立 mock 承担。"""


def response_for(source: Source, *, managed: bool = True) -> tuple[LLMResponse, ManagedStream | None, AsyncMock]:
    """将离线 source 包装为开启自动追加的响应，可选 managed 以验证旧流兼容。"""
    terminal = AsyncMock()
    session = Session()
    stream = ManagedStream(
        source, timeout=None, started_at=time.perf_counter(),
        context=AttemptContext(session, session.first()), terminal=terminal,
        resume=AsyncMock(side_effect=AssertionError("不应重试")),
    ) if managed else None
    response = LLMResponse(
        _stream=stream if managed else source,
        _upper=cast(LLMRequest, SimpleNamespace(context_manager=None)),
        _auto_append_response=True, payloads=[], model_set=[],
    )
    return response, stream, terminal


def iterator_for(response: LLMResponse, entry: str) -> AsyncGenerator[Any, None]:
    """按 entry 创建事件、文本或缓冲生成器，不提前执行它。"""
    if entry == "events":
        return cast(AsyncGenerator[Any, None], response.stream_events())
    if entry == "text":
        return cast(AsyncGenerator[Any, None], response.__aiter__())
    return cast(AsyncGenerator[Any, None], response.stream_with_buffer(buffer_size=1))


async def collect(response: LLMResponse, entry: str) -> str:
    """通过 entry 完整消费 response，并保持回调参数为对应公开类型。"""
    async def on_text(text: str) -> None:
        """接收文本但不改变响应归并结果。"""

    async def on_event(event: StreamEvent) -> None:
        """接收原始事件但不改变响应归并结果。"""

    if entry == "await":
        return await response
    if entry == "text_callback":
        return await response.stream_with_callback(on_text)
    if entry == "event_callback":
        return await response.stream_events_with_callback(on_event)
    iterator = iterator_for(response, entry)
    try:
        async for _ in iterator:
            pass
    finally:
        await iterator.aclose()
    return response.message or ""


async def reject_duplicate(response: LLMResponse, entry: str) -> None:
    """执行 entry 并确认重复消费被拒绝，包括生成器首次推进。"""
    with pytest.raises(LLMResponseConsumedError):
        await collect(response, entry)


@pytest.mark.parametrize("owner", GENERATORS)
@pytest.mark.parametrize("duplicate", ENTRIES)
async def test_duplicate_after_first_chunk_preserves_owner(owner: str, duplicate: str) -> None:
    """首块 one 后各重复入口不得关闭、清空或截断合法 owner。"""
    source = Source()
    response, stream, terminal = response_for(source)
    recorder = Mock()
    response._stream_stats_recorder = recorder
    iterator = iterator_for(response, owner)
    try:
        first = await anext(iterator)
        assert (first.text_delta if isinstance(first, StreamEvent) else first) == "one"
        calls = response.call_list
        state = ProviderReplayState("gemini", ("endpoint", "v1", "model"), "[]", "test")
        response.provider_state = state
        await reject_duplicate(response, duplicate)
        assert source.closed == 0
        assert response.call_list is calls
        if owner != "buffer":
            assert calls
        assert response.provider_state is state
        terminal.assert_not_awaited()
        recorder.assert_not_called()
        remaining = [value async for value in iterator]
        assert [value.text_delta if isinstance(value, StreamEvent) else value for value in remaining] == ["two"]
        assert response.message == "onetwo"
        assert len(response.payloads) == 1
        assert [part.text for part in response.payloads[0].content if isinstance(part, Text)] == ["onetwo"]
        assert source.closed == 1
        assert stream is not None and stream.phase == "completed"
        terminal.assert_awaited_once()
        recorder.assert_called_once()
    finally:
        await iterator.aclose()
        await response.aclose()


@pytest.mark.parametrize("owner", ("await", *CALLBACKS))
@pytest.mark.parametrize("duplicate", ENTRIES)
async def test_coroutine_owner_rejects_nested_consumer(owner: str, duplicate: str) -> None:
    """协程 owner 读过 one 后仍持有消费权，嵌套重复者不可借当前任务身份越权。"""
    source = Source()
    response, stream, terminal = response_for(source)

    async def probe() -> None:
        """第二块读取前验证重复消费没有释放当前尝试。"""
        await reject_duplicate(response, duplicate)
        assert source.closed == 0
        terminal.assert_not_awaited()

    source.before_second = probe
    try:
        assert await collect(response, owner) == "onetwo"
        assert response.message == "onetwo"
        assert len(response.payloads) == 1
        assert stream is not None and stream.phase == "completed"
        assert source.closed == 1
        terminal.assert_awaited_once()
    finally:
        await response.aclose()


@pytest.mark.parametrize("entry", GENERATORS)
async def test_generator_close_discards_partial_state_once(entry: str) -> None:
    """合法生成器提前关闭须同步关闭源，丢弃半成品而不重复发布终态。"""
    source = Source()
    response, stream, terminal = response_for(source)
    iterator = iterator_for(response, entry)
    await anext(iterator)
    assert response.call_list or entry == "buffer"
    response.provider_state = ProviderReplayState("gemini", ("endpoint", "v1", "model"), "[]", "test")
    await iterator.aclose()
    await iterator.aclose()
    await response.aclose()
    assert source.closed == 1
    assert stream is not None and stream.phase == "abandoned"
    terminal.assert_awaited_once()
    assert response.call_list == []
    assert response.provider_state is None
    assert response.payloads == []


@pytest.mark.parametrize("entry", CALLBACKS)
@pytest.mark.parametrize("cancel", [False, True])
async def test_callback_failure_or_cancellation_closes_once(entry: str, cancel: bool) -> None:
    """回调异常或取消仍归合法消费者清理，不吞异常、不追加半成品。"""
    source = Source()
    response, stream, terminal = response_for(source)

    async def fail(value: str | StreamEvent) -> None:
        """模拟调用方处理 value 时失败或被取消。"""
        if cancel:
            raise asyncio.CancelledError
        raise ValueError("回调失败")

    method = response.stream_with_callback if entry == "text_callback" else response.stream_events_with_callback
    with pytest.raises(asyncio.CancelledError if cancel else ValueError):
        await method(fail)
    await response.aclose()
    assert source.closed == 1
    assert stream is not None and stream.phase in ("cancelled", "abandoned")
    terminal.assert_awaited_once()
    assert response.call_list == []
    assert response.provider_state is None
    assert response.payloads == []


@pytest.mark.parametrize("entry", ENTRIES)
async def test_external_task_cancellation_closes_once(entry: str) -> None:
    """读取或回调挂起时的真实任务取消应保留取消并发布一次 cancelled 终态。"""
    source = Source()
    response, stream, terminal = response_for(source)
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def block() -> None:
        """通知取消窗口已经到达，并等待外部取消。"""
        entered.set()
        await blocker.wait()

    async def callback(value: str | StreamEvent) -> None:
        """在首块 value 的调用方处理期间等待取消。"""
        await block()

    async def run() -> None:
        """通过 entry 启动合法消费者，回调路径与源读取路径分别固定取消窗口。"""
        if entry == "text_callback":
            await response.stream_with_callback(callback)
        elif entry == "event_callback":
            await response.stream_events_with_callback(callback)
        else:
            source.before_second = block
            await collect(response, entry)

    info = get_task_manager().create_task(run(), name="review-response-cancel")
    task = info.task
    assert task is not None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert source.closed == 1
        assert stream is not None and stream.phase == "cancelled"
        terminal.assert_awaited_once()
        assert isinstance(terminal.await_args.args[0], asyncio.CancelledError)
        assert response.call_list == []
        assert response.provider_state is None
        assert response.payloads == []
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await response.aclose()


@pytest.mark.parametrize("entry", ENTRIES)
async def test_nonmanaged_stream_keeps_legacy_semantics(entry: str) -> None:
    """旧非托管迭代器仍完整归并并追加，包装器不擅自关闭其资源。"""
    source = Source()
    response, _, terminal = response_for(source, managed=False)
    assert await collect(response, entry) == "onetwo"
    assert source.closed == 0
    assert len(response.payloads) == 1
    assert any(isinstance(part, ToolCall) for part in response.payloads[0].content)
    await reject_duplicate(response, entry)
    assert source.closed == 0
    terminal.assert_not_awaited()
    await response.aclose()
    assert source.closed == 1


def test_wrappers_preserve_signature_metadata_and_async_shape() -> None:
    """公开方法保留 wraps、签名、类型注解以及异步生成器或协程形态。"""
    for name in ("stream_events", "__aiter__", "stream_with_buffer", "stream_with_callback", "stream_events_with_callback"):
        method = getattr(LLMResponse, name)
        original = method.__wrapped__
        assert inspect.signature(method) == inspect.signature(original)
        assert get_type_hints(method) == get_type_hints(original)
        assert method.__doc__ == original.__doc__
        assert inspect.isasyncgenfunction(method) == inspect.isasyncgenfunction(original)
        assert inspect.iscoroutinefunction(method) == inspect.iscoroutinefunction(original)
