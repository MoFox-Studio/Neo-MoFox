"""检查 Gemini 测试自身在断言失败、取消和部分构造后的资源清理。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from uuid import uuid4
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from io import BytesIO
from typing import Any

import httpx
import pytest

from src.kernel.concurrency import TaskManager
from src.kernel.llm.model_client import gemini_client
from src.kernel.llm.model_client.base import ModelClientResult
from src.kernel.llm.model_client.gemini_client import (
    GeminiChatClient,
    _ClientLease,
    _OwnedStream,
    _response_owner,
)
from test.kernel.llm import test_gemini_coverage_edges as edges
from test.kernel.llm import test_pr181_gemini_continuations as continuations
from test.kernel.llm.test_gemini_client import model


def _assert_lease_closed(lease: _ClientLease) -> None:
    """检查实际同步/异步客户端状态，而不只统计 close 调用次数。"""
    assert lease.active == 0
    assert lease.http.is_closed
    assert lease.sdk._api_client._httpx_client.is_closed
    assert lease.sdk._api_client._async_httpx_client.is_closed


@pytest.mark.parametrize("case,args", [
    (edges.test_invalid_model_settings_fail_before_http, ({"max_tokens": 0}, "max_tokens")),
    (edges.test_generation_config_cannot_override_reserved_fields, ("temperature",)),
    (edges.test_invalid_timeout_is_rejected, (0,)),
    (edges.test_transport_overrides_cannot_change_auth_or_routing, ({"query": {"key": "dummy"}},)),
    (edges.test_invalid_model_identifier_is_rejected, ("a/b",)),
    (edges.test_model_set_requires_one_mapping, (None,)),
    (edges.test_empty_conversation_is_rejected, ([],)),
    (edges.test_sdk_constructor_failure_closes_injected_http_client, ()),
])
async def test_configuration_case_closes_client_after_unexpected_success(
    monkeypatch: pytest.MonkeyPatch,
    case: Callable[..., Awaitable[None]],
    args: tuple[Any, ...],
) -> None:
    """若被测校验回归并成功分配资源，测试断言失败仍必须关闭自有客户端。"""
    async with aclosing(GeminiChatClient(transport=httpx.MockTransport(
        lambda request: pytest.fail("unexpected HTTP"),
    ))) as client:
        # 预建真实资源，避免构造失败用例内部的 SDK patch 阻止故障注入。
        lease = await client._get_client(model())
        lease.active -= 1
        monkeypatch.setattr(edges, "GeminiChatClient", lambda **kwargs: client)

        async def unexpected_success(*args: Any, **kwargs: Any) -> ModelClientResult:
            """模拟本应被拒绝的请求意外成功，保留真实池资源供退出状态检查。"""
            return ModelClientResult(message="unexpected success")

        monkeypatch.setattr(edges, "create", unexpected_success)
        with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
            await case(*args)
        _assert_lease_closed(lease)
        assert not client._pools[asyncio.get_running_loop()]


async def test_constructor_case_reclaims_http_after_failed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK 构造失败且产品漏关 HTTP 时，原测试仍报错并兜底回收自有 HTTP。"""
    clients: list[httpx.AsyncClient] = []
    original_http = httpx.AsyncClient
    async with edges._test_resources() as recovery:
        def track_http(**kwargs: Any) -> httpx.AsyncClient:
            """记录实际分配的 HTTP，并为红回归登记独立清理。"""
            client = original_http(**kwargs)
            recovery.push_async_callback(client.aclose)
            clients.append(client)
            return client

        async def omit_cleanup(resource: Any, primary: BaseException) -> None:
            """模拟构造异常处理遗漏资源关闭，而不改变原构造错误。"""

        monkeypatch.setattr(httpx, "AsyncClient", track_http)
        monkeypatch.setattr(gemini_client, "_close_after_error", omit_cleanup)
        with pytest.raises(AssertionError):
            await edges.test_sdk_constructor_failure_closes_injected_http_client()
        assert len(clients) == 1
        assert clients[0].is_closed


