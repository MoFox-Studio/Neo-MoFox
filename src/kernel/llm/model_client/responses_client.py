"""
OpenAI Responses API 模型客户端实现。

实现 ChatModelClient 协议，基于 openai>=2.x SDK 的 ``client.responses.create``，
支持异步聊天、流式输出、函数工具调用与思考内容，同时兼容服务端内置工具
（如 ``web_search`` / ``web_search_2025_08_26`` / ``custom`` 等）的声明。

与 Chat Completions 的差异：

- 请求体是 ``input`` item 列表（``message`` / ``function_call`` /
  ``function_call_output`` / ``reasoning`` / ``web_search_call``）。
- 工具声明 ``tools`` 除了 ``function`` 外还允许内置工具类型；内置工具由服务端
  执行，客户端无需本地实现 execute。其他内置工具类型（如 ``file_search`` /
  ``code_interpreter`` 等）若供应商不支持会被静默忽略。
- 服务端执行的内置工具（如 ``web_search``）通过模型配置的 ``extra_params.body``
  自定义请求体声明，例如::

      extra_params = { body = { tools = [{ type = "web_search" }] } }

  客户端会把 body 中的内置工具与内部函数工具合并去重后一起发送。

本模块复用 ``openai_client`` 中的传输层客户端缓存、usage/reasoning 提取等
共享逻辑，只聚焦 Responses 特有的 payload 映射与事件解析。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import Any, AsyncIterator

from src.kernel.llm.payload.tooling import LLMUsable

from ..exceptions import LLMConfigurationError, LLMContentFilterError
from ..payload import Image, LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from ..roles import ROLE
from ..token_counter import count_payload_tokens
from ..tool_call_compat import build_tool_call_compat_prompt
from .base import StreamEvent
from .openai_client import (
    OpenAIChatClient,
    _extract_usage_from_obj,
    _get_usage_field,
    _image_to_data_url,
    _to_openai_tool,
)
from .shared import log_provider_request_body


def _get_attr(data: Any, name: str, default: Any = None) -> Any:
    """从对象或字典中读取属性，兼容 SDK 对象与原始 dict 响应。"""
    if isinstance(data, dict):
        return data.get(name, default)
    return getattr(data, name, default)


# Responses API 顶层标准参数（SDK ``responses.create`` 直接接受的关键字）。
_RESPONSES_STANDARD_PARAMS = {
    "background",
    "context_management",
    "conversation",
    "include",
    "input",
    "instructions",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "model",
    "parallel_tool_calls",
    "previous_response_id",
    "prompt",
    "prompt_cache_key",
    "prompt_cache_retention",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "truncation",
    "user",
    "extra_headers",
    "extra_query",
    "extra_body",
}


def _tool_to_response_tool(item: Any) -> dict[str, Any] | None:
    """把一个工具声明转换为 Responses ``tools`` 条目。

    支持两类输入：

    - 实现了 ``to_schema()`` 的 LLMUsable：转换为 ``{"type": "function", ...}``。
    - 原始工具声明 dict（如 ``{"type": "web_search"}``）：原样透传，
      以支持服务端内置工具。

    Args:
        item: ROLE.TOOL payload 中的单个内容项。

    Returns:
        Responses 工具声明 dict；无法识别时返回 None。
    """
    if isinstance(item, dict):
        if "type" in item:
            return dict(item)
        return None
    if hasattr(item, "to_schema"):
        return _to_openai_tool(item)
    return None


def _parse_tool_arguments(raw: Any) -> dict[str, Any] | str:
    """把函数调用参数文本解析为 dict，失败时保留原始字符串。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"input": parsed}
        except Exception:
            return raw
    return {}


