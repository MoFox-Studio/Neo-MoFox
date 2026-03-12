"""
提供了一个工具调用兼容性的提示构建函数和一个响应解析函数，用于处理 LLM 输出的工具调用信息。
"""
from __future__ import annotations

import json
from typing import Any

from json_repair import repair_json

from .exceptions import LLMError


def build_tool_call_compat_prompt(tool_schemas: list[dict[str, Any]]) -> str:
    """构建一个提示文本，指导 LLM 输出符合工具调用兼容性要求的 JSON 格式响应。"""
    schema_text = json.dumps(tool_schemas, ensure_ascii=False)
    return (
        "请只返回一个 JSON 对象，格式如下：\n"
        "{\n"
        "  \"message\": \"可选，给用户的自然语言回复\",\n"
        "  \"tool_calls\": [\n"
        "    {\n"
        "      \"id\": \"可选，字符串\",\n"
        "      \"name\": \"工具名\",\n"
        "      \"args\": {\"参数名\": \"参数值\"}\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "可用工具 schema（JSON）如下：\n"
        f"{schema_text}"
    )


def _repair_to_obj(raw: str) -> Any:
    """尝试修复一个原始字符串并将其解析为 JSON 对象。"""
    try:
        return repair_json(raw, return_objects=True)
    except Exception as e:
        raise LLMError(f"tool_call_compat JSON repair 失败: {e}") from e


def _normalize_args(args: Any) -> dict[str, Any] | str:
    """规范化工具调用的参数，确保最终返回一个字典对象。"""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        stripped = args.strip()
        if not stripped:
            return {}
        # 尝试修复并解析字符串形式的参数，如果修复后的结果是一个对象则返回，否则抛出错误
        repaired = _repair_to_obj(stripped)
        if isinstance(repaired, dict):
            return repaired
        raise LLMError("tool_call_compat 参数解析失败：args 不是对象")
    if args is None:
        return {}
    raise LLMError("tool_call_compat 参数类型非法")


def _normalize_single_call(item: Any, index: int) -> dict[str, Any]:
    """规范化单个工具调用项，确保包含有效的 name 和 args 字段。"""
    if not isinstance(item, dict):
        raise LLMError(f"tool_call_compat 第 {index} 个调用项不是对象")

    name = item.get("name")
    args = item.get("args")

    function_obj = item.get("function")
    if isinstance(function_obj, dict):
        name = function_obj.get("name", name)
        if args is None:
            args = function_obj.get("arguments")

    if not isinstance(name, str) or not name:
        raise LLMError(f"tool_call_compat 第 {index} 个调用缺少有效 name")

    normalized_args = _normalize_args(args)
    call_id = item.get("id")
    return {
        "id": call_id if isinstance(call_id, str) and call_id else None,
        "name": name,
        "args": normalized_args,
    }


def parse_tool_call_compat_response(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    """解析 LLM 输出的工具调用兼容性响应，返回一个包含自然语言回复和工具调用列表的元组。"""
    repaired = _repair_to_obj(raw_text)

    if isinstance(repaired, list):
        calls = [_normalize_single_call(item, idx) for idx, item in enumerate(repaired)]
        return "", calls

    if not isinstance(repaired, dict):
        raise LLMError("tool_call_compat 返回必须是 JSON 对象")

    message = repaired.get("message")
    if message is None:
        message = repaired.get("content", "")

    if message is None:
        message_text = ""
    elif isinstance(message, str):
        message_text = message
    else:
        message_text = str(message)

    raw_calls = repaired.get("tool_calls")
    if raw_calls is None:
        raw_calls = repaired.get("calls", [])

    if not isinstance(raw_calls, list):
        raise LLMError("tool_call_compat 的 tool_calls 字段必须是数组")

    calls = [_normalize_single_call(item, idx) for idx, item in enumerate(raw_calls)]
    return message_text, calls
