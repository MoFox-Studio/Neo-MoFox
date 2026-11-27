"""以一般受管源和真实 SDK 离线流固定 P1-A 的异常、清理及重试契约。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from src.kernel.concurrency import TaskManager
from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.llm.exceptions import LLMAPIError, LLMTimeoutError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.payload import ToolCall
from src.kernel.llm.policy.base import ModelStep
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from src.kernel.llm.provider_state import ProviderReplayState
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream
from src.kernel.llm.response import LLMResponse
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_review_gemini_resources import _Wire, _adapter

ENTRIES = ("await", "events", "text", "text_callback", "event_callback", "buffer")


class Source:
    """提供可控读取、关闭故障及 Event 窗口，不依赖时间竞争。"""

    def __init__(self, *, error: BaseException | None = None,
                 close_error: BaseException | None = None, text: bool = True,
                 block_read: bool = False, block_close: bool = False) -> None:
        """保存本次源的错误与阻塞条件。"""
        self.error, self.close_error = error, close_error
        self.events = iter([StreamEvent(text_delta="one", tool_call_id="partial",
                                       tool_name="lookup", tool_args_delta='{"x":')] if text else [])
        self.block_read, self.block_close = block_read, block_close
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.close_entered, self.close_release = asyncio.Event(), asyncio.Event()
        self.closed = 0

    def __aiter__(self) -> Source:
        """返回当前单次源。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """先产出前缀，再进入受控错误或耗尽。"""
        event = next(self.events, None)
        if event is not None:
            return event
        self.entered.set()
        if self.block_read:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        raise StopAsyncIteration

    async def aclose(self) -> None:
        """等待受控关闭并记录实际调用，不替包装器隐藏重复关闭。"""
        self.closed += 1
        self.close_entered.set()
        if self.block_close:
            await self.close_release.wait()
        if self.close_error is not None:
            raise self.close_error


class Session:
    """记录策略推进，不允许测试意外发送真实请求。"""

    def __init__(self) -> None:
        """初始化策略推进次数。"""
        self.calls = 0

    def next_after_error(self, error: BaseException) -> ModelStep:
        """推进一次后耗尽，错误仅作为协议参数。"""
        self.calls += 1
        return ModelStep(model={} if self.calls == 1 else None)


def managed_response(source: Source, *, managed: bool = True) -> tuple[LLMResponse, ManagedStream, AsyncMock, AsyncMock]:
    """为 source 建立可观察终态、策略和恢复请求的响应。"""
    terminal, resume = AsyncMock(), AsyncMock(side_effect=AssertionError("不应创建 replacement"))
    stream = ManagedStream(source, timeout=None, started_at=time.perf_counter(),
                           context=AttemptContext(cast(Any, Session()), ModelStep(model={})),
                           terminal=terminal, resume=resume)
    response = LLMResponse(_stream=stream if managed else source,
                           _upper=cast(LLMRequest, SimpleNamespace(context_manager=None)),
                           _auto_append_response=True, payloads=[],
                           model_set=cast(Any, [{"max_retry": 2, "retry_interval": 0}]))
    return response, stream, terminal, resume


def iterator_for(response: LLMResponse, entry: str) -> AsyncGenerator[Any, None]:
    """返回三个生成器消费入口之一。"""
    if entry == "events":
        return cast(AsyncGenerator[Any, None], response.stream_events())
    if entry == "text":
        return cast(AsyncGenerator[Any, None], response.__aiter__())
    return cast(AsyncGenerator[Any, None], response.stream_with_buffer(1))


async def consume(response: LLMResponse, entry: str,
                  callback: Callable[[Any], Awaitable[None]] | None = None) -> None:
    """完整消费指定入口，生成器由显式作用域负责退出。"""
    async def noop(value: Any) -> None:
        """忽略回调值，隔离供应商读取故障。"""
    if entry == "await":
        await response
    elif entry == "text_callback":
        await response.stream_with_callback(callback or noop)
    elif entry == "event_callback":
        await response.stream_events_with_callback(callback or noop)
    else:
        iterator = iterator_for(response, entry)
        try:
            async for _ in iterator:
                pass
        finally:
            await iterator.aclose()


def assert_safe_close_diagnostic(error: BaseException, *, secret: str) -> None:
    """断言真实关闭诊断存在、字段完整，且不含清理错误原文或 traceback。"""
    notes = getattr(error, "__notes__", [])
    expected = "LLM stream cleanup failed: stage=close; error_type=RuntimeError; handoff_blocked=true"
    assert expected in notes
    diagnostic = "\n".join(notes)
    assert secret not in diagnostic
    assert "Traceback" not in diagnostic


