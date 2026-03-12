"""
token 计数器

负责计算一段文本或一组 LLMPayload 中包含的 token 数量，以便在构建上下文时进行裁剪和预算控制。
"""

from __future__ import annotations

import json

from .payload import LLMPayload, Text, ToolCall, ToolResult


def _get_tiktoken_encoding(model_identifier: str):
    """
    获取指定模型的 tiktoken 编码器。
    """
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model_identifier)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def _serialize_payload(payload: LLMPayload) -> str:
    """将一个 LLMPayload 对象序列化为一个字符串，供 token 计数器使用。

    序列化策略：
    1. 以 role 开头，格式为 "role:{role}"。
    2. 对于 content 中的每个部分，根据类型进行处理：
        - Text: 直接使用 text 属性的值。
        - ToolResult: 调用 to_text() 方法获取文本表示。
        - ToolCall: 使用 name 属性作为文本表示，如果 args 是 dict，则将其 JSON 序列化后追加；否则直接转换为字符串。
        - 其他类型：优先尝试调用 to_schema() 方法获取结构化表示并 JSON 序列化；如果没有该方法或调用失败，则尝试使用 text 或 value 属性；最后退回到直接转换为字符串。
    """
    chunks: list[str] = [f"role:{payload.role.value}"]

    for part in payload.content:
        if isinstance(part, Text):
            chunks.append(part.text)
            continue

        if isinstance(part, ToolResult):
            chunks.append(part.to_text())
            continue

        if isinstance(part, ToolCall):
            chunks.append(part.name)
            if isinstance(part.args, dict):
                chunks.append(json.dumps(part.args, ensure_ascii=False, sort_keys=True))
            else:
                chunks.append(str(part.args))
            continue

        to_schema = getattr(part, "to_schema", None)
        if callable(to_schema):
            try:
                chunks.append(json.dumps(to_schema(), ensure_ascii=False, sort_keys=True))
                continue
            except Exception:
                chunks.append(str(part))
                continue

        text = getattr(part, "text", None)
        if isinstance(text, str):
            chunks.append(text)
            continue

        value = getattr(part, "value", None)
        if isinstance(value, str):
            chunks.append(value)
            continue

        chunks.append(str(part))

    return "\n".join(chunks)


def count_payload_tokens(payloads: list[LLMPayload], *, model_identifier: str) -> int:
    """计算一组 LLMPayload 中包含的 token 数量。"""
    encoding = _get_tiktoken_encoding(model_identifier)
    total = 0
    for payload in payloads:
        serialized = _serialize_payload(payload)
        total += len(encoding.encode(serialized))
    return total


def count_text_tokens(text: str, *, model_identifier: str) -> int:
    """计算一段文本包含的 token 数量。"""
    encoding = _get_tiktoken_encoding(model_identifier)
    return len(encoding.encode(text))