def _message_item(
    role: str,
    parts: list[Any],
    *,
    output_text: bool,
) -> dict[str, Any]:
    """把一组内容片段转换为 Responses message item。

    Args:
        role: ``user`` / ``assistant`` / ``system`` / ``developer``。
        parts: 内容片段列表。
        output_text: 是否为 assistant 输出（使用 ``output_text`` 块）。

    Returns:
        Responses ``message`` 输入 item。
    """
    text_chunks: list[str] = []
    blocks: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, Text):
            text_chunks.append(part.text)
            blocks.append(
                {
                    "type": "output_text" if output_text else "input_text",
                    "text": part.text,
                }
            )
            continue
        if isinstance(part, Image):
            blocks.append(
                {"type": "input_image", "image_url": _image_to_data_url(part.value)}
            )
            continue
        if isinstance(part, ReasoningText):
            continue
        to_text = getattr(part, "to_text", None)
        if callable(to_text):
            try:
                value = to_text()
            except Exception:
                value = ""
            value = value if isinstance(value, str) else str(value)
            text_chunks.append(value)
            blocks.append(
                {
                    "type": "output_text" if output_text else "input_text",
                    "text": value,
                }
            )
            continue
        text_chunks.append(str(part))
        blocks.append(
            {
                "type": "output_text" if output_text else "input_text",
                "text": str(part),
            }
        )

    content: str | list[dict[str, Any]]
    if len(blocks) == 1 and blocks[0]["type"] in ("input_text", "output_text"):
        content = str(blocks[0]["text"])
    elif blocks:
        content = blocks
    else:
        content = "".join(text_chunks)

    return {"type": "message", "role": role, "content": content}


def _payloads_to_response_input(
    payloads: list[LLMPayload],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """将内部 LLMPayload 列表转换为 Responses input items 与 function tools。

    Args:
        payloads: 待转换的 payload 列表。

    Returns:
        二元组 ``(input_items, function_tools)``：
        ``input_items`` 为 Responses API 的输入 item 列表，
        ``function_tools`` 为从 ROLE.TOOL 中提取并转换的 function 工具列表。
    """
    items: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    canonical_tool_call_ids: dict[str, str] = {}
    assistant_tool_call_group = 0

    for payload in payloads:
        if payload.role == ROLE.TOOL:
            for part in payload.content:
                tool = _tool_to_response_tool(part)
                if tool is not None:
                    tools.append(tool)
            continue

        if payload.role == ROLE.TOOL_RESULT:
            for part in payload.content:
                if isinstance(part, ToolResult):
                    call_id = part.call_id or ""
                    stable_call_id = canonical_tool_call_ids.get(call_id, call_id)
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": stable_call_id or f"call_{assistant_tool_call_group}",
                            "output": part.to_text(),
                        }
                    )
                    continue
                to_text = getattr(part, "to_text", None)
                if callable(to_text):
                    try:
                        value = to_text()
                    except Exception:
                        value = ""
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": f"call_{assistant_tool_call_group}",
                            "output": value if isinstance(value, str) else str(value),
                        }
                    )
            continue

        if payload.role == ROLE.SYSTEM:
            items.append(_message_item("system", payload.content, output_text=False))
            continue

        if payload.role == ROLE.ASSISTANT:
            text_parts: list[Text] = []
            reasoning_parts: list[ReasoningText] = []
            function_calls: list[dict[str, Any]] = []

            for part in payload.content:
                if isinstance(part, ToolCall):
                    args_text = (
                        json.dumps(part.args, ensure_ascii=False)
                        if isinstance(part.args, dict)
                        else str(part.args)
                    )
                    call_id = f"call_{assistant_tool_call_group}_{len(function_calls)}"
                    if part.id:
                        canonical_tool_call_ids[part.id] = call_id
                    function_calls.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": part.name,
                            "arguments": args_text,
                        }
                    )
                    continue
                if isinstance(part, ReasoningText):
                    reasoning_parts.append(part)
                    continue
                if isinstance(part, Text):
                    text_parts.append(part)
                    continue
                to_text = getattr(part, "to_text", None)
                if callable(to_text):
                    try:
                        value = to_text()
                    except Exception:
                        value = ""
                    text_parts.append(Text(value if isinstance(value, str) else str(value)))

            if reasoning_parts:
                items.append(
                    {
                        "type": "reasoning",
                        "id": f"rs_{assistant_tool_call_group}",
                        "content": [
                            {"type": "reasoning_text", "text": part.text}
                            for part in reasoning_parts
                        ],
                    }
                )

            message_content: list[dict[str, Any]] = [
                {"type": "output_text", "text": "".join(part.text for part in text_parts)}
            ]
            items.append(
                {"type": "message", "role": "assistant", "content": message_content}
            )

            if function_calls:
                assistant_tool_call_group += 1
                items.extend(function_calls)
            continue

        # 默认按 user 处理
        items.append(_message_item("user", payload.content, output_text=False))

    return items, tools


