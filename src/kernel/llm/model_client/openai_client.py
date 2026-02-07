from __future__ import annotations

import base64
import json
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator

from ..payload import Image, LLMPayload, Text, Tool, ToolResult
from ..roles import ROLE
from .base import StreamEvent
from src.kernel.logger import get_logger


logger = get_logger("llm_openai_client")


def _is_data_url(value: str) -> bool:
    return value.startswith("data:")


def _image_to_data_url(value: str) -> str:
    if value.startswith("base64|"):
        # 兼容设计稿："base64|..."（不含 mime）
        b64 = value.split("|", 1)[1]
        return f"data:image/png;base64,{b64}"

    if _is_data_url(value):
        return value

    path = Path(value)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image file not found: {value}")

    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    # 简化：默认 png
    return f"data:image/png;base64,{b64}"


def _payloads_to_openai_messages(payloads: list[LLMPayload]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    for payload in payloads:
        if payload.role == ROLE.TOOL:
            # TOOL role 不进入 messages；只收集 tools schema
            for item in payload.content:
                if isinstance(item, Tool):
                    tools.append(item.to_openai_tool())
            continue

        if payload.role == ROLE.TOOL_RESULT:
            # OpenAI tool message
            # content 里可能是 ToolResult；在 request.py 里会规范化成文本
            tool_call_id = None
            content_text = None
            for part in payload.content:
                if isinstance(part, ToolResult):
                    if tool_call_id is None and part.call_id:
                        tool_call_id = part.call_id
                    if content_text is None:
                        content_text = part.to_text()
                    continue

                if isinstance(part, Text) and content_text is None:
                    content_text = part.text

                # 兜底：允许用户塞入自定义对象，带 call_id/to_text
                if tool_call_id is None and hasattr(part, "call_id"):
                    tool_call_id = getattr(part, "call_id")
                if content_text is None and hasattr(part, "to_text"):
                    try:
                        content_text = part.to_text()   # type: ignore[attr-defined]
                    except Exception:
                        pass

            if content_text is None:
                content_text = ""

            messages.append(
                {
                    "role": "tool",
                    "content": content_text,
                    **({"tool_call_id": tool_call_id} if tool_call_id else {}),
                }
            )
            continue

        role = payload.role.value

        # content 支持 list[Content]，需转成 OpenAI 的多模态 content parts
        if len(payload.content) == 1 and isinstance(payload.content[0], Text):
            messages.append({"role": role, "content": payload.content[0].text})
            continue

        parts: list[dict[str, Any]] = []
        for part in payload.content:
            if isinstance(part, Text):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, Image):
                url = _image_to_data_url(part.value)
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                # 兜底：转成文本
                parts.append({"type": "text", "text": str(part)})

        messages.append({"role": role, "content": parts})

    return messages, tools


