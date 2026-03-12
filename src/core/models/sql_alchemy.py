"""SQLAlchemy数据库模型定义

本文件只包含纯模型定义，使用SQLAlchemy 2.0的Mapped类型注解风格。
引擎和会话管理已移至core/engine.py和core/session.py。

支持的数据库类型：
- SQLite: 使用 Text 类型
- PostgreSQL: 使用 Text 类型（PostgreSQL 的 Text 类型性能与 VARCHAR 相当）

所有模型使用统一的类型注解风格：
    field_name: Mapped[PyType] = mapped_column(Type, ...)

这样IDE/Pylance能正确推断实例属性类型。
"""

import datetime

from datetime import timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

# 创建基类
Base = declarative_base()


# 数据库兼容的字段类型辅助函数
def get_string_field(*_: Any, **kwargs: Any) -> Text:
    """
    返回字符串字段类型（统一使用 Text）

    Text 类型适用于所有数据库：
    - PostgreSQL: Text 性能与 VARCHAR 相当
    - SQLite: Text 无长度限制

    Args:
        *_: 保留位置参数以兼容旧调用方式
        **kwargs: 传递给 Text 的额外参数

    Returns:
        SQLAlchemy Text 类型
    """
    # 统一使用 Text，避免在模块导入时访问配置
    return Text(**kwargs)


class ChatStreams(Base):
    """聊天流模型 - 管理活跃聊天会话"""

    __tablename__ = "chat_streams"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 唯一标识
    stream_id: Mapped[str] = mapped_column(
        get_string_field(64),
        nullable=False,
        unique=True,
        index=True,
        comment="聊天流唯一标识"
    )

    # 关联用户（通过 person_id）
    person_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        index=True,
        comment="关联的用户person_id（外键目标：PersonInfo.person_id）"
    )

    # 聊天上下文信息
    platform: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        comment="平台标识"
    )
    group_id: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        index=True,
        comment="群组ID（私聊时为NULL）"
    )
    group_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="群组名称"
    )
    chat_type: Mapped[str] = mapped_column(
        get_string_field(20),
        nullable=False,
        comment="聊天类型：private/group/discuss"
    )

    # 时间管理
    created_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="聊天流创建时间"
    )
    last_active_time: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        index=True,
        comment="最后活跃时间"
    )

    __table_args__ = (
        Index("idx_chatstreams_person_id", "person_id"),
        Index("idx_chatstreams_platform_group", "platform", "group_id"),
        Index("idx_chatstreams_last_active", "last_active_time"),
    )


class LLMUsage(Base):
    """LLM使用记录模型"""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 模型信息
    model_name: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        comment="模型名称"
    )
    model_assign_name: Mapped[str] = mapped_column(
        get_string_field(100),
        comment="模型分配名称"
    )
    model_api_provider: Mapped[str] = mapped_column(
        get_string_field(100),
        comment="API提供商"
    )

    # 请求信息
    user_id: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        comment="用户ID"
    )
    request_type: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        comment="请求类型"
    )
    endpoint: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="API端点"
    )

    # Token 统计
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)

    # 成本与性能
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    time_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 状态
    status: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        index=True,
        default=datetime.datetime.now
    )

    __table_args__ = (
        Index("idx_llmusage_timestamp", "timestamp"),
        Index("idx_llmusage_model_name", "model_name"),
        Index("idx_llmusage_user_timestamp", "user_id", "timestamp"),
        Index("idx_llmusage_status_timestamp", "status", "timestamp"),
    )


class Messages(Base):
    """消息模型 - 短期上下文存储"""

    __tablename__ = "messages"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 唯一标识
    message_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        unique=True,
        index=True,
        comment="平台原始消息ID"
    )

    # 关联信息
    stream_id: Mapped[str] = mapped_column(
        get_string_field(64),
        nullable=False,
        index=True,
        comment="所属聊天流ID"
    )
    person_id: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        index=True,
        comment="发送者的person_id（系统消息可能为NULL）"
    )

    # 时间与顺序
    time: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        index=True,
        comment="消息时间戳（Unix timestamp）"
    )

    # 消息内容
    message_type: Mapped[str] = mapped_column(
        get_string_field(20),
        nullable=False,
        comment="消息类型：text/image/voice/video/file等"
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="消息原始内容"
    )
    processed_plain_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="处理后的纯文本（用于LLM输入）"
    )

    # 回复关系
    reply_to: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        comment="回复的消息ID"
    )

    # 平台信息（冗余，便于查询）
    platform: Mapped[str | None] = mapped_column(
        get_string_field(50),
        nullable=True,
        comment="平台标识（冗余，便于按平台查询）"
    )

    __table_args__ = (
        Index("idx_messages_stream_time", "stream_id", "time"),
        Index("idx_messages_person_id", "person_id"),
    )


