"""聊天流 API 模块。

为插件提供聊天流的创建、查询与管理接口。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

from mofox_wire import MessageEnvelope

from src.core.components.types import ChatType

API_VERSION = "1.0.0"

if TYPE_CHECKING:
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream, StreamContext
    from src.core.models.sql_alchemy import Messages
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


async def import_history_message(
    envelope: MessageEnvelope, *, peer_user_id: str = "", is_bot: bool = False,
) -> "Messages":
    """保存入站历史信封，不发布实时事件或触发聊天。

    Args:
        envelope: 包含原始 message_info.time 的历史信封。
        peer_user_id: 私聊对端 ID；Bot 自身发言时必须指定。
        is_bot: 是否为当前 Bot 自身发送的历史消息。

    Returns:
        已提交的核心消息记录。
    """
    from src.core.models.stream import ChatStream
    from src.core.transport.message_receive.converter import MessageConverter
    from src.app.plugin_system.api.person_api import get_or_create_person, get_person

    info = envelope.get("message_info") or {}
    original_time = info.get("time")
    if (
        envelope.get("direction") != "incoming"
        or isinstance(original_time, bool)
        or not isinstance(original_time, (int, float))
        or not math.isfinite(original_time)
        or original_time <= 0
    ):
        raise ValueError("历史信封必须包含有效的原始消息时间")
    _validate_non_empty(str(info.get("message_id") or ""), "message_id")
    message = await MessageConverter().envelope_to_message(envelope, recognize_media=False)
    _validate_non_empty(message.platform, "platform")
    _validate_non_empty(message.sender_id, "sender_id")
    if message.chat_type == "private":
        if is_bot:
            _validate_non_empty(peer_user_id, "peer_user_id")
        peer_user_id = peer_user_id or message.sender_id
        message.stream_id = ChatStream.generate_stream_id(
            platform=message.platform, user_id=peer_user_id,
        )
    if is_bot:
        message.sender_role = "bot"
    else:
        if await get_person(message.platform, message.sender_id) is None:
            await get_or_create_person(
                platform=message.platform, user_id=message.sender_id,
                nickname=message.sender_name, cardname=message.sender_cardname,
            )
    manager = _get_stream_manager()
    await manager.get_or_create_stream(
        stream_id=message.stream_id, platform=message.platform,
        user_id=peer_user_id if message.chat_type == "private" else message.sender_id,
        chat_type=message.chat_type,
        group_id=str(message.extra.get("group_id") or ""),
        group_name=str(message.extra.get("group_name") or ""),
    )
    return await manager.add_history_message(message)


async def get_history_anchor(
    platform: str, chat_type: str, target_id: str, before_time: float,
) -> str | None:
    """读取连接恢复前已提交的会话消息 ID，不创建聊天流。

    Args:
        platform: 平台名称。
        chat_type: group 或 private。
        target_id: 群或私聊对端 ID。
        before_time: 原始消息时间的排他上界。

    Returns:
        最后提交的消息 ID；没有已有记录时为 None。
    """
    from src.core.models.sql_alchemy import Messages
    from src.core.models.stream import ChatStream
    from src.kernel.db import QueryBuilder

    _validate_non_empty(platform, "platform")
    _validate_non_empty(target_id, "target_id")
    if chat_type not in {"group", "private"}:
        raise ValueError("chat_type 必须是 group 或 private")
    stream_id = ChatStream.generate_stream_id(
        platform=platform,
        group_id=target_id if chat_type == "group" else "",
        user_id=target_id if chat_type == "private" else "",
    )
    rows = await (
        QueryBuilder(Messages).filter(
            stream_id=stream_id, platform=platform, time__lt=before_time,
        ).order_by("-time", "-id").limit(1).all()
    )
    return str(cast(Messages, rows[0]).message_id) if rows else None


async def get_history_targets(platform: str) -> list[tuple[str, str]]:
    """读取核心库中的已有群及私聊对端，不创建流或更新活跃时间。"""
    from src.core.models.sql_alchemy import ChatStreams, PersonInfo
    from src.kernel.db import CRUDBase, QueryBuilder

    _validate_non_empty(platform, "platform")
    streams = cast(list[ChatStreams], await QueryBuilder(ChatStreams).filter(platform=platform).all())
    targets: list[tuple[str, str]] = []
    for stream in streams:
        if stream.chat_type == "group" and stream.group_id:
            targets.append(("group", stream.group_id))
        elif stream.chat_type == "private" and stream.person_id:
            person = await CRUDBase(PersonInfo).get_by(person_id=stream.person_id, platform=platform)
            if person is not None:
                targets.append(("private", person.user_id))
    return targets


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
    """按入库 ID 分页续读消息，页内返回入库正序。

    迟到历史可能具有更大的 ID 和更早的原时间。时间轴翻阅使用
    get_stream_history，而不是此接口。

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


