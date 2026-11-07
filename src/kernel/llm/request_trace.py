"""原生请求生命周期观测，不包装 SDK 或流迭代器。

通过执行上下文关联重试。提供商方法显式报告最终参数、SDK 对象与既有解析结果。
流生成器保留 Attempt 引用，确保跨任务消费时不会将数据归入其他请求。
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
    """策略重试与响应消费重试共享的逻辑请求关联信息。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _attempt: int = 0
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def next_attempt(self) -> int:
        """在线程锁保护下分配唯一的尝试序号，从 1 开始递增。

        Returns:
            int: 当前逻辑请求内唯一的尝试序号。
        """
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
    """仅复用显式传递的响应重试上下文，不关联无关的外层模型请求。

    Returns:
        LogicalRequest: 显式重试沿用的关联对象，或新建的逻辑请求。
    """
    logical = _retry.get()
    if logical is not None:
        # 重试关联仅使用一次；嵌套的上下文压缩请求视为新的发送。
        _retry.set(None)
        return logical
    return LogicalRequest()


@contextmanager
def retry_scope(logical: LogicalRequest | None) -> Iterator[None]:
    """仅在响应内部重试发送时传递逻辑请求关联，退出后恢复原上下文。

    Args:
        logical: 本次调用所属的逻辑请求；允许为空时表示不传递重试关联。

    Returns:
        Iterator[None]: 临时传递重试关联的上下文管理器。
    """
    token = _retry.set(logical)
    try:
        yield
    finally:
        _retry.reset(token)


@contextmanager
def attempt_scope(attempt: Attempt) -> Iterator[None]:
    """在创建调用期间绑定提供商尝试，退出时恢复原上下文。

    Args:
        attempt: 当前调用尝试的观测句柄。

    Returns:
        Iterator[None]: 临时绑定当前尝试的上下文管理器。
    """
    token = _current.set(attempt)
    try:
        yield
    finally:
        _current.reset(token)


