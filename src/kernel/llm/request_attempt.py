"""在响应消费者之间共享单次尝试状态、截止时间与流关闭责任。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from src.kernel.logger import get_logger

from ._stream_close import CloseOutcome, close_outcome, select_close_error
from .exceptions import LLMTimeoutError, classify_exception, decide_retry
from .model_client.base import StreamEvent
from .policy.base import ModelStep, PolicySession

logger = get_logger("kernel.llm.request_attempt", display="LLM 尝试")


@dataclass(slots=True)
class AttemptContext:
    """跨流失败保留同一个策略会话、当前模型步骤和已用重试次数。"""

    session: PolicySession
    step: ModelStep
    retry_count: int = 0


class ManagedStream:
    """管理供应商流的尝试截止时间、重试交接、关闭与一次性终态统计。"""

    def __init__(
        self, source: AsyncIterator[StreamEvent], *, timeout: float | None,
        started_at: float, context: AttemptContext,
        terminal: Callable[[BaseException | None, dict[str, Any] | None, float, str], Awaitable[None]],
        resume: Callable[[AttemptContext], Awaitable[Any]],
    ) -> None:
        """绑定一个尝试的流与回调，不创建后台任务。

        Args:
            source: 当前供应商的事件迭代器，由本对象负责关闭。
            timeout: 尝试总时限，单位秒；None 或非正数表示不设截止时间。
            started_at: 与 ``time.perf_counter()`` 同一基准的尝试开始时间，
                而非 Unix 时间戳；首块请求耗时也计入总时限。
            context: 同一逻辑请求共享的策略会话、当前步骤与重试次数。
            terminal: 接收异常、最终用量、耗时秒数和终态的异步回调，
                每个尝试至多调用一次，不把创建流当作成功完成。
            resume: 尚未向调用方暴露内容且策略允许时，使用更新后的
                context 返回替代响应；原尝试在交接前释放自己的流。

        调用方应耗尽本对象或显式 ``aclose()``。取消继续传播，提前放弃
        不发布半成品工具为成功结果，重试仍使用原策略的剩余预算。
        """
        self.source = source
        self.context = context
        self.started_at = started_at
        self.deadline = started_at + timeout if timeout and timeout > 0 else None
        self.terminal = terminal
        self.resume = resume
        self.usage: dict[str, Any] | None = None
        self.exposed = False
        self.finished = False
        self.phase: str | None = None
        self.buffered = False
        self._iterator = self._run()
        self._replacement: Any = None
        self._source_result: CloseOutcome | None = None
        self._source_lock = asyncio.Lock()
        self._close_result: CloseOutcome | None = None
        self._close_lock = asyncio.Lock()
        self._retry_blocked = False
        self._closing_primary: BaseException | None = None
        self.result_response: Any = None
        self.on_replacement: Callable[[Any], None] | None = None

    def __aiter__(self) -> ManagedStream:
        """返回当前独占供应商流的迭代器。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """通过托管执行循环读取下一个流事件。"""
        return await anext(self._iterator)

    async def _finish(self, error: BaseException | None, phase: str) -> None:
        """按 error 与 phase 至多发布一次当前尝试的终态观测。"""
        if not self.finished:
            self.finished = True
            self.phase = phase
            try:
                await self.terminal(error, self.usage, time.perf_counter()-self.started_at, phase)
            except Exception as observer_error:
                # 只记录固定阶段与类型，不泄漏观察器文本、URL 或 traceback。
                logger.warning("LLM terminal observer failed: stage=terminal; error_type="
                               + type(observer_error).__name__)

    async def _run(self) -> AsyncIterator[StreamEvent]:
        """读取、关闭、终态顺序固定；仅关闭成功后才使用原策略预算交接。"""
        pending: list[StreamEvent] = []
        primary: BaseException | None = None
        try:
            while True:
                try:
                    if self.deadline is not None:
                        remaining = self.deadline-time.perf_counter()
                        if remaining <= 0:
                            raise LLMTimeoutError("LLM stream attempt deadline exceeded")
                        event = await asyncio.wait_for(anext(self.source), remaining)
                    else:
                        event = await anext(self.source)
                except StopAsyncIteration:
                    break
                if event.usage:
                    self.usage = dict(event.usage)
                if self.buffered:
                    pending.append(event)
                else:
                    if event.text_delta or event.reasoning_delta or event.tool_call_id or event.tool_name or event.tool_args_delta:
                        self.exposed = True
                    yield event
        except BaseException as exc:
            if isinstance(exc, GeneratorExit) and self._closing_primary is not None:
                primary = self._closing_primary
            else:
                primary = classify_exception(exc) if isinstance(exc, Exception) else exc
        outcome = await self._close_source()
        self._retry_blocked = outcome.error is not None
        error = select_close_error(primary, outcome)
        if error is None:
            await self._finish(None, "completed")
            for event in pending:
                yield event
            return
        consumer_abandoned = (primary is not None and not isinstance(primary, GeneratorExit)
                              and self._closing_primary is primary)
        phase = ("cancelled" if isinstance(error, asyncio.CancelledError)
                 else "abandoned" if isinstance(error, GeneratorExit) or consumer_abandoned
                 else "failed")
        await self._finish(error, phase)
        if self._closing_primary is primary and primary is not None and not outcome.fresh_control:
            # 调用方主错由消费包装器传播，不缓存为生成器关闭故障。
            return
        if (primary is None or not isinstance(error, Exception)
                or self._retry_blocked or self.exposed or not decide_retry(error).retryable):
            raise error
        self.context.retry_count += 1
        self.context.step = self.context.session.next_after_error(error)
        if self.context.step.model is None:
            raise error
        # replacement 不属于上面原尝试的读取捕获区，具有独立终态与异常。
        response = await self.resume(self.context)
        self._replacement = response._stream
        if self._replacement is None:
            raise RuntimeError("Streaming retry returned no stream")
        if isinstance(self._replacement, ManagedStream):
            self._replacement.buffered = self.buffered
        if not self.buffered:
            yield StreamEvent(reset=True)
        async for event in self._replacement:
            yield event
        final_response = (self._replacement.result_response if isinstance(self._replacement, ManagedStream) else None) or response
        self.result_response = final_response
        if self.on_replacement is not None:
            self.on_replacement(final_response)

    async def _close_source(self) -> CloseOutcome:
        """串行等待原始源的一次关闭；缓存结果不冒充本次新控制异常。"""
        try:
            async with self._source_lock:
                if self._source_result is None:
                    result = await close_outcome(self.source)
                    self._source_result = CloseOutcome(result.error)
                    return result
                return self._source_result
        except BaseException as error:
            return CloseOutcome(error, not isinstance(error, Exception))

    async def _close_outcome(self, *, cancelled: bool = False, primary: BaseException | None = None) -> CloseOutcome:
        """分别清理生成器、源及 replacement，普通前项失败不跳过后项。"""
        try:
            async with self._close_lock:
                if self._close_result is not None:
                    return self._close_result
                selected: BaseException | None = None
                fresh_control = False
                self._closing_primary = primary
                for resource in (self._iterator,):
                    result = await close_outcome(resource)
                    selected = select_close_error(selected, result)
                    fresh_control |= result.fresh_control
                result = await self._close_source()
                selected = select_close_error(selected, result)
                fresh_control |= result.fresh_control
                if self._replacement is not None:
                    if isinstance(self._replacement, ManagedStream):
                        result = await self._replacement._close_outcome(cancelled=cancelled, primary=primary)
                    else:
                        result = await close_outcome(self._replacement)
                    selected = select_close_error(selected, result)
                    fresh_control |= result.fresh_control
                if not self.finished:
                    terminal_error = select_close_error(primary, CloseOutcome(selected, fresh_control))
                    terminal_error = terminal_error or (asyncio.CancelledError() if cancelled else GeneratorExit())
                    consumer_abandoned = primary is not None and not isinstance(primary, GeneratorExit)
                    phase = ("cancelled" if isinstance(terminal_error, asyncio.CancelledError)
                             else "abandoned" if isinstance(terminal_error, GeneratorExit) or consumer_abandoned
                             else "failed")
                    try:
                        await self._finish(terminal_error, phase)
                    except BaseException as error:
                        selected = error
                        fresh_control = not isinstance(error, Exception)
                self._close_result = CloseOutcome(selected)
                return CloseOutcome(selected, fresh_control)
        except BaseException as error:
            return CloseOutcome(error, not isinstance(error, Exception))

    async def aclose(self, *, cancelled: bool = False) -> None:
        """显式关闭等待同一结果，重复调用仍报告缓存错误但不重复释放。"""
        result = await self._close_outcome(cancelled=cancelled)
        if result.error is not None:
            raise result.error