def _merge_tools(
    function_tools: list[dict[str, Any]],
    custom_tools: list[Any],
) -> list[dict[str, Any]]:
    """合并函数工具与自定义工具声明，按 ``(type, name)`` 去重。

    Args:
        function_tools: 由 LLMUsable 转换来的 function 工具。
        custom_tools: 配置侧传入的原始工具声明（可含内置工具）。

    Returns:
        合并去重后的 ``tools`` 列表。
    """
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def dedup_key(tool: dict[str, Any]) -> tuple[str, str]:
        tool_type = str(tool.get("type") or "function")
        name = tool.get("name")
        if name is None:
            function_obj = tool.get("function")
            if isinstance(function_obj, dict):
                name = function_obj.get("name")
        return (tool_type, str(name or ""))

    for tool in [*function_tools, *custom_tools]:
        if not isinstance(tool, dict):
            continue
        key = dedup_key(tool)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(tool))
    return merged


def _extract_custom_tools(explicit_body: Any) -> list[Any]:
    """从自定义请求体 ``extra_params.body`` 中提取 tools 声明。

    用户通过模型配置声明服务端内置工具（如 ``web_search``）时，通常写成::

        extra_params = { body = { tools = [{ type = "web_search" }] } }

    此处仅提取，不去重，真正的合并去重交给 ``_merge_tools``。

    Args:
        explicit_body: ``extra_params.body`` 的内容。

    Returns:
        原始 tools 列表；不存在或不是 list 时返回空列表。
    """
    if not isinstance(explicit_body, Mapping):
        return []
    raw_tools = explicit_body.get("tools")
    if not isinstance(raw_tools, list):
        return []
    return [tool for tool in raw_tools if isinstance(tool, dict)]


def _extract_response_output(resp: Any) -> tuple[str, list[dict[str, Any]], str | None]:
    """从 Responses 响应对象中提取文本、工具调用与 reasoning 内容。

    Args:
        resp: OpenAI ``Response`` 对象或原始 dict。

    Returns:
        三元组 ``(message, tool_calls, reasoning_content)``。
        其中 tool_calls 每个元素形如 ``{"id": .., "name": .., "args": ..}``。
    """
    output = _get_attr(resp, "output", [])
    if isinstance(output, dict):
        output = output.get("data", [])
    if not isinstance(output, list):
        output = []

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for item in output:
        item_type = _get_attr(item, "type")
        if item_type == "message":
            for block in _get_attr(item, "content", []):
                block_text = _get_attr(block, "text", None)
                if isinstance(block_text, str):
                    text_parts.append(block_text)
            continue
        if item_type == "reasoning":
            for block in _get_attr(item, "content", []):
                block_text = _get_attr(block, "text", None)
                if isinstance(block_text, str):
                    reasoning_parts.append(block_text)
            continue
        if item_type == "function_call":
            tool_calls.append(
                {
                    "id": _get_attr(item, "call_id", None),
                    "name": str(_get_attr(item, "name", "")),
                    "args": _parse_tool_arguments(_get_attr(item, "arguments", {})),
                }
            )
            continue
        # 服务端执行的内置工具（web_search_call / file_search_call /
        # code_interpreter_call / custom_tool_call 等）不在本地执行，
        # 不进入 tool_calls，避免框架尝试本地 execute。

    reasoning_content = "".join(reasoning_parts) or None
    return "".join(text_parts), tool_calls, reasoning_content


