"""
OpenAI 模型客户端实现。

实现了 ChatModelClient、EmbeddingModelClient、RerankModelClient 协议，
基于 openai>=2.x SDK，支持异步聊天、流式输出、embedding 与 rerank。
"""

from __future__ import annotations

import base64
import inspect
import json
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from src.kernel.llm.payload.tooling import LLMUsable
from src.kernel.llm.tool_call_compat import (
    build_tool_call_compat_prompt,
    parse_tool_call_compat_response,
)

from ..exceptions import LLMConfigurationError, LLMContentFilterError
from ..payload import Image, LLMPayload, Text, ToolCall, ToolResult
from ..roles import ROLE
from .base import StreamEvent


def _log_openai_request_body(api_name: str, params: dict[str, Any]) -> None:
    """将 OpenAI 请求体送入请求检视器，便于在 WebUI 中核查 payload 结构。"""
    try:
        from src.kernel.llm.request_inspector import capture
        capture(api_name, params)
    except Exception:
        pass


def _build_httpx_timeout(timeout: float | None) -> Any:
    """根据总超时时间构造 httpx.Timeout 实例。

    Args:
        timeout: 总超时秒数，非正数或非数值时返回 None。

    Returns:
        httpx.Timeout 实例，或 None（不限超时）。
    """
    import httpx

    if not isinstance(timeout, (int, float)):
        return None

    total = float(timeout)
    if total <= 0:
        return None

    connect_timeout = min(total, 10.0)
    pool_timeout = min(total, 5.0)
    return httpx.Timeout(
        timeout=total,
        connect=connect_timeout,
        read=total,
        write=total,
        pool=pool_timeout,
    )


def _is_data_url(value: str) -> bool:
    """判断字符串是否为 data URL 格式。

    Args:
        value: 待判断字符串。

    Returns:
        是 data URL 则返回 True，否则 False。
    """
    return value.startswith("data:")


def _image_to_data_url(value: str) -> str:
    """将各种图片表示转换为 data URL 字符串。

    支持以下格式：
    - ``base64|<b64>``：已有 base64 内容
    - ``data:...``：已是 data URL，直接返回
    - 文件路径：读取并编码
    - 纯 base64 字符串

    Args:
        value: 图片表示字符串。

    Returns:
        ``data:image/png;base64,...`` 格式的 data URL。

    Raises:
        FileNotFoundError: 无法识别或文件不存在时抛出。
    """
    if value.startswith("base64|"):
        b64 = value.split("|", 1)[1]
        return f"data:image/png;base64,{b64}"

    if _is_data_url(value):
        return value

    path = Path(value)
    if path.exists() and path.is_file():
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"

    # 尝试作为纯 base64 字符串处理（Image.value 规范化后为纯 base64）
    try:
        base64.b64decode(value, validate=True)
        return f"data:image/png;base64,{value}"
    except Exception:
        pass

    raise FileNotFoundError(f"Image file not found: {value}")


def _to_openai_tool(tool: Any) -> dict[str, Any]:
    """将单个 LLMUsable 工具转换为 OpenAI tools 格式。

    自动注入 ``reason`` 必填参数，帮助模型说明选用该工具的原因。

    Args:
        tool: 实现了 ``to_schema()`` 的工具对象。

    Returns:
        符合 OpenAI tools 格式的 dict。
    """
    schema = tool.to_schema()
    # 兼容两类 schema：
    # 1) 已经是 OpenAI tools 格式：{"type":"function","function":{...}}
    # 2) 仅 function schema：{"name":...,"description":...,"parameters":...}
    if schema.get("type") == "function" and "function" in schema:
        result: dict[str, Any] = schema
    else:
        result = {"type": "function", "function": schema}

    func = result.get("function", {})
    params = func.get("parameters", {})
    if isinstance(params, dict):
        _normalize_schema_for_grammar(params)
    props = params.get("properties", {})
    if "reason" not in props:
        props["reason"] = {
            "type": "string",
            "description": "说明你选择此动作/工具的原因",
        }
        params["properties"] = props
        required: list[str] = params.get("required", [])
        if "reason" not in required:
            required.append("reason")
        params["required"] = required
        func["parameters"] = params
        result["function"] = func

    return result