@pytest.mark.parametrize("interrupt", ["failure", "cancel"])
async def test_concurrent_case_drains_partial_borrows(
    monkeypatch: pytest.MonkeyPatch, interrupt: str,
) -> None:
    """部分借用成功后失败或取消，所有子任务及已获租约均须在测试退出前收回。"""
    client = GeminiChatClient(transport=httpx.MockTransport(
        lambda request: pytest.fail("unexpected HTTP"),
    ))
    borrowers: list[asyncio.Task[Any]] = []
    acquired: list[_ClientLease] = []
    observed: list[BaseException] = []
    ready, blocked = asyncio.Event(), asyncio.Event()
    failure = RuntimeError("injected borrow failure")
    original_get = client._get_client

    async def controlled_get(entry: dict[str, Any]) -> _ClientLease:
        """保留第一份真实租约，让后续借用在受控等待中失败或被取消。"""
        task = asyncio.current_task()
        assert task is not None
        borrowers.append(task)
        index = len(borrowers) - 1
        if index == 0:
            lease = await original_get(entry)
            acquired.append(lease)
            return lease
        if index == 7:
            ready.set()
        if index == 1 and interrupt == "failure":
            raise failure
        await blocked.wait()
        raise AssertionError("blocked borrower unexpectedly resumed")

    async def invoke_case() -> None:
        """捕获注入的退出信号，供外层逐项检查其身份及退出后的资源。"""
        try:
            await edges.test_concurrent_same_key_shares_one_client_and_counts_borrows()
        except (RuntimeError, ExceptionGroup, asyncio.CancelledError) as error:
            observed.append(error)

    try:
        monkeypatch.setattr(edges, "GeminiChatClient", lambda **kwargs: client)
        monkeypatch.setattr(client, "_get_client", controlled_get)
        async with edges._test_resources() as cleanup:
            manager = TaskManager(process_workers=1)
            cleanup.callback(manager.shutdown_process_pool, wait=True)
            cleanup.push_async_callback(edges._finish_tasks, manager)
            operation = manager.create_task(invoke_case(), name="gemini-case-probe").task
            await ready.wait()
            if interrupt == "cancel":
                operation.cancel()
            await operation
        assert len(observed) == 1
        if interrupt == "failure":
            error = observed[0]
            assert error is failure or (
                isinstance(error, ExceptionGroup) and error.exceptions == (failure,)
            )
        else:
            assert isinstance(observed[0], asyncio.CancelledError)
        assert all(task.done() for task in borrowers)
        assert len(acquired) == 1
        _assert_lease_closed(acquired[0])
        assert not client._pools[asyncio.get_running_loop()]
    finally:
        # 红回归也只回收本用例创建的任务和借用，不影响宿主或其他客户端。
        for task in borrowers:
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(*borrowers, return_exceptions=True)
        finally:
            for lease in acquired:
                if lease.active:
                    lease.active -= 1
            await client.aclose()


@pytest.mark.parametrize("case,args", [
    (edges.test_invalid_model_settings_fail_before_http, ({"max_tokens": 0}, "max_tokens")),
    (edges.test_generation_config_cannot_override_reserved_fields, ("temperature",)),
    (edges.test_invalid_timeout_is_rejected, (0,)),
    (edges.test_transport_overrides_cannot_change_auth_or_routing, ({"query": {"key": "dummy"}},)),
    (edges.test_invalid_model_identifier_is_rejected, ("a/b",)),
    (edges.test_model_set_requires_one_mapping, (None,)),
    (edges.test_empty_conversation_is_rejected, ([],)),
    (edges.test_sdk_constructor_failure_closes_injected_http_client, ()),
    (edges.test_custom_header_and_timeout_reach_real_sdk_request, ()),
])
@pytest.mark.parametrize("primary_type", [AssertionError, asyncio.CancelledError])
async def test_client_cleanup_preserves_body_error(
    monkeypatch: pytest.MonkeyPatch, case: Callable[..., Awaitable[None]],
    args: tuple[Any, ...], primary_type: type[BaseException],
) -> None:
    """所有客户端清理入口都保留体内原对象，同时完整报告清理 traceback。"""
    primary = primary_type("injected body error")
    cleanup_error = RuntimeError("client cleanup sentinel")
    async with edges._test_resources() as recovery:
        client = GeminiChatClient()
        original_close = client.aclose
        recovery.push_async_callback(original_close)

        async def fail_body(*args: Any, **kwargs: Any) -> ModelClientResult:
            """在测试体中传播已知的原始异常对象。"""
            raise primary

        async def fail_cleanup() -> None:
            """完成关闭后独立报告清理失败。"""
            await original_close()
            raise cleanup_error

        monkeypatch.setattr(edges, "GeminiChatClient", lambda **kwargs: client)
        monkeypatch.setattr(edges, "create", fail_body)
        monkeypatch.setattr(client, "aclose", fail_cleanup)
        with pytest.raises(primary_type) as caught:
            await case(*args)
        assert caught.value is primary
        notes = "\n".join(getattr(primary, "__notes__", []))
        assert "RuntimeError: client cleanup sentinel" in notes
        assert "Traceback (most recent call last)" in notes
        assert "fail_cleanup" in notes


