"""固定重试后 replacement 的消费主错及未启动上下文 GeneratorExit 终态。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.kernel.concurrency import TaskManager
from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.llm.exceptions import LLMTimeoutError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_p1_exception_arbitration import (
    Source, assert_safe_close_diagnostic, consume, managed_response,
)
from test.kernel.llm.test_review_gemini_resources import _Wire, _adapter


@pytest.fixture
def manager() -> TaskManager:
    """用项目任务管理器创建所有取消窗口任务。"""
    manager = TaskManager()
    manager.shutdown_process_pool(wait=False)
    return manager


@pytest.mark.parametrize("entry", ["text_callback", "event_callback"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("close_failed", [False, True])
async def test_replacement_receives_consumer_primary(
    entry: str, cancelled: bool, close_failed: bool, manager: TaskManager,
) -> None:
    """replacement 终态必须收到调用方主错或真实取消，不可只看到 GeneratorExit。"""
    read_error = LLMTimeoutError("first-read")
    first_source = Source(text=False, error=read_error)
    response, stream, first_terminal, resume = managed_response(first_source)
    next_source = Source(close_error=RuntimeError("close-secondary") if close_failed else None)
    replacement, next_stream, next_terminal, _ = managed_response(next_source)
    resume.side_effect = None
    resume.return_value = replacement
    primary = ValueError("callback-primary")
    entered = asyncio.Event()

    async def callback(value: Any) -> None:
        """忽略交接 reset，等 replacement 真实内容后制造调用方失败。"""
        if isinstance(value, StreamEvent) and value.reset:
            return
        entered.set()
        if cancelled:
            await asyncio.Event().wait()
        raise primary

    if cancelled:
        task = manager.create_task(consume(response, entry, callback), name="p1-replacement-cancel", daemon=True).task
        assert task is not None
        await entered.wait()
        task.cancel("replacement-cancel")
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert task.cancelled() and raised.value.args == ("replacement-cancel",)
        consumer_error = raised.value
    else:
        with pytest.raises(ValueError) as raised:
            await consume(response, entry, callback)
        assert raised.value is primary
        consumer_error = primary
    assert first_source.closed == next_source.closed == 1
    assert stream.phase == "failed" and first_terminal.await_count == 1
    assert first_terminal.await_args.args[0] is read_error
    assert next_terminal.await_count == 1
    assert next_terminal.await_args.args[0] is consumer_error
    assert next_stream.phase == ("cancelled" if cancelled else "abandoned")
    if close_failed:
        assert_safe_close_diagnostic(consumer_error, secret="close-secondary")


async def test_never_started_context_generator_exit_close_failure_is_failed() -> None:
    """未开始消费时上下文 GeneratorExit 不能把唯一关闭故障误记 abandoned。"""
    failure = RuntimeError("close-only")
    source = Source(text=False, close_error=failure)
    response, stream, terminal, resume = managed_response(source)
    with pytest.raises(RuntimeError) as raised:
        async with response:
            raise GeneratorExit()
    assert raised.value is failure
    assert terminal.await_count == 1 and terminal.await_args.args[0] is failure
    assert terminal.await_args.args[3] == stream.phase == "failed"
    assert source.closed == 1 and response.call_list == [] and response.provider_state is None
    resume.assert_not_awaited()


class QuietFailure(_Wire):
    """真实 SDK 首块无文本后读错，允许非缓冲消费按原规则重试。"""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """提供 SDK 可解析的空内容，第二块触发可重试读取失败。"""
        yield b'data: {"candidates":[{"index":0,"content":{"role":"model","parts":[]}}]}\n\n'
        raise LLMTimeoutError("first-read")


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("close_failed", [False, True])
async def test_real_sdk_replacement_consumer_terminal(
    cancelled: bool, close_failed: bool, manager: TaskManager,
) -> None:
    """真实公共 SDK 重试后 replacement 终态和 body 一次关闭保持消费主错。"""
    first, second = QuietFailure(), _Wire(close_error=close_failed)
    client = _adapter([first, second])
    request = LLMRequest(model_set=[model()], clients=ModelClientRegistry(gemini=client),
                         policy=RoundRobinPolicy(), enable_metrics=False)
    request.context_manager = None
    request.payloads = [LLMPayload(ROLE.USER, Text("offline"))]
    primary = ValueError("callback-primary")
    entered = asyncio.Event()
    next_terminal = AsyncMock()

    async def callback(text: str) -> None:
        """消费 replacement 的真实文本后失败或进入真实取消窗口。"""
        entered.set()
        if cancelled:
            await asyncio.Event().wait()
        raise primary

    try:
        response = await request.send(stream=True)
        stream = response._stream
        original_resume = stream.resume

        async def resume(context: Any) -> Any:
            """不替换恢复逻辑，只观察新尝试的真实终态调用参数。"""
            replacement = await original_resume(context)
            next_terminal.side_effect = replacement._stream.terminal
            replacement._stream.terminal = next_terminal
            return replacement

        stream.resume = resume
        if cancelled:
            task = manager.create_task(response.stream_with_callback(callback), name="p1-sdk-replacement-cancel", daemon=True).task
            assert task is not None
            await entered.wait()
            task.cancel("sdk-replacement-cancel")
            with pytest.raises(asyncio.CancelledError) as raised:
                await task
            assert task.cancelled()
            consumer_error = raised.value
        else:
            with pytest.raises(ValueError) as raised:
                await response.stream_with_callback(callback)
            assert raised.value is primary
            consumer_error = primary
        replacement_stream = stream._replacement
        assert stream.context.retry_count == 1 and stream.phase == "failed"
        assert first.closed == second.closed == 1
        assert replacement_stream.phase == ("cancelled" if cancelled else "abandoned")
        assert next_terminal.await_count == 1 and next_terminal.await_args.args[0] is consumer_error
        if close_failed:
            assert_safe_close_diagnostic(consumer_error, secret="wire close failed")
    finally:
        await client.aclose()
