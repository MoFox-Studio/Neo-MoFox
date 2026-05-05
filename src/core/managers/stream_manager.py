"""Stream Manager - 统一的聊天流管理器。

本模块提供 StreamManager，负责聊天流的完整生命周期管理：
- 从数据库构建和加载聊天流
- 统一的流创建、获取和管理
- 消息添加、序号分配
- TTL 过期消息清理
- 流实例全局单例管理

替代 MessageRetentionManager，提供更完整的流管理功能。

每个 stream_id 对应一个全局唯一的 ChatStream 实例，一旦创建即永久存在。
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any
from async_lru import alru_cache
from sqlalchemy import update as sa_update

from src.kernel.db import CRUDBase, QueryBuilder, get_db_session
from src.kernel.logger import get_logger
from src.core.config import get_core_config

if TYPE_CHECKING:
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream, StreamContext
    from src.core.models.sql_alchemy import Messages


logger = get_logger("stream_manager", display="StreamMgr")

# 含原始二进制的媒体类型，序列化入库时仅在 payload 过大时剔除 data 字段
_BINARY_MEDIA_TYPES: frozenset[str] = frozenset({"image", "emoji", "voice"})
_MAX_MEDIA_DATA_BYTES = 1024


def _get_content_size_bytes(content: Any) -> int:
    """估算内容的 UTF-8 序列化字节数。"""
    if isinstance(content, bytes):
        return len(content)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return len(str(content).encode("utf-8"))


def _strip_oversized_media_data(item: dict[str, Any]) -> dict[str, Any]:
    """移除超出阈值的媒体 data 字段，保留其余元信息。"""
    media_data = item.get("data")
    if media_data is None:
        return item

    if _get_content_size_bytes(media_data) <= _MAX_MEDIA_DATA_BYTES:
        return item

    return {key: value for key, value in item.items() if key != "data"}


def _serialize_content_for_db(content: Any) -> str:
    """将消息 content 序列化为数据库存储字符串。

    内存中的 content 字典可能包含图片/表情包/语音的原始 base64 数据（供 Chatter 多模态
    使用），但这些数据不需要持久化，持久化会造成数据库存储暴涨。本函数在序列化前检查
    image/emoji/voice 媒体项中的 ``data`` 实际大小，超过阈值时剔除该字段，其余元信息保留。
    """
    if not isinstance(content, dict):
        return str(content)

    media = content.get("media")
    if not isinstance(media, list):
        return str(content)

    stripped_media = []
    for item in media:
        if not isinstance(item, dict):
            continue

        if item.get("type") in _BINARY_MEDIA_TYPES:
            stripped_media.append(_strip_oversized_media_data(item))
            continue

        stripped_media.append(item)

    return str({**content, "media": stripped_media})


class StreamManager:
    """统一的聊天流管理器。

    负责聊天流的创建、加载和全局单例管理。
    每个 stream_id 对应一个全局唯一的 ChatStream 实例。

    Attributes:
        _streams: 全局流实例字典 (stream_id -> ChatStream)
        _stream_locks: 每个流的锁字典（用于并发控制）
        _cleanup_task_ids: 定期清理任务ID列表

    Examples:
        >>> sm = get_stream_manager()
        >>> stream = await sm.get_or_create_stream(
        ...     platform="qq",
        ...     user_id="123",
        ...     chat_type="private"
        ... )
    """

    def __init__(self) -> None:
        """初始化 StreamManager."""
        # 延迟导入避免循环依赖
        from src.core.models.sql_alchemy import ChatStreams, Messages

        self._streams_crud: CRUDBase[ChatStreams] = CRUDBase[ChatStreams](ChatStreams)
        self._messages_crud: CRUDBase[Messages] = CRUDBase[Messages](Messages)
        self._Messages = Messages
        self._ChatStreams = ChatStreams

        # 全局流实例存储 (stream_id -> ChatStream)
        # 每个 stream_id 对应一个全局唯一的 ChatStream 实例
        self._streams: dict[str, "ChatStream"] = {}

        # 每个流的锁（用于并发控制）
        self._stream_locks: dict[str, asyncio.Lock] = {}
        self._global_lock: asyncio.Lock = asyncio.Lock()

        # 清理任务ID
        self._cleanup_task_ids: list[str] = []

        logger.info("StreamManager 初始化完成")

    # ==================== Stream Creation & Retrieval ====================

    async def get_or_create_stream(
        self,
        stream_id: str = "",
        platform: str = "",
        user_id: str = "",
        group_id: str = "",
        group_name: str = "",
        chat_type: str = "private",

    ) -> "ChatStream":
        """获取现有流或创建新流。

        如果流已存在，从缓存或数据库加载并返回。
        如果流不存在，创建新的流记录并返回。

        Args:
            stream_id: 聊天流唯一标识符（如果已知）
            platform: 平台标识
            user_id: 用户ID（私聊时使用）
            group_id: 群组ID（群聊时使用）
            group_name: 群组名称（群聊时使用，用于填充 stream_name）
            chat_type: 聊天类型（private/group/discuss）

        Returns:
            ChatStream: 完整初始化的流对象

        Raises:
            ValueError: 如果既没有 user_id 也没有 group_id

        Examples:
            >>> stream = await sm.get_or_create_stream(
            ...     platform="qq",
            ...     user_id="123",
            ...     chat_type="private"
            ... )
        """
        from src.core.models.stream import ChatStream

        if not stream_id:
            # 生成 stream_id
            stream_id = ChatStream.generate_stream_id(
                platform=platform,
                user_id=user_id,
                group_id=group_id,
            )

        # 并发保护：同一个 stream_id 的创建/加载必须串行化
        lock = self._get_stream_lock(stream_id)
        async with lock:
            # 检查，避免等待锁期间已被其他协程创建
            existed = self._streams.get(stream_id)
            if existed is not None:
                logger.debug(f"获取已存在的流实例: {stream_id}")
                return existed

            # 查询数据库
            stream_record = await self._streams_crud.get_by(stream_id=stream_id)

            if stream_record:
                # 从数据库构建流
                logger.debug(f"从数据库加载流: {stream_id}")
                chat_stream = await self.build_stream_from_database(stream_id)
                if not chat_stream:
                    # 数据库记录存在但构建失败，创建新的
                    chat_stream = await self._create_new_stream(
                        stream_id=stream_id,
                        platform=platform,
                        user_id=user_id,
                        group_id=group_id,
                        group_name=group_name,
                        chat_type=chat_type,
                    )
                elif group_name and chat_stream.stream_name != group_name:
                    # 调用方传入了新名称（如群名变更、私聊显示名更新），同步到内存和数据库
                    logger.debug(
                        f"更新流名称: {stream_id}, "
                        f"旧={chat_stream.stream_name!r} -> 新={group_name!r}"
                    )
                    chat_stream.stream_name = group_name
                    _record = await self._streams_crud.get_by(stream_id=stream_id)
                    if _record:
                        await self._streams_crud.update(
                            _record.id, {"group_name": group_name}
                        )
            else:
                # 创建新流
                logger.debug(f"创建新流: {stream_id}")
                chat_stream = await self._create_new_stream(
                    stream_id=stream_id,
                    platform=platform,
                    user_id=user_id,
                    group_id=group_id,
                    group_name=group_name,
                    chat_type=chat_type,
                )

            # 存储到全局单例字典
            self._streams[stream_id] = chat_stream

            return chat_stream

    async def build_stream_from_database(self, stream_id: str) -> "ChatStream | None":
        """从数据库记录构建 ChatStream。

        Args:
            stream_id: 流ID

        Returns:
            ChatStream | None: 构建的流对象，如果未找到则返回 None

        Examples:
            >>> stream = await sm.build_stream_from_database("abc123")
        """
        from src.core.models.stream import ChatStream

        # 查询流记录
        stream_record = await self._streams_crud.get_by(stream_id=stream_id)
        if not stream_record:
            return None

        # 创建 ChatStream 对象
        chat_stream = ChatStream(
            stream_id=stream_record.stream_id,
            platform=stream_record.platform,
            chat_type=stream_record.chat_type,
            stream_name=stream_record.group_name or "",
        )
        chat_stream.create_time = stream_record.created_at
        chat_stream.last_active_time = stream_record.last_active_time

        # 加载上下文
        chat_stream.context = await self.load_stream_context(stream_id, max_messages=get_core_config().chat.max_context_size)

        logger.debug(f"从数据库构建流: {stream_id}")

        return chat_stream

    async def load_stream_context(
        self,
        stream_id: str,
        max_messages: int | None = None,
    ) -> "StreamContext":
        """从数据库加载 StreamContext。

        Args:
            stream_id: 流ID
            max_messages: 最大加载消息数，None 表示加载全部消息

        Returns:
            StreamContext: 加载的上下文对象

        Examples:
            >>> context = await sm.load_stream_context("abc123", max_messages=100)
        """
        from src.core.models.stream import StreamContext

        # 获取流配置
        stream_record = await self._streams_crud.get_by(stream_id=stream_id)
        if not stream_record:
            # 创建空上下文
            return StreamContext(
                stream_id=stream_id,
                chat_type="private",
            )

        # 查询历史消息（如果有清空时间戳，只取该时间点之后的消息）
        query = QueryBuilder(self._Messages).filter(stream_id=stream_id)
        if stream_record.context_cleared_at is not None:
            query = query.filter(time__gt=stream_record.context_cleared_at)
        query = query.order_by("-id")
        if max_messages is not None:
            query = query.limit(max_messages)
        messages_records = await query.all()

        # 转换为运行时 Message 对象
        history_messages = []
        for msg_record in reversed(messages_records):  # 按时间正序
            history_messages.append(await self._db_message_to_runtime(msg_record))  # type: ignore    

        # 创建 StreamContext
        context = StreamContext(
            stream_id=stream_id,
            chat_type=stream_record.chat_type,
            max_context_size=max_messages if max_messages else get_core_config().chat.max_context_size,  # 仅用于内存限制
            history_messages=history_messages,
        )

        logger.debug(f"加载上下文: {stream_id}, 消息数: {len(history_messages)}")

        return context

    # ==================== Message Management ====================

    async def add_message(
        self,
        message: "Message",
    ) -> "Messages":
        """添加消息到流。

        自动分配序号、设置TTL、更新缓存。

        Args:
            message: 运行时消息对象

        Returns:
            Messages: 创建的数据库消息记录

        Examples:
            >>> db_msg = await sm.add_message(message)
        """
        stream_id = message.stream_id

        # 获取流级锁
        lock = self._get_stream_lock(stream_id)
        async with lock:
            person_id = self._resolve_person_id_from_message(message)

            # 构建数据库消息数据
            message_data = {
                "message_id": message.message_id,
                "stream_id": stream_id,
                "person_id": person_id,
                "time": message.time,
                "message_type": message.message_type.value,
                "content": _serialize_content_for_db(message.content),
                "processed_plain_text": message.processed_plain_text,
                "reply_to": message.reply_to,
                "platform": message.platform,
            }

            # 持久化到数据库
            db_message = await self._messages_crud.get_by(
                message_id=message.message_id, 
                platform=message.platform,
                stream_id=stream_id
            )
            if not db_message:
                db_message = await self._messages_crud.create(message_data)

            # 更新流实例内容
            chat_stream = self._streams.get(stream_id)
            if chat_stream:
                chat_stream.context.add_unread_message(message)
                chat_stream.update_active_time()

            # 更新流活跃时间
            await self._update_stream_active_time(stream_id)

            return db_message

    async def add_sent_message_to_history(
        self,
        message: "Message",
    ) -> "Messages":
        """添加“已发送消息”到流历史消息。

        与 ``add_message`` 不同：
        - 该方法会将消息直接写入 ``history_messages``
        - 不会写入 ``unread_messages``

        Args:
            message: 运行时消息对象

        Returns:
            Messages: 创建或已存在的数据库消息记录
        """
        stream_id = message.stream_id

        lock = self._get_stream_lock(stream_id)
        async with lock:

            message_data = {
                "message_id": message.message_id,
                "stream_id": stream_id,
                "person_id": "bot",
                "time": message.time,
                "message_type": message.message_type.value,
                "content": str(message.content),
                "processed_plain_text": message.processed_plain_text,
                "reply_to": message.reply_to,
                "platform": message.platform,
            }

            db_message = await self._messages_crud.get_by(
                message_id=message.message_id,
                platform=message.platform,
                stream_id=stream_id,
            )
            if not db_message:
                db_message = await self._messages_crud.create(message_data)

            chat_stream = self._streams.get(stream_id)
            if chat_stream:
                context = chat_stream.context

                # 移除同 ID 的未读消息，避免同一条消息同时出现在 unread/history
                context.unread_messages = [
                    msg
                    for msg in context.unread_messages
                    if msg.message_id != message.message_id
                ]

                exists_in_history = any(
                    msg.message_id == message.message_id
                    for msg in context.history_messages
                )
                if not exists_in_history:
                    context.add_history_message(message)

                chat_stream.update_active_time()

            await self._update_stream_active_time(stream_id)

            return db_message

    # ==================== Stream Lifecycle ====================

    async def delete_stream(
        self,
        stream_id: str,
        delete_messages: bool = True,
    ) -> bool:
        """删除流及其消息。

        Args:
            stream_id: 流ID
            delete_messages: 是否删除关联的消息

        Returns:
            bool: 是否成功删除

        Examples:
            >>> success = await sm.delete_stream("abc123", delete_messages=True)
        """
        # 删除消息
        if delete_messages:
            messages = await QueryBuilder(self._Messages).filter(
                stream_id=stream_id
            ).all(as_dict=True)

            for msg in messages:
                await self._messages_crud.delete(msg["id"])

            logger.info(f"删除流的消息: {stream_id}, 数量: {len(messages)}")

        # 删除流记录
        stream = await self._streams_crud.get_by(stream_id=stream_id)
        if stream:
            await self._streams_crud.delete(stream.id)

        # 清理缓存
        self.clear_cache(stream_id)

        logger.info(f"删除流: {stream_id}")

        return True

    # ==================== Query & Utilities ====================

    @alru_cache(maxsize=256)
    async def get_stream_info(self, stream_id: str) -> dict[str, Any] | None:
        """获取流的综合信息。

        Args:
            stream_id: 流ID

        Returns:
            dict | None: 流信息字典，如果未找到则返回 None

        Examples:
            >>> info = await sm.get_stream_info("abc123")
        """
        stream = await self._streams_crud.get_by(stream_id=stream_id)
        if not stream:
            return None

        normalized_person_id = self._normalize_person_id(
            person_id=stream.person_id,
            platform=stream.platform,
        )
        if normalized_person_id and normalized_person_id != stream.person_id:
            await self._streams_crud.update(stream.id, {"person_id": normalized_person_id})
            stream.person_id = normalized_person_id
            logger.warning(
                f"stream_id={stream_id} 检测到原始 person_id，已自动规范化为哈希格式"
            )

        message_count = await QueryBuilder(self._Messages).filter(
            stream_id=stream_id
        ).count()

        return {
            "stream_id": stream.stream_id,
            "platform": stream.platform,
            "chat_type": stream.chat_type,
            "group_id": stream.group_id,
            "group_name": stream.group_name,
            "person_id": normalized_person_id or stream.person_id,
            "message_count": message_count,
            "last_active_time": stream.last_active_time,
            "created_at": stream.created_at,
        }

    async def get_stream_messages(
        self,
        stream_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list["Message"]:
        """获取流的消息（支持分页）。

        Args:
            stream_id: 流ID
            limit: 最大返回消息数
            offset: 跳过的消息数

        Returns:
            list[Message]: 运行时消息对象列表

        Examples:
            >>> messages = await sm.get_stream_messages("abc123", limit=50, offset=0)
        """
        messages_records = (
            await QueryBuilder(self._Messages)
            .filter(stream_id=stream_id)
            .order_by("-id")
            .limit(limit)
            .offset(offset)
            .all()
        )

        return [
            await self._db_message_to_runtime(msg) for msg in reversed(messages_records) # type: ignore
        ]

    def clear_cache(self, stream_id: str | None = None) -> None:
        """清理流实例。

        注意：由于流是全局单例，清理后下次访问会重新从数据库加载。

        Args:
            stream_id: 要清理的流ID，None 表示清理全部

        Examples:
            >>> sm.clear_cache("abc123")  # 清理特定流
            >>> sm.clear_cache()  # 清理全部
        """
        if stream_id:
            self._streams.pop(stream_id, None)
            self._stream_locks.pop(stream_id, None)
            logger.debug(f"清理流实例: {stream_id}")
        else:
            self._streams.clear()
            self._stream_locks.clear()
            logger.debug("清理全部流实例")

    async def refresh_stream(self, stream_id: str) -> "ChatStream | None":
        """强制从数据库刷新流。

        Args:
            stream_id: 流ID

        Returns:
            ChatStream | None: 刷新后的流，如果未找到则返回 None

        Examples:
            >>> stream = await sm.refresh_stream("abc123")
        """
        # 清理现有实例
        self._streams.pop(stream_id, None)

        # 从数据库重新构建
        stream = await self.build_stream_from_database(stream_id)

        if stream:
            self._streams[stream_id] = stream
            logger.debug(f"刷新流: {stream_id}")

        return stream
    
    async def activate_stream(self, stream_id: str) -> "ChatStream | None":
        """激活流，更新其最后活跃时间。

        Args:
            stream_id: 流ID

        Examples:
            >>> await sm.activate_stream("abc123")

        """
        stream = self._streams.get(stream_id)
        if stream is None:
            stream_record = await self._streams_crud.get_by(stream_id=stream_id)
            if not stream_record:
                return None
            stream = await self.build_stream_from_database(stream_id)
            if stream:
                self._streams[stream_id] = stream

        if stream:
            stream.update_active_time()
            await self._update_stream_active_time(stream_id)

        return stream

    async def clear_stream_context(self, stream_id: str) -> bool:
        """清空流的内存上下文，并将清空时间戳持久化到数据库。

        若流已在内存中，立即清空其历史消息与未读消息；
        同时将 ChatStreams.context_cleared_at 更新为当前时间，使清空效果在
        重启后依然生效（重启后加载消息时只取该时间点以后的消息）。

        若流在数据库中不存在（该流从未有过真实对话），则内存清空仍然执行，
        但 DB 无需更新（该流没有历史消息可过滤）。

        Args:
            stream_id: 流ID

        Returns:
            始终返回 True

        Examples:
            >>> cleared = await sm.clear_stream_context("abc123")
        """
        clear_time = time.time()

        # 清空内存中的流
        stream = self._streams.get(stream_id)
        if stream is not None:
            stream.context.history_messages.clear()
            stream.context.unread_messages.clear()
            logger.debug(f"已直接清空内存流上下文: {stream_id}")

        # 持久化清空时间戳到 DB（仅在有 DB 记录时更新）
        record = await self._streams_crud.get_by(stream_id=stream_id)
        if record:
            await self._streams_crud.update(record.id, {"context_cleared_at": clear_time})
            logger.debug(f"已持久化清空时间戳: {stream_id}")

        return True

    async def bulk_clear_streams(self, chat_type: str = "") -> int:
        """批量清空流上下文，持久化清空时间戳到数据库。

        此操作通过单条 UPDATE SQL 完成 DB 持久化，效率极高，
        适用于 /清空上下文 私、/清空上下文 群、/清空上下文 all 等批量命令。

        Args:
            chat_type: 聊天类型（"group"/"private"），空字符串表示清空所有类型

        Returns:
            数据库中成功更新的流数量

        Examples:
            >>> count = await sm.bulk_clear_streams("private")
        """
        clear_time = time.time()

        # 清空内存中符合条件的流
        for stream in self._streams.values():
            if not chat_type or stream.chat_type == chat_type:
                stream.context.history_messages.clear()
                stream.context.unread_messages.clear()

        # 批量更新 DB 中的 context_cleared_at
        async with get_db_session() as session:
            stmt = sa_update(self._ChatStreams).values(context_cleared_at=clear_time)
            if chat_type:
                stmt = stmt.where(self._ChatStreams.chat_type == chat_type)
            result = await session.execute(stmt)
            await session.commit()
            count = result.rowcount  # type: ignore[union-attr]

        logger.debug(f"批量清空上下文，类型={chat_type or '全部'}，影响 {count} 条记录")
        return count

    async def get_stream_ids_by_chat_type(self, chat_type: str = "") -> list[str]:
        """从数据库查询流ID列表。

        Args:
            chat_type: 聊天类型（"group"/"private"），空字符串返回所有类型

        Returns:
            流ID列表

        Examples:
            >>> ids = await sm.get_stream_ids_by_chat_type("private")
        """
        if chat_type:
            records = await self._streams_crud.get_multi(limit=10000, chat_type=chat_type)
        else:
            records = await self._streams_crud.get_multi(limit=10000)
        return [r.stream_id for r in records]

    # ==================== Private Helper Methods ====================

    async def _create_new_stream(
        self,
        platform: str,
        chat_type: str = "private",
        user_id: str = "",
        group_id: str = "",
        group_name: str = "",
        stream_id: str = "",
    ) -> "ChatStream":
        """创建新流。

        Args:
            platform: 平台标识
            chat_type: 聊天类型
            user_id: 用户ID
            group_id: 群组ID
            group_name: 群组名称（群聊时使用）

        Returns:
            ChatStream: 新创建的流对象
        """
        from src.core.models.stream import ChatStream
        from src.core.managers.adapter_manager import get_adapter_manager
        from src.core.utils.user_query_helper import get_user_query_helper

        # 生成 stream_id
        if not stream_id:
            stream_id = ChatStream.generate_stream_id(
                platform=platform,
                user_id=user_id,
                group_id=group_id,
            )

        if user_id:
            person_id = get_user_query_helper().generate_person_id(
                platform=platform,
                user_id=user_id,
            )
        else:
            # 群聊流无特定用户，使用 group_id 生成 person_id 占位
            person_id = get_user_query_helper().generate_person_id(
                platform=platform,
                user_id=group_id or "unknown",
            )

        # 创建数据库记录
        now = time.time()
        stream_data = {
            "stream_id": stream_id,
            "person_id": person_id,
            "platform": platform,
            "group_id": group_id or None,
            "group_name": group_name or None,
            "chat_type": chat_type,
            "created_at": now,
            "last_active_time": now,
        }

        await self._streams_crud.create(stream_data)

        bot_id = ""
        bot_nickname = ""
        try:
            bot_info = await get_adapter_manager().get_bot_info_by_platform(platform)
            if bot_info:
                bot_id = str(bot_info.get("bot_id", "") or "")
                bot_nickname = str(bot_info.get("bot_name", "") or "")
        except Exception as e:
            logger.warning(f"获取 Bot 信息失败，将使用空值: platform={platform}, error={e}")

        # stream_name：由调用方按聊天类型传入（群聊=群名，私聊=xxx的私聊）
        stream_name = group_name or ""

        # 创建 ChatStream 对象
        chat_stream = ChatStream(
            stream_id=stream_id,
            platform=platform,
            chat_type=chat_type,
            bot_id=bot_id,
            bot_nickname=bot_nickname,
            stream_name=stream_name,
        )
        chat_stream.create_time = now
        chat_stream.last_active_time = now

        logger.info(f"创建新流: {stream_id}, 类型: {chat_type}")

        return chat_stream

    async def _update_stream_active_time(self, stream_id: str) -> None:
        """更新流的最后活跃时间。

        Args:
            stream_id: 流ID
        """
        stream = await self._streams_crud.get_by(stream_id=stream_id)
        if stream:
            await self._streams_crud.update(
                stream.id, {"last_active_time": time.time()}
            )

    def _get_stream_lock(self, stream_id: str) -> asyncio.Lock:
        """获取流级锁。

        Args:
            stream_id: 流ID

        Returns:
            asyncio.Lock: 流的锁
        """
        if stream_id not in self._stream_locks:
            self._stream_locks[stream_id] = asyncio.Lock()
        return self._stream_locks[stream_id]

    async def _db_message_to_runtime(self, db_message: "Messages") -> "Message":
        """将数据库消息转换为运行时消息。

        Args:
            db_message: 数据库消息对象

        Returns:
            Message: 运行时消息对象
        """
        from src.core.models.message import Message, MessageType
        from src.core.utils.user_query_helper import get_user_query_helper
        from src.core.managers import get_stream_manager

        stream_info = await get_stream_manager().get_stream_info(db_message.stream_id)

        sender_id = "system"
        sender_name = "未知用户"
        sender_cardname = ""

        if db_message.person_id:
            try:
                person = await get_user_query_helper().person_crud.get_by(
                    person_id=db_message.person_id
                )
                if person:
                    sender_id = str(person.user_id or person.person_id or "")
                    sender_name = person.nickname or sender_id or "未知用户"
                    sender_cardname = person.cardname or ""
                else:
                    sender_id = db_message.person_id
                    sender_name = "未知用户"
            except Exception as e:
                logger.warning(
                    f"恢复发送者信息失败: person_id={db_message.person_id}, error={e}"
                )
                sender_id = db_message.person_id
                sender_name = "未知用户"

        # person_id == "bot" 表示 Bot 发送的消息
        if db_message.person_id == "bot" and db_message.platform:
            try:
                from src.core.managers.adapter_manager import get_adapter_manager

                bot_info = await get_adapter_manager().get_bot_info_by_platform(
                    db_message.platform
                )
                if bot_info:
                    bot_id = str(bot_info.get("bot_id", "") or "")
                    bot_nickname = str(bot_info.get("bot_name", "") or "")
                    sender_id = bot_id or "bot"
                    sender_name = bot_nickname or "Bot"
                    sender_cardname = bot_nickname or ""
            except Exception as e:
                logger.warning(
                    f"恢复 Bot 发送者信息失败: platform={db_message.platform}, error={e}"
                )
                sender_id = "bot"
                sender_name = "Bot"

        normalized_plain_text = db_message.processed_plain_text
        if normalized_plain_text is None:
            normalized_plain_text = str(db_message.content)
        
        return Message(
            message_id=db_message.message_id,
            time=db_message.time,
            reply_to=db_message.reply_to,
            content=db_message.content,
            processed_plain_text=normalized_plain_text,
            message_type=MessageType(db_message.message_type),
            sender_id=sender_id,
            sender_name=sender_name,
            sender_cardname=sender_cardname,
            platform=db_message.platform or "",
            chat_type=stream_info.get("chat_type", "private") if stream_info else "private",
            stream_id=db_message.stream_id,
            raw_data=None,
            extra={},
        )

    def _resolve_person_id_from_message(self, message: "Message") -> str | None:
        """从运行时消息解析 person_id，用于数据库持久化。"""
        from src.core.utils.user_query_helper import get_user_query_helper

        direct_person_id = getattr(message, "person_id", None)
        if isinstance(direct_person_id, str) and direct_person_id:
            normalized_direct_person_id = self._normalize_person_id(
                person_id=direct_person_id,
                platform=str(message.platform or ""),
            )
            if normalized_direct_person_id:
                return normalized_direct_person_id

        extra_person_id = message.extra.get("person_id")
        if isinstance(extra_person_id, str) and extra_person_id:
            normalized_extra_person_id = self._normalize_person_id(
                person_id=extra_person_id,
                platform=str(message.platform or ""),
            )
            if normalized_extra_person_id:
                return normalized_extra_person_id

        sender_id = str(message.sender_id or "")
        platform = str(message.platform or "")
        if not sender_id or not platform:
            return None

        return get_user_query_helper().generate_person_id(platform, sender_id)

    def _normalize_person_id(self, person_id: str, platform: str) -> str | None:
        """将 person_id 规范化为哈希格式。"""
        from src.core.utils.user_query_helper import get_user_query_helper

        if not person_id:
            return None

        if ":" not in person_id:
            return person_id

        raw_platform, raw_user_id = person_id.split(":", 1)
        if not raw_platform or not raw_user_id:
            return None

        if platform and raw_platform != platform:
            return None

        return get_user_query_helper().generate_person_id(raw_platform, raw_user_id)


# 全局单例
_global_stream_manager: StreamManager | None = None


def get_stream_manager() -> StreamManager:
    """获取全局 StreamManager 单例。

    Returns:
        StreamManager: 全局 StreamManager 实例

    Examples:
        >>> sm = get_stream_manager()
        >>> stream = await sm.get_or_create_stream(platform="qq", user_id="123")
    """
    global _global_stream_manager
    if _global_stream_manager is None:
        _global_stream_manager = StreamManager()
    return _global_stream_manager


__all__ = [
    "StreamManager",
    "get_stream_manager",
]