async def test_cleanup_only_error_is_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有体异常时，清理失败自身仍须原样使测试失败。"""
    client = GeminiChatClient()
    original_close = client.aclose
    error = RuntimeError("cleanup only sentinel")
    async with edges._test_resources() as recovery:
        recovery.push_async_callback(original_close)

        async def fail_cleanup() -> None:
            """关闭真实客户端后注入唯一错误。"""
            await original_close()
            raise error

        monkeypatch.setattr(edges, "GeminiChatClient", lambda **kwargs: client)
        monkeypatch.setattr(client, "aclose", fail_cleanup)
        with pytest.raises(RuntimeError) as caught:
            await edges.test_invalid_timeout_is_rejected(0)
        assert caught.value is error


async def test_concurrent_case_owns_and_shuts_down_task_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实八次借用必须经本用例自有管理器创建，任务与其池均已结束。"""
    managers: list[TaskManager] = []
    pools: list[Any] = []
    tasks: list[asyncio.Task[Any]] = []
    async with edges._test_resources() as recovery:
        def make_manager(*args: Any, **kwargs: Any) -> TaskManager:
            """记录实际管理器和任务，只为本次新建池登记兜底。"""
            manager = TaskManager(*args, **kwargs)
            recovery.callback(manager.shutdown_process_pool, wait=True)
            managers.append(manager)
            pools.append(manager._process_pool)
            original_create = manager.create_task

            def create_task(*args: Any, **kwargs: Any) -> Any:
                """记录由管理器实际创建的任务。"""
                info = original_create(*args, **kwargs)
                tasks.append(info.task)
                return info

            monkeypatch.setattr(manager, "create_task", create_task)
            return manager

        monkeypatch.setattr(edges, "TaskManager", make_manager, raising=False)
        await edges.test_concurrent_same_key_shares_one_client_and_counts_borrows()
        assert len(managers) == 1
        assert len(tasks) == 8
        assert all(task.done() for task in tasks)
        assert not managers[0].get_active_tasks()
        assert managers[0]._process_pool is None
        assert pools[0]._shutdown_thread


class _Body(httpx.AsyncByteStream):
    """保留未读尽的真实 HTTP body，并在实际异步关闭完成后标记。"""

    def __init__(self) -> None:
        """初始化仅属于当前请求的关闭状态。"""
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """返回安全拦截首块，保持 SDK 的响应流处于预取后状态。"""
        yield ("data: " + json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}) + "\n\n").encode()

    async def aclose(self) -> None:
        """让出一次事件循环，确保调用方确实等待完成。"""
        await asyncio.sleep(0)
        self.closed = True


