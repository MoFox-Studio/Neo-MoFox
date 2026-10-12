"""LLM 请求体调试检视器。

本模块在进程内记录每次 OpenAI 兼容 API 的完整请求体，并把请求体转换为
结构化渲染模型（role、工具调用、工具结果与 Markdown 文本），便于调试 payload 结构。

**本模块是纯进程内工具，不对外提供任何 HTTP 端点。** 可视化调试请使用官方 WebUI：
https://github.com/MoFox-Studio/MoFox-Core-Webui

使用方式：
    # 在发起请求前调用
    from src.kernel.llm.request_inspector import capture
    capture("chat.completions.create", params)
"""

from __future__ import annotations

import json
import time
from collections import deque
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
    except Exception:
        return str(value)


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

    messages = params.get("messages", [])
    if isinstance(messages, list):
        rendered_messages.extend(messages)

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


@dataclass
class CapturedRequest:
    """单条捕获记录。"""

    id: int
    ts: float
    api_name: str
    model: str
    params: dict[str, Any]
    metadata: dict[str, Any]

    def to_summary(self) -> dict[str, Any]:
        """返回列表展示用的摘要。"""
        msg_count = len(self.params.get("messages", []))
        tool_count = len(self.params.get("tools", []))
        return {
            "id": self.id,
            "ts": self.ts,
            "ts_str": time.strftime("%H:%M:%S", time.localtime(self.ts)),
            "api_name": self.api_name,
            "model": self.model,
            "api_provider": str(self.metadata.get("api_provider", "-")),
            "request_name": str(self.metadata.get("request_name", "")),
            "estimated_input_tokens": self.metadata.get("estimated_input_tokens"),
            "msg_count": msg_count,
            "tool_count": tool_count,
        }

    def to_full(self) -> dict[str, Any]:
        """返回完整记录与结构化渲染模型。"""
        summary = self.to_summary()
        summary["params"] = self.params
        summary["metadata"] = self.metadata
        summary["rendered"] = build_render_view(
            self.api_name, self.model, self.params, self.metadata
        )
        return summary


class RequestInspector:
    """LLM 请求体检视器，保存最近 N 条请求记录供进程内调试。"""

    def __init__(self, max_records: int = 200) -> None:
        self._max_records = max_records
        self._records: deque[CapturedRequest] = deque(maxlen=max_records)
        self._counter: int = 0

    def capture(
        self,
        api_name: str,
        params: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """捕获一条请求体，存入内存环形缓冲。"""
        self._counter += 1
        try:
            copied = json.loads(json.dumps(params, default=str))
        except Exception:
            copied = {"_raw": str(params)}
        effective_metadata = metadata.copy() if isinstance(metadata, dict) else {}

        model = str(copied.get("model", "—"))
        record = CapturedRequest(
            id=self._counter,
            ts=time.time(),
            api_name=api_name,
            model=model,
            params=copied,
            metadata=effective_metadata,
        )
        self._records.append(record)

    def import_json(self, payload: Any) -> list[CapturedRequest]:
        """导入外部 JSON，并转换为可渲染的捕获记录。"""
        imported: list[CapturedRequest] = []
        for api_name, params, metadata in _normalize_import_payload(payload):
            next_id = self._counter + 1
            self.capture(api_name, params, metadata)
            record = self._records[-1]
            if record.id != next_id:
                raise RuntimeError("导入记录 ID 不连续")
            imported.append(record)
        return imported


_inspector: RequestInspector | None = None


def get_inspector() -> RequestInspector:
    """获取全局 RequestInspector 单例。"""
    global _inspector
    if _inspector is None:
        _inspector = RequestInspector()
    return _inspector


def capture(
    api_name: str,
    params: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """捕获一条 OpenAI 请求体。"""
    get_inspector().capture(api_name, params, metadata)


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

    api_name = str(payload.get("api_name") or payload.get("api") or "imported.request")

    if "params" in payload:
        params = payload.get("params")
        if not isinstance(params, dict):
            raise ValueError("params 字段必须是对象")
        if "source" in payload and "import_source" not in metadata:
            metadata["import_source"] = str(payload.get("source"))
        return [(api_name, params, metadata)]

    if any(key in payload for key in ("messages", "tools", "model", "input")):
        params = payload.copy()
        params.pop("metadata", None)
        return [(api_name, params, metadata)]

    raise ValueError(
        "无法识别导入 JSON 结构；请提供原始请求体、包含 params 的对象，或 requests 数组"
    )