async def get_stream_history(
    stream_id: str, limit: int = 100, offset: int = 0,
) -> list["Message"]:
    """按原始时间翻阅历史，不改变按入库顺序续读接口的语义。"""
    _validate_non_empty(stream_id, "stream_id")
    _validate_limit_offset(limit, "limit")
    _validate_limit_offset(offset, "offset")
    return await _get_stream_manager().get_stream_history(stream_id, limit, offset)


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


def clear_context(stream_id: str) -> bool:
    """清空指定流的内存上下文（仅当流已在内存中时生效）。

    将 StreamContext 的 history_messages 和 unread_messages 全部清空，
    使 Chatter 下一轮从空白上下文开始处理。若流尚未加载到内存，返回 False。
    重启后上下文会从数据库重新加载，该操作不影响持久化记录。

    Args:
        stream_id: 聊天流 ID

    Returns:
        True 表示成功清空，False 表示流不存在于内存中
    """
    _validate_non_empty(stream_id, "stream_id")
    stream = _get_stream_manager()._streams.get(stream_id)
    if stream is None:
        return False
    stream.context.history_messages.clear()
    stream.context.unread_messages.clear()
    return True


async def load_and_clear_context(stream_id: str) -> bool:
    """清空指定流的内存上下文。

    若流已在内存中，立即清空；若流不在内存中，将其加入待清空标记集，
    下次该流通过 get_or_create_stream 加载时自动应用清空。
    此调用始终是瞬时的，不会触发数据库加载，因此适合批量操作。

    Args:
        stream_id: 聊天流 ID

    Returns:
        始终返回 True
    """
    _validate_non_empty(stream_id, "stream_id")
    return await _get_stream_manager().clear_stream_context(stream_id)


def get_all_stream_ids() -> list[str]:
    """获取当前内存中所有活跃流的 ID 列表。

    Returns:
        流 ID 字符串列表
    """
    return list(_get_stream_manager()._streams.keys())


async def get_stream_ids_from_db(chat_type: str = "") -> list[str]:
    """从数据库查询流ID列表。

    Args:
        chat_type: 聊天类型（"group"/"private"），空字符串表示查询所有类型

    Returns:
        流ID列表
    """
    return await _get_stream_manager().get_stream_ids_by_chat_type(chat_type)


async def bulk_clear_streams(chat_type: str = "") -> int:
    """批量清空流上下文，持久化清空时间戳到数据库。

    通过单条 UPDATE SQL 完成，效率极高。重启 bot 后清空效果依然生效，
    因为 load_stream_context 只会加载 context_cleared_at 时间点之后的消息。

    Args:
        chat_type: 聊天类型（"group"/"private"），空字符串表示清空所有类型

    Returns:
        数据库中成功更新的流数量
    """
    return await _get_stream_manager().bulk_clear_streams(chat_type)


__all__ = [
    "API_VERSION",
    "get_or_create_stream",
    "get_stream",
    "build_stream_from_database",
    "load_stream_context",
    "add_message_to_stream",
    "add_message",
    "add_sent_message_to_history",
    "import_history_message",
    "get_history_anchor",
    "get_history_targets",
    "delete_stream",
    "get_stream_info",
    "get_stream_messages",
    "get_stream_history",
    "clear_stream_cache",
    "refresh_stream",
    "activate_stream",
    "clear_context",
    "load_and_clear_context",
    "get_all_stream_ids",
    "get_stream_ids_from_db",
    "bulk_clear_streams",
]
