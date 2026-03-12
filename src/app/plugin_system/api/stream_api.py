"""聊天流 API 模块。

为插件提供聊天流的创建、查询与管理接口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

if TYPE_CHECKING:
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream, StreamContext
    from src.core.models.sql_alchemy import ChatStreams, Messages
    from src.core.managers.stream_manager import StreamManager


def _get_stream_manager() -> "StreamManager":
    """延迟获取 StreamManager，避免循环依赖。

    Returns:
        流管理器实例
    """
    from src.core.managers.stream_manager import get_stream_manager

    return get_stream_manager()


def _normalize_chat_type(chat_type: ChatType | str) -> str:
    """规范化 chat_type 输入为字符串。

    Args:
        chat_type: 聊天类型

    Returns:
        规范化后的聊天类型字符串
    """
    if isinstance(chat_type, ChatType):
        return chat_type.value
    if isinstance(chat_type, str):
        return chat_type
    raise TypeError("chat_type 必须是 ChatType 或 str")


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def _validate_limit_offset(value: int, name: str) -> None:
    """校验分页参数。

    Args:
        value: 分页数值
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数")


async def get_or_create_stream(
    stream_id: str = "",
    platform: str = "",
    user_id: str = "",
    group_id: str = "",
    chat_type: ChatType | str = "private",
) -> "ChatStream":
    """获取现有流或创建新流。

    Args:
        stream_id: 聊天流 ID，可选
        platform: 平台名称
        user_id: 用户 ID
        group_id: 群组 ID
        chat_type: 聊天类型

    Returns:
        聊天流实例
    """
    if stream_id:
        return await _get_stream_manager().get_or_create_stream(
            stream_id=stream_id,
            platform=platform,
            user_id=user_id,
            group_id=group_id,
            chat_type=_normalize_chat_type(chat_type),
        )

    _validate_non_empty(platform, "platform")
    if not user_id and not group_id:
        raise ValueError("user_id 或 group_id 必须提供至少一个")
    return await _get_stream_manager().get_or_create_stream(
        platform=platform,
        user_id=user_id,
        group_id=group_id,
        chat_type=_normalize_chat_type(chat_type),
    )

async def get_stream(
    stream_id: str = "",
) -> "ChatStream | None":
    """获取现有流。

    Args:
        stream_id: 聊天流 ID

    Returns:
        聊天流实例，未找到则返回 None
    """

    _validate_non_empty(stream_id, "stream_id")
    return _get_stream_manager()._streams.get(stream_id)


async def build_stream_from_database(stream_id: str) -> "ChatStream | None":
    """从数据库记录构建 ChatStream。

    Args:
        stream_id: 聊天流 ID

    Returns:
        聊天流实例，未找到则返回 None
    """
    _validate_non_empty(stream_id, "stream_id")
    return await _get_stream_manager().build_stream_from_database(stream_id)


async def load_stream_context(
    stream_id: str,
    max_messages: int | None = None,
) -> "StreamContext":
    """从数据库加载 StreamContext。

    Args:
        stream_id: 聊天流 ID
        max_messages: 最大加载消息数，可选

    Returns:
        聊天流上下文
    """
    _validate_non_empty(stream_id, "stream_id")
    if max_messages is not None:
        _validate_limit_offset(max_messages, "max_messages")
    return await _get_stream_manager().load_stream_context(stream_id, max_messages)


async def add_message_to_stream(message: "Message") -> "Messages":
    """添加消息到流。

    Args:
        message: 消息对象

    Returns:
        入库后的消息记录
    """
    if message is None:
        raise ValueError("message 不能为空")
    return await _get_stream_manager().add_message(message)


async def add_message(message: "Message") -> "Messages":
    """添加消息到流。

    Args:
        message: 消息对象

    Returns:
        入库后的消息记录
    """
    if message is None:
        raise ValueError("message 不能为空")
    return await _get_stream_manager().add_message(message)


async def add_sent_message_to_history(message: "Message") -> "Messages":
    """添加“已发送消息”到流历史消息。

    Args:
        message: 消息对象

    Returns:
        入库后的消息记录
    """
    if message is None:
        raise ValueError("message 不能为空")
    return await _get_stream_manager().add_sent_message_to_history(message)


async def delete_stream(stream_id: str, delete_messages: bool = True) -> bool:
    """删除流及其消息。

    Args:
        stream_id: 聊天流 ID
        delete_messages: 是否删除关联消息

    Returns:
        是否删除成功
    """
    _validate_non_empty(stream_id, "stream_id")
    return await _get_stream_manager().delete_stream(
        stream_id=stream_id,
        delete_messages=delete_messages,
    )


async def get_stream_info(stream_id: str) -> dict[str, Any] | None:
    """获取流的综合信息。

    Args:
        stream_id: 聊天流 ID

    Returns:
        流信息字典，未找到则返回 None
    """
    _validate_non_empty(stream_id, "stream_id")
    return await _get_stream_manager().get_stream_info(stream_id)


async def get_stream_messages(
    stream_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list["Message"]:
    """获取流的消息（支持分页）。

    Args:
        stream_id: 聊天流 ID
        limit: 单页数量
        offset: 偏移量

    Returns:
        消息列表
    """
    _validate_non_empty(stream_id, "stream_id")
    _validate_limit_offset(limit, "limit")
    _validate_limit_offset(offset, "offset")
    return await _get_stream_manager().get_stream_messages(
        stream_id=stream_id,
        limit=limit,
        offset=offset,
    )


def clear_stream_cache(stream_id: str | None = None) -> None:
    """清理流实例缓存。

    Args:
        stream_id: 聊天流 ID，可选

    Returns:
        None
    """
    if stream_id is not None:
        _validate_non_empty(stream_id, "stream_id")
    _get_stream_manager().clear_cache(stream_id)


async def refresh_stream(stream_id: str) -> "ChatStream | None":
    """强制从数据库刷新流。

    Args:
        stream_id: 聊天流 ID

    Returns:
        聊天流实例，未找到则返回 None
    """
    _validate_non_empty(stream_id, "stream_id")
    return await _get_stream_manager().refresh_stream(stream_id)


async def activate_stream(stream_id: str) -> "ChatStream | None":
    """激活流，更新其最后活跃时间。

    Args:
        stream_id: 聊天流 ID

    Returns:
        聊天流实例，未找到则返回 None
    """
    _validate_non_empty(stream_id, "stream_id")
    return await _get_stream_manager().activate_stream(stream_id)


__all__ = [
    "get_or_create_stream",
    "build_stream_from_database",
    "load_stream_context",
    "add_message_to_stream",
    "add_message",
    "add_sent_message_to_history",
    "delete_stream",
    "get_stream_info",
    "get_stream_messages",
    "clear_stream_cache",
    "refresh_stream",
    "activate_stream",
]
