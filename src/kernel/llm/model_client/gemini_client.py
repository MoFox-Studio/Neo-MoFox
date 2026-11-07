"""Gemini 原生 Developer API 客户端实现。

基于官方 Google Gen AI SDK 提供异步聊天和流式输出。MoFox 负责共享传输、
请求级响应的资源所有权和非生成探测；消息与结果转换由 gemini_codec 处理。
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, TypeVar, cast
from weakref import WeakKeyDictionary

import httpx
from google import genai
from google.genai import types

from src.kernel.network import build_httpx_timeout, build_httpx_transport

from .._stream_close import CloseOutcome, close_outcome, select_close_error
from ..exceptions import LLMConfigurationError
from ..payload import LLMPayload, LLMUsable
from .base import ModelClientResult, StreamEvent
from .gemini_codec import (
    decode_response,
    encode_payloads,
    gemini_options,
    GeminiTransportSnapshot,
    resolve_gemini_transport,
    target_identity,
)
from .probe_contract import ProviderProbeResult
from .shared import extract_model_transport_params, log_provider_request_body

_ResultT = TypeVar("_ResultT")


async def _await_sdk(operation: Awaitable[_ResultT]) -> _ResultT:
    """原样等待 SDK 操作，不推测异常链或取消计数。

    Args:
        operation: 本次 SDK 调用或流拉取返回的可等待对象。

    Returns:
        SDK 操作的原始结果；异常原样传播，关闭错误由响应边界隔离。
    """
    return await operation


def _raise_close_errors(errors: list[BaseException]) -> None:
    """传播资源关闭期间收集的异常。

    Args:
        errors: 按关闭顺序收集的异常，允许包含取消等控制异常。

    Raises:
        BaseException: 遇到控制异常时立即原样抛出；仅有一个普通错误时原样抛出。
        ExceptionGroup: 收集到多个普通错误且没有控制异常。
    """
    ordinary: list[Exception] = []
    for error in errors:
        if not isinstance(error, Exception):
            raise error
        ordinary.append(error)
    if len(ordinary) == 1:
        raise ordinary[0]
    if ordinary:
        raise ExceptionGroup("Gemini 资源关闭失败", ordinary)


async def _close_all(resources: list[Any]) -> None:
    """逐项等待资源关闭，再传播收集到的错误或取消。

    即使某项关闭失败，也会继续尝试其余资源；不重试已执行的关闭。

    Args:
        resources: 按关闭顺序排列、提供异步 aclose() 的资源；None 会被跳过。
    """
    errors: list[BaseException] = []
    for resource in resources:
        if resource is not None:
            try:
                await resource.aclose()
            except BaseException as error:
                # 仅延后传播控制异常，确保真实响应仍有一次确定性关闭机会。
                errors.append(error)
    _raise_close_errors(errors)


async def _close_after_error(resource: Any, primary: BaseException) -> None:
    """发生主异常后关闭资源，并按统一规则仲裁清理异常。

    Args:
        resource: 需要关闭的资源，由 close_outcome() 获取其关闭结果。
        primary: 调用方已经捕获、准备继续传播的请求主异常。

    Raises:
        BaseException: 仲裁选中了不同于主异常的清理异常，例如新到达的取消。
            普通清理错误不会替换主异常，主异常仍由调用方继续传播。
    """
    selected = select_close_error(primary, await close_outcome(resource))
    if selected is not None and selected is not primary:
        raise selected


class _ResponseBody(httpx.AsyncByteStream):
    """代理真实 body，立即等待关闭，仅把普通关闭错误延后到 owner 报告。"""

    def __init__(self, source: httpx.AsyncByteStream) -> None:
        """初始化响应体代理，不接管共享 HTTP 客户端。

        Args:
            source: 当前响应的原始字节流，关闭尝试由本代理接管。
        """
        self.source = source
        self.close_error: BaseException | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """原样交付字节和读取异常，不参与 HTTP/SSE 解码。"""
        async for chunk in self.source:
            yield chunk

    async def aclose(self) -> None:
        """等待唯一一次底层关闭，并缓存关闭结果。

        普通关闭错误留给请求 owner 报告；取消及其他控制异常原样传播，
        后续调用不会重试关闭，但仍会传播缓存的控制异常。
        """
        async with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    await self.source.aclose()
                except BaseException as error:
                    self.close_error = error
                    if not isinstance(error, Exception):
                        raise
            elif self.close_error is not None and not isinstance(
                self.close_error, Exception
            ):
                raise self.close_error


class _ResponseOwner:
    """登记一个逻辑请求的真实 HTTP 响应，不持有共享客户端的关闭权。"""

    def __init__(self) -> None:
        """为本次请求创建独立响应列表及关闭状态。"""
        self.responses: list[httpx.Response] = []
        self.bodies: list[_ResponseBody] = []
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_error: BaseException | None = None

    async def aclose(self) -> None:
        """等待实际响应关闭；失败不重试，后续关闭仍报告同一失败。"""
        async with self._lock:
            if not self._closed:
                self._closed = True
                errors: list[BaseException] = []
                try:
                    try:
                        await _close_all(self.responses)
                    except BaseException as error:
                        errors.append(error)
                    # HTTPX 在进入 body 关闭前已标记 Response 关闭，因此另行等待代理锁。
                    # SDK 提前关闭时缓存的普通错误，也必须在最终 owner 边界报告。
                    for body in self.bodies:
                        try:
                            await body.aclose()
                        except BaseException as error:
                            if not any(error is item for item in errors):
                                errors.append(error)
                        if body.close_error is not None and not any(
                            body.close_error is item for item in errors
                        ):
                            errors.append(body.close_error)
                    _raise_close_errors(errors)
                except BaseException as error:
                    self._close_error = error
                    raise
            elif self._close_error is not None:
                raise self._close_error


_response_owner: ContextVar[_ResponseOwner | None] = ContextVar(
    "gemini_response_owner",
    default=None,
)


async def _capture_response(response: httpx.Response) -> None:
    """通过 HTTPX 公开 hook 登记请求响应，并接管响应体的关闭尝试。

    Args:
        response: SDK 当前收到的真实 HTTP 响应；没有关联 owner 时不做处理。
    """
    owner = _response_owner.get()
    if owner is not None:
        body = _ResponseBody(cast(httpx.AsyncByteStream, response.stream))
        response.stream = body
        owner.responses.append(response)
        owner.bodies.append(body)


@dataclass(slots=True)
class _ClientLease:
    """共享 SDK/HTTP 资源；active 表示逻辑请求占用，不证明关闭成功。"""

    sdk: genai.Client
    http: httpx.AsyncClient
    active: int = 0

    async def close(self) -> None:
        """尝试关闭 SDK 同步及异步资源和自有 HTTP 客户端，保留取消。"""
        errors: list[BaseException] = []
        try:
            await self.sdk.aio.aclose()
        except BaseException as error:
            errors.append(error)
        try:
            self.sdk.close()
        except BaseException as error:
            errors.append(error)
        try:
            await self.http.aclose()
        except BaseException as error:
            errors.append(error)
        _raise_close_errors(errors)


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """代理调用方传输，但不接管其生命周期。"""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        """初始化借用传输的代理，关闭责任仍属于调用方。

        Args:
            transport: 调用方创建并负责关闭的异步传输。
        """
        self.transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """将请求交给借用的传输处理。

        Args:
            request: 需要发送的 HTTP 请求。

        Returns:
            借用传输返回的原始 HTTP 响应。
        """
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        """不关闭借用资源，由其创建者完成关闭。"""


class _OwnedStream:
    """拥有预取后的迭代器和请求响应，即使从未开始消费也能关闭。"""

    def __init__(
        self,
        events: AsyncIterator[StreamEvent],
        source: Any,
        lease: _ClientLease,
        owner: _ResponseOwner | None = None,
    ) -> None:
        """初始化拥有请求资源的流，兼容不传 owner 的旧构造。

        Args:
            events: 将 SDK 响应转换为统一事件的迭代器。
            source: 已完成首块预取的原始 SDK 迭代器。
            lease: 本次请求占用的共享资源引用，清理尝试完成后释放逻辑占用。
            owner: 当前请求的真实 HTTP 响应 owner；None 表示未关联 owner。
        """
        self.events, self.source, self.lease = events, source, lease
        self.owner = owner
        self.closed = False
        self._close_lock = asyncio.Lock()
        self._close_error: BaseException | None = None

    def __aiter__(self) -> _OwnedStream:
        """返回当前拥有资源的迭代器。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """在每次拉取期间恢复请求关联，所有退出路径等待真实响应关闭。"""
        token = _response_owner.set(self.owner)
        try:
            try:
                return await _await_sdk(anext(self.events))
            except StopAsyncIteration:
                await self.aclose()
                raise
            except BaseException as primary:
                await _close_after_error(self, primary)
                raise
        finally:
            _response_owner.reset(token)

    async def _close_outcome(self) -> CloseOutcome:
        """串行关闭流资源，并区分新到达的控制异常与缓存重放。

        Returns:
            本次关闭结果；首次清理会释放 lease 的逻辑占用，后续调用只重放
            缓存结果。等待锁期间发生的控制异常作为本次新异常返回。
        """
        try:
            async with self._close_lock:
                if self.closed:
                    return CloseOutcome(self._close_error)
                self.closed = True
                token = _response_owner.set(self.owner)
                try:
                    await _close_all([self.events, self.source, self.owner])
                except BaseException as error:
                    self._close_error = error
                    return CloseOutcome(error, not isinstance(error, Exception))
                finally:
                    _response_owner.reset(token)
                    self.lease.active -= 1
                return CloseOutcome()
        except BaseException as error:
            return CloseOutcome(error, not isinstance(error, Exception))

    async def aclose(self) -> None:
        """只执行一次关闭并释放逻辑占用；重复关闭仍报告缓存错误。"""
        result = await self._close_outcome()
        if result.error is not None:
            raise result.error


