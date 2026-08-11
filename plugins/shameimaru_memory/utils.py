"""Shameimaru Memory 公共工具函数。"""

from __future__ import annotations

import time
from typing import Any

from src.core.models.message import Message


def message_time(message: Any) -> float:
    """获取消息时间戳（秒）。"""
    value = getattr(message, "time", None)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def person_id_of(message: Any) -> str:
    """从消息中提取人物 ID。

    人物 ID 格式为 ``platform:user_id``。若消息的 sender_id 已经是该格式
    则原样使用，否则用平台 + sender_id 拼接。

    Args:
        message: 运行时 Message 对象。

    Returns:
        str: 人物 ID；信息不足时返回空字符串。
    """
    platform = str(getattr(message, "platform", "") or "").strip()
    sender_id = str(getattr(message, "sender_id", "") or "").strip()
    if not platform or not sender_id:
        return ""
    prefix = f"{platform}:"
    if sender_id.startswith(prefix):
        return sender_id
    return f"{prefix}{sender_id}"


def person_name_of(message: Any) -> str:
    """从消息中提取人物展示名称。"""
    name = str(getattr(message, "sender_name", "") or "").strip()
    if name:
        return name
    sender_id = str(getattr(message, "sender_id", "") or "").strip()
    return sender_id


def format_local_time(timestamp: float) -> str:
    """将时间戳格式化为本地时间字符串（HH:MM）。"""
    try:
        return time.strftime("%H:%M", time.localtime(timestamp))
    except (OSError, ValueError, OverflowError):
        return ""


def is_group_message(message: Any) -> bool:
    """判断消息是否属于群聊。"""
    return str(getattr(message, "chat_type", "") or "") == "group"
