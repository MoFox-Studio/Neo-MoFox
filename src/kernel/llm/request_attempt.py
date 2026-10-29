"""Attempt state and a managed stream shared by every response consumer."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from .exceptions import LLMTimeoutError, classify_exception, decide_retry
from .model_client.base import StreamEvent
from .policy.base import ModelStep, PolicySession


@dataclass(slots=True)
class AttemptContext:
    """Keep one policy session and its remaining budget across stream failures."""

    session: PolicySession
    step: ModelStep
    retry_count: int = 0


class ManagedStream:
    """Own deadline, closure and terminal accounting for a provider iterator."""

    def __init__(
        self, source: AsyncIterator[StreamEvent], *, timeout: float | None,
        started_at: float, context: AttemptContext,
        terminal: Callable[[BaseException | None, dict[str, Any] | None, float, str], Awaitable[None]],
        resume: Callable[[AttemptContext], Awaitable[Any]],
    ) -> None:
        """Bind callbacks without starting a background task."""
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
        self._source_closed = False
        self._closed = False
        self.result_response: Any = None
        self.on_replacement: Callable[[Any], None] | None = None

    def __aiter__(self) -> ManagedStream:
        """Return the single owned iterator."""
        return self

    async def __anext__(self) -> StreamEvent:
        """Read through the managed execution loop."""
        return await anext(self._iterator)

    async def _finish(self, error: BaseException | None, phase: str) -> None:
        """Publish a terminal observation at most once."""
        if not self.finished:
            self.finished = True
            self.phase = phase
            await self.terminal(error, self.usage, time.perf_counter()-self.started_at, phase)

    async def _run(self) -> AsyncIterator[StreamEvent]:
        """Classify stream failures before applying the remaining policy budget."""
        pending: list[StreamEvent] = []
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
            await self._finish(None, "completed")
            for event in pending:
                yield event
        except (asyncio.CancelledError, GeneratorExit) as exc:
            await self._finish(exc, "cancelled" if isinstance(exc, asyncio.CancelledError) else "abandoned")
            raise
        except Exception as exc:
            error = classify_exception(exc)
            await self._finish(error, "failed")
            if self.exposed or not decide_retry(error).retryable:
                raise error
            self.context.retry_count += 1
            self.context.step = self.context.session.next_after_error(error)
            if self.context.step.model is None:
                raise error
            await self._close_source()
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
        finally:
            await self._close_source()

    async def _close_source(self) -> None:
        """Close the original iterator once, including never-started wrappers."""
        if not self._source_closed:
            self._source_closed = True
            close = getattr(self.source, "aclose", None)
            if close:
                await close()

    async def aclose(self, *, cancelled: bool = False) -> None:
        """Close even an iterator never consumed by its caller."""
        if self._closed:
            return
        self._closed = True
        if cancelled and not self.finished:
            await self._finish(asyncio.CancelledError(), "cancelled")
        try:
            await self._iterator.aclose()
            await self._close_source()
            if self._replacement is not None:
                close = getattr(self._replacement, "aclose", None)
                if close:
                    await close()
        finally:
            if not self.finished:
                await self._finish(GeneratorExit(), "abandoned")
