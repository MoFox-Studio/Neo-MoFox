"""
Anthropic 模型客户端实现。

实现 ChatModelClient 协议，基于 anthropic SDK 提供异步聊天、流式输出、
工具调用和 thinking/reasoning 内容支持。
"""

from __future__ import annotations

# 这个模块把内部 payload 模型适配到 Anthropic 的 message 和 tool 格式。
# timeout、schema 和请求观测等共享逻辑直接来自 ``model_client.shared``，
# 这样当前文件可以更聚焦在 Anthropic 特有的转换规则和流处理上。

import json
import threading
from collections.abc import Mapping
from typing import Any, AsyncIterator, cast

from src.kernel.llm.payload.tooling import LLMUsable

from ..exceptions import LLMConfigurationError
from ..payload import Image, LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from ..roles import ROLE
from ..token_counter import count_payload_tokens
from .base import StreamEvent
from .shared import (
    build_httpx_timeout,
    extract_model_transport_params,
    inject_reason_parameter,
    log_provider_request_body,
    normalize_schema_for_grammar,
)


_ClientCacheKey = tuple[str, str | None, int, float | None, bool, bool]
_ProviderPayload = dict[str, object]


def _get_attr(data: Any, name: str, default: object | None = None) -> Any:
    """从对象或字典中读取属性。"""
    if isinstance(data, dict):
        return data.get(name, default)
    return getattr(data, name, default)


def _to_anthropic_tool(tool: LLMUsable) -> _ProviderPayload:
    """将单个 LLMUsable 工具转换为 Anthropic tools 格式。"""
    schema = tool.to_schema()
    if schema.get("type") == "function" and "function" in schema:
        function_schema = dict(schema["function"])
    else:
        function_schema = dict(schema)

    input_schema = function_schema.get("parameters", {})
    if isinstance(input_schema, dict):
        input_schema = dict(input_schema)
        # grammar 清理和 reason 注入统一集中处理，避免不同 provider 适配器
        # 在工具 schema 行为上逐渐分叉。
        normalize_schema_for_grammar(input_schema)
        input_schema = inject_reason_parameter(tool, input_schema)

    return {
        "name": str(function_schema.get("name") or "tool"),
        "description": str(function_schema.get("description") or ""),
        "input_schema": input_schema,
    }


def _to_openai_compatible_tool(tool: LLMUsable) -> _ProviderPayload:
    """将工具转换为 OpenAI 风格的 tools 格式。"""
    schema = tool.to_schema()
    if schema.get("type") == "function" and "function" in schema:
        function_schema = dict(schema["function"])
    else:
        function_schema = dict(schema)

    parameters = function_schema.get("parameters", {})
    if isinstance(parameters, dict):
        parameters = dict(parameters)
        normalize_schema_for_grammar(parameters)

    return {
        "type": "function",
        "function": {
            "name": str(function_schema.get("name") or "tool"),
            "description": str(function_schema.get("description") or ""),
            "parameters": parameters,
        },
    }


def _image_to_anthropic_source(image: Image) -> dict[str, str]:
    """将内部 Image 转换为 Anthropic image source。"""
    return {
        "type": "base64",
        "media_type": "image/png",
        "data": image.value,
    }


def _to_plain_text(parts: list[object]) -> str:
    """把内容片段尽量转成纯文本。"""
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, Text):
            chunks.append(part.text)
            continue
        if isinstance(part, ReasoningText):
            continue
        to_text = getattr(part, "to_text", None)
        if callable(to_text):
            try:
                value = to_text()
            except Exception:
                value = ""
            chunks.append(value if isinstance(value, str) else str(value))
            continue
        chunks.append(str(part))
    return "".join(chunks)


def _reasoning_part_to_anthropic_block(part: ReasoningText) -> _ProviderPayload | None:
    """将带元数据的 ReasoningText 转换为 Anthropic thinking block。"""
    if isinstance(part.redacted_data, str) and part.redacted_data:
        return {
            "type": "redacted_thinking",
            "data": part.redacted_data,
        }

    if isinstance(part.signature, str):
        return {
            "type": "thinking",
            "thinking": part.text,
            "signature": part.signature,
        }

    return None


