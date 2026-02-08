"""消息接收辅助工具。

提供 stream_id 生成、base64 规范化等工具函数。
"""

from __future__ import annotations

from typing import Any

from mofox_wire import MessageInfoPayload


def extract_stream_id(message_info: MessageInfoPayload) -> str:
    """根据 MessageInfoPayload 生成聊天会话 stream_id（标准哈希格式）。

    使用 ChatStream.generate_stream_id 生成 SHA-256 哈希 ID，确保与数据库存储格式一致。

    规则：
    - 含 group_info → 使用 group_id 生成哈希
    - 仅 user_info → 使用 user_id 生成哈希
    - 均缺失 → 使用 "no_id" 生成哈希

    Args:
        message_info: 消息信息字典

    Returns:
        str: SHA-256 哈希的 stream_id（标准格式）

    Examples:
        >>> info = {"platform": "qq", "message_id": "1",
        ...         "group_info": {"platform": "qq", "group_id": "123", "group_name": "g"}}
        >>> extract_stream_id(info)
        'a1b2c3...  # SHA-256 哈希值
    """
    from src.core.models.stream import ChatStream

    platform: str = message_info.get("platform", "unknown")

    group_info = message_info.get("group_info")
    if group_info:
        return ChatStream.generate_stream_id(
            platform=platform,
            group_id=group_info["group_id"]
        )

    user_info = message_info.get("user_info")
    if user_info:
        return ChatStream.generate_stream_id(
            platform=platform,
            user_id=user_info["user_id"],
        )

    # 兜底：使用 "no_id" 作为 user_id
    return ChatStream.generate_stream_id(
        platform=platform,
        user_id="no_id",
    )


def infer_chat_type(message_info: MessageInfoPayload) -> str:
    """根据 MessageInfoPayload 推断聊天类型。

    Args:
        message_info: 消息信息字典

    Returns:
        str: ``"group"`` 或 ``"private"``

    Examples:
        >>> infer_chat_type({"platform": "qq", "message_id": "1",
        ...                  "group_info": {"platform": "qq", "group_id": "1", "group_name": "g"}})
        'group'
    """
    if message_info.get("group_info"):
        return "group"
    return "private"


def normalize_base64(data: str) -> str:
    """统一 base64 数据格式。

    已带 ``base64|`` 前缀或 ``data:`` URL 的直接返回；
    否则添加 ``base64|`` 前缀。

    Args:
        data: 原始 base64 字符串

    Returns:
        str: 规范化后的字符串

    Examples:
        >>> normalize_base64("iVBORw0KGgo=")
        'base64|iVBORw0KGgo='
        >>> normalize_base64("base64|iVBORw0KGgo=")
        'base64|iVBORw0KGgo='
        >>> normalize_base64("data:image/png;base64,ABC")
        'data:image/png;base64,ABC'
    """
    if not data:
        return data
    if data.startswith("base64|") or data.startswith("data:"):
        return data
    return f"base64|{data}"


def safe_json_loads(data: str) -> Any:
    """安全地将字符串解析为 JSON，失败时返回原始字符串。

    Args:
        data: 待解析的字符串

    Returns:
        Any: 解析结果或原始字符串

    Examples:
        >>> safe_json_loads('{"name": "test"}')
        {'name': 'test'}
        >>> safe_json_loads('plain text')
        'plain text'
    """
    import json

    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return data
