"""Native request lifecycle observations, not SDK or iterator wrappers.

Execution scopes correlate retries. Provider methods explicitly report their final
parameters, SDK objects and already-parsed results. Streaming generators retain
an Attempt reference, so consumption in a different task cannot misattribute data.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

from src.kernel.logger import get_logger

from .request_inspector import get_inspector

logger = get_logger("kernel.llm.inspection", display="LLM 请求观测")


@dataclass
class LogicalRequest:
    """Correlation shared by policy retries and response-consumption retries."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _attempt: int = 0
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def next_attempt(self) -> int:
        """Allocate a unique one-based attempt number."""
        with self._lock:
            self._attempt += 1
            return self._attempt


_current: ContextVar[Attempt | None] = ContextVar(
    "llm_inspection_attempt", default=None
)
_retry: ContextVar[LogicalRequest | None] = ContextVar(
    "llm_inspection_retry", default=None
)


def new_logical_request() -> LogicalRequest:
    """Reuse an explicit response retry, never an unrelated outer model request."""
    logical = _retry.get()
    if logical is not None:
        # Consume the retry handoff once; nested context-compression requests are new sends.
        _retry.set(None)
        return logical
    return LogicalRequest()


@contextmanager
def retry_scope(logical: LogicalRequest | None) -> Iterator[None]:
    """Carry correlation only across the internal response retry send."""
    token = _retry.set(logical)
    try:
        yield
    finally:
        _retry.reset(token)


@contextmanager
def attempt_scope(attempt: Attempt) -> Iterator[None]:
    """Bind a provider attempt during creation, with deterministic restoration."""
    token = _current.set(attempt)
    try:
        yield
    finally:
        _current.reset(token)