def _normalize_schema_for_grammar(schema: Any) -> None:
    """就地归一化 JSON Schema，提升与 grammar 编译器的兼容性。"""
    if isinstance(schema, list):
        for item in schema:
            _normalize_schema_for_grammar(item)
        return

    if not isinstance(schema, dict):
        return

    if schema.get("default") is None:
        schema.pop("default", None)

    if schema.get("type") == "array" and "items" not in schema:
        schema["items"] = {"type": "string"}

    if schema.get("type") == "object" and "properties" not in schema:
        schema.setdefault("additionalProperties", {"type": "string"})

    for key in (
        "properties",
        "items",
        "additionalProperties",
        "anyOf",
        "allOf",
        "oneOf",
    ):
        value = schema.get(key)
        if isinstance(value, dict):
            for child in value.values():
                _normalize_schema_for_grammar(child)
        elif isinstance(value, list):
            for child in value:
                _normalize_schema_for_grammar(child)


def _payloads_to_openai_messages(
    payloads: list[LLMPayload],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """将内部 LLMPayload 列表转换为 OpenAI messages 与 tools 格式。

    Args:
        payloads: 待转换的 payload 列表。

    Returns:
        二元组 (messages, tools)，均为 OpenAI API 所需的 dict 列表。
    """
    messages: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    for payload in payloads:
        if payload.role == ROLE.TOOL:
            # TOOL role 不进入 messages；只收集 tools schema
            for item in payload.content:
                tools.append(_to_openai_tool(item))
            continue

        if payload.role == ROLE.TOOL_RESULT:
            tool_payloads: list[tuple[str | None, str]] = []
            fallback_text: str | None = None

            for part in payload.content:
                if isinstance(part, ToolResult):
                    tool_payloads.append((part.call_id, part.to_text()))
                    continue

                if isinstance(part, Text) and fallback_text is None:
                    fallback_text = part.text
                    continue

                call_id_value = getattr(part, "call_id", None)
                call_id = call_id_value if isinstance(call_id_value, str) and call_id_value else None

                to_text = getattr(part, "to_text", None)
                if callable(to_text):
                    try:
                        text_value = to_text()
                        text = text_value if isinstance(text_value, str) else str(text_value)
                    except Exception:
                        text = ""
                    tool_payloads.append((call_id, text))

            if tool_payloads:
                for tool_call_id, content_text in tool_payloads:
                    messages.append(
                        {
                            "role": "tool",
                            "content": content_text,
                            **({"tool_call_id": tool_call_id} if tool_call_id else {}),
                        }
                    )
            else:
                messages.append(
                    {
                        "role": "tool",
                        "content": fallback_text or "",
                    }
                )
            continue

        role = payload.role.value

        if payload.role == ROLE.ASSISTANT:
            tool_calls_list: list[dict[str, Any]] = []
            text_parts: list[str] = []

            for idx, part in enumerate(payload.content):
                if isinstance(part, ToolCall):
                    args_text = (
                        json.dumps(part.args, ensure_ascii=False)
                        if isinstance(part.args, dict)
                        else str(part.args)
                    )
                    call_id = part.id or f"call_{idx}"
                    tool_calls_list.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": part.name,
                                "arguments": args_text,
                            },
                        }
                    )
                    continue

                if isinstance(part, Text):
                    text_parts.append(part.text)
                    continue

            if tool_calls_list:
                messages.append(
                    {
                        "role": role,
                        "content": "".join(text_parts),
                        "tool_calls": tool_calls_list,
                    }
                )
                continue

        # 单纯文本消息走简洁格式
        if len(payload.content) == 1 and isinstance(payload.content[0], Text):
            messages.append({"role": role, "content": payload.content[0].text})
            continue

        # 多模态内容
        parts: list[dict[str, Any]] = []
        for part in payload.content:
            if isinstance(part, Text):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, Image):
                url = _image_to_data_url(part.value)
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                parts.append({"type": "text", "text": str(part)})

        messages.append({"role": role, "content": parts})

    return messages, tools