def _extract_anthropic_reasoning_parts(message: object) -> list[ReasoningText]:
    """从 Anthropic 响应中提取可回传的 thinking block。"""
    reasoning_parts: list[ReasoningText] = []
    content = _get_attr(message, "content", [])
    if not isinstance(content, list):
        return reasoning_parts

    for block in content:
        block_type = _get_attr(block, "type")
        if block_type == "thinking":
            thinking_text = _get_attr(block, "thinking", "")
            signature = _get_attr(block, "signature")
            reasoning_parts.append(
                ReasoningText(
                    thinking_text if isinstance(thinking_text, str) else "",
                    signature=str(signature) if isinstance(signature, str) else None,
                )
            )
            continue
        if block_type == "redacted_thinking":
            data = _get_attr(block, "data")
            if isinstance(data, str):
                reasoning_parts.append(ReasoningText("", redacted_data=data))

    return reasoning_parts


def _payloads_to_anthropic_messages(
    payloads: list[LLMPayload],
    *,
    tool_format: str = "anthropic",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """将内部 LLMPayload 列表转换为 Anthropic messages、tools 与 system。"""
    system_blocks: list[_ProviderPayload] = []
    messages: list[_ProviderPayload] = []
    tools: list[_ProviderPayload] = []
    previous_role: ROLE | None = None

    for payload in payloads:
        if payload.role == ROLE.TOOL:
            for item in payload.content:
                if hasattr(item, "to_schema"):
                    tool = cast(LLMUsable, item)
                    if tool_format == "openai":
                        tools.append(_to_openai_compatible_tool(tool))
                    else:
                        tools.append(_to_anthropic_tool(tool))
            previous_role = payload.role
            continue

        if payload.role == ROLE.SYSTEM:
            for part in payload.content:
                if isinstance(part, Text):
                    system_blocks.append({"type": "text", "text": part.text})
                else:
                    system_blocks.append({"type": "text", "text": str(part)})
            previous_role = payload.role
            continue

        if payload.role == ROLE.TOOL_RESULT:
            content_blocks: list[_ProviderPayload] = []
            fallback_parts: list[object] = []
            for part in payload.content:
                if isinstance(part, ToolResult):
                    tool_content = part.to_text()
                    block: _ProviderPayload = {
                        "type": "tool_result",
                        "content": tool_content,
                    }
                    if part.call_id:
                        block["tool_use_id"] = part.call_id
                    if part.name:
                        block["tool_name"] = part.name
                    content_blocks.append(block)
                    continue
                fallback_parts.append(part)

            fallback_text = _to_plain_text(fallback_parts)
            if fallback_text:
                content_blocks.append({"type": "text", "text": fallback_text})

            messages.append({
                "role": "user",
                "content": content_blocks or [{"type": "text", "text": ""}],
            })
            previous_role = payload.role
            continue

        role = "assistant" if payload.role == ROLE.ASSISTANT else "user"
        content_blocks: list[_ProviderPayload] = []
        text_chunks: list[str] = []
        has_reasoning_block = False

        for index, part in enumerate(payload.content):
            if isinstance(part, Text):
                content_blocks.append({"type": "text", "text": part.text})
                text_chunks.append(part.text)
                continue

            if isinstance(part, Image):
                content_blocks.append(
                    {
                        "type": "image",
                        "source": _image_to_anthropic_source(part),
                    }
                )
                continue

            if isinstance(part, ToolCall) and role == "assistant":
                raw_args = part.args
                if isinstance(raw_args, dict):
                    parsed_args: dict[str, object] = raw_args
                elif isinstance(raw_args, str):
                    try:
                        loaded = json.loads(raw_args)
                    except Exception:
                        loaded = {"input": raw_args}
                    parsed_args = (
                        {str(key): value for key, value in loaded.items()}
                        if isinstance(loaded, dict)
                        else {"input": loaded}
                    )
                else:
                    parsed_args = {"input": str(raw_args)}

                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": part.id or f"toolu_{index}",
                        "name": part.name,
                        "input": parsed_args,
                    }
                )
                continue

            if isinstance(part, ReasoningText):
                if role == "assistant":
                    reasoning_block = _reasoning_part_to_anthropic_block(part)
                    if reasoning_block is not None:
                        content_blocks.append(reasoning_block)
                        has_reasoning_block = True
                continue

            to_text = getattr(part, "to_text", None)
            if callable(to_text):
                try:
                    value = to_text()
                except Exception:
                    value = ""
                content_blocks.append({"type": "text", "text": value if isinstance(value, str) else str(value)})
                continue

            content_blocks.append({"type": "text", "text": str(part)})

        if (
            payload.role == ROLE.ASSISTANT
            and previous_role == ROLE.TOOL_RESULT
            and not has_reasoning_block
        ):
            synthesized_thinking = "".join(chunk for chunk in text_chunks if isinstance(chunk, str)).strip()
            if synthesized_thinking:
                content_blocks.insert(
                    0,
                    {
                        "type": "thinking",
                        "thinking": synthesized_thinking,
                    },
                )

        messages.append({
            "role": role,
            "content": content_blocks or [{"type": "text", "text": ""}],
        })
        previous_role = payload.role

    return messages, tools, system_blocks


