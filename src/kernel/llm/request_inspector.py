"""LLM 原生调用生命周期检视器。

本模块以线程安全的内存缓冲保存脱敏请求、标准化响应和 SDK 原始 JSON。
原生适配器直接报告生命周期，不包装 SDK、不提供 HTTP 端点。

**本模块是纯进程内工具，不对外提供任何 HTTP 端点。** 可视化调试请使用官方 WebUI：
https://github.com/MoFox-Studio/MoFox-Core-Webui

使用方式：
    # 读取：供 WebUI / 调试工具消费
    from src.kernel.llm import get_inspector
    inspector = get_inspector()
    inspector.list_summaries()      # 摘要列表
    inspector.get_record(record_id) # 单条完整请求体

    # 在发起请求前调用（内部由 model_client 自动调用）
    from src.kernel.llm.request_inspector import capture
    capture("chat.completions.create", params)
"""

from __future__ import annotations

import copy
import json
import time
import math
import re
import threading
import uuid
from collections import Counter
from dataclasses import field, fields, is_dataclass
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import dataclass
from typing import Any


_OVERVIEW_FIELDS: list[tuple[str, str]] = [
    ("stream", "流式"),
    ("temperature", "temperature"),
    ("max_tokens", "max_tokens"),
    ("top_p", "top_p"),
    ("presence_penalty", "presence_penalty"),
    ("frequency_penalty", "frequency_penalty"),
    ("seed", "seed"),
    ("tool_choice", "tool_choice"),
    ("parallel_tool_calls", "parallel_tool_calls"),
    ("response_format", "response_format"),
]

_ROLE_LABELS: dict[str, str] = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool",
    "tool_result": "Tool Result",
}


def _json_dumps(value: Any) -> str:
    """将值安全序列化为 JSON 字符串。"""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError, RecursionError):
        return "[unavailable]"