class Attempt:
    """An explicit observation handle; it never invokes or replaces an SDK API."""

    def __init__(
        self,
        logical: LogicalRequest,
        model: Mapping[str, Any],
        request_name: str,
        *,
        kind: str = "chat",
        stream: bool = False,
        managed: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Allocate pending state; collection failures must not prevent execution."""
        self.logical = logical
        self.managed = managed
        self.kind = kind
        self.streaming = stream
        self.bound = False
        self.ready = False
        self.done = False
        self._reducer: Any = None
        self._close: Callable[[], Any] | None = None
        self._inspector: Any = None
        self.record_id: int | None = None
        self._warned = False
        self._metadata: dict[str, Any] = {}
        self._has_raw = False
        self._usage_fields: set[str] = set()
        try:
            self._inspector = get_inspector()
            key = model.get("api_key")
            keys = key if isinstance(key, list) else [key]
            secrets = tuple(k for k in keys if isinstance(k, str) and k)
            self.record_id = self._inspector.capture(
                "provider.create",
                {"model": model.get("model_identifier", "—")},
                {
                    **dict(metadata or {}),
                    "request_name": request_name,
                    "api_provider": model.get("api_provider")
                    or model.get("base_url")
                    or "-",
                },
                native=True,
                logical_request_id=logical.id,
                attempt=logical.next_attempt(),
                stream=stream,
                secrets=secrets,
            )
        except Exception as error:  # noqa: BLE001 - optional observation boundary
            self._warn(error)

    def _warn(self, error: Exception) -> None:
        """Report at most once per attempt, without exception text or traceback."""
        if self._warned:
            return
        self._warned = True
        logger.warning(f"请求观测不完整: {type(error).__name__}")

    def _emit(self, method: str, *args: Any, **kwargs: Any) -> None:
        """Isolate observation failures without swallowing execution exceptions."""
        if self.record_id is None:
            return
        try:
            getattr(self._inspector, method)(self.record_id, *args, **kwargs)
        except Exception as error:  # noqa: BLE001 - optional observation boundary
            self._warn(error)

    def start(
        self,
        api_name: str,
        params: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record final SDK arguments after all transport parameters are added."""
        self.bound = True
        self._emit(
            "start",
            params,
            api_name=api_name,
            metadata={**self._metadata, **(metadata or {})},
        )
        self._emit("update", {"kind": self.kind})

    def raw(self, value: Any, *, event: bool = False) -> None:
        """Report an SDK object before protocol parsing, including ignored events."""
        self._has_raw = True
        # Presence, not provider-normalizer defaults, determines observed counters.
        # Raw usage may arrive in response.usage or message.usage on streamed APIs.
        try:

            def get(obj: Any, key: str) -> Any:
                """Read SDK-shaped objects and ordinary mappings alike."""
                return (
                    obj.get(key)
                    if isinstance(obj, Mapping)
                    else getattr(obj, key, None)
                )

            for owner in (value, get(value, "response"), get(value, "message")):
                usage = get(owner, "usage")
                for canonical, aliases in (
                    ("prompt_tokens", ("prompt_tokens", "input_tokens")),
                    ("completion_tokens", ("completion_tokens", "output_tokens")),
                ):
                    if any(
                        isinstance(get(usage, name), (int, float)) for name in aliases
                    ):
                        self._usage_fields.add(canonical)
        except Exception as error:  # noqa: BLE001 - optional observation boundary
            self._warn(error)
        self._emit("raw", value, event=event)

    def result(
        self,
        *,
        message: str | None = None,
        tool_calls: Any = None,
        reasoning_content: str | None = None,
        reasoning_parts: Any = None,
        usage: Any = None,
        stop_reason: str | None = None,
        embeddings: Any = None,
        results: Any = None,
    ) -> None:
        """Publish existing parser output, without duplicating protocol parsing."""
        self._emit(
            "update",
            {
                "kind": self.kind,
                "message": message,
                "tool_calls": tool_calls or [],
                "reasoning_content": reasoning_content,
                "reasoning_parts": reasoning_parts or [],
                "usage": self._actual_usage(usage),
                "stop_reason": stop_reason,
                "embeddings": embeddings or [],
                "results": results or [],
            },
        )
        if not self.managed and not self.streaming:
            self.finish()

    def _actual_usage(self, usage: Any) -> dict[str, Any] | None:
        """Do not expose zeros invented by existing business metrics normalization."""
        if not isinstance(usage, dict) or not usage:
            return None
        result = dict(usage)
        if self._has_raw:
            for key in ("prompt_tokens", "completion_tokens"):
                if key not in self._usage_fields:
                    result.pop(key, None)
        return result

    def annotate(self, metadata: dict[str, Any]) -> None:
        """Collect input metadata before final SDK parameters are known."""
        self._metadata.update(metadata)

    def event(self, event: Any) -> Any:
        """Observe an already-parsed StreamEvent and return the same event unchanged."""
        try:
            if self._reducer is None:
                from .stream_state import LLMStreamReducer

                self._reducer = LLMStreamReducer()
            self._reducer.apply(event)
            state = self._reducer.finalize()
            self._emit(
                "update",
                {
                    "kind": self.kind,
                    "message": state.message,
                    "reasoning_content": state.reasoning_content,
                    "reasoning_parts": state.reasoning_parts,
                    "tool_calls": [asdict(call) for call in state.call_list],
                    "usage": self._actual_usage(state.usage),
                    "stop_reason": state.stop_reason,
                },
                content=bool(
                    event.text_delta or event.reasoning_delta or event.tool_args_delta
                ),
            )
        except Exception as error:  # noqa: BLE001 - optional observation boundary
            self._warn(error)
        return event

    def opened(self, resource: Any = None) -> None:
        """Remember a close callback even if the returned async generator is never entered."""
        self.ready = True
        if resource is not None:
            self._close = getattr(resource, "aclose", None) or getattr(
                resource, "close", None
            )

    async def close_resource(self) -> None:
        """Close once; cleanup failure must not replace an execution error."""
        close, self._close = self._close, None
        if not callable(close):
            return
        try:
            value = close()
            if inspect.isawaitable(value):
                await value
        except Exception as error:  # noqa: BLE001 - optional observation boundary
            self._warn(error)

    def finish(
        self, error: BaseException | None = None, *, cancelled: bool = False
    ) -> None:
        """Commit a terminal state once; partial results are already in the buffer."""
        if self.done:
            return
        self.done = True
        self._emit("mark_unavailable", request=not self.bound, raw=not self._has_raw)
        self._emit(
            "finish",
            error=error,
            cancelled=cancelled
            or isinstance(error, (asyncio.CancelledError, GeneratorExit)),
        )
        self._reducer = None

    @contextmanager
    def consume(self) -> Iterator[None]:
        """Account for the lifetime of the native provider generator itself."""
        try:
            yield
        except BaseException as error:
            self.finish(error)
            raise
        else:
            self.finish()


@contextmanager
def provider_attempt(
    api_name: str,
    model: Mapping[str, Any] | Any,
    request_name: str,
    *,
    kind: str = "chat",
    stream: bool = False,
) -> Iterator[Attempt]:
    """Reuse execution correlation or create a standalone native invocation."""
    active = _current.get()
    # A nested provider invocation must not reuse a handle already bound to another call.
    trace = (
        active
        if active is not None and not active.bound
        else Attempt(
            LogicalRequest(),
            model if isinstance(model, Mapping) else {},
            request_name,
            kind=kind,
            stream=stream,
            managed=False,
        )
    )
    with attempt_scope(trace):
        try:
            yield trace
        except BaseException as error:
            if not trace.managed:
                trace.finish(error)
            raise
        else:
            if not trace.managed and not trace.ready:
                trace.finish()


def current_attempt() -> Attempt | None:
    """Get creation-time context; native generators must capture this before returning."""
    return _current.get()


def start_provider(
    api_name: str, params: dict[str, Any], *, kind: str = "chat", stream: bool = False
) -> Attempt:
    """Start native SDK execution; also supports direct private-method tests."""
    trace = current_attempt()
    if trace is None:
        trace = Attempt(
            LogicalRequest(),
            {"model_identifier": params.get("model")},
            "",
            kind=kind,
            stream=stream,
            managed=False,
        )
    trace.start(api_name, params)
    return trace