class ActionRecords(Base):
    """动作记录模型"""

    __tablename__ = "action_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 标识
    action_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        index=True,
        comment="动作唯一标识"
    )

    # 关联
    stream_id: Mapped[str] = mapped_column(
        get_string_field(64),
        nullable=False,
        index=True,
        comment="关联的聊天流ID"
    )
    person_id: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        comment="触发动作的用户person_id"
    )

    # 时间
    time: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        index=True,
        comment="动作发生时间"
    )

    # 动作内容
    action_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="动作名称"
    )
    action_data: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="动作数据（JSON格式）"
    )

    # 执行状态
    action_done: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="动作是否完成"
    )
    action_build_into_prompt: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否将动作结果构建到Prompt中"
    )
    action_prompt_display: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="动作在Prompt中的显示文本"
    )

    __table_args__ = (
        Index("idx_actionrecords_action_id", "action_id"),
        Index("idx_actionrecords_stream_id", "stream_id"),
        Index("idx_actionrecords_time", "time"),
        Index("idx_actionrecords_stream_time", "stream_id", "time"),
    )


class Images(Base):
    """图像信息模型"""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str] = mapped_column(get_string_field(500), nullable=False, unique=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    vlm_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_images_path", "path"),
    )


class ImageDescriptions(Base):
    """图像描述信息模型"""

    __tablename__ = "image_descriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    image_description_hash: Mapped[str] = mapped_column(get_string_field(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("idx_imagedesc_hash", "image_description_hash"),)


class OnlineTime(Base):
    """在线时长记录模型"""

    __tablename__ = "online_time"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False, default=str(datetime.datetime.now))
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    start_timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, default=datetime.datetime.now)
    end_timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)

    __table_args__ = (Index("idx_onlinetime_end_timestamp", "end_timestamp"),)


class PersonInfo(Base):
    """统一用户信息模型 - 跨平台用户身份中心"""

    __tablename__ = "person_info"

    # 主键与唯一标识
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        unique=True,
        index=True,
        comment="全局唯一用户标识，格式：platform:user_id"
    )

    # 平台与身份信息
    platform: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        index=True,
        comment="平台标识（qq/wechat/dingtalk等）"
    )
    user_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        index=True,
        comment="平台内部用户ID"
    )

    # 名称信息（冗余存储，快速访问）
    nickname: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="用户昵称"
    )
    cardname: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="群名片或备注名"
    )

    # 记忆与关系字段
    impression: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Bot对用户的长期印象"
    )
    short_impression: Mapped[str | None] = mapped_column(
        get_string_field(500),
        nullable=True,
        comment="简短印象摘要"
    )
    points: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="用户特征点（JSON格式存储）"
    )
    info_list: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="用户信息列表（JSON格式）"
    )

    # 时间字段（统一为 float Unix 时间戳）
    first_interaction: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="首次交互时间（Unix timestamp）"
    )
    last_interaction: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        index=True,
        comment="最后交互时间（Unix timestamp）"
    )
    interaction_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="交互次数统计"
    )

    # 关系评估
    attitude: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=50,
        comment="关系态度评分（0-100）"
    )

    # 元数据
    created_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="记录创建时间（Unix timestamp）"
    )
    updated_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="记录最后更新时间（Unix timestamp）"
    )

    __table_args__ = (
        Index("idx_personinfo_platform_user", "platform", "user_id"),
        Index("idx_personinfo_last_interaction", "last_interaction"),
        Index("idx_personinfo_attitude", "attitude"),
    )