def _format_scalar(value: Any) -> str:
    """将任意标量格式化为便于展示的字符串。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return _json_dumps(value)
    return str(value)


def _build_overview(
    api_name: str,
    model: str,
    params: dict[str, Any],
    metadata: dict[str, Any],
) -> list[dict[str, str]]:
    """构造顶层请求摘要。"""
    messages = _get_inspector_messages(params)
    tools = params.get("tools", [])
    overview = [
        {"label": "API", "value": api_name},
        {"label": "提供商", "value": _format_scalar(metadata.get("api_provider", "-"))},
        {"label": "模型", "value": model or "-"},
        {
            "label": "消息数",
            "value": str(len(messages) if isinstance(messages, list) else 0),
        },
        {"label": "工具数", "value": str(len(tools) if isinstance(tools, list) else 0)},
    ]

    estimated_input_tokens = metadata.get("estimated_input_tokens")
    if estimated_input_tokens is not None:
        overview.append(
            {
                "label": "预估输入 Tokens",
                "value": _format_scalar(estimated_input_tokens),
            }
        )

    request_name = metadata.get("request_name")
    if request_name:
        overview.append({"label": "请求名称", "value": _format_scalar(request_name)})

    for key, label in _OVERVIEW_FIELDS:
        if key in params:
            overview.append({"label": label, "value": _format_scalar(params[key])})

    reserved_keys = {"messages", "tools", "model", "system"}
    reserved_keys.update(name for name, _ in _OVERVIEW_FIELDS)
    for key in sorted(key for key in params.keys() if key not in reserved_keys):
        overview.append({"label": key, "value": _format_scalar(params[key])})

    return overview


def _schema_type_text(schema: dict[str, Any]) -> str:
    """读取 JSON Schema 的类型信息。"""
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    if isinstance(schema_type, list):
        return " | ".join(str(item) for item in schema_type)
    if "anyOf" in schema:
        return "anyOf"
    if "oneOf" in schema:
        return "oneOf"
    if "allOf" in schema:
        return "allOf"
    return "unknown"


def _build_tools_view(params: dict[str, Any]) -> list[dict[str, Any]]:
    """将 tools schema 转为更适合前端展示的结构。"""
    tools = params.get("tools", [])
    if not isinstance(tools, list):
        return []

    rendered_tools: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            rendered_tools.append(
                {
                    "index": index,
                    "name": f"tool_{index}",
                    "kind": "unknown",
                    "description": "",
                    "required": [],
                    "properties": [],
                    "raw_json": _json_dumps(tool),
                }
            )
            continue

        function_obj = tool.get("function")
        if isinstance(function_obj, dict):
            function_schema: dict[str, Any] = function_obj
        else:
            function_schema = tool
        parameters_obj = function_schema.get("parameters")
        if not isinstance(parameters_obj, dict):
            parameters_obj = function_schema.get("input_schema")
        parameters: dict[str, Any] = (
            parameters_obj if isinstance(parameters_obj, dict) else {}
        )
        properties_obj = parameters.get("properties")
        properties: dict[str, Any] = (
            properties_obj if isinstance(properties_obj, dict) else {}
        )
        required_obj = parameters.get("required")
        required: list[Any] = required_obj if isinstance(required_obj, list) else []
        rendered_tools.append(
            {
                "index": index,
                "name": str(function_schema.get("name", f"tool_{index}")),
                "kind": str(tool.get("type", "function")),
                "description": str(function_schema.get("description", "")),
                "required": [str(item) for item in required],
                "properties": [
                    {
                        "name": str(prop_name),
                        "type": (
                            _schema_type_text(prop_schema)
                            if isinstance(prop_schema, dict)
                            else "unknown"
                        ),
                        "description": (
                            str(prop_schema.get("description", ""))
                            if isinstance(prop_schema, dict)
                            else ""
                        ),
                        "required": prop_name in required,
                    }
                    for prop_name, prop_schema in properties.items()
                ],
                "raw_json": _json_dumps(tool),
            }
        )
    return rendered_tools


def _make_block(block_type: str, **kwargs: Any) -> dict[str, Any]:
    """构造统一的消息块结构。"""
    block = {"type": block_type}
    block.update(kwargs)
    return block


def _render_unknown_content(content: Any, label: str = "未知内容") -> dict[str, Any]:
    """为未知结构构造回退块。"""
    return _make_block("unknown", label=label, text=_json_dumps(content))


def _render_content_list(content: list[Any]) -> list[dict[str, Any]]:
    """将 OpenAI content 数组转换为消息块。"""
    blocks: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            blocks.append(_make_block("markdown", text=item))
            continue

        if not isinstance(item, dict):
            blocks.append(_render_unknown_content(item))
            continue

        item_type = str(item.get("type", "unknown"))
        if item_type in {"text", "input_text", "output_text"}:
            blocks.append(_make_block("markdown", text=str(item.get("text", ""))))
            continue

        if item_type in {"image_url", "input_image"}:
            image_url_obj = item.get("image_url")
            image_url: dict[str, Any] = (
                image_url_obj if isinstance(image_url_obj, dict) else {}
            )
            url = image_url.get("url") or item.get("url") or ""
            blocks.append(
                _make_block(
                    "media",
                    media_type="image",
                    title="图片内容",
                    text="请求中包含图片输入。",
                    meta=_format_scalar(url)[:120],
                )
            )
            continue

        if item_type == "image":
            source_obj = item.get("source")
            source = source_obj if isinstance(source_obj, dict) else {}
            meta = source.get("media_type") or source.get("type") or "image"
            blocks.append(
                _make_block(
                    "media",
                    media_type="image",
                    title="图片内容",
                    text="请求中包含图片输入。",
                    meta=_format_scalar(meta),
                )
            )
            continue

        if item_type in {"audio", "input_audio", "output_audio"}:
            blocks.append(
                _make_block(
                    "media",
                    media_type="audio",
                    title="音频内容",
                    text="请求中包含音频输入。",
                    meta="音频数据已省略",
                )
            )
            continue

        if item_type == "refusal":
            blocks.append(
                _make_block(
                    "markdown", text=str(item.get("refusal", "")), label="拒绝说明"
                )
            )
            continue

        if item_type == "thinking":
            blocks.append(
                _make_block(
                    "markdown",
                    text=str(item.get("thinking", "")),
                    label="Reasoning",
                )
            )
            continue

        if item_type == "tool_use":
            blocks.append(
                _make_block(
                    "tool_call",
                    call_id=str(item.get("id", "") or ""),
                    name=str(item.get("name", "unknown_tool")),
                    arguments_text=_json_dumps(item.get("input", {})),
                )
            )
            continue

        if item_type == "tool_result":
            blocks.append(
                _make_block(
                    "tool_result",
                    name=str(item.get("tool_name", "")),
                    call_id=str(item.get("tool_use_id", "") or ""),
                    text=_format_scalar(item.get("content", "")),
                )
            )
            continue

        blocks.append(
            _render_unknown_content(item, label=f"未知 content 类型: {item_type}")
        )
    return blocks


def _get_inspector_messages(params: dict[str, Any]) -> list[Any]:
    """获取用于检视器展示的消息列表，兼容 Anthropic 的 system 字段。"""
    rendered_messages: list[Any] = []

    system = params.get("system")
    if isinstance(system, str):
        rendered_messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        rendered_messages.append({"role": "system", "content": system})

    instructions = params.get("instructions")
    if isinstance(instructions, str):
        rendered_messages.append({"role": "system", "content": instructions})
    messages = params.get("messages", params.get("input", []))
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, str):
                rendered_messages.append({"role": "user", "content": message})
            elif (
                isinstance(message, dict)
                and message.get("type") == "function_call_output"
            ):
                rendered_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.get("call_id"),
                        "content": message.get("output"),
                    }
                )
            elif isinstance(message, dict) and message.get("type") == "function_call":
                rendered_messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": message.get("call_id"),
                                "name": message.get("name"),
                                "args": message.get("arguments"),
                            }
                        ],
                    }
                )
            else:
                rendered_messages.append(message)
    if "query" in params:
        rendered_messages.append({"role": "user", "content": params["query"]})
    if isinstance(params.get("documents"), list):
        for document in params["documents"]:
            rendered_messages.append({"role": "user", "content": document})

    return rendered_messages


def _build_message_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    """将单条 message 转换为前端可渲染的块列表。"""
    blocks: list[dict[str, Any]] = []
    role = str(message.get("role", "unknown"))
    content = message.get("content")

    if role == "tool":
        tool_name = str(message.get("name", ""))
        blocks.append(
            _make_block(
                "tool_result",
                name=tool_name,
                call_id=str(message.get("tool_call_id", "") or ""),
                text=_format_scalar(content if content is not None else ""),
            )
        )
    elif isinstance(content, str):
        blocks.append(_make_block("markdown", text=content))
    elif isinstance(content, list):
        blocks.extend(_render_content_list(content))
    elif content is None:
        blocks.append(_make_block("empty", text="此消息没有文本内容。"))
    else:
        blocks.append(_render_unknown_content(content))

    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        blocks.append(
            _make_block("markdown", text=reasoning_content, label="Reasoning")
        )

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                blocks.append(_render_unknown_content(tool_call, label="未知工具调用"))
                continue
            function_block_obj = tool_call.get("function")
            function_block: dict[str, Any] = (
                function_block_obj if isinstance(function_block_obj, dict) else {}
            )
            name = str(
                function_block.get("name", tool_call.get("name", "unknown_tool"))
            )
            arguments = function_block.get("arguments", tool_call.get("args", {}))
            arguments_text = (
                arguments if isinstance(arguments, str) else _json_dumps(arguments)
            )
            blocks.append(
                _make_block(
                    "tool_call",
                    call_id=str(tool_call.get("id", "") or ""),
                    name=name,
                    arguments_text=arguments_text,
                )
            )

    if not blocks:
        blocks.append(_make_block("empty", text="此消息没有可展示内容。"))

    return blocks


def _build_messages_view(params: dict[str, Any]) -> list[dict[str, Any]]:
    """将请求中的 messages 转换为卡片化展示模型。"""
    messages = _get_inspector_messages(params)
    if not messages:
        return []

    rendered_messages: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            rendered_messages.append(
                {
                    "index": index,
                    "role": "unknown",
                    "label": "Unknown",
                    "meta": "非标准 message 结构",
                    "blocks": [_render_unknown_content(message)],
                }
            )
            continue

        role = str(message.get("role", "unknown"))
        meta_parts: list[str] = []
        if isinstance(message.get("name"), str) and message["name"]:
            meta_parts.append(f"name: {message['name']}")
        if isinstance(message.get("tool_call_id"), str) and message["tool_call_id"]:
            meta_parts.append(f"tool_call_id: {message['tool_call_id']}")
        if isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
            meta_parts.append(f"tools: {len(message['tool_calls'])}")

        rendered_messages.append(
            {
                "index": index,
                "role": role,
                "label": _ROLE_LABELS.get(role, role.title()),
                "meta": " · ".join(meta_parts),
                "blocks": _build_message_blocks(message),
            }
        )

    return rendered_messages


def build_render_view(
    api_name: str,
    model: str,
    params: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造详情页使用的结构化渲染模型。"""
    effective_metadata = metadata or {}
    return {
        "overview": _build_overview(api_name, model, params, effective_metadata),
        "tools": _build_tools_view(params),
        "messages": _build_messages_view(params),
    }