def _responses_usage_to_dict(usage_obj: Any) -> dict[str, Any]:
    """把 Responses usage 对象转换为普通 dict。"""
    if isinstance(usage_obj, dict):
        return dict(usage_obj)

    model_dump = getattr(usage_obj, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass

    def coerce(value: Any) -> Any:
        if isinstance(value, dict):
            return value
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            try:
                return dump()
            except Exception:
                return None
        return value

    out: dict[str, Any] = {}
    for field in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_tokens_details",
        "output_tokens_details",
    ):
        value = getattr(usage_obj, field, None)
        if value is not None:
            out[field] = coerce(value)
    return out


def _extract_responses_usage(resp: Any) -> dict[str, Any]:
    """从 Responses 响应中提取标准化 token 统计。

    Responses 的 usage 使用 ``input_tokens`` / ``output_tokens`` 命名，
    这里先映射为 ``prompt_tokens`` / ``completion_tokens`` 再复用统一的
    ``_extract_usage_from_obj``，与 Chat Completions 客户端产出保持一致。

    Args:
        resp: OpenAI ``Response`` 对象或原始 dict。

    Returns:
        包含 token 统计的字典；无 usage 时返回空 dict。
    """
    usage_obj = _get_usage_field(resp, "usage", None)
    if usage_obj is None:
        return {}

    mapped = _responses_usage_to_dict(usage_obj)
    if "prompt_tokens" not in mapped and "input_tokens" in mapped:
        mapped["prompt_tokens"] = mapped.get("input_tokens", 0)
    if "completion_tokens" not in mapped and "output_tokens" in mapped:
        mapped["completion_tokens"] = mapped.get("output_tokens", 0)
    return _extract_usage_from_obj(mapped)