@pytest.mark.parametrize("primary_type", [AssertionError, asyncio.CancelledError])
@pytest.mark.parametrize("close_errors", [(), ("stream",), ("client",), ("stream", "client")])
async def test_stream_case_closes_owner_before_client_on_early_exit(
    monkeypatch: pytest.MonkeyPatch, primary_type: type[BaseException], close_errors: tuple[str, ...],
) -> None:
    """首次断言退出也要关闭 body/owner；流清理报错不能阻止 SDK/HTTP 清理。"""
    primary = primary_type("injected first assertion")
    close_error = RuntimeError("injected stream close failure")
    client_error = RuntimeError("injected client close failure")
    body = _Body()
    captured: list[_OwnedStream] = []
    original_create = edges.create
    previous_owner = _response_owner.get()
    async with edges._test_resources() as recovery:
        transport = await recovery.enter_async_context(httpx.MockTransport(
            lambda request: httpx.Response(200, stream=body, headers={"content-type": "text/event-stream"}),
        ))
        client = GeminiChatClient(transport=transport)
        original_client_close = client.aclose
        recovery.push_async_callback(original_client_close)

        async def failing_client_close() -> None:
            """真实客户端关闭后报错，检查原体异常仍为顶层。"""
            await original_client_close()
            raise client_error

        if "client" in close_errors:
            monkeypatch.setattr(client, "aclose", failing_client_close)
        monkeypatch.setattr(edges, "GeminiChatClient", lambda **kwargs: client)

        async def tracked_create(*args: Any, **kwargs: Any) -> ModelClientResult:
            """保留实际 SDK 流，仅在测试取得资源后的第一条断言注入中断。"""
            result = await original_create(*args, **kwargs)
            stream = result.stream_iter
            original_close = stream.aclose
            recovery.push_async_callback(original_close)
            assert isinstance(stream, _OwnedStream)
            captured.append(stream)

            def interrupt(stream: _OwnedStream) -> _OwnedStream:
                """在 anext 之前退出，避免产品消费路径替测试完成清理。"""
                raise primary

            async def failing_close() -> None:
                """真实关闭已完成后报告故障，验证外层客户端清理仍执行。"""
                await original_close()
                raise close_error

            monkeypatch.setattr(_OwnedStream, "__aiter__", interrupt)
            if "stream" in close_errors:
                monkeypatch.setattr(stream, "aclose", failing_close)
            return result

        monkeypatch.setattr(edges, "create", tracked_create)
        with pytest.raises(primary_type) as caught:
            await edges.test_stream_prompt_block_is_terminal_and_releases_lease()
        assert caught.value is primary
        notes = "\n".join(getattr(primary, "__notes__", []))
        for stage in close_errors:
            assert f"RuntimeError: injected {stage} close failure" in notes
            assert "Traceback (most recent call last)" in notes
            assert "failing_close" in notes if stage == "stream" else "failing_client_close" in notes
        assert len(captured) == 1
        stream = captured[0]
        assert body.closed
        assert stream.closed
        assert stream.owner is not None and stream.owner._closed
        assert all(response.is_closed for response in stream.owner.responses)
        assert all(body._closed for body in stream.owner.bodies)
        assert stream.source.ag_frame is None
        _assert_lease_closed(stream.lease)
        assert not client._pools[asyncio.get_running_loop()]
        assert _response_owner.get() is previous_owner


@pytest.mark.parametrize("error_type", [None, AssertionError, asyncio.CancelledError])
async def test_offline_fixture_awaits_client_cleanup(
    error_type: type[BaseException] | None,
) -> None:
    """直接推进现有 fixture，检查正常、断言失败及取消退出后的实际状态。"""
    async with aclosing(continuations.offline.__wrapped__()) as fixture:
        wire, client = await anext(fixture)
        wire.replies.append([{"text": "ok"}])
        result = await edges.create(client, model(), stream=True)
        async with aclosing(result.stream_iter) as stream:
            assert isinstance(stream, _OwnedStream)
            assert [event async for event in stream]
        assert stream.lease.active == 0
        assert not stream.lease.http.is_closed
        if error_type is None:
            with pytest.raises(StopAsyncIteration):
                await anext(fixture)
        else:
            error = error_type("injected fixture body exit")
            with pytest.raises(error_type) as caught:
                await fixture.athrow(error)
            assert caught.value is error
        _assert_lease_closed(stream.lease)
        assert not client._pools[asyncio.get_running_loop()]


