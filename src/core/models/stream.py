"""Chat/stream related models.

本模块提供聊天流相关的数据模型，包括 StreamContext 和 ChatStream 类。
"""

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.models.message import Message


@dataclass
class StreamContext:
    """聊天流上下文信息。

    参考 old/common/data_models/message_manager_data_model.py 中的 StreamContext 实现。
    简化版，移除数据库相关功能，保留核心上下文管理。

    Attributes:
        stream_id: 聊天流唯一标识符
        chat_type: 聊天类型（private/group/discuss）
        chat_mode: 聊天模式（focus/normal/proactive/priority）
        max_context_size: 最大上下文大小
        unread_messages: 未读消息列表
        history_messages: 历史消息列表
        is_active: 是否活跃
        is_chatter_processing: Chatter 是否正在处理
        message_cache: 消息缓存队列
        is_cache_enabled: 是否启用消息缓存
    """

    stream_id: str
    chat_type: str = "private"  # private/group/discuss
    max_context_size: int = 100
    unread_messages: list["Message"] = field(default_factory=list)
    history_messages: list["Message"] = field(default_factory=list)
    is_active: bool = True
    is_chatter_processing: bool = False

    # 当前消息
    current_message: "Message | None" = None
    triggering_user_id: str | None = None
    processing_message_id: str | None = None

    # 消息缓存系统
    message_cache: deque["Message"] = field(default_factory=deque)
    is_cache_enabled: bool = False

    # 流循环任务引用
    stream_loop_task: asyncio.Task | None = field(default=None, repr=False)

    def add_unread_message(self, message: "Message") -> None:
        """添加未读消息。

        Args:
            message: 消息对象
        """
        self.unread_messages.append(message)

    def add_history_message(self, message: "Message") -> None:
        """添加历史消息。

        Args:
            message: 消息对象
        """
        self.history_messages.append(message)
        # 限制历史消息大小
        if len(self.history_messages) > self.max_context_size:
            self.history_messages = self.history_messages[-self.max_context_size :]

    def check_types(self, types: list[str]) -> bool:
        """检查当前消息是否支持指定的类型。

        根据 Message 的 extra 字段中的 format_info.accept_format 检查类型支持。

        Args:
            types: 需要检查的消息类型列表，如 ["text", "image", "emoji"]

        Returns:
            bool: 如果消息支持所有指定的类型则返回 True，否则返回 False

        Examples:
            >>> context.check_types(["text", "image"])
            True
        """
        if not self.current_message:
            return False

        if not types:
            # 如果没有指定类型要求，默认为支持
            return True

        # 从 extra 字段中获取 format_info
        format_info = self.current_message.extra.get("format_info", {})
        accept_format = format_info.get("accept_format", [])

        # 确保 accept_format 是列表类型
        if isinstance(accept_format, str):
            accept_format = [accept_format]
        elif not isinstance(accept_format, list):
            accept_format = (
                list(accept_format) if hasattr(accept_format, "__iter__") else []
            )

        # 如果没有 accept_format，默认支持所有类型
        if not accept_format:
            return True

        # 检查所有请求的类型是否都被支持
        for requested_type in types:
            if requested_type not in accept_format:
                return False

        return True

    def flush_unreads_to_history(self) -> list["Message"]:
        """将未读消息flush到历史消息列表。

        Returns:
            list[Message]: 已flush的消息列表

        Examples:
            >>> flushed = context.flush_unreads_to_history()
            >>> print(f"Flushed {len(flushed)} messages")
        """
        if not self.unread_messages:
            return []

        flushed = list(self.unread_messages)  # Copy
        for msg in flushed:
            self.add_history_message(msg)

        self.unread_messages.clear()
        return flushed


class ChatStream:
    """聊天流对象，存储一个完整的聊天上下文。

    参考 old/chat/message_receive/chat_stream.py 中的 ChatStream 实现。
    简化版，移除数据库相关功能。

    Attributes:
        stream_id: 聊天流唯一标识符（SHA-256 哈希）
        platform: 平台标识
        bot_id: 机器人 ID
        bot_nickname: 机器人昵称
        message: 初始消息
        context: 聊天流上下文
        create_time: 创建时间
        last_active_time: 最后活跃时间

    Examples:
        >>> stream = ChatStream(
        ...     stream_id="abc123",
        ...     platform="qq",
        ...     message=message
        ... )
    """

    def __init__(
        self,
        stream_id: str,
        platform: str = "",
        chat_type: str = "private",
        bot_id: str = "",
        bot_nickname: str = "",
    ) -> None:
        """初始化聊天流。

        Args:
            stream_id: 聊天流唯一标识符
            platform: 平台标识
            chat_type: 聊天类型（private/group/discuss）
            bot_id: 机器人 ID
            bot_nickname: 机器人昵称
        """
        self.stream_id = stream_id
        self.platform = platform
        self.chat_type = chat_type
        self.bot_id = bot_id
        self.bot_nickname = bot_nickname
        self.create_time = time.time()
        self.last_active_time = time.time()

        # 初始化 StreamContext
        self.context: StreamContext = StreamContext(
            stream_id=stream_id,
            chat_type=chat_type,
        )

    def update_active_time(self) -> None:
        """更新最后活跃时间。"""
        self.last_active_time = time.time()

    def get_raw_id(self) -> str:
        """获取原始的、未哈希的聊天流ID字符串。

        Returns:
            str: 原始 ID 字符串，格式为 "platform:stream_id:type"
        """
        # 从 stream_id 反向推导不太可能，返回哈希值
        # 实际使用时应该在外部保存原始 ID
        return f"{self.platform}:{self.stream_id}:{self.chat_type}"

    async def set_context(self, message: "Message") -> None:
        """设置聊天消息上下文。

        Args:
            message: 消息对象
        """
        self.message = message
        self.context.current_message = message
        self.update_active_time()

    @staticmethod
    @lru_cache(maxsize=10000)
    def _generate_stream_id_cached(key: str) -> str:
        """缓存的 stream_id 生成（内部使用）。

        Args:
            key: 原始键

        Returns:
            str: SHA-256 哈希值
        """
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def generate_stream_id(platform: str, user_id: str = "", group_id: str = "") -> str:
        """生成聊天流唯一 ID。

        使用 SHA-256 哈希生成唯一标识符。

        Args:
            platform: 平台标识
            user_id: 用户 ID（私聊时使用）
            group_id: 群组 ID（群聊时使用）

        Returns:
            str: SHA-256 哈希的 stream_id

        Raises:
            ValueError: 如果既没有 user_id 也没有 group_id

        Examples:
            >>> ChatStream.generate_stream_id("qq", user_id="123")
            "abc123..."
            >>> ChatStream.generate_stream_id("qq", group_id="456")
            "def456..."
        """
        if not user_id and not group_id:
            raise ValueError("user_id 或 group_id 必须提供至少一个")

        if group_id:
            key = f"{platform}_{group_id}"
        else:
            key = f"{platform}_{user_id}_private"

        return ChatStream._generate_stream_id_cached(key)