@pytest.fixture
def manager() -> TaskManager:
    """提供无进程池工作的项目任务管理器。"""
    manager = TaskManager()
    manager.shutdown_process_pool(wait=False)
    return manager


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("kind", ["value", "transport", "cancel"])
async def test_sdk_read_primary_survives_close_failure(entry: str, kind: str) -> None:
    """真实 SDK/HTTPX 整链保留读错身份或既定分类，真实 body 只关闭一次。"""
    primary = {"value": ValueError("read-primary"), "transport": httpx.ReadError("read-primary"),
               "cancel": asyncio.CancelledError("read-primary")}[kind]
    wire = _Wire(fail_at=1, read_error=primary, close_error=True)
    client = _adapter([wire])
    entry_model = model()
    entry_model["max_retry"] = 0
    request = LLMRequest(model_set=[entry_model], clients=ModelClientRegistry(gemini=client),
                         policy=RoundRobinPolicy(), enable_metrics=False)
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("offline"))]
    try:
        response = await request.send(stream=True)
        expected = {"value": ValueError, "transport": ConnectionError, "cancel": asyncio.CancelledError}[kind]
        with pytest.raises(expected) as raised:
            await consume(response, entry)
        if kind != "transport":
            assert raised.value is primary
        assert wire.closed == 1
        assert_safe_close_diagnostic(raised.value, secret="wire close failed")
        assert response._stream.phase == ("cancelled" if kind == "cancel" else "failed")
        # 显式 adapter 重复关闭仍报告缓存故障，不得改成静默成功。
        with pytest.raises(RuntimeError, match="wire close failed"):
            await response._stream.source.aclose()
        assert wire.closed == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("entry", ENTRIES)