def test_image_case_closes_its_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证内存图像缓冲区由创建它的测试显式关闭。"""
    with BytesIO() as data:
        monkeypatch.setattr(edges, "BytesIO", lambda: data)
        edges.test_valid_but_unsupported_image_mime_is_rejected()
        assert data.closed


@pytest.mark.parametrize("primary_type", [None, AssertionError, asyncio.CancelledError])
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_pool_case_finishes_idle_leases_after_cleanup_cancellation(
    monkeypatch: pytest.MonkeyPatch, primary_type: type[BaseException] | None, cancel_count: int,
) -> None:
    """真实池测试退出时取消一至两次，兜底前全部自有租约应已关闭且主错不变。"""
    primary = primary_type("pool body sentinel") if primary_type is not None else None
    leases: list[_ClientLease] = []
    cancellations: list[asyncio.CancelledError] = []
    sdk_finished: list[int] = []
    cleanup_started = False
    async with edges._test_resources() as recovery:
        transport = await recovery.enter_async_context(httpx.MockTransport(
            lambda request: pytest.fail("unexpected HTTP"),
        ))
        client = GeminiChatClient(transport=transport)
        original_close, original_get = client.aclose, client._get_client
        recovery.push_async_callback(original_close)
        with monkeypatch.context() as patcher:
            async def borrowed_close() -> None:
                """被测客户端没有关闭调用方借用传输的权限。"""
                raise AssertionError("borrowed transport was closed")

            async def tracked_get(entry: dict[str, Any]) -> _ClientLease:
                """只记录真实成功取得的租约，保持原测试的淘汰和容量断言。"""
                lease = await original_get(entry)
                index = len(leases)
                leases.append(lease)
                original_sdk_close = lease.sdk.aio.aclose

                async def close_sdk() -> None:
                    """真实 SDK 关闭完成后取消，验证其余池项不会被遗忘。"""
                    await original_sdk_close()
                    await asyncio.sleep(0)
                    sdk_finished.append(index)
                    if cleanup_started and len(cancellations) < cancel_count:
                        asyncio.current_task().cancel(f"pool-cleanup-cancel-{len(cancellations)}")
                        try:
                            await asyncio.sleep(0)
                        except asyncio.CancelledError as error:
                            cancellations.append(error)
                            raise

                patcher.setattr(lease.sdk.aio, "aclose", close_sdk)
                return lease

            async def close_client() -> None:
                """在原测试体的 active-only 关闭处注入主错，其后仍运行原 finally。"""
                nonlocal cleanup_started
                pool = client._pools[asyncio.get_running_loop()]
                if pool and all(lease.active for lease in pool.values()):
                    if primary is not None:
                        raise primary
                else:
                    cleanup_started = True
                await original_close()

            patcher.setattr(transport, "aclose", borrowed_close)
            patcher.setattr(edges, "GeminiChatClient", lambda **kwargs: client)
            patcher.setattr(client, "_get_client", tracked_get)
            patcher.setattr(client, "aclose", close_client)
            with pytest.raises(BaseException) as caught:
                await edges.test_pool_evicts_oldest_idle_but_preserves_active_leases()
            # 所有状态均在 recovery 的原始客户端兜底之前检查。
            assert len(leases) == 17
            pool = client._pools[asyncio.get_running_loop()]
            assert not pool, f"idle leases left before recovery: {len(pool)}"
            for lease in leases:
                _assert_lease_closed(lease)
            assert sorted(sdk_finished) == list(range(17)), sdk_finished
            assert len(cancellations) == cancel_count
            if primary is not None:
                assert caught.value is primary
                detail = "\n".join(getattr(primary, "__notes__", []))
            else:
                errors = caught.value.exceptions if isinstance(caught.value, BaseExceptionGroup) else (caught.value,)
                assert len(errors) == cancel_count
                assert all(error is expected for error, expected in zip(errors, cancellations))
                detail = "".join(traceback.format_exception(caught.value))
            assert "Traceback (most recent call last)" in detail and "close_sdk" in detail
            assert all(f"pool-cleanup-cancel-{index}" in detail for index in range(cancel_count))


@pytest.mark.parametrize("target", ["edges", "offline"])
async def test_terminal_client_cleanup_reports_no_progress(
    monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    """无法摘除任何空闲项时立即明确报错，不重试同一 lease 或忙等。"""
    attempts = 0
    cancellation = asyncio.CancelledError("no-progress cancellation sentinel")
    async with edges._test_resources() as recovery:
        transport = await recovery.enter_async_context(httpx.MockTransport(
            lambda request: httpx.Response(200, json=edges.response()),
        ))
        client = GeminiChatClient(transport=transport)
        original_close = client.aclose
        recovery.push_async_callback(original_close)
        with monkeypatch.context() as patcher:
            async def no_progress() -> None:
                """第一次取消后池不变；若错误实现重试，立刻退出以免红回归挂死。"""
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise cancellation
                raise AssertionError("retried cleanup without progress")

            patcher.setattr(client, "aclose", no_progress)
            if target == "edges":
                def make_client(**kwargs: Any) -> GeminiChatClient:
                    """保留原测试的记录传输，只替换该客户端的终结行为。"""
                    nonlocal client
                    client = GeminiChatClient(**kwargs)
                    recovery.push_async_callback(client.aclose)
                    patcher.setattr(client, "aclose", no_progress)
                    return client

                patcher.setattr(edges, "GeminiChatClient", make_client)
                with pytest.raises(BaseException) as caught:
                    await edges.test_custom_header_and_timeout_reach_real_sdk_request()
            else:
                patcher.setattr(continuations, "GeminiChatClient", lambda **kwargs: client)
                async with aclosing(continuations.offline.__wrapped__()) as fixture:
                    _, owned = await anext(fixture)
                    await edges.create(owned, model())
                    with pytest.raises(BaseException) as caught:
                        await anext(fixture)
            assert attempts == 1
            detail = "".join(traceback.format_exception(caught.value))
            assert "no progress" in detail and "no-progress cancellation sentinel" in detail
            error = caught.value.__cause__ if target == "offline" else caught.value
            assert isinstance(error, BaseExceptionGroup)
            assert error.exceptions[0] is cancellation
            assert isinstance(error.exceptions[1], RuntimeError)
            assert client._pools[asyncio.get_running_loop()]


async def test_idle_terminal_cleanup_preserves_active_lease_and_borrowed_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """终结帮助函数只回收空闲项，仍由原 owner 释放活动借用和传输。"""
    async with edges._test_resources() as recovery:
        transport = await recovery.enter_async_context(httpx.MockTransport(
            lambda request: pytest.fail("unexpected HTTP"),
        ))
        client = GeminiChatClient(transport=transport)
        recovery.push_async_callback(client.aclose)
        with monkeypatch.context() as patcher:
            async def forbidden_transport_close() -> None:
                """任何来自借用客户端的传输关闭都应显式失败。"""
                raise AssertionError("borrowed transport was closed")

            patcher.setattr(transport, "aclose", forbidden_transport_close)
            idle = await client._get_client(model())
            idle.active -= 1
            active = await client._get_client(dict(model(), api_key="dummy-active"))
            try:
                await edges._close_idle_client(client)
                _assert_lease_closed(idle)
                assert active.active == 1
                assert not active.http.is_closed
                assert not active.sdk._api_client._httpx_client.is_closed
                assert list(client._pools[asyncio.get_running_loop()].values()) == [active]
            finally:
                active.active -= 1


_OFFLINE_PYTEST_CONFTEST = r'''"""观察 offline 夹具在真实 pytest 各阶段的资源与状态恢复。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import pytest

