"""消息相关数据模型。

本模块提供 Message 类及相关数据模型，表示聊天消息的完整信息。
参考 old/common/data_models/database_data_model.py 中的 DatabaseMessages 实现。
"""

from datetime import datetime
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """消息类型枚举。

    定义不同类型的消息内容。
    """

    TEXT = "text"  # 纯文本消息
    IMAGE = "image"  # 图片消息
    VOICE = "voice"  # 语音消息
    VIDEO = "video"  # 视频消息
    FILE = "file"  # 文件消息
    LOCATION = "location"  # 位置消息
    EMOJI = "emoji"  # 表情消息
    NOTICE = "notice"  # 通知消息
    UNKNOWN = "unknown"  # 未知类型


class Message:
    """消息类。

    表示一条聊天消息的完整信息，包含内容、发送者、时间戳等。
    参考旧版 DatabaseMessages 的字段设计，但简化为运行时使用。

    Attributes:
        # 基础字段
        message_id: 消息唯一标识符
        time: 消息时间戳
        reply_to: 回复的目标消息 ID

        # 内容字段
        content: 消息内容（文本或结构化数据）
        processed_plain_text: 处理后的纯文本内容

        # 类型字段
        message_type: 消息类型

        # 用户信息
        sender_id: 发送者 ID（平台原始 ID）
        sender_name: 发送者名称
        sender_cardname: 发送者备注名或群名片
        sender_role: 发送者在群组中的角色（owner/operator/member/bot/other）

        # 聊天上下文
        platform: 消息来源平台
        chat_type: 聊天类型（private/group/discuss）
        stream_id: 所属聊天会话 ID

        # 运行时字段
        raw_data: 原始平台数据
        extra: 额外元数据

    Examples:
        >>> message = Message(
        ...     message_id="msg_001",
        ...     content="你好",
        ...     sender_id="user_123",
        ...     sender_name="Alice",
        ...     platform="test"
        ... )
    """

    def __init__(
        self,
        # 基础字段
        message_id: str = "",
        time: datetime | float | None = None,
        reply_to: str | None = None,
        # 内容字段
        content: str | Any = "",
        processed_plain_text: str | None = None,
        # 类型字段
        message_type: MessageType = MessageType.TEXT,
        # 用户信息
        sender_id: str = "",
        sender_name: str = "",
        sender_cardname: str | None = None,
        sender_role: str | None = None,
        # 聊天上下文
        platform: str = "",
        chat_type: str = "",
        stream_id: str = "",
        # 运行时字段
        raw_data: Any = None,
        **extra: Any,
    ) -> None:
        """初始化消息对象。

        Args:
            message_id: 消息唯一标识符
            time: 消息时间戳
            stream_id: 所属聊天会话 ID
            reply_to: 回复的目标消息 ID
            content: 消息内容
            processed_plain_text: 处理后的纯文本内容
            message_type: 消息类型
            sender_id: 发送者 ID（平台原始 ID）
            sender_name: 发送者名称
            sender_cardname: 发送者备注名或群名片
            sender_role: 发送者在群组中的角色（owner/operator/member/bot/other）
            platform: 消息来源平台
            chat_type: 聊天类型
            raw_data: 原始平台数据
            **extra: 额外元数据
        """
        # 基础字段
        self.message_id = message_id
        self.time = time if time is not None else datetime.now()
        if isinstance(self.time, datetime):
            self.time = self.time.timestamp()
        self.reply_to = reply_to

        # 内容字段
        self.content = content
        self.processed_plain_text = processed_plain_text

        # 类型字段
        self.message_type = message_type

        # 用户信息
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.sender_cardname = sender_cardname
        self.sender_role = sender_role

        # 聊天上下文
        self.platform = platform
        self.chat_type = chat_type
        self.stream_id = stream_id
        # 运行时字段
        self.raw_data = raw_data
        self.extra = extra

    def __repr__(self) -> str:
        """返回消息的字符串表示。"""
        return (
            f"Message(id={self.message_id}, "
            f"sender={self.sender_name}, "
            f"type={self.message_type.value}, "
            f"content={str(self.content)[:50]})"
        )

    def to_dict(self) -> dict[str, Any]:
        """将消息转换为字典。

        Returns:
            dict[str, Any]: 包含所有消息字段的字典
        """
        return {
            # 基础字段
            "message_id": self.message_id,
            "time": self.time,
            "reply_to": self.reply_to,
            # 内容字段
            "content": self.content,
            "processed_plain_text": self.processed_plain_text,
            # 类型字段
            "message_type": self.message_type.value,
            # 用户信息
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sender_cardname": self.sender_cardname,
            "sender_role": self.sender_role,
            # 聊天上下文
            "platform": self.platform,
            "chat_type": self.chat_type,
            "stream_id": self.stream_id,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """从字典创建消息对象。

        Args:
            data: 包含消息数据的字典

        Returns:
            Message: 消息对象

        Examples:
            >>> data = {
            ...     "message_id": "msg_001",
            ...     "content": "你好",
            ...     "sender_id": "user_123",
            ...     "sender_name": "Alice",
            ...     "platform": "test"
            ... }
            >>> message = Message.from_dict(data)
        """
        # 处理 message_type
        message_type_str = data.get("message_type", "text")
        try:
            message_type = MessageType(message_type_str)
        except ValueError:
            message_type = MessageType.TEXT

        # 提取额外字段
        extra_fields = {
            k: v
            for k, v in data.items()
            if k not in {
                "message_id",
                "time",
                "stream_id",
                "reply_to",
                "content",
                "processed_plain_text",
                "message_type",
                "sender_id",
                "sender_name",
                "sender_cardname",
                "sender_role",
                "platform",
                "chat_type",
            }
        }

        return cls(
            message_id=data.get("message_id", ""),
            time=data.get("time"),
            stream_id=data.get("stream_id", ""),
            reply_to=data.get("reply_to"),
            content=data.get("content", ""),
            processed_plain_text=data.get("processed_plain_text"),
            message_type=message_type,
            sender_id=data.get("sender_id", ""),
            sender_name=data.get("sender_name", ""),
            sender_cardname=data.get("sender_cardname"),
            sender_role=data.get("sender_role"),
            platform=data.get("platform", ""),
            chat_type=data.get("chat_type", ""),
            raw_data=None,
            **extra_fields,
        )