class BanUser(Base):
    """被禁用用户模型

    使用 SQLAlchemy 2.0 类型标注写法，方便静态类型检查器识别实际字段类型，
    避免在业务代码中对属性赋值时报 `Column[...]` 不可赋值的告警。
    """

    __tablename__ = "ban_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        comment="平台标识"
    )
    user_id: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        comment="用户ID"
    )
    violation_num: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="违规次数"
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="封禁原因"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.datetime.now,
        comment="创建时间"
    )

    __table_args__ = (
        Index("idx_banuser_platform_user_id", "platform", "user_id"),
    )


class PermissionNodes(Base):
    """权限节点模型"""

    __tablename__ = "permission_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_name: Mapped[str] = mapped_column(
        get_string_field(255),
        nullable=False,
        unique=True,
        comment="权限节点名称"
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="权限描述"
    )
    plugin_name: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        comment="所属插件"
    )
    default_granted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="默认是否授权"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(timezone.utc),
        nullable=False,
        comment="创建时间"
    )

    __table_args__ = (
        Index("idx_permission_plugin", "plugin_name"),
    )


class UserPermissions(Base):
    """用户权限模型

    保留用于向后兼容，但建议使用新的 PermissionGroups 和 CommandPermissions 模型。
    """

    __tablename__ = "user_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(
        get_string_field(50),
        nullable=False,
        comment="平台标识"
    )
    user_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        comment="用户ID"
    )
    permission_node: Mapped[str] = mapped_column(
        get_string_field(255),
        nullable=False,
        comment="权限节点"
    )
    granted: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="是否授权"
    )
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(timezone.utc),
        nullable=False,
        comment="授权时间"
    )
    granted_by: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        comment="授权人"
    )

    __table_args__ = (
        Index("idx_user_platform_id", "platform", "user_id"),
        Index("idx_user_permission", "platform", "user_id", "permission_node"),
    )


class PermissionGroups(Base):
    """权限组模型 - 存储用户的基本权限组级别

    用于管理用户的基础权限组（owner/operator/user/guest）。
    配合 CommandPermissions 实现细粒度的权限控制。
    """

    __tablename__ = "permission_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 用户标识（使用 person_id 格式）
    person_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        unique=True,
        index=True,
        comment="用户ID，格式：platform:user_id 的哈希值"
    )

    # 权限组级别
    permission_level: Mapped[str] = mapped_column(
        get_string_field(20),
        nullable=False,
        default="user",
        comment="权限组级别：owner/operator/user/guest"
    )

    # 时间戳（使用 float Unix 时间戳）
    created_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="设置时间（Unix timestamp）"
    )
    updated_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="更新时间（Unix timestamp）"
    )

    # 元数据
    granted_by: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        comment="授权人person_id"
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="权限变更原因"
    )

    __table_args__ = (
        Index("idx_permission_groups_person_id", "person_id"),
        Index("idx_permission_groups_level", "permission_level"),
    )


class CommandPermissions(Base):
    """命令权限覆盖模型 - 存储用户对特定命令的权限覆盖

    用于实现命令级的权限覆盖，允许在不改变用户权限组的前提下，
    单独授予或禁止某个用户执行特定命令的权限。
    """

    __tablename__ = "command_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 用户标识
    person_id: Mapped[str] = mapped_column(
        get_string_field(100),
        nullable=False,
        index=True,
        comment="用户ID，格式：platform:user_id 的哈希值"
    )

    # 命令标识
    command_signature: Mapped[str] = mapped_column(
        get_string_field(255),
        nullable=False,
        index=True,
        comment="命令组件签名，格式：plugin_name:command:command_name"
    )

    # 权限覆盖
    granted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="是否授权执行该命令（True=允许，False=禁止）"
    )

    # 时间戳（使用 float Unix 时间戳）
    created_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="创建时间（Unix timestamp）"
    )
    updated_at: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="更新时间（Unix timestamp）"
    )

    # 元数据
    granted_by: Mapped[str | None] = mapped_column(
        get_string_field(100),
        nullable=True,
        comment="授权人person_id"
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="权限设置原因"
    )

    __table_args__ = (
        Index("idx_command_permissions_person", "person_id"),
        Index("idx_command_permissions_command", "command_signature"),
        Index(
            "idx_command_permissions_unique",
            "person_id",
            "command_signature",
            unique=True,
        ),
    )