from test.kernel.llm import test_pr181_gemini_continuations as target
from test.kernel.llm.test_pr181_gemini_continuations import offline

BODY, CANCEL_LEASES = __BODY__, __CANCEL_LEASES__
CLIENTS, STREAMS, LOOPS, CANCEL_ERRORS, PATCHES = [], [], [], [], []
SDK_FINISHED, HTTP_FINISHED, REPORTS = [], [], []
BODY_ERROR = None
ORIGINAL = target.GeminiChatClient
STATE = {"finalizers": []}


def closed_state() -> dict[str, bool]:
    """在兜底之前读取真实 SDK、HTTP、流和池的关闭完成状态。"""
    leases = [stream.lease for stream in STREAMS]
    return {
        "http_closed": all(lease.http.is_closed for lease in leases),
        "sdk_closed": all(lease.sdk._api_client._httpx_client.is_closed and
                          lease.sdk._api_client._async_httpx_client.is_closed for lease in leases),
        "inactive": all(lease.active == 0 for lease in leases),
        "pools_empty": all(not pool for client in CLIENTS for pool in client._pools.values()),
        "streams_closed": all(stream.closed and stream.source.ag_frame is None for stream in STREAMS),
        "owners_closed": all(stream.owner._closed and
                             all(response.is_closed for response in stream.owner.responses) and
                             all(body._closed for body in stream.owner.bodies) for stream in STREAMS),
        "close_awaited": SDK_FINISHED == [0, 1] and HTTP_FINISHED == [0, 1],
    }


