"""Official Google Gen AI SDK adapter with MoFox-owned HTTP transport."""
from __future__ import annotations

import asyncio
import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, AsyncIterator
from weakref import WeakKeyDictionary

import httpx
from google import genai
from google.genai import types

from src.kernel.network import build_httpx_timeout, build_httpx_transport

from ..exceptions import LLMConfigurationError
from ..payload import LLMPayload, LLMUsable
from .base import ModelClientResult, StreamEvent
from .gemini_codec import decode_response, encode_payloads, normalize_endpoint, target_identity, gemini_options
from .shared import extract_model_transport_params, log_provider_request_body


@dataclass(slots=True)
class _ClientLease:
    """Owned SDK/HTTP resources and their in-flight reference count."""

    sdk: genai.Client
    http: httpx.AsyncClient
    active: int = 0

    async def close(self) -> None:
        """Close both SDK-created sync resources and the injected async client."""
        try:
            await self.sdk.aio.aclose()
        finally:
            try:
                self.sdk.close()
            finally:
                await self.http.aclose()


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Delegate requests without transferring the caller's transport ownership."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        """Retain the borrowed transport."""
        self.transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send through the caller transport."""
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        """The caller closes the underlying transport."""


class _OwnedStream:
    """Close a prefetched stream even if its generator has never been entered."""

    def __init__(self, events: AsyncIterator[StreamEvent], source: Any, lease: _ClientLease) -> None:
        """Own the two iterators and one lease reference."""
        self.events, self.source, self.lease = events, source, lease
        self.closed = False

    def __aiter__(self) -> _OwnedStream:
        """Return this iterator."""
        return self

    async def __anext__(self) -> StreamEvent:
        """Read events and release resources on every exit."""
        try:
            return await anext(self.events)
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        """Release resources exactly once."""
        if not self.closed:
            self.closed = True
            try:
                try:
                    await self.events.aclose()
                finally:
                    await self.source.aclose()
            finally:
                self.lease.active -= 1


class GeminiChatClient:
    """Convert MoFox requests to official SDK calls; retry belongs to policy."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """Optionally inject a caller-owned transport for offline tests."""
        self._transport = transport
        self._pools: WeakKeyDictionary[Any, OrderedDict[tuple[Any, ...], _ClientLease]] = WeakKeyDictionary()
        self._locks: WeakKeyDictionary[Any, asyncio.Lock] = WeakKeyDictionary()

    async def _get_client(self, model: dict[str, Any]) -> _ClientLease:
        """Serialize pool creation/eviction within the owning event loop."""
        lock = self._locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            return await self._get_client_locked(model)

    async def _get_client_locked(self, model: dict[str, Any]) -> _ClientLease:
        """Reuse bounded, loop-local SDK clients without import-time resources."""
        key, base, timeout, trust, ipv4, _, headers, query, _ = extract_model_transport_params(model)
        options = gemini_options(model)
        root, version = normalize_endpoint(base, options.get("api_version", "v1beta"))
        if query:
            raise LLMConfigurationError("Gemini query overrides are not supported")
        if any(name.casefold() in {"authorization", "x-goog-api-key", "host", "content-type"} for name in headers):
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
            idle = next((k for k, entry in pool.items() if not entry.active), None)
            if idle is None:
                raise LLMConfigurationError("Gemini client pool is busy; wait before adding credentials")
            await pool.pop(idle).close()
        http = httpx.AsyncClient(timeout=build_httpx_timeout(timeout), trust_env=trust,
            transport=_BorrowedTransport(self._transport) if self._transport is not None else build_httpx_transport(force_ipv4=ipv4),
            follow_redirects=False, limits=httpx.Limits(max_connections=100, max_keepalive_connections=5))
        try:
            sdk = genai.Client(vertexai=False, api_key=key, http_options=types.HttpOptions(
                base_url=root, api_version=version, httpx_async_client=http, headers=headers,
                timeout=max(1, int(timeout * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            ))
        except Exception:
            await http.aclose()
            raise
        lease = _ClientLease(sdk, http, active=1)
        pool[cache_key] = lease
        return lease

    async def aclose(self) -> None:
        """Release this event loop's idle resources; active streams retain ownership."""
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            pool = self._pools.get(loop, {})
            for key, lease in list(pool.items()):
                if not lease.active:
                    await lease.close()
                    del pool[key]

    async def create(
        self, *, model_name: str, payloads: list[LLMPayload], tools: list[LLMUsable],
        request_name: str, model_set: Any, stream: bool,
    ) -> ModelClientResult:
        """Build SDK inputs and return a uniform non-stream or managed stream result."""
        if not isinstance(model_set, dict):
            raise LLMConfigurationError("Gemini requires one model entry")
        name = model_name.removeprefix("models/")
        if not name or any(c in name for c in "/:?&#\\") or name in {".", ".."}:
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
        forbidden = {"http_options", "system_instruction", "tools", "automatic_function_calling", "automatic_continuation", "continuation_token", "candidate_count", "max_output_tokens", "temperature"}
        if set(config_values) & forbidden or set(config_values) - set(types.GenerateContentConfig.model_fields):
            raise LLMConfigurationError("Gemini config contains an unknown or reserved field")
        maximum = model_set.get("max_tokens", 1024)
        temperature = model_set.get("temperature")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise LLMConfigurationError("Gemini max_tokens must be a positive integer")
        if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature)):
            raise LLMConfigurationError("Gemini temperature must be finite")
        try:
            config = types.GenerateContentConfig(**config_values, system_instruction=types.Content(parts=system) if system else None,
                tools=declared or None, candidate_count=1, max_output_tokens=maximum, temperature=temperature,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), automatic_continuation=False)
        except (TypeError, ValueError):
            raise LLMConfigurationError("Invalid Gemini generation_config field types") from None
        # Signatures are intentionally excluded from the observation view.
        log_provider_request_body("gemini.generate_content", {"model": name, "contents_count": len(contents), "tool_count": len(declared)},
            model_set=model_set, request_name=request_name)
        lease = await self._get_client(model_set)
        if not stream:
            try:
                response = await lease.sdk.aio.models.generate_content(model=name, contents=contents, config=config)
                return decode_response(response, target)
            finally:
                lease.active -= 1
        try:
            source = await lease.sdk.aio.models.generate_content_stream(model=name, contents=contents, config=config)
            # Establish the real request within the caller's attempt timeout.
            first = await anext(source)
        except BaseException:
            lease.active -= 1
            if "source" in locals():
                await source.aclose()
            raise

        async def events() -> AsyncIterator[StreamEvent]:
            """Map decoded SDK chunks; commit tools/replay only after a valid finish."""
            parts: list[types.Part] = []
            finish: Any = None
            usage: Any = None
            try:
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
                                yield StreamEvent(reasoning_delta=part.text) if part.thought else StreamEvent(text_delta=part.text)
                    try:
                        chunk = await anext(source)
                    except StopAsyncIteration:
                        break
                merged = types.GenerateContentResponse(candidates=[types.Candidate(index=0,
                    content=types.Content(role="model", parts=parts), finish_reason=finish)], usage_metadata=usage)
                result = decode_response(merged, target)
                for call in result.tool_calls or []:
                    yield StreamEvent(tool_call_id=call["id"], tool_name=call["name"], tool_args_delta=json.dumps(call["args"]))
                yield StreamEvent(usage=result.usage, stop_reason=result.stop_reason, provider_state=result.provider_state)
            finally:
                await source.aclose()
        return ModelClientResult(stream_iter=_OwnedStream(events(), source, lease))


_default_client: GeminiChatClient | None = None


def get_default_gemini_client() -> GeminiChatClient:
    """Lazily share the loop-aware adapter, never a cross-loop HTTP connection."""
    global _default_client
    if _default_client is None:
        _default_client = GeminiChatClient()
    return _default_client


async def close_gemini_clients() -> None:
    """Release the current runtime loop's Gemini HTTP resources."""
    if _default_client is not None:
        await _default_client.aclose()
