"""LLM 原生调用生命周期检视器。

本模块以线程安全的内存缓冲保存请求、标准化响应和 SDK 原始 JSON。
数据不脱敏，键名及内容原样保留；外部读取必须认证并限制访问。
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
import threading
import uuid
from collections import Counter
from dataclasses import field, fields, is_dataclass
from types import SimpleNamespace
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
_UNAVAILABLE = "[unavailable]"


def _json_size(value: Any) -> int:
    """计算快照中紧凑 UTF-8 JSON 表示的准确字节数。

    Args:
        value: 待观测或转换的原始数据。

    Returns:
        int: 紧凑 JSON 编码后的 UTF-8 字节数。
    """
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


class _JsonConverter:
    """转换为独立的 JSON 兼容数据，不脱敏、不改写字符串键或内容。"""

    def __init__(self) -> None:
        """初始化无法转换与超出预算的标记。"""
        self.unavailable = False
        self.truncated = False

    def convert(self, value: Any, limit: int | None = None) -> Any:
        """转换为独立的 JSON 兼容数据，并按需严格限制输出字节数。

        Args:
            value: 待观测或转换的原始数据。
            limit: 允许保留的 JSON 字节数；为 None 时不设置字节上限。

        Returns:
            Any: 独立的 JSON 兼容数据，必要时仅保留部分结构或使用不可用标记。
        """
        seen: set[int] = set()

        def visit(item: Any, budget: int | None, depth: int) -> Any:
            """仅遍历可保留的子节点，并预留 JSON 容器自身的字节开销。

            Args:
                item: 当前待转换的节点。
                budget: 当前节点可使用的 JSON 字节预算。
                depth: 当前递归层级。

            Returns:
                Any: 当前节点在预算内转换得到的安全值。
            """
            if depth > 64 or id(item) in seen:
                self.unavailable = True
                item = _UNAVAILABLE
            if item is None or isinstance(item, (bool, int, float, str)):
                if isinstance(item, float) and not math.isfinite(item):
                    self.unavailable = True
                    item = None
                if isinstance(item, str) and budget is not None and len(item) > budget:
                    # 避免对单个超大的 SDK 文本增量执行无上限的 JSON 编码。
                    item = item[:budget]
                    self.truncated = True
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
                    return visit(item, budget, depth + 1)
                out: Any = {} if isinstance(item, dict) else []
                used = 2
                entries = item.items() if isinstance(item, dict) else enumerate(item)
                for key, child in entries:
                    if isinstance(out, dict) and not isinstance(key, str):
                        self.unavailable = True
                        continue
                    overhead = (1 if out else 0) + (
                        _json_size(key) + 1 if isinstance(out, dict) else 0
                    )
                    remaining = None if budget is None else budget - used - overhead
                    if remaining is not None and remaining < 4:
                        self.truncated = True
                        break
                    result = visit(child, remaining, depth + 1)
                    size = _json_size(result)
                    if budget is not None and used + overhead + size > budget:
                        self.truncated = True
                        break
                    if isinstance(out, dict):
                        out[key] = result
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
    """创建包含公共标准化响应全部字段的空结果。

    Returns:
        dict[str, Any]: 具有完整标准化响应字段的空结果。
    """
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
    """有界缓冲中的单条记录，对外转换不暴露内部字段。"""

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
            # 保留既有读取契约；当前不执行脱敏，该字段恒为 False。
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

    def to_summary(self) -> dict[str, Any]:
        """返回记录摘要，不包含请求正文或响应正文。

        Returns:
            dict[str, Any]: 不含正文的记录摘要。
        """
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
        """生成与内部状态隔离的详情，所有展示视图均来自同一参数快照。

        Returns:
            dict[str, Any]: 包含安全参数、派生视图、响应与采集状态的独立详情。
        """
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
    """线程安全的内存生命周期记录器，不接管模型执行。"""

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
        """在持锁期间递增缓冲版本与记录版本。

        Args:
            record: 需要处理的内部记录。
        """
        record.version += 1
        self._revision += 1

    def _serialize(
        self, record: CapturedRequest, value: Any, unavailable: str | None = None
    ) -> Any:
        """在保留数据前转换为独立的 JSON 兼容值，并更新完整性标记。

        Args:
            record: 需要处理的内部记录。
            value: 待观测或转换的原始数据。
            unavailable: 需要更新的不可用标记字段名；为 None 时不更新该标记。

        Returns:
            Any: 不改写键名及内容的独立 JSON 兼容数据。
        """
        converter = _JsonConverter()
        result = converter.convert(value)
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
    ) -> int:
        """创建手工捕获记录或原生待执行记录，返回单调递增的记录 ID。

        Args:
            api_name: 用于标识 SDK 调用入口的接口名称。
            params: 最终传入 SDK 的请求参数；允许为空时保留原参数。
            metadata: 需要合并到记录中的请求元数据。
            native: 是否创建原生调用的 pending 记录；否则创建 captured 记录。
            logical_request_id: 关联重试的逻辑请求 ID；未提供时自动生成。
            attempt: 本逻辑请求的尝试序号，通常从 1 开始。
            stream: 是否以流式方式执行本次调用。

        Returns:
            int: 新记录的单调递增 ID，清空后也不重复使用。
        """
        with self._lock:
            self._counter += 1
            record = CapturedRequest(self._counter, time.time(), "", "", {}, {})
            record.api_name = self._serialize(record, api_name)
            record.params = self._serialize(record, params, "request_unavailable")
            record.metadata = self._serialize(
                record, metadata or {}, "metadata_unavailable"
            )
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
    ) -> None:
        """在调用提供商前记录最终 SDK 参数，并将记录设为执行中。

        Args:
            record_id: 待操作的记录 ID；记录已清空或淘汰时忽略更新。
            params: 最终传入 SDK 的请求参数；允许为空时保留原参数。
            api_name: 用于标识 SDK 调用入口的接口名称。
            metadata: 需要合并到记录中的请求元数据。
        """
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            if params is not None:
                record.params = self._serialize(record, params, "request_unavailable")
                record.model = str(record.params.get("model", record.model))
                record.stream = record.params.get("stream") is True or record.stream
            if api_name is not None:
                record.api_name = self._serialize(record, api_name)
            if metadata:
                record.metadata.update(
                    self._serialize(record, metadata, "metadata_unavailable")
                )
            if record.started_at is None:
                record.started_at = time.time()
                record._clock = time.perf_counter()
            record.status = "running"
            self._touch(record)

    def update(
        self, record_id: int, response: dict[str, Any], *, content: bool = False
    ) -> None:
        """更新标准化部分结果，不受原始数据预算限制。

        Args:
            record_id: 待操作的记录 ID；记录已清空或淘汰时忽略更新。
            response: 已经解析的标准化响应字段。
            content: 是否收到有效内容增量，用于推进流状态与首个内容耗时。
        """
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            record.response.update(self._serialize(record, response))
            if record.stream and (content or record.raw_events):
                record.status = "streaming"
            if content and record.first_token_ms is None and record._clock is not None:
                record.first_token_ms = (time.perf_counter() - record._clock) * 1000
            self._touch(record)

    def raw(self, record_id: int, value: Any, *, event: bool = False) -> None:
        """在响应与事件共用预算内保留 SDK JSON，不使用对象文本表示兜底。

        Args:
            record_id: 待操作的记录 ID；记录已清空或淘汰时忽略更新。
            value: 待观测或转换的原始数据。
            event: 是否将原始数据作为流事件追加，而非替换原始响应。
        """
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
            converter = _JsonConverter()
            data = converter.convert(value, available)
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
        """幂等地记录执行终态，失败时保留已接收的部分输出。

        Args:
            record_id: 待操作的记录 ID；记录已清空或淘汰时忽略更新。
            response: 已经解析的标准化响应字段。
            error: 执行或观测过程中捕获的异常。
            cancelled: 是否因调用方取消而结束。
        """
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            if response:
                record.response.update(self._serialize(record, response))
            if error is not None:
                # 异常的 __str__ 可能不安全，因此仅保留其字符串参数。
                message = next(
                    (arg for arg in error.args if isinstance(arg, str)),
                    type(error).__name__,
                )
                code = getattr(error, "status_code", None)
                record.error = self._serialize(
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
        """标记确实不可用的数据，不伪造提供商请求或响应。

        Args:
            record_id: 待操作的记录 ID；记录已清空或淘汰时忽略更新。
            request: 是否标记原始请求参数不可用。
            raw: 是否标记原始 SDK 响应或事件不可用。
        """
        with self._lock:
            record = self._records.get(record_id)
            if record is None or record.status in _TERMINAL:
                return
            record.capture_info["request_unavailable"] |= request
            record.capture_info["raw_unavailable"] |= raw
            self._touch(record)

    def snapshot(self) -> dict[str, Any]:
        """原子地读取记录摘要及当前缓冲统计，不返回请求或响应正文。

        Returns:
            dict[str, Any]: 包含 instance_id、revision、capacity、records 和 stats 的快照。
        """
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
        """保留按捕获顺序返回摘要的原有接口，不暴露正文。

        Returns:
            list[dict[str, Any]]: 按捕获顺序排列的独立记录摘要。
        """
        return self.snapshot()["records"]

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        """返回与内部状态隔离的详情；记录已清空或淘汰时返回 None。

        Args:
            record_id: 需要读取详情的记录 ID。

        Returns:
            dict[str, Any] | None: 包含实例标识的独立详情；记录不存在时返回 None。
        """
        with self._lock:
            record = self._records.get(record_id)
            return (
                {"instance_id": self._instance_id, **record.to_full()}
                if record is not None
                else None
            )

    def clear_snapshot(self) -> dict[str, Any]:
        """清空已保留的记录，不取消实际调用，也不重置记录 ID。

        Returns:
            dict[str, Any]: 包含实例标识、更新后的缓冲版本及删除数量的结果。
        """
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
        """保留仅返回删除数量的原有清空接口。

        Returns:
            int: 本次删除的记录数量。
        """
        return self.clear_snapshot()["cleared_count"]

    def import_json(self, payload: Any) -> list[CapturedRequest]:
        """导入为手工捕获状态，返回与内部状态隔离、保留原始字段的记录副本。

        Args:
            payload: 待导入的 JSON 对象、对象列表或包含 requests 的包装对象。

        Returns:
            list[CapturedRequest]: 导入记录的独立副本，状态均为 captured。
        """
        with self._lock:
            imported = []
            for api_name, params, metadata in _normalize_import_payload(payload):
                record_id = self.capture(api_name, params, metadata)
                record = copy.deepcopy(self._records[record_id])
                imported.append(record)
            return imported


_inspector: RequestInspector | None = None
_inspector_lock = threading.Lock()


def get_inspector() -> RequestInspector:
    """获取进程级监视器单例，并保证并发首次访问安全。

    Returns:
        RequestInspector: 当前进程共享的线程安全监视器。
    """
    global _inspector
    with _inspector_lock:
        if _inspector is None:
            _inspector = RequestInspector()
        return _inspector


def capture(
    api_name: str, params: dict[str, Any], metadata: dict[str, Any] | None = None
) -> int:
    """手工捕获请求输入，不表示该请求已经执行。

    Args:
        api_name: 用于标识 SDK 调用入口的接口名称。
        params: 最终传入 SDK 的请求参数；允许为空时保留原参数。
        metadata: 需要合并到记录中的请求元数据。

    Returns:
        int: 新建手工捕获记录的 ID。
    """
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