@pytest.fixture
def tail() -> Iterator[None]:
    """标记最晚执行的 finalizer，观察前序取消是否阻断收尾。"""
    yield
    STATE["finalizers"].append("tail")


@pytest.fixture(autouse=True)
def monitor(tail: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """透传真实客户端创建，并在被测夹具清理后记录恢复状态。"""
    PATCHES.append(monkeypatch)
    def make_client(**kwargs: Any) -> target.GeminiChatClient:
        """登记真实离线客户端，不替代 SDK 或 HTTP 的关闭实现。"""
        client = ORIGINAL(**kwargs)
        CLIENTS.append(client)
        return client
    monkeypatch.setattr(target, "GeminiChatClient", make_client)
    yield
    STATE["monitor_after_cleanup"] = all(closed_state().values())
    STATE["finalizers"].append("monitor")


def leaves(error: BaseException | None) -> list[BaseException | None]:
    """展开异常组以核对取消对象，保留空 cause 的原有结果。"""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in leaves(child)]
    return [error]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any],
) -> Generator[None, Any, None]:
    """通过原报告流程区分体错误和 teardown 错误，并保留对象身份。"""
    outcome = yield
    report = outcome.get_result()
    error = call.excinfo.value if call.excinfo else None
    if call.when == "call" and error is not None:
        STATE["body_identity"] = error is BODY_ERROR
        STATE["body_type"] = type(error).__name__
    if call.when == "teardown" and error is not None:
        STATE["teardown_type"] = type(error).__name__
        STATE["teardown_after_cleanup"] = all(closed_state().values())
        cause_errors = leaves(error.__cause__)
        STATE["cancel_causes_preserved"] = (
            len(cause_errors) == len(CANCEL_ERRORS) and
            all(actual is expected for actual, expected in zip(cause_errors, CANCEL_ERRORS))
        )
    REPORTS.append({"when": report.when, "failed": report.failed, "detail": str(report.longrepr)})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """先记录会话终态，再兜底本探针的 patch 与尚未关闭的循环。"""
    STATE.update(closed_state())
    STATE.update(
        exitstatus=int(exitstatus), clients=len(CLIENTS), streams=len(STREAMS),
        cancel_count=len(CANCEL_ERRORS), reports=REPORTS,
        factory_restored=target.GeminiChatClient is ORIGINAL,
        loops_closed=all(loop.is_closed() for loop in LOOPS),
    )
    Path(__file__).with_name("state.json").write_text(json.dumps(STATE), encoding="utf-8")
    # 在记录红回归事实之后，仅兜底清理本子会话注入的 patch 和所属循环中的资源。
    for patch in PATCHES:
        patch.undo()
    for loop in LOOPS:
        if not loop.is_closed():
            try:
                for client in CLIENTS:
                    loop.run_until_complete(ORIGINAL.aclose(client))
            finally:
                loop.close()
