"""使用官方 Google Gen AI SDK，并由 MoFox 管理共享传输和请求级响应。"""
from __future__ import annotations

import asyncio
import json
import math
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, TypeVar
from weakref import WeakKeyDictionary

import httpx
from google import genai
from google.genai import types

from src.kernel.network import build_httpx_timeout, build_httpx_transport

from ..exceptions import LLMConfigurationError
from ..payload import LLMPayload, LLMUsable
from .base import ModelClientResult, StreamEvent
from .gemini_codec import (
    decode_response,
    encode_payloads,
    gemini_options,
    normalize_endpoint,
    target_identity,
)
from .shared import extract_model_transport_params, log_provider_request_body


_ResultT = TypeVar("_ResultT")


async def _await_sdk(operation: Awaitable[_ResultT]) -> _ResultT:
    """原样等待 SDK 操作；关闭错误在响应边界隔离，不再推测异常链或取消计数。"""
    return await operation


def _raise_close_errors(errors: list[BaseException]) -> None:
    """保留取消等控制异常，单一普通错误原样抛出，多项普通错误聚合。"""
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
    """逐项等待关闭，即使某项失败也尝试其余资源，最后传播错误或取消。"""
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
    """保留请求主异常；普通清理错误只作提示，清理期间的新取消仍传播。"""
    try:
        await resource.aclose()
    except Exception:
        primary.add_note("Gemini 资源清理也发生错误；请求主异常保持不变。")


class _ResponseBody(httpx.AsyncByteStream):
    """代理真实 body，立即等待关闭，仅把普通关闭错误延后到 owner 报告。"""

    def __init__(self, source: httpx.AsyncByteStream) -> None:
        """接管单个响应 body 的关闭尝试，不接管共享 HTTP 客户端。"""
        self.source = source
        self.close_error: BaseException | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """原样交付字节和读取异常，不参与 HTTP/SSE 解码。"""
        async for chunk in self.source:
            yield chunk

    async def aclose(self) -> None:
        """只尝试一次底层关闭；普通错误留存，取消及其他控制异常原样传播。"""
        async with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    await self.source.aclose()
                except BaseException as error:
                    self.close_error = error
                    if not isinstance(error, Exception):
                        raise
            elif self.close_error is not None and not isinstance(self.close_error, Exception):
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
    "gemini_response_owner", default=None,
)


async def _capture_response(response: httpx.Response) -> None:
    """通过 HTTPX 公开 hook 将响应登记到当前拉取阶段所属的请求。"""
    owner = _response_owner.get()
    if owner is not None:
        body = _ResponseBody(response.stream)
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
        """保存借用的 transport，关闭责任仍属于调用方。"""
        self.transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """将请求交给借用的传输处理。"""
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
        """接管两个迭代器、一个 lease 引用及可选请求 owner，兼容旧构造。"""
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

    async def aclose(self) -> None:
        """只执行一次关闭并释放逻辑占用；失败会缓存而不是伪装成功。"""
        async with self._close_lock:
            if not self.closed:
                self.closed = True
                token = _response_owner.set(self.owner)
                try:
                    await _close_all([self.events, self.source, self.owner])
                except BaseException as error:
                    self._close_error = error
                    raise
                finally:
                    _response_owner.reset(token)
                    self.lease.active -= 1
            elif self._close_error is not None:
                raise self._close_error