class Attempt:
    """显式观测句柄，不调用或替换 SDK 接口。"""

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
        """建立待执行记录，采集失败不得阻断模型调用。

        Args:
            logical: 本次调用所属的逻辑请求。
            model: 模型配置，仅提取模型标识与提供商信息用于观测。
            request_name: 用于展示和关联业务场景的请求名称。
            kind: 标准化响应类型，如聊天、嵌入或重排序。
            stream: 是否以流式方式执行本次调用。
            managed: 是否由外层执行流程负责非流式调用的终态。
            metadata: 需要合并到记录中的请求元数据。
        """
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
            )
        except Exception as error:  # noqa: BLE001 - 可选观测逻辑的异常隔离边界
            self._warn(error)

    def _warn(self, error: Exception) -> None:
        """每次尝试最多报告一次观测错误，不输出异常正文或堆栈。

        Args:
            error: 执行或观测过程中捕获的异常。
        """
        if self._warned:
            return
        self._warned = True
        logger.warning(f"请求观测不完整: {type(error).__name__}")

    def _emit(self, method: str, *args: Any, **kwargs: Any) -> None:
        """隔离观测失败，不吞掉模型执行异常。

        Args:
            method: 监视器上需要调用的方法名。
            args: 传递给监视器方法的位置参数。
            kwargs: 传递给监视器方法的关键字参数。
        """
        if self.record_id is None:
            return
        try:
            getattr(self._inspector, method)(self.record_id, *args, **kwargs)
        except Exception as error:  # noqa: BLE001 - 可选观测逻辑的异常隔离边界
            self._warn(error)

    def start(
        self,
        api_name: str,
        params: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """在补齐所有传输参数后记录最终 SDK 参数。

        Args:
            api_name: 用于标识 SDK 调用入口的接口名称。
            params: 最终传入 SDK 的请求参数；允许为空时保留原参数。
            metadata: 需要合并到记录中的请求元数据。
        """
        self.bound = True
        self._emit(
            "start",
            params,
            api_name=api_name,
            metadata={**self._metadata, **(metadata or {})},
        )
        self._emit("update", {"kind": self.kind})

    def raw(self, value: Any, *, event: bool = False) -> None:
        """在协议解析前报告 SDK 原始对象，包括业务解析跳过的事件。

        Args:
            value: 待观测或转换的原始数据。
            event: 是否将原始数据作为流事件追加，而非替换原始响应。
        """
        self._has_raw = True
        # 仅记录实际出现的用量字段，不采用提供商归一化时补出的默认值。
        # 流式接口的原始用量可能位于 response.usage 或 message.usage。
        try:

            def get(obj: Any, key: str) -> Any:
                """统一读取 SDK 对象与普通映射中的字段。

                Args:
                    obj: SDK 对象或普通映射。
                    key: 待检查的字段名。

                Returns:
                    Any: 对应字段的值；不存在时返回 None。
                """
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
        except Exception as error:  # noqa: BLE001 - 可选观测逻辑的异常隔离边界
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
        """记录既有解析器输出，不重复解析供应商协议。

        Args:
            message: 解析后的正文内容。
            tool_calls: 解析后的工具调用列表。
            reasoning_content: 解析后的推理正文。
            reasoning_parts: 解析后的结构化推理片段。
            usage: 解析器提供的用量数据，缺失值不推断为零。
            stop_reason: 提供商返回的结束原因。
            embeddings: 解析后的嵌入向量列表。
            results: 解析后的重排序结果列表。
        """
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
        """保留实际用量字段，不将业务统计补出的零值当作实际用量。

        Args:
            usage: 解析器提供的用量数据，缺失值不推断为零。

        Returns:
            dict[str, Any] | None: 保留实际观测字段的用量副本；无有效用量时返回 None。
        """
        if not isinstance(usage, dict) or not usage:
            return None
        result = dict(usage)
        if self._has_raw:
            for key in ("prompt_tokens", "completion_tokens"):
                if key not in self._usage_fields:
                    result.pop(key, None)
        return result

    def annotate(self, metadata: dict[str, Any]) -> None:
        """在最终 SDK 参数确定前汇集请求元数据。

        Args:
            metadata: 需要合并到记录中的请求元数据。
        """
        self._metadata.update(metadata)

    def event(self, event: Any) -> Any:
        """观测已解析的 StreamEvent，并原样返回同一事件。

        Args:
            event: 已经由适配器解析的标准化流事件。

        Returns:
            Any: 传入的同一流事件，不修改其内容。
        """
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
        except Exception as error:  # noqa: BLE001 - 可选观测逻辑的异常隔离边界
            self._warn(error)
        return event

    def opened(self, resource: Any = None) -> None:
        """保存资源关闭回调，支持返回的异步生成器尚未迭代即关闭。

        Args:
            resource: 已经打开的 SDK 资源，用于获取关闭回调。
        """
        self.ready = True
        if resource is not None:
            self._close = getattr(resource, "aclose", None) or getattr(
                resource, "close", None
            )

    async def close_resource(self) -> None:
        """至多关闭一次资源，清理失败不得覆盖模型执行异常。"""
        close, self._close = self._close, None
        if not callable(close):
            return
        try:
            value = close()
            if inspect.isawaitable(value):
                await value
        except Exception as error:  # noqa: BLE001 - 可选观测逻辑的异常隔离边界
            self._warn(error)

    def finish(
        self, error: BaseException | None = None, *, cancelled: bool = False
    ) -> None:
        """幂等地记录终态，保留缓冲中已经收到的部分结果。

        Args:
            error: 执行或观测过程中捕获的异常。
            cancelled: 是否因调用方取消而结束。
        """
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
        """跟踪原生提供商生成器的消费生命周期，并原样传播异常。

        Returns:
            Iterator[None]: 跟踪流消费结束、失败与取消的上下文管理器。
        """
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
    """复用执行层关联信息；直接调用适配器时建立独立尝试。

    Args:
        api_name: 用于标识 SDK 调用入口的接口名称。
        model: 模型配置，仅提取模型标识与提供商信息用于观测。
        request_name: 用于展示和关联业务场景的请求名称。
        kind: 标准化响应类型，如聊天、嵌入或重排序。
        stream: 是否以流式方式执行本次调用。

    Returns:
        Iterator[Attempt]: 提供当前观测句柄的上下文管理器。
    """
    active = _current.get()
    # 嵌套的提供商调用不能复用已经绑定到其他调用的句柄。
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
    """获取创建阶段的尝试上下文，原生生成器须在返回前保存该句柄。

    Returns:
        Attempt | None: 当前创建上下文中的句柄；未绑定时返回 None。
    """
    return _current.get()


def start_provider(
    api_name: str, params: dict[str, Any], *, kind: str = "chat", stream: bool = False
) -> Attempt:
    """开始原生 SDK 调用的采集，也支持直接测试适配器私有方法。

    Args:
        api_name: 用于标识 SDK 调用入口的接口名称。
        params: 最终传入 SDK 的请求参数；允许为空时保留原参数。
        kind: 标准化响应类型，如聊天、嵌入或重排序。
        stream: 是否以流式方式执行本次调用。

    Returns:
        Attempt: 已记录最终参数的本次调用观测句柄。
    """
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