class OpenAIResponsesClient(OpenAIChatClient):
    """OpenAI Responses API 聊天客户端。

    依赖 openai>=2.x，纯异步实现。继承 ``OpenAIChatClient`` 复用其传输层
    客户端缓存、超时构建与请求观测逻辑，仅覆盖 ``create`` 及其解析路径。

    配置说明：

    - 传输层与 HTTP 层特殊键（``headers`` / ``query`` / ``body``）的处理与
      ``OpenAIChatClient`` 完全一致。
    - 服务端内置工具（如 ``web_search``）通过 ``extra_params.body.tools``
      声明；客户端会与函数工具合并去重后一起发送。
    - 其余 ``extra_params`` 中属于 Responses 标准参数的键直接透传；
      非标准参数自动并入 ``extra_body``（供应商会静默忽略不支持字段）。
    """

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
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None, str | None, dict[str, Any] | None]:
        """发起一次 Responses API 请求。

        Args:
            model_name: 模型名称（如 ``deepseek-v4-flash``）。
            payloads: 消息负载列表。
            tools: 工具定义列表（保持协议兼容，实际通过 payloads 中 ROLE.TOOL 传入）。
            request_name: 请求名称，用于追踪。
            model_set: 单个模型配置 dict。
            stream: 是否开启流式输出。

        Returns:
            五元组 ``(message, tool_calls, stream_iter, reasoning_content, usage)``：
            - 非流时：``(完整文本, 工具调用列表, None, 推理内容, usage)``
            - 流式时：``(None, None, AsyncIterator[StreamEvent], None, None)``

        Raises:
            TypeError: model_set 不是 dict 时抛出。
            ValueError: api_key 为空或 extra_params 非 dict 时抛出。
            LLMConfigurationError: 请求缺少 input 且缺少 instructions 时抛出。
            LLMContentFilterError: 模型返回空 output 时抛出。
        """
        del tools

        if not isinstance(model_set, dict):
            raise TypeError("OpenAIResponsesClient 期望 model_set 为单个模型配置 dict")

        (
            api_key,
            base_url,
            timeout,
            trust_env,
            force_ipv4,
            extra_params,
            extra_headers,
            extra_query,
            explicit_body,
        ) = self._extract_model_params(model_set)

        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )

        items, function_tools = _payloads_to_response_input(payloads)
        custom_tools = _extract_custom_tools(explicit_body)

        # 兼容 Chat Completions 配置：extra_params.tools 也可声明额外工具
        extra_tools = extra_params.pop("tools", None)
        if isinstance(extra_tools, list):
            custom_tools = [*custom_tools, *extra_tools]

        merged_tools = _merge_tools(function_tools, custom_tools)

        # tool_call_compat：把函数工具以提示词形式追加到 input 末尾
        tool_call_compat = bool(model_set.get("tool_call_compat", False))
        if tool_call_compat and merged_tools:
            compat_prompt = build_tool_call_compat_prompt(merged_tools)
            items.append(
                {"type": "message", "role": "user", "content": compat_prompt}
            )

        params: dict[str, Any] = {
            "model": model_name,
        }
        if items:
            params["input"] = items

        max_output_tokens = model_set.get("max_tokens")
        if isinstance(max_output_tokens, int):
            params["max_output_tokens"] = max_output_tokens
        temperature = model_set.get("temperature")
        if isinstance(temperature, (int, float)):
            params["temperature"] = float(temperature)
        if merged_tools:
            params["tools"] = merged_tools

        # 标准 Responses 参数直接透传，非标准参数并入 extra_body
        standard_extra: dict[str, Any] = {}
        non_standard_body: dict[str, Any] = {}
        for key, value in extra_params.items():
            if key in _RESPONSES_STANDARD_PARAMS:
                standard_extra[key] = value
            else:
                non_standard_body[key] = value
        params.update(standard_extra)

        # 自定义请求体优先级最高：body 覆盖 extra_params 中的同名标准参数
        body_overrides: dict[str, Any] = {}
        if isinstance(explicit_body, Mapping):
            for key, value in explicit_body.items():
                if key == "tools":
                    continue  # 已在合并分支处理
                body_overrides[key] = value
        params.update(body_overrides)

        # 非标准参数与 body 中无法落到 params 的字段统一进入 extra_body
        merged_body = dict(non_standard_body)
        for key in list(body_overrides.keys()):
            if key not in _RESPONSES_STANDARD_PARAMS:
                merged_body[key] = body_overrides[key]

        if merged_body:
            params["extra_body"] = merged_body

        if extra_headers:
            params["extra_headers"] = extra_headers
        if extra_query:
            params["extra_query"] = extra_query

        # 至少需要 input 或 instructions 之一
        extra_body = params.get("extra_body")
        has_input = bool(params.get("input")) or bool(
            isinstance(extra_body, dict) and extra_body.get("input")
        )
        has_instructions = bool(params.get("instructions")) or bool(
            isinstance(extra_body, dict) and extra_body.get("instructions")
        )
        if not has_input and not has_instructions:
            raise LLMConfigurationError(
                "Responses API 请求必须提供 input 或 instructions（当前两者均为空）"
            )

        log_provider_request_body(
            "responses.create",
            params,
            model_set=model_set,
            payloads=payloads,
            request_name=request_name,
            token_counter=count_payload_tokens,
        )

        if stream:
            return await self._create_responses_stream(client=client, params=params)
        return await self._create_responses_non_stream(
            client=client,
            params=params,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
            model_name=model_name,
        )

    async def _create_responses_non_stream(
        self,
        *,
        client: Any,
        params: dict[str, Any],
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
        model_name: str,
    ) -> tuple[str | None, list[dict[str, Any]] | None, None, str | None, dict[str, Any] | None]:
        """执行非流式 Responses 请求并返回解析结果。

        遇到网络/超时异常时会驱逐缓存的客户端，以便下次请求重建连接。

        Args:
            client: AsyncOpenAI 实例。
            params: 请求参数 dict。
            api_key: API 密钥（用于驱逐缓存）。
            base_url: base URL（用于驱逐缓存）。
            timeout: 超时（用于驱逐缓存）。
            trust_env: 代理环境变量开关（用于驱逐缓存）。
            force_ipv4: IPv4 强制标志（用于驱逐缓存）。
            model_name: 模型名称（用于错误信息）。

        Returns:
            四元组 ``(message_content, tool_calls, None, reasoning_content)``。

        Raises:
            LLMContentFilterError: 模型返回空 output 时抛出。
        """
        try:
            resp = await client.responses.create(**params)
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

        output = _get_attr(resp, "output", [])
        if isinstance(output, dict):
            output = output.get("data", [])
        if not isinstance(output, list) or not output:
            raise LLMContentFilterError(
                f"模型返回空响应（可能触发了安全过滤器）。Response: {resp}",
                filter_type="empty_output",
                model=model_name,
            )

        message, tool_calls, reasoning_content = _extract_response_output(resp)
        usage = _extract_responses_usage(resp)
        return message, tool_calls, None, reasoning_content, usage

    async def _create_responses_stream(
        self,
        *,
        client: Any,
        params: dict[str, Any],
    ) -> tuple[None, None, AsyncIterator[StreamEvent], None, dict[str, Any] | None]:
        """执行流式 Responses 请求并返回事件迭代器。

        Args:
            client: AsyncOpenAI 实例。
            params: 请求参数 dict（不含 ``stream`` 键）。

        Returns:
            五元组 ``(None, None, AsyncIterator[StreamEvent], None, None)``。
        """
        stream_params = dict(params)
        stream_params["stream"] = True
        stream_resp = await client.responses.create(**stream_params)

        async def iter_events() -> AsyncIterator[StreamEvent]:
            """逐事件迭代流式响应，产出 StreamEvent。

            DeepSeek Responses 流以 ``response.completed`` / ``response.incomplete``
            / ``response.failed`` 作结，没有 ``data: [DONE]``。
            """
            function_call_meta: dict[str, tuple[str | None, str | None]] = {}

            try:
                async for event in stream_resp:
                    event_type = _get_attr(event, "type")

                    if event_type in ("response.completed", "response.incomplete"):
                        resp_body = _get_attr(event, "response")
                        usage = _extract_responses_usage(resp_body)
                        if usage:
                            yield StreamEvent(usage=usage)
                        yield StreamEvent(
                            stop_reason=str(_get_attr(resp_body, "status") or event_type)
                        )
                        continue

                    if event_type == "response.failed":
                        resp_body = _get_attr(event, "response")
                        usage = _extract_responses_usage(resp_body)
                        if usage:
                            yield StreamEvent(usage=usage)
                        error = _get_attr(resp_body, "error", {}) or {}
                        message = _get_attr(error, "message", "Responses 流式响应失败")
                        raise RuntimeError(str(message))

                    if event_type == "response.error":
                        raise RuntimeError(
                            str(_get_attr(event, "message", "Responses 流式响应错误"))
                        )

                    if event_type == "response.output_item.added":
                        item = _get_attr(event, "item")
                        if _get_attr(item, "type") == "function_call":
                            call_id = _get_attr(item, "call_id")
                            name = _get_attr(item, "name")
                            item_id = _get_attr(item, "id")
                            if item_id:
                                function_call_meta[item_id] = (call_id, name)
                            yield StreamEvent(
                                tool_call_id=call_id or item_id,
                                tool_name=name,
                            )
                        continue

                    if event_type == "response.function_call_arguments.delta":
                        item_id = _get_attr(event, "item_id")
                        call_id, name = function_call_meta.get(item_id, (item_id, None))
                        yield StreamEvent(
                            tool_call_id=call_id,
                            tool_name=name,
                            tool_args_delta=str(_get_attr(event, "delta", "")),
                        )
                        continue

                    if event_type == "response.reasoning_text.delta":
                        delta = _get_attr(event, "delta", "")
                        if delta:
                            yield StreamEvent(reasoning_delta=str(delta))
                        continue

                    if event_type == "response.output_text.delta":
                        delta = _get_attr(event, "delta", "")
                        if delta:
                            yield StreamEvent(text_delta=str(delta))
                        continue

                    # 其余事件（web_search_call / file_search_call 等服务端执行
                    # 工具的状态更新）不产出 StreamEvent，静默跳过。
            finally:
                close = getattr(stream_resp, "aclose", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result

                close_sync = getattr(stream_resp, "close", None)
                if callable(close_sync):
                    result = close_sync()
                    if inspect.isawaitable(result):
                        await result

        return None, None, iter_events(), None, None


__all__ = [
    "OpenAIResponsesClient",
    "_payloads_to_response_input",
    "_extract_response_output",
    "_extract_responses_usage",
    "_merge_tools",
]