_TERMINAL = frozenset({"captured", "succeeded", "failed", "cancelled"})
_REDACTED = "[REDACTED]"
_UNAVAILABLE = "[unavailable]"
_SENSITIVE = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "apikey",
        "xapikey",
        "key",
        "token",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "password",
        "passwd",
        "secret",
        "clientsecret",
        "cookie",
        "setcookie",
        "credential",
        "credentials",
        "xamzsignature",
        "xgoogsignature",
        "xauthtoken",
        "subscriptionkey",
        "ocpapimsubscriptionkey",
    }
)
_URL = re.compile(r"https?://[^\s\"'<>]+")
_CREDENTIAL = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+|basic\s+)?[^\s,;\"']+"
    r"|(\b(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+"
)


def _sensitive(key: str) -> bool:
    """Recognize credential keys without masking token usage counters."""
    return re.sub(r"[^a-z0-9]", "", key.lower()) in _SENSITIVE


def _json_size(value: Any) -> int:
    """Measure exactly the compact UTF-8 JSON representation exposed by snapshots."""
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


class _Sanitizer:
    """Credential-aware JSON conversion; unknown objects never use repr/str."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        self.secrets = {value for value in secrets if isinstance(value, str) and value}
        self.redacted = False
        self.unavailable = False
        self.truncated = False

    def learn(self, value: Any, seen: set[int] | None = None, depth: int = 0) -> None:
        """Discover credential values before converting sibling text fields."""
        seen = seen if seen is not None else set()
        if id(value) in seen or depth > 64:
            return
        if isinstance(value, (dict, list, tuple)):
            seen.add(id(value))
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and _sensitive(key):
                    candidates = item if isinstance(item, (list, tuple)) else [item]
                    for candidate in candidates:
                        if isinstance(candidate, str) and candidate:
                            self.secrets.add(candidate)
                            if candidate.lower().startswith(("bearer ", "basic ")):
                                self.secrets.add(candidate.split(" ", 1)[1])
                self.learn(item, seen, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.learn(item, seen, depth + 1)

    def text(self, value: str) -> str:
        """Remove known credentials and URL credentials from ordinary text."""
        original = value
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, _REDACTED)

        def clean_url(match: re.Match[str]) -> str:
            """Keep non-sensitive URL parameters while removing authentication."""
            try:
                url = urlsplit(match.group())
                netloc = url.netloc.rsplit("@", 1)[-1]
                pairs = parse_qsl(url.query, keep_blank_values=True)
                query = [(k, _REDACTED if _sensitive(k) else v) for k, v in pairs]
                if netloc == url.netloc and query == pairs:
                    return match.group()
                return urlunsplit(
                    (url.scheme, netloc, url.path, urlencode(query), url.fragment)
                )
            except ValueError:
                return "[invalid URL]"

        value = _URL.sub(clean_url, value)
        value = _CREDENTIAL.sub(lambda m: (m.group(1) or m.group(2)) + _REDACTED, value)
        self.redacted |= value != original
        return value

    def convert(self, value: Any, limit: int | None = None) -> Any:
        """Convert to independent JSON, optionally with an exact bounded output."""
        self.learn(value)
        seen: set[int] = set()

        def visit(item: Any, budget: int | None, depth: int) -> Any:
            """Traverse only retained children, reserving JSON container overhead."""
            if depth > 64 or id(item) in seen:
                self.unavailable = True
                item = _UNAVAILABLE
            if item is None or isinstance(item, (bool, int, float, str)):
                if isinstance(item, float) and not math.isfinite(item):
                    self.unavailable = True
                    item = None
                if isinstance(item, str):
                    # Avoid an unbounded JSON encoding of one giant SDK text delta.
                    if budget is not None and len(item) > budget:
                        item = item[
                            : budget + max((len(v) for v in self.secrets), default=0)
                        ]
                        self.truncated = True
                    item = self.text(item)
                if budget is None or _json_size(item) <= budget:
                    return item
                self.truncated = True
                if not isinstance(item, str):
                    return None
                lo, hi = 0, len(item)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if _json_size(item[:mid]) <= budget:
                        lo = mid
                    else:
                        hi = mid - 1
                return item[:lo]
            seen.add(id(item))
            original_id = id(item)
            try:
                if not isinstance(item, (dict, list, tuple)):
                    if is_dataclass(item) and not isinstance(item, type):
                        item = {f.name: getattr(item, f.name) for f in fields(item)}
                    elif isinstance(item, SimpleNamespace):
                        item = vars(item)
                    elif callable(getattr(item, "model_dump", None)):
                        item = item.model_dump(mode="json")
                    else:
                        self.unavailable = True
                        return visit(_UNAVAILABLE, budget, depth + 1)
                    self.learn(item)
                    return visit(item, budget, depth + 1)
                out: Any = {} if isinstance(item, dict) else []
                used = 2
                entries = item.items() if isinstance(item, dict) else enumerate(item)
                for key, child in entries:
                    if isinstance(out, dict) and not isinstance(key, str):
                        self.unavailable = True
                        continue
                    safe_key = self.text(key) if isinstance(out, dict) else key
                    overhead = (1 if out else 0) + (
                        _json_size(safe_key) + 1 if isinstance(out, dict) else 0
                    )
                    remaining = None if budget is None else budget - used - overhead
                    if remaining is not None and remaining < 4:
                        self.truncated = True
                        break
                    if isinstance(key, str) and _sensitive(key):
                        self.redacted = True
                        child = _REDACTED
                    result = visit(child, remaining, depth + 1)
                    size = _json_size(result)
                    if budget is not None and used + overhead + size > budget:
                        self.truncated = True
                        break
                    if isinstance(out, dict):
                        out[safe_key] = result
                    else:
                        out.append(result)
                    used += overhead + size
                    if self.truncated:
                        break
                return out
            except Exception:
                self.unavailable = True
                return visit(_UNAVAILABLE, budget, depth + 1)
            finally:
                seen.discard(original_id)

        return visit(value, limit, 0)


def _empty_response() -> dict[str, Any]:
    """Return all fields of the public normalized response contract."""
    return {
        "kind": None,
        "message": None,
        "reasoning_content": None,
        "reasoning_parts": [],
        "tool_calls": [],
        "usage": None,
        "stop_reason": None,
        "embeddings": [],
        "results": [],
    }


@dataclass
class CapturedRequest:
    """One bounded-buffer record; public conversion never exposes private fields."""

    id: int
    ts: float
    api_name: str
    model: str
    params: dict[str, Any]
    metadata: dict[str, Any]
    version: int = 1
    status: str = "captured"
    logical_request_id: str = ""
    attempt: int = 1
    stream: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: float | None = None
    first_token_ms: float | None = None
    response: dict[str, Any] = field(default_factory=_empty_response)
    raw_response: Any = None
    raw_events: list[Any] = field(default_factory=list)
    error: dict[str, Any] | None = None
    capture_info: dict[str, Any] = field(
        default_factory=lambda: {
            "redacted": False,
            "raw_bytes": 0,
            "raw_limit_bytes": 2 * 1024 * 1024,
            "raw_truncated": False,
            "raw_unavailable": False,
            "request_unavailable": False,
            "metadata_unavailable": False,
        }
    )
    _clock: float | None = field(default=None, repr=False)
    _raw_response_bytes: int = field(default=0, repr=False)
    _secrets: set[str] = field(default_factory=set, repr=False)

    def to_summary(self) -> dict[str, Any]:
        """Return metadata only, without request/response bodies or credential state."""
        usage = self.response.get("usage") or {}
        return {
            "id": self.id,
            "version": self.version,
            "ts": self.ts,
            "ts_str": time.strftime("%H:%M:%S", time.localtime(self.ts)),
            "api_name": self.api_name,
            "model": self.model,
            "api_provider": str(self.metadata.get("api_provider", "-")),
            "request_name": str(self.metadata.get("request_name", "")),
            "estimated_input_tokens": copy.deepcopy(
                self.metadata.get("estimated_input_tokens")
            ),
            "msg_count": len(_get_inspector_messages(self.params)),
            "tool_count": len(self.params.get("tools", []))
            if isinstance(self.params.get("tools"), list)
            else 0,
            "status": self.status,
            "logical_request_id": self.logical_request_id,
            "attempt": self.attempt,
            "stream": self.stream,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "first_token_ms": self.first_token_ms,
            "tokens_in": usage.get("prompt_tokens", usage.get("input_tokens")),
            "tokens_out": usage.get("completion_tokens", usage.get("output_tokens")),
        }

    def to_full(self) -> dict[str, Any]:
        """Build the detached detail, deriving all views from the sanitized snapshot."""
        return copy.deepcopy(
            {
                **self.to_summary(),
                "params": self.params,
                "metadata": self.metadata,
                "rendered": build_render_view(
                    self.api_name, self.model, self.params, self.metadata
                ),
                "response": self.response,
                "raw_response": self.raw_response,
                "raw_events": self.raw_events,
                "error": self.error,
                "capture_info": self.capture_info,
            }
        )


class RequestInspector:
    """Thread-safe, in-memory lifecycle recorder; never owns model execution."""

    def __init__(
        self, max_records: int = 200, *, raw_limit_bytes: int = 2 * 1024 * 1024
    ) -> None:
        if max_records < 1 or raw_limit_bytes < 4:
            raise ValueError("max_records must be positive and raw_limit_bytes >= 4")
        self._max_records = max_records
        self._raw_limit = raw_limit_bytes
        self._records: dict[int, CapturedRequest] = {}
        self._counter = 0
        self._revision = 0
        self._instance_id = uuid.uuid4().hex
        self._lock = threading.RLock()

    def _touch(self, record: CapturedRequest) -> None:
        """Advance buffer and record versions while holding the lock."""
        record.version += 1
        self._revision += 1

    def _safe(
        self, record: CapturedRequest, value: Any, unavailable: str | None = None
    ) -> Any:
        """Sanitize data before retaining it and remember completeness flags."""
        converter = _Sanitizer(tuple(record._secrets))
        result = converter.convert(value)
        record._secrets.update(converter.secrets)
        record.capture_info["redacted"] |= converter.redacted
        if unavailable:
            record.capture_info[unavailable] |= converter.unavailable
        return result

    def capture(
        self,
        api_name: str,
        params: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        *,
        native: bool = False,
        logical_request_id: str | None = None,
        attempt: int = 1,
        stream: bool = False,
        secrets: tuple[str, ...] = (),
    ) -> int:
        """Create a capture or native pending record and return its monotonic ID."""
        with self._lock:
            self._counter += 1
            record = CapturedRequest(self._counter, time.time(), "", "", {}, {})
            record._secrets.update(secrets)
            learner = _Sanitizer(tuple(secrets))
            learner.learn(params)
            learner.learn(metadata)
            record._secrets.update(learner.secrets)
            record.api_name = self._safe(record, api_name)
            record.params = self._safe(record, params, "request_unavailable")
            record.metadata = self._safe(record, metadata or {}, "metadata_unavailable")
            if not isinstance(record.params, dict):
                record.params = {"_unavailable": True}
                record.capture_info["request_unavailable"] = True
            if not isinstance(record.metadata, dict):
                record.metadata = {"_unavailable": True}
                record.capture_info["metadata_unavailable"] = True
            record.model = str(record.params.get("model", "—"))
            record.status = "pending" if native else "captured"
            record.logical_request_id = logical_request_id or uuid.uuid4().hex
            record.attempt = attempt
            record.stream = stream or record.params.get("stream") is True
            record.capture_info["raw_limit_bytes"] = self._raw_limit
            self._records[record.id] = record
            if len(self._records) > self._max_records:
                del self._records[next(iter(self._records))]
            self._revision += 1
            return record.id

    def start(
        self,
        record_id: int,
        params: dict[str, Any] | None = None,
        *,
        api_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        """Record the final SDK parameters immediately before the provider call."""
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            record._secrets.update(secrets)
            if params is not None:
                record.params = self._safe(record, params, "request_unavailable")
                record.model = str(record.params.get("model", record.model))
                record.stream = record.params.get("stream") is True or record.stream
            if api_name is not None:
                record.api_name = self._safe(record, api_name)
            if metadata:
                record.metadata.update(
                    self._safe(record, metadata, "metadata_unavailable")
                )
            if record.started_at is None:
                record.started_at = time.time()
                record._clock = time.perf_counter()
            record.status = "running"
            self._touch(record)

    def update(
        self, record_id: int, response: dict[str, Any], *, content: bool = False
    ) -> None:
        """Publish a normalized partial result independently of raw-data limits."""
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            record.response.update(self._safe(record, response))
            if record.stream and (content or record.raw_events):
                record.status = "streaming"
            if content and record.first_token_ms is None and record._clock is not None:
                record.first_token_ms = (time.perf_counter() - record._clock) * 1000
            self._touch(record)

    def raw(self, record_id: int, value: Any, *, event: bool = False) -> None:
        """Retain SDK JSON up to the shared response/events budget, never a repr."""
        with self._lock:
            record = self._records.get(record_id)
            if (
                record is None
                or record.status in _TERMINAL
                or record.capture_info["raw_truncated"]
            ):
                return
            info = record.capture_info
            overhead = (1 if record.raw_events else 2) if event else 0
            available = (
                self._raw_limit
                - info["raw_bytes"]
                - overhead
                + (0 if event else record._raw_response_bytes)
            )
            if available < 4:
                info["raw_truncated"] = True
                self._touch(record)
                return
            converter = _Sanitizer(tuple(record._secrets))
            data = converter.convert(value, available)
            record._secrets.update(converter.secrets)
            info["redacted"] |= converter.redacted
            info["raw_unavailable"] |= converter.unavailable
            info["raw_truncated"] |= converter.truncated
            size = _json_size(data)
            if event:
                record.raw_events.append(data)
                record.status = "streaming"
                info["raw_bytes"] += overhead + size
            else:
                record.raw_response = data
                info["raw_bytes"] += size - record._raw_response_bytes
                record._raw_response_bytes = size
            self._touch(record)

    def finish(
        self,
        record_id: int,
        response: dict[str, Any] | None = None,
        *,
        error: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        """Commit a terminal state once, retaining partial output on failure."""
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            if response:
                record.response.update(self._safe(record, response))
            if error is not None:
                # Exceptions may have unsafe __str__; only string arguments are retained.
                message = next(
                    (arg for arg in error.args if isinstance(arg, str)),
                    type(error).__name__,
                )
                code = getattr(error, "status_code", None)
                record.error = self._safe(
                    record,
                    {
                        "type": type(error).__name__,
                        "message": message,
                        "status_code": code if isinstance(code, int) else None,
                    },
                )
            record.status = (
                "cancelled"
                if cancelled
                else "failed"
                if error is not None
                else "succeeded"
            )
            record.finished_at = time.time()
            record.duration_ms = (
                (time.perf_counter() - record._clock) * 1000
                if record._clock is not None
                else None
            )
            self._touch(record)

    def mark_unavailable(
        self, record_id: int, *, request: bool = False, raw: bool = False
    ) -> None:
        """Mark data genuinely unavailable rather than fabricating provider payloads."""
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            record.capture_info["request_unavailable"] |= request
            record.capture_info["raw_unavailable"] |= raw
            self._touch(record)

    def snapshot(self) -> dict[str, Any]:
        """Return one atomic metadata-only snapshot and current-buffer statistics."""
        with self._lock:
            summaries = [record.to_summary() for record in self._records.values()]
            return {
                "instance_id": self._instance_id,
                "revision": self._revision,
                "capacity": self._max_records,
                "records": summaries,
                "stats": {
                    "total": len(summaries),
                    "by_status": dict(Counter(r["status"] for r in summaries)),
                    "tokens_in": sum(r["tokens_in"] or 0 for r in summaries),
                    "tokens_out": sum(r["tokens_out"] or 0 for r in summaries),
                },
            }

    def list_summaries(self) -> list[dict[str, Any]]:
        """Keep the original capture-order summary API without exposing bodies."""
        return self.snapshot()["records"]

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        """Return detached detail or None after clear/eviction."""
        with self._lock:
            record = self._records.get(record_id)
            return (
                {"instance_id": self._instance_id, **record.to_full()}
                if record is not None
                else None
            )

    def clear_snapshot(self) -> dict[str, Any]:
        """Forget retained records, not executions; IDs are never reused."""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            self._revision += 1
            return {
                "instance_id": self._instance_id,
                "revision": self._revision,
                "cleared_count": count,
            }

    def clear(self) -> int:
        """Preserve the old count-only clear API."""
        return self.clear_snapshot()["cleared_count"]

    def import_json(self, payload: Any) -> list[CapturedRequest]:
        """Import captured-only records and return detached, credential-free copies."""
        with self._lock:
            imported = []
            for api_name, params, metadata in _normalize_import_payload(payload):
                record_id = self.capture(api_name, params, metadata)
                record = copy.deepcopy(self._records[record_id])
                record._secrets.clear()
                imported.append(record)
            return imported


_inspector: RequestInspector | None = None
_inspector_lock = threading.Lock()


def get_inspector() -> RequestInspector:
    """Get the process-wide inspector, including concurrent first access."""
    global _inspector
    with _inspector_lock:
        if _inspector is None:
            _inspector = RequestInspector()
        return _inspector


def capture(
    api_name: str, params: dict[str, Any], metadata: dict[str, Any] | None = None
) -> int:
    """Capture an input without claiming that it has been executed."""
    return get_inspector().capture(api_name, params, metadata)


def _normalize_import_payload(
    payload: Any,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """将导入 JSON 归一化为 capture 所需的记录列表。"""
    entries = _normalize_import_entries(payload)
    if not entries:
        raise ValueError("导入 JSON 为空，无法渲染")
    return entries


def _normalize_import_entries(
    payload: Any,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """递归解析导入 JSON，支持单条、列表和包装对象。"""
    if isinstance(payload, list):
        entries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for item in payload:
            entries.extend(_normalize_import_entries(item))
        return entries

    if not isinstance(payload, dict):
        raise ValueError(
            "导入 JSON 必须是对象、对象数组，或包含 requests/params 的对象"
        )

    requests_payload = payload.get("requests")
    if requests_payload is not None:
        if not isinstance(requests_payload, list):
            raise ValueError("requests 字段必须是数组")
        entries: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for item in requests_payload:
            entries.extend(_normalize_import_entries(item))
        return entries

    metadata_raw = payload.get("metadata")
    metadata = metadata_raw.copy() if isinstance(metadata_raw, dict) else {}
    metadata.setdefault("imported", True)

    api_name = payload.get("api_name") or payload.get("api") or "imported.request"
    if not isinstance(api_name, str):
        raise ValueError("api_name must be a string")

    if "params" in payload:
        params = payload.get("params")
        if not isinstance(params, dict):
            raise ValueError("params 字段必须是对象")
        if "source" in payload and "import_source" not in metadata:
            metadata["import_source"] = payload.get("source")
        return [(api_name, params, metadata)]

    if any(key in payload for key in ("messages", "tools", "model", "input")):
        params = payload.copy()
        params.pop("metadata", None)
        return [(api_name, params, metadata)]

    raise ValueError(
        "无法识别导入 JSON 结构；请提供原始请求体、包含 params 的对象，或 requests 数组"
    )
