"""补充 P1-A 缓存控制异常、历史取消、成功交接与独立 replacement 终态。"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from unittest.mock import AsyncMock

import pytest

from src.kernel.concurrency import TaskManager
from src.kernel.llm.exceptions import LLMAPIError, LLMTimeoutError
from src.kernel.llm.model_client.gemini_client import _ClientLease, _OwnedStream
from src.kernel.llm.payload import ToolCall
from src.kernel.llm.response import LLMResponse
from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.base import ModelStep, PolicySession
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_p1_exception_arbitration import (
    ENTRIES, Source, assert_safe_close_diagnostic, consume, managed_response,
)
from test.kernel.llm.test_review_gemini_resources import _Wire


class RecordingSession:
    """仅观察真实 RoundRobin 会话返回的步骤和输入错误，不替换预算算法。"""

    def __init__(self, session: PolicySession) -> None:
        """保存真实策略和公开协议调用记录。"""
        self.session = session
        self.first_steps: list[ModelStep] = []
        self.error_steps: list[ModelStep] = []
        self.errors: list[BaseException] = []

    def first(self) -> ModelStep:
        """记录真实会话给出的独立请求起始步骤。"""
        step = self.session.first()
        self.first_steps.append(step)
        return step

    def next_after_error(self, error: BaseException) -> ModelStep:
        """原样转交错误并记录真实预算推进结果。"""
        step = self.session.next_after_error(error)
        self.errors.append(error)
        self.error_steps.append(step)
        return step

    def record_success(self, *, latency: float = 0.0, tokens: int = 0) -> None:
        """保持真实策略的成功记账契约。"""
        self.session.record_success(latency=latency, tokens=tokens)


class RecordingPolicy:
    """为公共请求保留策略身份，可选显式复用同一个真实策略会话。"""

    def __init__(self, *, reuse_session: bool) -> None:
        """绑定 RoundRobin，并初始化新会话记录。"""
        self.policy = RoundRobinPolicy()
        self.reuse_session = reuse_session
        self.sessions: list[RecordingSession] = []

    def new_session(self, *, model_set: Any, request_name: str) -> PolicySession:
        """正常创建或显式复用真实会话，供身份和预算断言使用。"""
        if self.reuse_session and self.sessions:
            return self.sessions[0]
        session = RecordingSession(self.policy.new_session(model_set=model_set, request_name=request_name))
        self.sessions.append(session)
        return session


@pytest.fixture
def manager() -> TaskManager:
    """统一通过 TaskManager 创建确定性竞态任务。"""
    manager = TaskManager()
    manager.shutdown_process_pool(wait=False)
    return manager


async def test_historical_cancel_count_does_not_mark_later_abandon(manager: TaskManager) -> None:
    """已被调用方处理的取消不应把随后普通 callback 失败重新解释成取消。"""
    response, stream, terminal, _ = managed_response(Source(close_error=RuntimeError("close-secondary")))
    primary = ValueError("caller-primary")
    entered, release = asyncio.Event(), asyncio.Event()

    async def run() -> None:
        """留下历史取消计数，再执行独立消费操作。"""
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass

        async def fail(value: Any) -> None:
            """回调失败和历史取消无关。"""
            raise primary

        with pytest.raises(ValueError) as raised:
            await consume(response, "text_callback", fail)
        assert raised.value is primary
        assert asyncio.current_task().cancelling() == 1

    task = manager.create_task(run(), name="p1-history", daemon=True).task
    assert task is not None
    await entered.wait()
    task.cancel()
    await task
    assert not task.cancelled()
    assert stream.phase == "abandoned" and terminal.await_count == 1


async def test_cached_adapter_cancel_does_not_replace_read_primary() -> None:
    """adapter 已完成关闭缓存的控制异常不等于此刻的新取消。"""
    primary, cached = ValueError("read-primary"), asyncio.CancelledError("cached-close")
    events = Source(text=False, error=primary)
    resource = Source(text=False, close_error=cached)
    lease = _ClientLease(sdk=None, http=None, active=1)
    adapter = _OwnedStream(events, resource, lease)
    with pytest.raises(asyncio.CancelledError) as initial:
        await adapter.aclose()
    assert initial.value is cached
    response, stream, terminal, resume = managed_response(adapter)
    with pytest.raises(ValueError) as raised:
        await response
    assert raised.value is primary
    assert terminal.await_args.args[0] is primary and stream.phase == "failed"
    assert events.closed == 1 and resource.closed == 1 and lease.active == 0
    resume.assert_not_awaited()
    with pytest.raises(asyncio.CancelledError) as repeated:
        await adapter.aclose()
    assert repeated.value is cached


async def test_second_cancel_during_cancel_cleanup_is_not_swallowed(manager: TaskManager) -> None:
    """原取消进入清理后，清理 await 实际收到第二次取消仍原样传播。"""
    source = Source(block_read=True, block_close=True)
    response, stream, terminal, _ = managed_response(source)
    task = manager.create_task(consume(response, "await"), name="p1-second-cancel", daemon=True).task
    assert task is not None
    await source.entered.wait()
    task.cancel("first")
    await source.close_entered.wait()
    task.cancel("second")
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    source.close_release.set()
    assert raised.value.args == ("second",)
    assert task.cancelled() and task.cancelling() == 2
    assert stream.phase == "cancelled" and terminal.await_count == 1


async def test_successful_close_precedes_retry_and_replacement_owns_terminal() -> None:
    """正常清理保留原 buffered 预算，replacement 失败不重记原尝试终态。"""
    primary, replacement_error = LLMAPIError("first", status_code=503), ValueError("replacement-primary")
    first_source = Source(error=primary)
    response, stream, first_terminal, resume = managed_response(first_source)
    replacement, next_stream, next_terminal, _ = managed_response(Source(error=replacement_error))

    async def replace(context: Any) -> LLMResponse:
        """断言释放旧源后才建立 replacement，且原策略只推进一步。"""
        assert first_source.closed == 1
        assert context.retry_count == 1 and context.session.calls == 1
        return replacement

    resume.side_effect = replace
    with pytest.raises(ValueError) as raised:
        await response
    assert raised.value is replacement_error
    assert first_terminal.await_count == next_terminal.await_count == 1
    assert first_terminal.await_args.args[0] is primary
    assert next_terminal.await_args.args[0] is replacement_error
    assert stream.phase == next_stream.phase == "failed"
    resume.assert_awaited_once()


async def test_buffered_completed_provider_not_rolled_back_by_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """buffered provider 已成功关闭后 callback 失败，只清消费者状态不改 terminal。"""
    source = Source()
    response, stream, terminal, _ = managed_response(source)
    stream.buffered = True
    primary = LLMTimeoutError("caller-primary")

    async def fail(value: Any) -> None:
        """模拟完成 provider 后消费缓冲区时失败。"""
        response.call_list = [ToolCall(id="partial", name="lookup", args="{")]
        assert source.closed == 1 and stream.phase == "completed"
        raise primary

    retry = AsyncMock(side_effect=AssertionError("callback 不可重试"))
    monkeypatch.setattr(LLMResponse, "_retry_send", retry)
    with pytest.raises(LLMTimeoutError) as raised:
        await consume(response, "text_callback", fail)
    assert raised.value is primary
    assert stream.phase == "completed" and terminal.await_count == 1
    assert terminal.await_args.args[0] is None
    assert response.call_list == [] and response.provider_state is None
    retry.assert_not_awaited()


async def test_prior_close_error_does_not_skip_owned_replacement() -> None:
    """显式关闭时前项普通错误不跳过已经拥有的 replacement。"""
    error = RuntimeError("old-cleanup")
    source = Source(text=False, close_error=error)
    response, stream, _, _ = managed_response(source)
    next_source = Source(text=False)
    replacement, next_stream, next_terminal, _ = managed_response(next_source)
    stream._replacement = next_stream
    with pytest.raises(RuntimeError) as raised:
        await response.aclose()
    assert raised.value is error
    assert source.closed == next_source.closed == 1
    assert next_terminal.await_count == 1 and next_stream.phase == "abandoned"


async def test_independent_request_retains_original_retry_budget() -> None:
    """清理失败的流不推进共享 session；后续独立请求仍能正常用一次策略。"""
    first, stream, _, _ = managed_response(Source(text=False, error=LLMTimeoutError("first"), close_error=RuntimeError("close")))
    with pytest.raises(LLMTimeoutError):
        await first
    second, second_stream, _, resume = managed_response(Source(text=False, error=LLMTimeoutError("second")))
    second_stream.context.session = stream.context.session
    final, final_stream, _, _ = managed_response(Source())
    resume.return_value = final
    resume.side_effect = None
    assert await second == "one"
    assert second_stream.context.session.calls == 1 and second_stream.context.retry_count == 1
    assert final_stream.phase == "completed"
    assert stream.context.retry_count == 0


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("reuse_session", [False, True])
async def test_public_sdk_close_failure_never_opens_another_request(entry: str, reuse_session: bool) -> None:
    """关闭失败不扣预算；独立公共请求实际用满两次重试，且可显式复用同会话。"""
    primary = LLMTimeoutError("read-primary")
    followup_errors = [LLMTimeoutError("followup-first"), LLMTimeoutError("followup-second")]
    first = _Wire(fail_at=1, read_error=primary, close_error=True)
    retry_wires = [_Wire(fail_at=1, read_error=error) for error in followup_errors]
    final = _Wire()
    wires = iter([first, *retry_wires, final])
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        """仅 MockTransport 响应，每个公共实际请求占用独立 body。"""
        nonlocal requests
        requests += 1
        return httpx.Response(200, stream=next(wires), headers={"content-type": "text/event-stream"})

    client = GeminiChatClient(transport=httpx.MockTransport(handler))
    entry_model = model()
    entry_model["max_retry"] = 2
    policy = RecordingPolicy(reuse_session=reuse_session)
    request = LLMRequest(model_set=[entry_model], clients=ModelClientRegistry(gemini=client),
                         policy=policy, enable_metrics=False)
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("offline"))]
    try:
        response = await request.send(stream=True)
        stream = response._stream
        session = stream.context.session
        original_step = stream.context.step
        with pytest.raises(LLMTimeoutError) as raised:
            await consume(response, entry)
        assert raised.value is primary
        assert_safe_close_diagnostic(primary, secret="wire close failed")
        assert requests == 1 and first.closed == 1
        assert all(wire.closed == 0 for wire in [*retry_wires, final])
        assert stream.context.retry_count == 0 and stream.context.step is original_step
        assert stream._replacement is None and stream.phase == "failed"
        assert session is policy.sessions[0] and session.errors == session.error_steps == []
        assert session.first_steps == [original_step]

        # 显式后续独立请求的首尝试与第一次重试都失败，直到配置允许的最后一次才成功。
        independent = await request.send(stream=True)
        next_stream = independent._stream
        next_session = next_stream.context.session
        assert (next_session is session) is reuse_session
        assert next_stream.context is not stream.context and next_stream.context.retry_count == 0
        assert await independent == "012"
        assert requests == 4 and all(wire.closed == 1 for wire in [first, *retry_wires, final])
        assert next_stream.context.retry_count == 2
        assert next_session.errors == followup_errors
        assert len(next_session.error_steps) == 2
        assert [step.meta["attempt"] for step in next_session.error_steps] == [2, 3]
        assert [step.meta["retry"] for step in next_session.error_steps] == [1, 2]
        assert all(step.model is entry_model for step in next_session.error_steps)
        assert next_stream.context.step is next_session.error_steps[-1]
        assert stream.context.retry_count == 0 and stream.context.step is original_step
        assert len(policy.sessions) == (1 if reuse_session else 2)
        assert len(next_session.first_steps) == (2 if reuse_session else 1)
    finally:
        await client.aclose()


async def test_concurrent_close_failure_replays_same_result(manager: TaskManager) -> None:
    """并发/重复显式关闭同等报告故障，真实源关闭恰好一次。"""
    failure = RuntimeError("close-only")
    source = Source(text=False, block_close=True, close_error=failure)
    response, _, terminal, _ = managed_response(source)
    first = manager.create_task(response.aclose(), name="p1-failed-close-first", daemon=True).task
    assert first is not None
    await source.close_entered.wait()
    entered = asyncio.Event()

    async def second_close() -> None:
        """固定并发第二个关闭者已经开始的窗口。"""
        entered.set()
        await response.aclose()

    second = manager.create_task(second_close(), name="p1-failed-close-second", daemon=True).task
    assert second is not None
    await entered.wait()
    premature = second.done()
    source.close_release.set()
    for task in (first, second):
        with pytest.raises(RuntimeError) as raised:
            await task
        assert raised.value is failure
    with pytest.raises(RuntimeError) as repeated:
        await response.aclose()
    assert repeated.value is failure
    assert not premature and source.closed == 1 and terminal.await_count == 1