class GeminiChatClient:
    """将 MoFox 请求映射为官方 SDK 调用，重试仍由上层策略管理。"""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """可注入调用方拥有的 transport；按事件循环管理其余自有资源。"""
        self._transport = transport
        self._pools: WeakKeyDictionary[
            Any, OrderedDict[tuple[Any, ...], _ClientLease]
        ] = WeakKeyDictionary()
        self._locks: WeakKeyDictionary[Any, asyncio.Lock] = WeakKeyDictionary()

    async def _get_client(self, model: dict[str, Any]) -> _ClientLease:
        """在所属事件循环的锁内串行创建或回收客户端。"""
        lock = self._locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            return await self._get_client_locked(model)

    async def _get_client_locked(self, model: dict[str, Any]) -> _ClientLease:
        """复用有界且按循环隔离的 SDK 资源，不在导入期间创建连接。"""
        key, base, timeout, trust, ipv4, _, headers, query, _ = (
            extract_model_transport_params(model)
        )
        options = gemini_options(model)
        root, version = normalize_endpoint(base, options.get("api_version", "v1beta"))
        if query:
            raise LLMConfigurationError("Gemini query overrides are not supported")
        if any(
            name.casefold() in {"authorization", "x-goog-api-key", "host", "content-type"}
            for name in headers
        ):
            raise LLMConfigurationError("Gemini authentication/host headers cannot be overridden")
        if timeout is None or not math.isfinite(timeout) or timeout <= 0:
            raise LLMConfigurationError("Gemini timeout must be a positive finite number")
        loop = asyncio.get_running_loop()
        pool = self._pools.setdefault(loop, OrderedDict())
        cache_key = (key, root, version, timeout, trust, ipv4, tuple(sorted(headers.items())))
        if cache_key in pool:
            pool.move_to_end(cache_key)
            pool[cache_key].active += 1
            return pool[cache_key]
        if len(pool) >= 16:
            idle = next((key for key, entry in pool.items() if not entry.active), None)
            if idle is None:
                raise LLMConfigurationError("Gemini client pool is busy; wait before adding credentials")
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
        """回收当前循环的 idle 资源；失败先摘除，普通错误不阻断后续条目。"""
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            pool = self._pools.get(loop, {})
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
            raise LLMConfigurationError("Gemini model_identifier must be a single model ID")
        target = target_identity(model_set)
        contents, system, declared = encode_payloads(payloads, target, tools)
        if not contents:
            raise LLMConfigurationError("Gemini request has no conversation contents")
        _, _, _, _, _, extras, _, _, body = extract_model_transport_params(model_set)
        options = extras.pop("gemini", {})
        if not isinstance(options, dict) or set(options) - {"api_version", "generation_config"}:
            raise LLMConfigurationError("Unknown Gemini configuration option")
        if extras or body:
            raise LLMConfigurationError("Use extra_params.gemini.generation_config for Gemini settings")
        config_values = dict(options.get("generation_config", {}))
        forbidden = {
            "http_options", "system_instruction", "tools", "automatic_function_calling",
            "automatic_continuation", "continuation_token", "candidate_count",
            "max_output_tokens", "temperature",
        }
        if set(config_values) & forbidden or set(config_values) - set(types.GenerateContentConfig.model_fields):
            raise LLMConfigurationError("Gemini config contains an unknown or reserved field")
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
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                automatic_continuation=False,
            )
        except (TypeError, ValueError):
            raise LLMConfigurationError("Invalid Gemini generation_config field types") from None
        # 签名等敏感原始内容不进入请求观测视图。
        log_provider_request_body(
            "gemini.generate_content",
            {"model": name, "contents_count": len(contents), "tool_count": len(declared)},
            model_set=model_set,
            request_name=request_name,
        )
        lease = await self._get_client(model_set)
        owner = _ResponseOwner()
        token = _response_owner.set(owner)
        if not stream:
            try:
                try:
                    response = await _await_sdk(
                        lease.sdk.aio.models.generate_content(
                            model=name, contents=contents, config=config,
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
                    model=name, contents=contents, config=config,
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
            """转换 SDK 块，仅在有效终态后提交工具和续轮状态；关闭由外层负责。"""
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
                    for part in (candidate.content.parts or []) if candidate.content else []:
                        parts.append(part)
                        if part.text:
                            yield (
                                StreamEvent(reasoning_delta=part.text)
                                if part.thought else StreamEvent(text_delta=part.text)
                            )
                try:
                    chunk = await anext(source)
                except StopAsyncIteration:
                    break
            merged = types.GenerateContentResponse(
                candidates=[types.Candidate(
                    index=0,
                    content=types.Content(role="model", parts=parts),
                    finish_reason=finish,
                )],
                usage_metadata=usage,
            )
            result = decode_response(merged, target)
            for call in result.tool_calls or []:
                yield StreamEvent(
                    tool_call_id=call["id"], tool_name=call["name"],
                    tool_args_delta=json.dumps(call["args"]),
                )
            yield StreamEvent(
                usage=result.usage, stop_reason=result.stop_reason,
                provider_state=result.provider_state,
            )
        return ModelClientResult(stream_iter=_OwnedStream(events(), source, lease, owner))


_default_client: GeminiChatClient | None = None


def get_default_gemini_client() -> GeminiChatClient:
    """惰性共享按循环隔离的适配器，不跨事件循环共享连接。"""
    global _default_client
    if _default_client is None:
        _default_client = GeminiChatClient()
    return _default_client


async def close_gemini_clients() -> None:
    """回收当前运行循环中 Gemini 的 idle 资源，活动请求仍由其 owner 负责。"""
    if _default_client is not None:
        await _default_client.aclose()