def _parse_completion_message(
    msg: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """从 OpenAI 响应消息对象中提取文本内容与工具调用列表。

    Args:
        msg: OpenAI ``ChatCompletionMessage`` 对象。

    Returns:
        二元组 (message_content, tool_calls)。
        tool_calls 中每个元素形如 ``{"id": ..., "name": ..., "args": ...}``。
    """
    message_content: str = msg.content or ""
    tool_calls: list[dict[str, Any]] = []

    if getattr(msg, "tool_calls", None):
        for tc in msg.tool_calls:
            try:
                args = (
                    json.loads(tc.function.arguments)
                    if tc.function.arguments
                    else {}
                )
            except Exception:
                args = tc.function.arguments
            tool_calls.append(
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": args,
                }
            )

    # 兼容旧式 function_call（部分提供商）
    fn_call = getattr(msg, "function_call", None)
    fn_name = getattr(fn_call, "name", None) if fn_call is not None else None
    if not tool_calls and isinstance(fn_name, str) and fn_name:
        fn_args_raw = getattr(fn_call, "arguments", None)
        try:
            args = json.loads(fn_args_raw) if fn_args_raw else {}
        except Exception:
            args = fn_args_raw
        tool_calls.append(
            {
                "id": None,
                "name": fn_name,
                "args": args,
            }
        )

    return message_content, tool_calls


# _ClientCacheKey: (api_key, base_url, loop_id, timeout, trust_env, force_ipv4)
_ClientCacheKey = tuple[str, str | None, int, float | None, bool, bool]