'''


_OFFLINE_PYTEST_CASE = r'''"""使用两个真实离线 lease 固定体退出与清理取消的组合。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing

import pytest

import conftest
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_gemini_coverage_edges import create


async def test_fixture_exit(
    offline: tuple[conftest.target._Wire, conftest.target.GeminiChatClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得并关闭两个流，再在指定的体或 SDK 关闭阶段注入取消。"""
    wire, client = offline
    conftest.LOOPS.append(asyncio.get_running_loop())
    for index in range(2):
        wire.replies.append([{"text": "ok"}])
        entry = dict(model(), api_key=f"dummy-{index}")
        result = await create(client, entry, stream=True)
        async with aclosing(result.stream_iter) as stream:
            conftest.STREAMS.append(stream)
            assert [event async for event in stream]
        lease = stream.lease
        original_sdk_close, original_http_close = lease.sdk.aio.aclose, lease.http.aclose

        async def close_sdk(
            index: int = index, original: Callable[[], Awaitable[None]] = original_sdk_close,
        ) -> None:
            """等待原 SDK 关闭后，按 lease 序号注入新的 teardown 取消。"""
            await original()
            await asyncio.sleep(0)
            conftest.SDK_FINISHED.append(index)
            if index < conftest.CANCEL_LEASES:
                asyncio.current_task().cancel(f"teardown-cancel-{index}")
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError as error:
                    conftest.CANCEL_ERRORS.append(error)
                    raise

        async def close_http(
            index: int = index, original: Callable[[], Awaitable[None]] = original_http_close,
        ) -> None:
            """实际等待 HTTP 关闭完成，再记录对应 lease 的完成顺序。"""
            await asyncio.sleep(0)
            await original()
            conftest.HTTP_FINISHED.append(index)

        monkeypatch.setattr(lease.sdk.aio, "aclose", close_sdk)
        monkeypatch.setattr(lease.http, "aclose", close_http)
    if conftest.BODY == "assertion":
        conftest.BODY_ERROR = AssertionError("body-assertion-sentinel")
        raise conftest.BODY_ERROR
    if conftest.BODY == "cancel":
        asyncio.current_task().cancel("body-cancel-sentinel")
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError as error:
            conftest.BODY_ERROR = error
            raise
'''


@pytest.mark.parametrize("body,cancel_leases", [
    (None, 0), ("assertion", 0), ("cancel", 0),
    (None, 2), ("assertion", 2), ("cancel", 2),
])
def test_offline_fixture_real_pytest_teardown(body: str | None, cancel_leases: int) -> None:
    """真实 pytest teardown 完成后检查独立取消报告、后续 finalizer、patch 和循环。"""
    root = Path(__file__).resolve().parents[3]
    probe_dir = root / "reports/pr181-test-cleanup/gemini" / (
        f"offline-teardown-{body or 'success'}-{cancel_leases}-{uuid4().hex}"
    )
    probe_dir.mkdir(parents=True)
    source = _OFFLINE_PYTEST_CONFTEST.replace("__BODY__", repr(body))
    source = source.replace("__CANCEL_LEASES__", repr(cancel_leases))
    (probe_dir / "conftest.py").write_text(source, encoding="utf-8")
    probe = probe_dir / "test_fixture_exit.py"
    probe.write_text(_OFFLINE_PYTEST_CASE, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TMPDIR=str(probe_dir),
               TEMP=str(probe_dir), TMP=str(probe_dir))
    run = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "pytest", str(probe), "-q", "-o", "addopts=",
         "--assert=plain", "-p", "pytest_asyncio.plugin", "-p", "pytest_mock", "-p", "no:cacheprovider",
         "--tb=short"],
        cwd=root, env=env, capture_output=True, encoding="utf-8", timeout=20, check=False,
    )
    (probe_dir / "run.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    assert run.returncode == (0 if body is None and not cancel_leases else 1), run.stdout + run.stderr
    state = json.loads((probe_dir / "state.json").read_text(encoding="utf-8"))
    assert state["clients"] == 1 and state["streams"] == 2, state
    assert state["cancel_count"] == cancel_leases, state
    for key in ("http_closed", "sdk_closed", "inactive", "pools_empty", "streams_closed",
                "owners_closed", "close_awaited", "monitor_after_cleanup", "factory_restored", "loops_closed"):
        assert state.get(key), (key, state, str(probe_dir))
    assert state["finalizers"] == ["monitor", "tail"], state
    failures = [report for report in state["reports"] if report["failed"]]
    assert [report["when"] for report in failures] == (
        (["call"] if body is not None else []) + (["teardown"] if cancel_leases else [])
    ), failures
    if body is not None:
        assert state["body_identity"], state
        assert state["body_type"] == ("AssertionError" if body == "assertion" else "CancelledError"), state
    if cancel_leases:
        assert state["teardown_type"] == "RuntimeError", state
        assert state["teardown_after_cleanup"] and state["cancel_causes_preserved"], state
        assert all(f"teardown-cancel-{index}" in failures[-1]["detail"] for index in range(cancel_leases))
