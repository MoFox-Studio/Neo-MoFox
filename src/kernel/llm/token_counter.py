"""
token 计数器

负责计算一段文本或一组 LLMPayload 中包含的 token 数量，以便在构建上下文时进行裁剪和预算控制。

媒体类型（Image/Audio/Video/File）的 token 估算不基于 base64 文本编码，
而是基于原始二进制字节数进行近似估算，避免将 base64 字符串当作普通文本计算导致的严重高估。
"""

from __future__ import annotations

import base64 as _stdlib_b64
import json

from .payload import Audio, File, Image, LLMPayload, Text, ToolCall, ToolResult, Video


def _get_tiktoken_encoding(model_identifier: str):
    """
    获取指定模型的 tiktoken 编码器。
    """
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model_identifier)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


# 媒体 token 估算的基准比例（token/字节）。
# 这些比例基于经验取值，用于上下文预算控制时的近似估算，并非精确计费。
# - 图片：约 1 token / 750 像素 ≈ 对于常见压缩图片约 1 token / 1000 字节
# - 音频：约 1 token / 100 字节（音频通常信息密度更高）
# - 视频：约 1 token / 200 字节
# - 通用文件：约 1 token / 500 字节
_MEDIA_TOKEN_RATIOS: dict[type, float] = {
    Image: 1 / 1000,
    Audio: 1 / 100,
    Video: 1 / 200,
    File: 1 / 500,
}


def _estimate_media_tokens(part: File) -> int:
    """基于原始二进制字节数估算媒体内容的 token 数量。

    Args:
        part: 继承自 File 的媒体内容（Image/Audio/Video/File），
            其 value 为纯 base64 编码字符串。

    Returns:
        估算的 token 数量，至少为 1。
    """
    value = part.value
    try:
        raw_bytes = _stdlib_b64.b64decode(value)
        raw_len = len(raw_bytes)
    except Exception:
        # base64 解码失败时回退到 base64 字符串长度的一半作为近似
        raw_len = len(value) * 3 // 4

    # 按 media_type 查找比例，子类优先，最后回退到 File 的通用比例
    ratio = _MEDIA_TOKEN_RATIOS.get(type(part)) or _MEDIA_TOKEN_RATIOS[File]
    return max(1, int(raw_len * ratio))


def _extract_media_parts(payload: LLMPayload) -> list[File]:
    """从 payload 的 content 中提取所有媒体类型部分。

    Args:
        payload: 待解析的 LLMPayload。

    Returns:
        content 中所有 File（含子类）实例的列表。
    """
    return [part for part in payload.content if isinstance(part, File)]


def _serialize_payload(payload: LLMPayload) -> str:
    """将一个 LLMPayload 对象序列化为一个字符串，供 token 计数器使用。

    序列化策略：
    1. 以 role 开头，格式为 "role:{role}"。
    2. 对于 content 中的每个部分，根据类型进行处理：
        - Text: 直接使用 text 属性的值。
        - ToolResult: 调用 to_text() 方法获取文本表示。
        - ToolCall: 使用 name 属性作为文本表示，如果 args 是 dict，则将其 JSON 序列化后追加；否则直接转换为字符串。
        - Image/Audio/Video/File: **跳过**，不将 base64 数据写入文本，
          其 token 由 _estimate_media_tokens 单独估算。
        - 其他类型：优先尝试调用 to_schema() 方法获取结构化表示并 JSON 序列化；
          如果没有该方法或调用失败，则尝试使用 text 属性；
          最后退回到直接转换为字符串（注意：不再回退到 value 属性，避免误读媒体 base64）。
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

        # 媒体类型（Image/Audio/Video/File）跳过文本序列化，
        # 其 token 通过 _estimate_media_tokens 单独估算。
        if isinstance(part, File):
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

        # 注意：不再回退到 value 属性。
        # 之前的实现会读取 value（即 base64 字符串）当作文本，
        # 导致图片等媒体的 token 被严重高估。媒体类型已在上方 File 分支处理。
        chunks.append(str(part))

    return "\n".join(chunks)


def count_payload_tokens(payloads: list[LLMPayload], *, model_identifier: str) -> int:
    """计算一组 LLMPayload 中包含的 token 数量。

    文本部分使用 tiktoken 精确编码计数；媒体部分（Image/Audio/Video/File）
    基于原始二进制字节数近似估算，避免将 base64 字符串当作文本计算。
    """
    encoding = _get_tiktoken_encoding(model_identifier)
    total = 0
    for payload in payloads:
        serialized = _serialize_payload(payload)
        total += len(encoding.encode(serialized))
        # 叠加媒体部分的估算 token
        for media in _extract_media_parts(payload):
            total += _estimate_media_tokens(media)
    return total


def count_text_tokens(text: str, *, model_identifier: str) -> int:
    """计算一段文本包含的 token 数量。"""
    encoding = _get_tiktoken_encoding(model_identifier)
    return len(encoding.encode(text))