class GeminiChatClient:
    """将 MoFox 聊天请求映射为官方 SDK 调用。

    SDK 和 HTTP 客户端按事件循环复用，单次请求持有独立的响应 owner。
    工具执行与重试仍由上层管理，本类不接管调用方注入传输的关闭责任。
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """初始化按事件循环隔离的客户端池。

        Args:
            transport: 可选的借用传输，关闭责任属于调用方；None 表示按模型
                配置创建自有传输。
        """
        self._transport = transport
        self._pools: WeakKeyDictionary[
            Any, OrderedDict[GeminiTransportSnapshot, _ClientLease]
        ] = WeakKeyDictionary()
        self._locks: WeakKeyDictionary[Any, asyncio.Lock] = WeakKeyDictionary()

    async def _get_client(self, model: dict[str, Any]) -> _ClientLease:
        """在所属事件循环的锁内获取客户端。

        Args:
            model: 已合并的单条模型配置，用于解析完整传输快照。

        Returns:
            已为本次请求增加逻辑占用的共享资源引用。
        """
        lock = self._locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            return await self._get_client_locked(model)

    async def _get_client_locked(self, model: dict[str, Any]) -> _ClientLease:
        """复用或创建 SDK 资源，并在池满时回收空闲条目。

        Args:
            model: 已合并的单条模型配置；调用方必须已持有当前循环的池锁。

        Returns:
            当前循环中匹配传输快照的资源引用，已计入本次请求的逻辑占用。

        Raises:
            LLMConfigurationError: 池已满且没有可回收的空闲条目。
            Exception: 配置解析、SDK 构造或空闲资源关闭失败。
        """
        snapshot = resolve_gemini_transport(model)
        key, root, version = snapshot.key, snapshot.root, snapshot.version
        timeout, trust, ipv4 = snapshot.timeout, snapshot.trust_env, snapshot.force_ipv4
        headers = dict(snapshot.headers)
        loop = asyncio.get_running_loop()
        pool: OrderedDict[GeminiTransportSnapshot, _ClientLease] = (
            self._pools.setdefault(loop, OrderedDict())
        )
        cache_key = snapshot
        if cache_key in pool:
            pool.move_to_end(cache_key)
            pool[cache_key].active += 1
            return pool[cache_key]
        if len(pool) >= 16:
            idle = next((key for key, entry in pool.items() if not entry.active), None)
            if idle is None:
                raise LLMConfigurationError(
                    "Gemini client pool is busy; wait before adding credentials"
                )
            await pool.pop(idle).close()
        http = httpx.AsyncClient(
            timeout=build_httpx_timeout(timeout),
            trust_env=trust,
            transport=(
                _BorrowedTransport(self._transport)
                if self._transport is not None
                else build_httpx_transport(force_ipv4=ipv4)
            ),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=5),
            event_hooks={"response": [_capture_response]},
        )
        try:
            sdk = genai.Client(
                vertexai=False,
                api_key=key,
                http_options=types.HttpOptions(
                    base_url=root,
                    api_version=version,
                    httpx_async_client=http,
                    headers=headers,
                    timeout=max(1, int(timeout * 1000)),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        except BaseException as primary:
            await _close_after_error(http, primary)
            raise
        lease = _ClientLease(sdk, http, active=1)
        pool[cache_key] = lease
        return lease

    async def aclose(self) -> None:
        """回收当前事件循环的空闲资源，不关闭活动请求。

        每项在关闭前先从池中摘除。普通关闭错误不阻断后续空闲条目；
        取消会继续传播，尚未尝试的条目留待下一次清理。

        Raises:
            ExceptionGroup: 一个或多个空闲条目关闭失败，包含各项普通错误。
        """
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            pool: dict[GeminiTransportSnapshot, _ClientLease] = self._pools.get(loop, {})
            errors: list[Exception] = []
            for key, lease in list(pool.items()):
                if not lease.active:
                    # 每项在关闭前摘除，取消后尚未尝试的项仍留池等待下一次清理。
                    del pool[key]
                    try:
                        await lease.close()
                    except Exception as error:
                        errors.append(error)
            if errors:
                raise ExceptionGroup("Gemini idle 客户端回收失败", errors)

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> ModelClientResult:
        """构建官方 SDK 请求，并返回统一结果或拥有请求资源的流。

        Args:
            model_name: Gemini 模型 ID，允许单个 ``models/`` 前缀。
            payloads: 按时间顺序排列的消息及可验证来源的续轮状态。
            tools: 本轮声明的工具；执行仍属于 MoFox，不启用 SDK 自动调用。
            request_name: 仅用于请求观测的名称。
            model_set: 单个已合并模型配置，包含凭据、传输和 Gemini 生成参数。
            stream: 是否返回流；流模式会在返回前预取一个 SDK 块。

        Returns:
            非流模式返回完整 ModelClientResult；流模式的 stream_iter 接管本次
            请求的响应和一个 lease 引用。调用方须消费至结束或 await aclose()。
            单流关闭只等待其真实响应关闭，不关闭共享 SDK/HTTP 客户端。逻辑
            引用在清理尝试完成后释放；关闭失败仍抛出，并由重复关闭原样报告。

        Raises:
            LLMConfigurationError: 模型、传输或生成参数不符合 Gemini 契约。
            Exception: SDK 请求、解码或资源关闭失败，交由上层异常归一化及策略处理。
        """
        if not isinstance(model_set, dict):
            raise LLMConfigurationError("Gemini requires one model entry")

        name = model_name.removeprefix("models/")
        if not name or any(char in name for char in "/:?&#\\") or name in {".", ".."}:
            raise LLMConfigurationError(
                "Gemini model_identifier must be a single model ID"
            )

        gemini_options(model_set)
        target = target_identity(model_set)
        contents, system, declared = encode_payloads(payloads, target, tools)
        if not contents:
            raise LLMConfigurationError("Gemini request has no conversation contents")

        _, _, _, _, _, extras, _, _, body = extract_model_transport_params(model_set)
        options = extras.pop("gemini", {})
        if not isinstance(options, dict) or set(options) - {
            "api_version", "generation_config"
        }:
            raise LLMConfigurationError("Unknown Gemini configuration option")
        if extras or body:
            raise LLMConfigurationError(
                "Use extra_params.gemini.generation_config for Gemini settings"
            )

        config_values = dict(options.get("generation_config", {}))
        forbidden = {
            "http_options",
            "system_instruction",
            "tools",
            "automatic_function_calling",
            "automatic_continuation",
            "continuation_token",
            "candidate_count",
            "max_output_tokens",
            "temperature",
        }
        if set(config_values) & forbidden or set(config_values) - set(
            types.GenerateContentConfig.model_fields
        ):
            raise LLMConfigurationError(
                "Gemini config contains an unknown or reserved field"
            )

        maximum = model_set.get("max_tokens", 1024)
        temperature = model_set.get("temperature")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise LLMConfigurationError("Gemini max_tokens must be a positive integer")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
        ):
            raise LLMConfigurationError("Gemini temperature must be finite")

        try:
            config = types.GenerateContentConfig(
                **config_values,
                system_instruction=types.Content(parts=system) if system else None,
                tools=declared or None,
                candidate_count=1,
                max_output_tokens=maximum,
                temperature=temperature,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                automatic_continuation=False,
            )
        except (TypeError, ValueError):
            raise LLMConfigurationError(
                "Invalid Gemini generation_config field types"
            ) from None
        # 签名等敏感原始内容不进入请求观测视图。
        log_provider_request_body(
            "gemini.generate_content",
            {
                "model": name,
                "contents_count": len(contents),
                "tool_count": len(declared),
            },
            model_set=model_set,
            request_name=request_name,
            summary_only=True,
        )
        lease = await self._get_client(model_set)
        owner = _ResponseOwner()
        token = _response_owner.set(owner)
        if not stream:
            try:
                try:
                    response = await _await_sdk(
                        lease.sdk.aio.models.generate_content(
                            model=name,
                            contents=contents,
                            config=config,
                        ),
                    )
                    result = decode_response(response, target)
                except BaseException as primary:
                    await _close_after_error(owner, primary)
                    raise
                await owner.aclose()
                return result
            finally:
                _response_owner.reset(token)
                lease.active -= 1

        source: Any = None
        try:
            source = await _await_sdk(
                lease.sdk.aio.models.generate_content_stream(
                    model=name,
                    contents=contents,
                    config=config,
                ),
            )
            # 在本次尝试的超时范围内建立真实请求并预取首块。
            first = await _await_sdk(anext(source))
        except BaseException as primary:
            try:
                try:
                    await _close_all([source, owner])
                except Exception:
                    primary.add_note("Gemini 建流清理也发生错误；请求主异常保持不变。")
            finally:
                lease.active -= 1
            raise
        finally:
            _response_owner.reset(token)

        async def events() -> AsyncIterator[StreamEvent]:
            """将 SDK 响应块转换为统一流事件，资源关闭由外层负责。

            文本与思考内容增量交付；工具调用和续轮状态只在流结束、终态与
            参数完整性校验通过后发布。

            Yields:
                文本、思考或完整工具调用事件，以及末尾的用量和续轮状态事件。
            """
            parts: list[types.Part] = []
            finish: Any = None
            usage: Any = None
            chunk = first

            while True:
                if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                    decode_response(chunk, target)
                if chunk.usage_metadata is not None:
                    usage = chunk.usage_metadata
                if chunk.candidates:
                    candidate = chunk.candidates[0]
                    if candidate.finish_reason:
                        finish = candidate.finish_reason
                    for part in (
                        (candidate.content.parts or []) if candidate.content else []
                    ):
                        parts.append(part)
                        if part.text:
                            yield (
                                StreamEvent(reasoning_delta=part.text)
                                if part.thought
                                else StreamEvent(text_delta=part.text)
                            )
                try:
                    chunk = await anext(source)
                except StopAsyncIteration:
                    break

            merged = types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        index=0,
                        content=types.Content(role="model", parts=parts),
                        finish_reason=finish,
                    )
                ],
                usage_metadata=usage,
            )
            result = decode_response(merged, target)
            for call in result.tool_calls or []:
                yield StreamEvent(
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    tool_args_delta=json.dumps(call["args"]),
                )
            yield StreamEvent(
                usage=result.usage,
                stop_reason=result.stop_reason,
                provider_state=result.provider_state,
            )

        return ModelClientResult(
            stream_iter=_OwnedStream(events(), source, lease, owner)
        )


class GeminiProviderProbe:
    """用独占临时资源执行官方 models.list，不借用或访问生成共享池。"""

    requires_model_entries = True
    requires_provider_entry = True
    allows_anonymous_probe = False

    async def _check_group(
        self, snapshot: GeminiTransportSnapshot, timeout: float
    ) -> None:
        """等待单组真实 SDK 请求及所有自有资源关闭，不重试且保留取消。

        Args:
            snapshot: 已验证的冻结技术配置，不涉及生成池或借用传输。
            timeout: 此组预检上限，和条目超时取较小值。

        Returns:
            无返回值；成功前等待真实响应、临时 SDK 和自有 HTTP 全部关闭。
            普通请求错误交由外层隔离，取消原样传播且不被普通清理错误替换。
        """
        effective_timeout = min(timeout, snapshot.timeout)
        owner = _ResponseOwner()
        http = httpx.AsyncClient(
            timeout=build_httpx_timeout(effective_timeout),
            trust_env=snapshot.trust_env,
            transport=build_httpx_transport(force_ipv4=snapshot.force_ipv4),
            follow_redirects=False,
            event_hooks={"response": [_capture_response]},
        )
        token = _response_owner.set(owner)
        lease: _ClientLease | None = None
        primary_error: BaseException | None = None
        try:
            try:
                sdk = genai.Client(
                    vertexai=False,
                    api_key=snapshot.key,
                    http_options=types.HttpOptions(
                        base_url=snapshot.root,
                        api_version=snapshot.version,
                        httpx_async_client=http,
                        headers=dict(snapshot.headers),
                        timeout=max(1, int(effective_timeout * 1000)),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                )
                lease = _ClientLease(sdk, http)
                await asyncio.wait_for(
                    sdk.aio.models.list(config={"page_size": 1}),
                    timeout=effective_timeout,
                )
            except BaseException as primary:
                await _close_after_error(owner, primary)
                raise
            else:
                await owner.aclose()
        except BaseException as error:
            primary_error = error
            raise
        finally:
            _response_owner.reset(token)
            try:
                if lease is None:
                    await http.aclose()
                else:
                    await lease.close()
            except Exception:
                if primary_error is None:
                    raise
                primary_error.add_note("Gemini 预检资源清理也发生错误；主异常保持不变。")

    async def check(
        self,
        entries: list[dict[str, Any]],
        timeout: float,
        *,
        provider_only: bool = False,
    ) -> list[ProviderProbeResult]:
        """按完整传输快照分组并隔离普通失败，全坏条目不回退默认配置。

        Args:
            entries: Core 已合并的模型配置，或无模型时显式给出的 provider 默认条目。
            timeout: 单组预检上限，和模型超时取较小值。
            provider_only: 只检查 provider 默认传输，不将其计为模型覆盖。

        Returns:
            每个坏条目和每组探测各一项安全结果，不携带配置或 SDK 原文。
        """
        results: list[ProviderProbeResult] = []
        groups: dict[GeminiTransportSnapshot, int] = {}
        scope = "仅 provider 默认配置，未覆盖模型；" if provider_only else ""
        for entry_index, entry in enumerate(entries, 1):
            try:
                if not math.isfinite(timeout) or timeout <= 0:
                    raise ValueError("invalid probe timeout")
                snapshot = resolve_gemini_transport(entry)
                groups[snapshot] = groups.get(snapshot, 0) + 1
            except Exception as error:
                results.append(
                    ProviderProbeResult(
                        "unverified",
                        scope + f"条目 {entry_index} 传输配置无效（{type(error).__name__}），该项未验证",
                        entry_index=entry_index,
                    )
                )

        for group_index, (snapshot, count) in enumerate(groups.items(), 1):
            covered = 0 if provider_only else count
            group_scope = scope + f"组 {group_index}/{len(groups)}："
            try:
                await self._check_group(snapshot, timeout)
            except Exception as error:
                if getattr(error, "code", None) in {404, 405, 501}:
                    results.append(
                        ProviderProbeResult(
                            "unsupported",
                            group_scope + "跳过不支持 models.list 的网关，未验证生成能力",
                            covered,
                            1,
                            group_index=group_index,
                        )
                    )
                else:
                    results.append(
                        ProviderProbeResult(
                            "unverified",
                            group_scope + f"models.list 失败（{type(error).__name__}），未验证生成能力",
                            covered,
                            1,
                            group_index=group_index,
                        )
                    )
            else:
                results.append(
                    ProviderProbeResult(
                        "available",
                        group_scope + "models.list 可用（非生成检查）",
                        covered,
                        1,
                        group_index=group_index,
                    )
                )

        return results


_default_client: GeminiChatClient | None = None


def get_default_gemini_client() -> GeminiChatClient:
    """惰性获取默认适配器，不跨事件循环共享连接。

    Returns:
        默认 Gemini 客户端；其内部资源池按事件循环隔离。
    """
    global _default_client
    if _default_client is None:
        _default_client = GeminiChatClient()
    return _default_client


async def close_gemini_clients() -> None:
    """回收当前运行循环中 Gemini 的 idle 资源，活动请求仍由其 owner 负责。"""
    if _default_client is not None:
        await _default_client.aclose()