class OpenAIChatClient:
    """OpenAI 聊天、embedding 与 rerank 客户端。

    依赖 openai>=2.x，纯异步实现。

    配置来源：由上层传入的单个模型配置 dict（见 ``LLMRequest`` 的 model_set 约束）。

    支持的 extra_params 保留键（不传递给 API）：

    - ``trust_env``：是否信任系统代理环境变量，默认 True。
    - ``force_ipv4``：是否强制使用 IPv4 出口，默认 False。
    - ``context_reserve_ratio`` / ``context_reserve_tokens``：由上层策略使用，此处忽略。
    - ``force_sync_http``：已废弃，忽略。
    """

    def __init__(self) -> None:
        """初始化客户端，建立内部缓存与锁."""
        self._lock = threading.Lock()
        self._clients: dict[_ClientCacheKey, Any] = {}
        self._platform_info: Any = None

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _get_loop_key(self) -> int:
        """获取当前事件循环的唯一标识，无循环时返回 0。

        Returns:
            事件循环对象的 id，或 0。
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return id(loop)
        except RuntimeError:
            return 0

    def _ensure_platform_info(self) -> Any:
        """懒加载并缓存 openai SDK 的平台信息，用于减少重复计算。

        Returns:
            平台信息对象，若获取失败则返回 None。
        """
        with self._lock:
            if self._platform_info is not None:
                return self._platform_info

        try:
            from openai._base_client import get_platform

            platform_info = get_platform()
        except Exception:
            platform_info = None

        with self._lock:
            if self._platform_info is None:
                self._platform_info = platform_info
        return self._platform_info

    def _get_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ) -> Any:
        """获取或创建异步 AsyncOpenAI 客户端（按循环缓存）。

        Args:
            api_key: OpenAI 兼容 API 密钥。
            base_url: 自定义 base URL，None 表示使用默认。
            timeout: 总超时秒数，None 表示不限。
            trust_env: 是否信任系统代理环境变量。
            force_ipv4: 是否强制使用 IPv4 出口。

        Returns:
            AsyncOpenAI 实例。
        """
        loop_key = self._get_loop_key()
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key: _ClientCacheKey = (
            api_key, base_url, loop_key, timeout_key, trust_env, force_ipv4
        )

        with self._lock:
            cached = self._clients.get(cache_key)
            if cached is not None:
                return cached

        from openai import AsyncOpenAI
        import httpx

        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=5,
            keepalive_expiry=10.0,
        )
        timeout_config = _build_httpx_timeout(timeout)
        base_transport = (
            httpx.AsyncHTTPTransport(local_address="0.0.0.0")
            if force_ipv4
            else httpx.AsyncHTTPTransport()
        )
        http_client_kwargs: dict[str, Any] = {
            "transport": base_transport,
            "trust_env": trust_env,
            "limits": limits,
        }
        if timeout_config is not None:
            http_client_kwargs["timeout"] = timeout_config

        http_client = httpx.AsyncClient(**http_client_kwargs)

        kwargs: dict[str, Any] = {"api_key": api_key, "http_client": http_client}
        if base_url:
            kwargs["base_url"] = base_url
        if isinstance(timeout, (int, float)):
            kwargs["timeout"] = float(timeout)
        # 重要：重试策略完全由 policy 控制，provider 侧必须禁用自动重试。
        kwargs["max_retries"] = 0

        client = AsyncOpenAI(**kwargs)
        try:
            platform_info = self._ensure_platform_info()
            if platform_info is not None:
                client._platform = platform_info
        except Exception:
            pass

        with self._lock:
            self._clients[cache_key] = client
        return client

    def _evict_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ) -> Any | None:
        """从缓存中移除并返回对应的异步客户端（用于连接错误后强制重建）。

        Args:
            api_key: API 密钥。
            base_url: 自定义 base URL。
            timeout: 超时秒数。
            trust_env: 是否信任代理环境变量。
            force_ipv4: 是否强制 IPv4。

        Returns:
            被移除的客户端实例，若不存在则为 None。
        """
        loop_key = self._get_loop_key()
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key: _ClientCacheKey = (
            api_key, base_url, loop_key, timeout_key, trust_env, force_ipv4
        )
        with self._lock:
            return self._clients.pop(cache_key, None)

    def _extract_model_params(
        self, model_set: dict[str, Any]
    ) -> tuple[str, str | None, float | None, bool, bool, dict[str, Any]]:
        """从 model_set dict 中解析并校验公共连接参数。

        Args:
            model_set: 单个模型配置字典。

        Returns:
            六元组 ``(api_key, base_url, timeout, trust_env, force_ipv4, extra_params)``。

        Raises:
            ValueError: api_key 为空或 extra_params 非 dict 时抛出。
        """
        api_key = str(model_set.get("api_key") or "")
        if not api_key:
            raise ValueError("model.api_key 不能为空")

        base_url = model_set.get("base_url")
        base_url = str(base_url) if base_url else None
        timeout = model_set.get("timeout")

        extra_params = model_set.get("extra_params")
        if extra_params is None:
            extra_params = {}
        if not isinstance(extra_params, dict):
            raise ValueError("model.extra_params 必须是 dict")

        extra_params = dict(extra_params)
        trust_env_raw = extra_params.pop("trust_env", None)
        trust_env = bool(trust_env_raw) if trust_env_raw is not None else True
        force_ipv4 = bool(extra_params.pop("force_ipv4", False))
        # 以下键由上层策略消费，client 侧不传给 API
        extra_params.pop("context_reserve_ratio", None)
        extra_params.pop("context_reserve_tokens", None)
        extra_params.pop("force_sync_http", None)

        timeout_float = float(timeout) if isinstance(timeout, (int, float)) else None
        return api_key, base_url, timeout_float, trust_env, force_ipv4, extra_params

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        """发起一次聊天请求。

        Args:
            model_name: 模型名称（如 ``gpt-4o``）。
            payloads: 消息负载列表。
            tools: 工具定义列表（保持协议兼容，实际通过 payloads 中 ROLE.TOOL 传入）。
            request_name: 请求名称，用于追踪，此处不使用。
            model_set: 单个模型配置 dict。
            stream: 是否开启流式输出。

        Returns:
            三元组 ``(message, tool_calls, stream_iter)``：

            - 非流时：``(完整文本, 工具调用列表, None)``
            - 流式时：``(None, None, AsyncIterator[StreamEvent])``

        Raises:
            TypeError: model_set 不是 dict 时抛出。
            ValueError: api_key 为空或 extra_params 非 dict 时抛出。
            LLMContentFilterError: 模型返回空 choices 时抛出。
        """
        del request_name  # 保留参数以满足协议，暂不使用
        del tools  # 通过 payloads 中 ROLE.TOOL 传入，此参数保持协议兼容

        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")

        api_key, base_url, timeout, trust_env, force_ipv4, extra_params = (
            self._extract_model_params(model_set)
        )
        # force_sync_http 已废弃，移除后不传给 API
        extra_params.pop("force_sync_http", None)

        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )
        messages, openai_tools = _payloads_to_openai_messages(payloads)
        tool_call_compat = bool(model_set.get("tool_call_compat", False))

        if tool_call_compat and openai_tools:
            compat_prompt = build_tool_call_compat_prompt(openai_tools)
            messages = [*messages, {"role": "user", "content": compat_prompt}]

        max_tokens = model_set.get("max_tokens")
        temperature = model_set.get("temperature")

        params: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if isinstance(max_tokens, int):
            params["max_tokens"] = max_tokens
        if isinstance(temperature, (int, float)):
            params["temperature"] = float(temperature)

        # 允许每模型注入额外参数（如 top_p/response_format/tool_choice 等）
        # 注意：tool_choice 的默认策略会在 tools 分支中补齐。
        params.update(extra_params)
        if openai_tools and not tool_call_compat:
            params["tools"] = openai_tools
            if "tool_choice" not in params:
                # 默认策略：统一使用 required。
                # 如果无法支持请在 model_set.extra_params 显式传入 tool_choice="auto"。
                params["tool_choice"] = "required"

        # 新增：区分标准参数与非标准参数（统一走 extra_body，避免 openai SDK 因未知参数报错）
        standard_params = {
            "model", "messages", "max_tokens", "temperature", "top_p", "n", "stream",
            "stop", "presence_penalty", "frequency_penalty", "logit_bias", "user",
            "tools", "tool_choice", "response_format", "seed", "parallel_tool_calls",
            "functions", "function_call", "extra_body"
        }
        extra_body: dict[str, Any] = {}
        for key in list(params.keys()):
            if key not in standard_params:
                extra_body[key] = params.pop(key)
        if extra_body:
            existing = params.get("extra_body")
            if isinstance(existing, dict):
                merged = {**existing, **extra_body}
                params["extra_body"] = merged
            else:
                params["extra_body"] = extra_body

        # 兼容：部分 OpenAI 兼容网关（如 Kimi）在开启 thinking 时，
        # 要求所有包含 tool_calls 的 assistant 消息携带 reasoning_content 字段。
        # 否则会返回 400（thinking enabled but reasoning_content is missing）。
        thinking_enabled = False
        extra_body_params = params.get("extra_body")
        if isinstance(extra_body_params, dict):
            enable_thinking = extra_body_params.get("enable_thinking")
            thinking_enabled = bool(enable_thinking) if enable_thinking is not None else False

        if thinking_enabled:
            for msg in messages:
                if (
                    msg.get("role") == "assistant"
                    and msg.get("tool_calls")
                    and "reasoning_content" not in msg
                ):
                    content = msg.get("content")
                    msg["reasoning_content"] = content if isinstance(content, str) else ""

        if not stream:
            return await self._create_non_stream(
                client=client,
                params=params,
                tool_call_compat=tool_call_compat,
                openai_tools=openai_tools,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                trust_env=trust_env,
                force_ipv4=force_ipv4,
                model_name=model_name,
            )

        return await self._create_stream(client=client, params=params)

    async def _create_non_stream(
        self,
        *,
        client: Any,
        params: dict[str, Any],
        tool_call_compat: bool,
        openai_tools: list[dict[str, Any]],
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
        model_name: str,
    ) -> tuple[str | None, list[dict[str, Any]] | None, None]:
        """执行非流式聊天请求并返回解析结果。

        遇到网络/超时异常时会驱逐缓存的客户端，以便下次请求重建连接。

        Args:
            client: AsyncOpenAI 实例。
            params: 请求参数 dict。
            tool_call_compat: 是否使用工具调用兼容模式。
            openai_tools: 已转换的 tools 列表。
            api_key: API 密钥（用于驱逐缓存）。
            base_url: base URL（用于驱逐缓存）。
            timeout: 超时（用于驱逐缓存）。
            trust_env: 代理环境变量开关（用于驱逐缓存）。
            force_ipv4: IPv4 强制标志（用于驱逐缓存）。
            model_name: 模型名称（用于错误信息）。

        Returns:
            三元组 ``(message_content, tool_calls, None)``。

        Raises:
            LLMContentFilterError: 模型返回空 choices 时抛出。
        """
        try:
            _log_openai_request_body("chat.completions.create", params)
            resp = await client.chat.completions.create(**params)
        except Exception as e:
            err_name = type(e).__name__.lower()
            err_text = str(e).lower()
            if any(
                kw in err_name or kw in err_text
                for kw in ("timeout", "connect", "network", "transport")
            ):
                stale = self._evict_client(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                    trust_env=trust_env,
                    force_ipv4=force_ipv4,
                )
                if stale is not None:
                    try:
                        await stale.close()
                    except Exception:
                        pass
            raise

        if not resp.choices:
            raise LLMContentFilterError(
                f"模型返回空响应（可能触发了安全过滤器）。Response: {resp}",
                filter_type="empty_choices",
                model=model_name,
            )

        msg = resp.choices[0].message
        message_content, tool_calls = _parse_completion_message(msg)

        if tool_call_compat and openai_tools and not tool_calls:
            parsed_message, parsed_calls = parse_tool_call_compat_response(
                message_content
            )
            return parsed_message, parsed_calls, None

        return message_content, tool_calls, None

    async def _create_stream(
        self,
        *,
        client: Any,
        params: dict[str, Any],
    ) -> tuple[None, None, AsyncIterator[StreamEvent]]:
        """执行流式聊天请求并返回事件迭代器。

        Args:
            client: AsyncOpenAI 实例。
            params: 请求参数 dict（不含 ``stream`` 键）。

        Returns:
            三元组 ``(None, None, AsyncIterator[StreamEvent])``。
        """
        stream_params = dict(params)
        stream_params["stream"] = True
        _log_openai_request_body("chat.completions.create", stream_params)
        stream_resp = await client.chat.completions.create(**params, stream=True)

        async def iter_events() -> AsyncIterator[StreamEvent]:
            """逐块迭代流式响应，产出 StreamEvent。

            OpenAI 标准流格式：首包携带 tool_call id，后续增量包 id 为 None，
            通过 index 归属。使用 ``index_to_id`` 维护映射关系。
            """
            index_to_id: dict[int, str] = {}

            try:
                async for chunk in stream_resp:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    content = getattr(delta, "content", None)
                    if content:
                        yield StreamEvent(text_delta=content)

                    tool_calls_delta = getattr(delta, "tool_calls", None)
                    if tool_calls_delta:
                        for tc in tool_calls_delta:
                            fn = getattr(tc, "function", None)
                            tc_id: str | None = getattr(tc, "id", None)
                            tc_index: int | None = getattr(tc, "index", None)

                            if tc_id and tc_index is not None:
                                index_to_id[tc_index] = tc_id

                            effective_id = tc_id or (
                                index_to_id.get(tc_index)
                                if tc_index is not None
                                else None
                            )

                            yield StreamEvent(
                                tool_call_id=effective_id,
                                tool_name=getattr(fn, "name", None) if fn else None,
                                tool_args_delta=(
                                    getattr(fn, "arguments", None) if fn else None
                                ),
                            )

                    function_call_delta = getattr(delta, "function_call", None)
                    if function_call_delta and not tool_calls_delta:
                        yield StreamEvent(
                            tool_call_id="function_call",
                            tool_name=getattr(function_call_delta, "name", None),
                            tool_args_delta=getattr(
                                function_call_delta, "arguments", None
                            ),
                        )
            finally:
                close = getattr(stream_resp, "aclose", None)
                if callable(close):
                    maybe_awaitable = close()
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                    return

                close_sync = getattr(stream_resp, "close", None)
                if callable(close_sync):
                    close_sync()

        return None, None, iter_events()

    async def create_embedding(
        self,
        *,
        model_name: str,
        inputs: list[str],
        request_name: str,
        model_set: Any,
    ) -> list[list[float]]:
        """发起 embedding 请求，返回向量列表。

        Args:
            model_name: embedding 模型名称。
            inputs: 待向量化的文本列表，不能为空。
            request_name: 请求名称，用于追踪，此处不使用。
            model_set: 单个模型配置 dict。

        Returns:
            与 ``inputs`` 等长的向量列表，每个向量为 float 列表。

        Raises:
            TypeError: model_set 不是 dict 时抛出。
            ValueError: api_key 为空、inputs 为空或 extra_params 非 dict 时抛出。
        """
        del request_name

        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")
        if not inputs:
            raise ValueError("inputs 不能为空")

        api_key, base_url, timeout, trust_env, force_ipv4, extra_params = (
            self._extract_model_params(model_set)
        )
        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )

        params: dict[str, Any] = {
            "model": model_name,
            "input": inputs,
        }
        params.update(extra_params)

        _log_openai_request_body("embeddings.create", params)
        resp = await client.embeddings.create(**params)
        data = getattr(resp, "data", None)
        if not data:
            return []

        out: list[list[float]] = []
        for item in data:
            vec = getattr(item, "embedding", None)
            if isinstance(vec, list):
                out.append([float(v) for v in vec])
        return out

    async def create_rerank(
        self,
        *,
        model_name: str,
        query: str,
        documents: list[Any],
        top_n: int | None,
        request_name: str,
        model_set: Any,
    ) -> list[dict[str, Any]]:
        """发起 rerank 请求，返回按相关性降序排列的结果。

        仅支持提供商 SDK 原生 ``rerank`` 接口。
        若 SDK 不存在该接口，抛出 ``LLMConfigurationError`` 交由上级处理。

        Args:
            model_name: rerank 模型名称。
            query: 查询文本，不能为空。
            documents: 待排序的文档列表（str 或 dict），不能为空。
            top_n: 返回结果数量上限，None 表示全部返回。
            request_name: 请求名称，用于追踪，此处不使用。
            model_set: 单个模型配置 dict。

        Returns:
            每个元素为 ``{"index": int, "score": float, "document": Any}``，
            按 score 降序排列。

        Raises:
            TypeError: model_set 不是 dict 时抛出。
            ValueError: query/documents 为空或 extra_params 非 dict 时抛出。
            LLMConfigurationError: 当前 SDK 不支持原生 rerank 接口时抛出。
        """
        del request_name

        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")
        if not query:
            raise ValueError("query 不能为空")
        if not documents:
            raise ValueError("documents 不能为空")

        api_key, base_url, timeout, trust_env, force_ipv4, extra_params = (
            self._extract_model_params(model_set)
        )
        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )

        # 尝试调用 SDK 原生 rerank 接口
        rerank_api = getattr(client, "rerank", None)
        rerank_create = (
            getattr(rerank_api, "create", None) if rerank_api is not None else None
        )
        if callable(rerank_create):
            params: dict[str, Any] = {
                "model": model_name,
                "query": query,
                "documents": documents,
            }
            if isinstance(top_n, int) and top_n > 0:
                params["top_n"] = top_n

            _log_openai_request_body("rerank.create", params)
            maybe_resp = rerank_create(**params)
            if inspect.isawaitable(maybe_resp):
                resp = await maybe_resp
            else:
                resp = maybe_resp

            data = (
                getattr(resp, "results", None)
                or getattr(resp, "data", None)
                or []
            )
            out: list[dict[str, Any]] = []
            for rec in data:
                idx = getattr(rec, "index", None)
                score = getattr(rec, "relevance_score", None)
                if score is None:
                    score = getattr(rec, "score", None)
                index = int(idx) if isinstance(idx, int) else 0
                out.append(
                    {
                        "index": index,
                        "score": float(score) if isinstance(score, (int, float)) else 0.0,
                        "document": documents[index] if 0 <= index < len(documents) else None,
                    }
                )
            return out

        raise LLMConfigurationError(
            f"当前 SDK 客户端不支持原生 rerank 接口，模型：{model_name}"
        )