def _parse_anthropic_message(message: object) -> tuple[str, list[dict[str, Any]], str | None]:
    """从 Anthropic 响应中提取文本、工具调用与 reasoning 内容。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[_ProviderPayload] = []

    content = _get_attr(message, "content", [])
    if not isinstance(content, list):
        content = []

    for block in content:
        block_type = _get_attr(block, "type")
        if block_type == "text":
            text_parts.append(str(_get_attr(block, "text", "")))
            continue
        if block_type == "thinking":
            thinking_text = _get_attr(block, "thinking", "")
            if isinstance(thinking_text, str) and thinking_text:
                reasoning_parts.append(thinking_text)
            continue
        if block_type == "tool_use":
            tool_calls.append(
                {
                    "id": _get_attr(block, "id"),
                    "name": str(_get_attr(block, "name", "")),
                    "args": _get_attr(block, "input", {}),
                }
            )

    reasoning_content = "".join(reasoning_parts) or None
    return "".join(text_parts), tool_calls, reasoning_content


class AnthropicChatClient:
    """Anthropic 聊天客户端。"""

    def __init__(self) -> None:
        """初始化客户端缓存。"""
        self._lock = threading.Lock()
        self._clients: dict[_ClientCacheKey, object] = {}

    def _get_loop_key(self) -> int:
        """获取当前事件循环的唯一标识。"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return id(loop)
        except RuntimeError:
            return 0

    def _extract_model_params(
        self, model_set: Mapping[str, object]
    ) -> tuple[str, str | None, float | None, bool, bool, dict[str, object]]:
        try:
            (
                api_key,
                base_url,
                timeout,
                trust_env,
                force_ipv4,
                extra_params,
            ) = extract_model_transport_params(model_set)
        except ValueError as exc:
            message = str(exc)
            if message == "model.api_key cannot be empty":
                raise ValueError("model.api_key 不能为空") from exc
            if message == "model.extra_params must be a dict":
                raise ValueError("model.extra_params 必须是 dict") from exc
            raise
        return (
            api_key,
            base_url,
            timeout,
            trust_env,
            force_ipv4,
            dict(extra_params),
        )

    def _get_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ) -> object:
        """获取或创建 AsyncAnthropic 客户端。"""
        loop_key = self._get_loop_key()
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key: _ClientCacheKey = (
            api_key,
            base_url,
            loop_key,
            timeout_key,
            trust_env,
            force_ipv4,
        )

        with self._lock:
            cached = self._clients.get(cache_key)
            if cached is not None:
                return cached

        try:
            from anthropic import AsyncAnthropic  # type: ignore[reportMissingImports]
        except ImportError as exc:
            raise LLMConfigurationError("Anthropic SDK 未安装，请先安装 anthropic 依赖") from exc

        import httpx

        # 统一复用共享 timeout builder，确保所有 provider 客户端应用同一套
        # httpx timeout 策略。
        timeout_config = build_httpx_timeout(timeout)
        transport = (
            httpx.AsyncHTTPTransport(local_address="0.0.0.0")
            if force_ipv4
            else httpx.AsyncHTTPTransport()
        )
        http_client_kwargs: dict[str, Any] = {
            "transport": transport,
            "trust_env": trust_env,
        }
        if timeout_config is not None:
            http_client_kwargs["timeout"] = timeout_config

        http_client = httpx.AsyncClient(**http_client_kwargs)
        kwargs: dict[str, Any] = {"api_key": api_key, "http_client": http_client, "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        if isinstance(timeout, (int, float)):
            kwargs["timeout"] = float(timeout)

        client = AsyncAnthropic(**kwargs)
        with self._lock:
            self._clients[cache_key] = client
        return client

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: object,
        stream: bool,
    ) -> tuple[
        str | None,
        list[dict[str, Any]] | None,
        AsyncIterator[StreamEvent] | None,
        str | list[ReasoningText] | None,
    ]:
        """发起一次 Anthropic 消息请求。"""
        del tools

        if not isinstance(model_set, dict):
            raise TypeError("AnthropicChatClient 期望 model_set 为单个模型配置 dict")

        api_key, base_url, timeout, trust_env, force_ipv4, extra_params = self._extract_model_params(model_set)
        tool_format = str(extra_params.pop("tool_format", "anthropic"))
        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )

        messages, anthropic_tools, system_blocks = _payloads_to_anthropic_messages(
            payloads,
            tool_format=tool_format,
        )
        params: dict[str, object] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": int(model_set.get("max_tokens") or 1024),
        }
        if system_blocks:
            params["system"] = system_blocks
        if anthropic_tools:
            params["tools"] = anthropic_tools
            params.setdefault("tool_choice", {"type": "auto"})

        temperature = model_set.get("temperature")
        if isinstance(temperature, (int, float)) and "thinking" not in extra_params:
            params["temperature"] = float(temperature)

        params.update(extra_params)

        # 请求检查和 token 估算统一走共享观测 seam，这里只负责上报原始请求体。
        log_provider_request_body(
            "messages.create",
            params,
            model_set=model_set,
            payloads=payloads,
            request_name=request_name,
            token_counter=count_payload_tokens,
        )

        if stream:
            return await self._create_stream(client=client, params=params)
        return await self._create_non_stream(client=client, params=params)

    async def _create_non_stream(
        self,
        *,
        client: Any,
        params: dict[str, object],
    ) -> tuple[str | None, list[dict[str, Any]] | None, None, list[ReasoningText] | None]:
        """执行非流式 Anthropic 请求。"""
        response = await client.messages.create(**params)
        message_text, tool_calls, reasoning_content = _parse_anthropic_message(response)
        reasoning_parts = _extract_anthropic_reasoning_parts(response)
        if not reasoning_parts and reasoning_content:
            reasoning_parts = [ReasoningText(reasoning_content)]
        return message_text, tool_calls, None, reasoning_parts or None

    async def _create_stream(
        self,
        *,
        client: Any,
        params: dict[str, object],
    ) -> tuple[None, None, AsyncIterator[StreamEvent], None]:
        """执行流式 Anthropic 请求并返回事件迭代器。"""
        stream_manager = client.messages.stream(**params)

        async def iter_events() -> AsyncIterator[StreamEvent]:
            tool_block_meta: dict[int, tuple[str | None, str | None]] = {}

            async with stream_manager as stream:
                async for event in stream:
                    event_type = _get_attr(event, "type")
                    if event_type in {"message_start", "message_stop", "ping", "content_block_stop", "message_delta"}:
                        continue

                    if event_type == "error":
                        error = _get_attr(event, "error", {})
                        message = _get_attr(error, "message", "Anthropic stream error")
                        raise RuntimeError(str(message))

                    if event_type == "content_block_start":
                        index = _get_attr(event, "index", -1)
                        content_block = _get_attr(event, "content_block")
                        block_type = _get_attr(content_block, "type")
                        if block_type in {"thinking", "redacted_thinking"}:
                            yield StreamEvent(reasoning_block_type=str(block_type))
                            continue
                        if block_type in {"tool_use", "server_tool_use"}:
                            tool_call_id = _get_attr(content_block, "id")
                            tool_name = _get_attr(content_block, "name")
                            tool_block_meta[int(index)] = (tool_call_id, tool_name)
                            yield StreamEvent(tool_call_id=tool_call_id, tool_name=tool_name)
                        continue

                    if event_type != "content_block_delta":
                        continue

                    index = int(_get_attr(event, "index", -1))
                    delta = _get_attr(event, "delta")
                    delta_type = _get_attr(delta, "type")

                    if delta_type == "text_delta":
                        yield StreamEvent(text_delta=str(_get_attr(delta, "text", "")))
                        continue

                    if delta_type == "thinking_delta":
                        yield StreamEvent(reasoning_delta=str(_get_attr(delta, "thinking", "")))
                        continue

                    if delta_type == "signature_delta":
                        yield StreamEvent(reasoning_signature_delta=str(_get_attr(delta, "signature", "")))
                        continue

                    if delta_type == "input_json_delta":
                        tool_call_id, tool_name = tool_block_meta.get(index, (None, None))
                        yield StreamEvent(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            tool_args_delta=str(_get_attr(delta, "partial_json", "")),
                        )

        return None, None, iter_events(), None


__all__ = [
    "AnthropicChatClient",
    "_payloads_to_anthropic_messages",
    "_parse_anthropic_message",
]
