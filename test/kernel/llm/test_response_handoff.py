"""固定响应交付前取消、关闭消费权和测试自有资源的显式生命周期。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.core.components.types import EventType
from src.kernel.concurrency import TaskManager
from src.kernel.event import EventDecision, get_event_bus
from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.llm.exceptions import LLMResponseConsumedError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from src.kernel.llm.request_attempt import ManagedStream
from src.kernel.llm.response import LLMResponse
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_p1_exception_arbitration import ENTRIES, Source, consume, managed_response
from test.kernel.llm.test_review_gemini_resources import _Wire, _adapter


async def _finish_tasks(manager: TaskManager, *, cancel_pending: bool = True) -> None:
    """只取消并等待本局部管理器的任务；清理中取消延后传播而非放弃等待。"""
    tasks = [info.task for info in manager.get_all_tasks() if info.task is not None]
    for task in tasks:
        if cancel_pending and not task.done() and not task.cancelling():
            task.cancel()
    waiter = asyncio.gather(*tasks, return_exceptions=True)
    interrupted: asyncio.CancelledError | None = None
    while True:
        try:
            results = await asyncio.shield(waiter)
            break
        except asyncio.CancelledError as error:
            if waiter.cancelled():
                raise
            interrupted = error
    errors: list[BaseException] = []
    for result in results:
        if isinstance(result, LLMResponse):
            try:
                await result.aclose()
            except BaseException as error:
                errors.append(error)
        elif isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            errors.append(result)
    manager.cleanup_tasks()
    assert all(task.done() for task in tasks)
    assert not manager.get_active_tasks()
    if interrupted is not None:
        errors.insert(0, interrupted)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("测试任务清理失败", errors)


@asynccontextmanager
async def _fixture_resources() -> AsyncIterator[AsyncExitStack]:
    """完成本地清理后将 teardown 取消显式报错，使 pytest 继续其他 fixture 的终结。"""
    try:
        async with AsyncExitStack() as resources:
            yield resources
    except asyncio.CancelledError as error:
        # pytest 的普通 fixture 错误路径会继续执行其余 finalizer，cause 保留取消。
        raise RuntimeError("测试 fixture 清理收到取消，已执行登记的释放") from error


@dataclass
class HandoffCase:
    """仅持有本次测试创建的 manager、SDK 客户端和同步窗口。"""

    manager: TaskManager
    wire: _Wire
    client: GeminiChatClient
    entered: asyncio.Event
    release: asyncio.Event
    terminal: list[dict[str, Any]]


@pytest_asyncio.fixture
async def handoff_case(close_error: bool) -> AsyncIterator[HandoffCase]:
    """逐项登记清理，setup 失败及测试异常均不遗漏已有资源。"""
    async with _fixture_resources() as resources:
        wire = _Wire(close_error=close_error)
        client = _adapter([wire])
        async def close_client() -> None:
            """任务退出后回收空闲 SDK/HTTP 资源，并检查真实关闭。"""
            leases = [lease for pool in client._pools.values() for lease in pool.values()]
            await client.aclose()
            assert all(lease.active == 0 and lease.http.is_closed for lease in leases)
            assert all(not pool for pool in client._pools.values())

        resources.push_async_callback(close_client)
        manager = TaskManager()
        resources.callback(manager.shutdown_process_pool, wait=False)
        resources.push_async_callback(_finish_tasks, manager)
        entered, release = asyncio.Event(), asyncio.Event()
        resources.callback(release.set)
        terminal: list[dict[str, Any]] = []

        async def pause_after(
            event_name: str, params: dict[str, Any],
        ) -> tuple[EventDecision, dict[str, Any]]:
            """让请求停在流已建立而响应尚未返回的确定窗口。"""
            entered.set()
            await release.wait()
            return EventDecision.SUCCESS, params

        async def record_terminal(
            event_name: str, params: dict[str, Any],
        ) -> tuple[EventDecision, dict[str, Any]]:
            """记录流的真实终态，不将取消发布成完成或重复计数。"""
            terminal.append(dict(params))
            return EventDecision.SUCCESS, params

        bus = get_event_bus()
        resources.callback(bus.unsubscribe, EventType.AFTER_LLM_REQUEST, pause_after)
        bus.subscribe(EventType.AFTER_LLM_REQUEST, pause_after)
        resources.callback(bus.unsubscribe, "on_llm_stream_finished", record_terminal)
        bus.subscribe("on_llm_stream_finished", record_terminal)
        yield HandoffCase(manager, wire, client, entered, release, terminal)
    assert manager._process_pool is None
    assert not manager.get_active_tasks()
    assert all(not pool for pool in client._pools.values())


@dataclass
class ClosingCase:
    """拥有一个受控响应与其关闭任务，未使用的包装器不会被创建。"""

    manager: TaskManager
    source: Source
    response: LLMResponse
    stream: ManagedStream | None
    terminal: AsyncMock
    resume: AsyncMock


@pytest_asyncio.fixture
async def closing_case(managed: bool) -> AsyncIterator[ClosingCase]:
    """先释放关闭窗口并等待任务，再回收自有池与未启动的响应。"""
    async with _fixture_resources() as resources:
        source = Source(text=False, block_close=True)
        if managed:
            response, stream, terminal, resume = managed_response(source)
        else:
            stream, terminal, resume = None, AsyncMock(), AsyncMock()
            response = LLMResponse(
                _stream=source, _upper=cast(Any, object()), _auto_append_response=True,
                payloads=[], model_set=cast(Any, [{"max_retry": 2, "retry_interval": 0}]),
            )

        async def close_unstarted_response() -> None:
            """没有开始关闭的响应仍由测试释放；已关闭源不重复关闭。"""
            if source.closed == 0:
                await response.aclose()
            assert source.closed == 1

        resources.push_async_callback(close_unstarted_response)
        # manager 构造也可能失败，关闭窗口必须先登记且早于响应关闭执行。
        resources.callback(source.close_release.set)
        manager = TaskManager()
        resources.callback(manager.shutdown_process_pool, wait=False)
        resources.push_async_callback(_finish_tasks, manager, cancel_pending=False)
        resources.callback(source.close_release.set)
        yield ClosingCase(manager, source, response, stream, terminal, resume)
    assert manager._process_pool is None
    assert not manager.get_active_tasks()


@pytest.mark.parametrize("close_error", [False, True])
async def test_cancel_before_handoff_closes_sdk_body_and_releases_lease(
    close_error: bool, handoff_case: HandoffCase,
) -> None:
    """AFTER 事件等待中取消时，未交付的真实 SDK 流仍恰好关闭一次。"""
    case = handoff_case
    entry = model()
    entry["max_retry"] = 0
    request = LLMRequest(
        model_set=[entry], clients=ModelClientRegistry(gemini=case.client),
        policy=RoundRobinPolicy(), enable_metrics=False,
    )
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("offline"))]
    task = case.manager.create_task(
        request.send(stream=True), name="pr181-undelivered-stream", daemon=True,
    ).task
    assert task is not None
    await asyncio.wait_for(case.entered.wait(), 2)
    lease = next(iter(case.client._pools[asyncio.get_running_loop()].values()))
    assert lease.active == 1 and case.wire.closed == 0 and case.wire.reads == 1
    task.cancel("cancel-before-handoff")
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert task.cancelled()
    assert raised.value.args == ("cancel-before-handoff",)
    assert case.wire.closed == 1
    assert lease.active == 0
    assert not lease.http.is_closed
    assert len(case.terminal) == 1
    assert case.terminal[0]["phase"] == "cancelled"
    assert case.terminal[0]["success"] is False
    if close_error:
        notes = "\n".join(getattr(raised.value, "__notes__", []))
        assert "error_type=RuntimeError" in notes
        assert "wire close failed" not in notes


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("managed", [True, False])
async def test_closing_response_rejects_new_consumers_before_first_await(
    entry: str, managed: bool, closing_case: ClosingCase,
) -> None:
    """关闭底层源暂停期间，各消费入口都必须拒绝，而非静默得到空响应。"""
    case = closing_case
    closing = case.manager.create_task(
        case.response.aclose(), name="pr181-close-response", daemon=True,
    ).task
    assert closing is not None
    await asyncio.wait_for(case.source.close_entered.wait(), 2)
    assert not closing.done()
    with pytest.raises(LLMResponseConsumedError):
        await asyncio.wait_for(consume(case.response, entry), 1)
    assert case.source.closed == 1
    case.resume.assert_not_awaited()
    case.source.close_release.set()
    await closing
    assert case.response._consumed
    assert case.source.closed == 1
    if managed:
        assert case.terminal.await_count == 1
        assert case.stream is not None and case.stream.phase == "abandoned"