async def test_real_task_cancel_remains_cancelled(entry: str, manager: TaskManager) -> None:
    """Event 固定真实取消窗口，清理普通失败不能将任务变成普通失败。"""
    wire = _Wire(block_at=1, close_error=True)
    client = _adapter([wire])
    entry_model = model()
    entry_model["max_retry"] = 0
    request = LLMRequest(model_set=[entry_model], clients=ModelClientRegistry(gemini=client),
                         policy=RoundRobinPolicy(), enable_metrics=False)
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("offline"))]
    try:
        response = await request.send(stream=True)
        task = manager.create_task(consume(response, entry), name="p1-real-cancel", daemon=True).task
        assert task is not None
        await asyncio.wait_for(wire.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert task.cancelled()
        assert_safe_close_diagnostic(raised.value, secret="wire close failed")
        assert wire.closed == 1 and response._stream.phase == "cancelled"
    finally:
        wire.release.set()
        await client.aclose()


@pytest.mark.parametrize("entry", ENTRIES)
async def test_cleanup_failure_blocks_budget_and_outer_retry(entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """§11 原分类错保持身份；重试计数、session、step 和外层重发均不推进。"""
    primary = LLMTimeoutError("read-primary")
    source = Source(error=primary, close_error=RuntimeError("secret-close-url"), text=False)
    response, stream, terminal, resume = managed_response(source)
    original_step = stream.context.step
    retry_send = AsyncMock(side_effect=AssertionError("response 不得重发"))
    # 类级替换适配 slots 响应，仅观察重发入口，不替换实际消费实现。
    monkeypatch.setattr(LLMResponse, "_retry_send", retry_send)
    with pytest.raises(LLMTimeoutError) as raised:
        await consume(response, entry)
    assert raised.value is primary
    assert stream.context.retry_count == 0 and stream.context.session.calls == 0
    assert stream.context.step is original_step
    resume.assert_not_awaited()
    retry_send.assert_not_awaited()
    assert terminal.await_count == 1
    assert terminal.await_args.args[0] is primary and terminal.await_args.args[3] == "failed"
    assert_safe_close_diagnostic(primary, secret="secret-close-url")


async def test_terminal_waits_for_close_then_reports_pure_failure(manager: TaskManager) -> None:
    """正常耗尽先等待实际关闭，唯一关闭错误只能记 failed 且最多一次。"""
    failure = RuntimeError("close-only")
    source = Source(text=False, close_error=failure, block_close=True)
    response, stream, terminal, _ = managed_response(source)
    task = manager.create_task(consume(response, "await"), name="p1-close-terminal", daemon=True).task
    assert task is not None
    await asyncio.wait_for(source.close_entered.wait(), 2)
    before_close = terminal.await_count
    source.close_release.set()
    with pytest.raises(RuntimeError) as raised:
        await task
    assert before_close == 0
    assert raised.value is failure
    assert terminal.await_count == 1 and stream.phase == "failed"
    assert terminal.await_args.args[0] is failure


@pytest.mark.parametrize("entry", ["text_callback", "event_callback", "context"])
async def test_consumer_primary_survives_cleanup_and_clears_state(entry: str) -> None:
    """调用方主错身份不被清理替换，半成品工具及 provider state 必须清除。"""
    primary = ValueError("consumer-primary")
    source = Source(close_error=RuntimeError("close-secondary"))
    response, stream, terminal, resume = managed_response(source)
    response.call_list = [ToolCall(id="partial", name="lookup", args="{")]
    response.provider_state = ProviderReplayState("gemini", ("endpoint", "v1", "model"), "[]", "test")

    async def fail(value: Any) -> None:
        """模拟消费者逻辑失败，不属于 provider 读取错误。"""
        raise primary

    with pytest.raises(ValueError) as raised:
        if entry == "context":
            async with response:
                raise primary
        else:
            await consume(response, entry, fail)
    assert raised.value is primary
    assert_safe_close_diagnostic(primary, secret="close-secondary")
    assert response.call_list == [] and response.provider_state is None
    assert stream.phase == "abandoned"
    assert terminal.await_count == 1
    resume.assert_not_awaited()


@pytest.mark.parametrize("entry", ["text_callback", "event_callback"])
async def test_legacy_callback_retryable_error_is_not_provider_retry(entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """非托管旧流 callback 的可重试类型不应驱动重发或扩大关闭所有权。"""
    response, _, _, _ = managed_response(Source(), managed=False)
    primary = LLMAPIError("caller-failure", status_code=503)
    retry_send = AsyncMock(side_effect=AssertionError("callback 不得重发"))
    monkeypatch.setattr(LLMResponse, "_retry_send", retry_send)

    async def fail(value: Any) -> None:
        """抛出类型上可重试、来源上属于调用方的错误。"""
        raise primary

    with pytest.raises(LLMAPIError) as raised:
        await consume(response, entry, fail)
    assert raised.value is primary
    retry_send.assert_not_awaited()
    assert response._stream.closed == 0


@pytest.mark.parametrize("entry", ["events", "text", "buffer"])
async def test_generator_close_reports_pure_close_error(entry: str) -> None:
    """GeneratorExit 不能隐藏显式生成器 aclose 的唯一资源关闭错误。"""
    failure = RuntimeError("close-only")
    response, _, terminal, _ = managed_response(Source(close_error=failure))
    iterator = iterator_for(response, entry)
    await anext(iterator)
    with pytest.raises(RuntimeError) as raised:
        await iterator.aclose()
    assert raised.value is failure
    assert terminal.await_count == 1
    assert terminal.await_args.args[0] is failure
    assert terminal.await_args.args[3] == "failed"
    assert response.call_list == [] and response.provider_state is None


async def test_new_cancel_during_cleanup_overrides_read_error(manager: TaskManager) -> None:
    """清理 await 实际收到的新取消必须传播，不按既有主错吞掉。"""
    source = Source(text=False, error=ValueError("read-primary"), block_close=True)
    response, _, _, resume = managed_response(source)
    task = manager.create_task(consume(response, "await"), name="p1-cleanup-cancel", daemon=True).task
    assert task is not None
    await asyncio.wait_for(source.close_entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    source.close_release.set()
    assert task.cancelled()
    resume.assert_not_awaited()


async def test_concurrent_close_waits_for_real_cleanup(manager: TaskManager) -> None:
    """并发关闭调用者须等待同一实际关闭结果，而非提前获得假成功。"""
    source = Source(text=False, block_close=True)
    response, _, terminal, _ = managed_response(source)
    first = manager.create_task(response.aclose(), name="p1-close-first", daemon=True).task
    assert first is not None
    await asyncio.wait_for(source.close_entered.wait(), 2)
    second_entered = asyncio.Event()

    async def close_second() -> None:
        """标记第二个关闭者开始，再等待真实关闭。"""
        second_entered.set()
        await response.aclose()

    second = manager.create_task(close_second(), name="p1-close-second", daemon=True).task
    assert second is not None
    await second_entered.wait()
    premature = second.done()
    source.close_release.set()
    await first
    await second
    assert not premature
    assert source.closed == 1 and terminal.await_count == 1


@pytest.mark.parametrize("control", [False, True])
async def test_terminal_observer_cannot_replace_result(control: bool) -> None:
    """普通观察故障不覆盖尝试主结果；控制异常仍传播，观察至多一次。"""
    response, stream, terminal, _ = managed_response(Source(text=False))
    primary = asyncio.CancelledError("observer-cancel") if control else ValueError("observer-error")
    terminal.side_effect = primary
    if control:
        with pytest.raises(asyncio.CancelledError) as raised:
            await response
        assert raised.value is primary
    else:
        assert await response == ""
    assert terminal.await_count == 1
    assert stream.finished