class OpenAIChatClient:
    """OpenAI provider。

    依赖 openai>=2.x。

    配置来源：由上层传入的单个模型配置 dict（见 `LLMRequest` 的 model_set 约束）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[tuple[str, str | None, int, float | None], Any] = {}
        self._sync_clients: dict[tuple[str, str | None, float | None, bool, bool], Any] = {}
        self._sync_http_executors: dict[int, ThreadPoolExecutor] = {}

    def _get_loop_key(self) -> int:
        try:
            loop = asyncio.get_running_loop()
            return id(loop)
        except RuntimeError:
            return 0

    def _get_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ):
        loop_key = self._get_loop_key()
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key = (api_key, base_url, loop_key, timeout_key, trust_env, force_ipv4)
        with self._lock:
            cached = self._clients.get(cache_key)
            if cached is not None:
                return cached


        from openai import AsyncOpenAI
        import httpx


        class _LoggingAsyncTransport(httpx.AsyncBaseTransport):
            def __init__(self, inner: "httpx.AsyncBaseTransport") -> None:
                self._inner = inner

            async def handle_async_request(self, request: "httpx.Request") -> "httpx.Response":
                response = await self._inner.handle_async_request(request)
                return response

        disable_keepalive = False
        if base_url:
            disable_keepalive = base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")

        limits = None
        headers = None
        if disable_keepalive:
            limits = httpx.Limits(max_connections=20, max_keepalive_connections=0, keepalive_expiry=0.0)
            headers = {"Connection": "close"}

        base_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0") if force_ipv4 else httpx.AsyncHTTPTransport()
        transport = _LoggingAsyncTransport(base_transport)
        http_client_kwargs: dict[str, Any] = {"transport": transport, "trust_env": trust_env}
        if limits:
            http_client_kwargs["limits"] = limits
        if headers:
            http_client_kwargs["headers"] = headers
        http_client = httpx.AsyncClient(**http_client_kwargs)

        kwargs: dict[str, Any] = {"api_key": api_key, "http_client": http_client}
        if base_url:
            kwargs["base_url"] = base_url
        if isinstance(timeout, (int, float)):
            kwargs["timeout"] = float(timeout)
        # 重要：重试策略完全由 policy 控制，provider 侧必须禁用自动重试。
        kwargs["max_retries"] = 0

        client = AsyncOpenAI(**kwargs)
        with self._lock:
            self._clients[cache_key] = client
        return client

    def _get_sync_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ):
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key = (api_key, base_url, timeout_key, trust_env, force_ipv4)
        with self._lock:
            cached = self._sync_clients.get(cache_key)
            if cached is not None:
                return cached

        from openai import OpenAI
        import httpx

        transport = httpx.HTTPTransport(local_address="0.0.0.0") if force_ipv4 else httpx.HTTPTransport()
        http_client = httpx.Client(transport=transport, trust_env=trust_env)

        kwargs: dict[str, Any] = {"api_key": api_key, "http_client": http_client}
        if base_url:
            kwargs["base_url"] = base_url
        if isinstance(timeout, (int, float)):
            kwargs["timeout"] = float(timeout)
        kwargs["max_retries"] = 0

        client = OpenAI(**kwargs)
        with self._lock:
            self._sync_clients[cache_key] = client
        return client

    def _get_sync_http_executor(self) -> ThreadPoolExecutor:
        loop_key = self._get_loop_key()
        with self._lock:
            executor = self._sync_http_executors.get(loop_key)
            if executor is None:
                executor = ThreadPoolExecutor(max_workers=4)
                self._sync_http_executors[loop_key] = executor
            return executor

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Tool],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")

        api_key = str(model_set.get("api_key") or "")
        if not api_key:
            raise ValueError("model.api_key 不能为空")

        base_url = model_set.get("base_url")
        base_url = str(base_url) if base_url else None
        timeout = model_set.get("timeout")

        max_tokens = model_set.get("max_tokens")
        temperature = model_set.get("temperature")
        extra_params = model_set.get("extra_params")
        if extra_params is None:
            extra_params = {}
        if not isinstance(extra_params, dict):
            raise ValueError("model.extra_params 必须是 dict")

        extra_params = dict(extra_params)
        trust_env = extra_params.pop("trust_env", None)
        trust_env = bool(trust_env) if trust_env is not None else True
        force_ipv4 = bool(extra_params.pop("force_ipv4", False))
        force_sync_http = bool(extra_params.pop("force_sync_http", False))

        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )
        messages, openai_tools = _payloads_to_openai_messages(payloads)

        params: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if isinstance(max_tokens, int):
            params["max_tokens"] = max_tokens
        if isinstance(temperature, (int, float)):
            params["temperature"] = float(temperature)
        if openai_tools:
            params["tools"] = openai_tools
            if "tool_choice" not in params:
                # Some providers require explicit auto tool choice to return tool_calls
                params["tool_choice"] = "required"

        # 允许每模型注入额外参数（如 top_p/response_format/tool_choice 等）
        params.update(extra_params)

        if not stream and force_sync_http:
            sync_client = self._get_sync_client(
                api_key=api_key,
                base_url=base_url,
                timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
                trust_env=trust_env,
                force_ipv4=force_ipv4,
            )

            def _sync_create():
                return sync_client.chat.completions.create(**params)

            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(self._get_sync_http_executor(), _sync_create)
            msg = resp.choices[0].message
            tool_calls = []
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except Exception:
                        args = tc.function.arguments
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "args": args,
                        }
                    )

            if not tool_calls and getattr(msg, "function_call", None):
                fn_call = msg.function_call
                try:
                    args = json.loads(fn_call.arguments) if fn_call.arguments else {}
                except Exception:
                    args = fn_call.arguments
                tool_calls.append(
                    {
                        "id": None,
                        "name": fn_call.name,
                        "args": args,
                    }
                )
            logger.debug(
                f"OpenAI create (sync) done: model={model_name} request={request_name}"
            )
            return msg.content or "", tool_calls, None

        if not stream:
            resp = await client.chat.completions.create(**params)
            msg = resp.choices[0].message
            tool_calls = []
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except Exception:
                        args = tc.function.arguments
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "args": args,
                        }
                    )

            if not tool_calls and getattr(msg, "function_call", None):
                fn_call = msg.function_call
                try:
                    args = json.loads(fn_call.arguments) if fn_call.arguments else {}
                except Exception:
                    args = fn_call.arguments
                tool_calls.append(
                    {
                        "id": None,
                        "name": fn_call.name,
                        "args": args,
                    }
                )
            return msg.content or "", tool_calls, None

        stream_resp = await client.chat.completions.create(**params, stream=True)

        async def iter_events() -> AsyncIterator[StreamEvent]:
            async for chunk in stream_resp:
                choice = chunk.choices[0]
                delta = choice.delta

                content = getattr(delta, "content", None)
                if content:
                    yield StreamEvent(text_delta=content)

                # 工具调用增量：可能分段传 arguments
                tool_calls_delta = getattr(delta, "tool_calls", None)
                if tool_calls_delta:
                    for tc in tool_calls_delta:
                        fn = getattr(tc, "function", None)
                        yield StreamEvent(
                            tool_call_id=getattr(tc, "id", None),
                            tool_name=getattr(fn, "name", None) if fn else None,
                            tool_args_delta=getattr(fn, "arguments", None) if fn else None,
                        )

                function_call_delta = getattr(delta, "function_call", None)
                if function_call_delta and not tool_calls_delta:
                    yield StreamEvent(
                        tool_call_id="function_call",
                        tool_name=getattr(function_call_delta, "name", None),
                        tool_args_delta=getattr(function_call_delta, "arguments", None),
                    )

        return None, None, iter_events()
