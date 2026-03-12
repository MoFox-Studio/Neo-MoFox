"""Booku Memory Agent 插件 SQLAlchemy 数据库模型定义。

使用独立的 declarative_base，与主程序数据库的 Base 完全隔离。
由 PluginDatabase 负责在指定 SQLite 文件中按需建表。
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

# 独立 Base，与核心数据库隔离
Base = declarative_base()


class BookuMemoryRecordModel(Base):
    """记忆主表，对应 booku_memory_records。"""

    __tablename__ = "booku_memory_records"

    memory_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="记忆唯一 ID")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="标题")
    folder_id: Mapped[str] = mapped_column(Text, nullable=False, comment="所属文件夹 ID")
    bucket: Mapped[str] = mapped_column(Text, nullable=False, comment="存储桶：inherent/emergent/archived")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="记忆内容")
    source: Mapped[str] = mapped_column(Text, nullable=False, comment="来源标识")
    novelty_energy: Mapped[float] = mapped_column(Float, nullable=False, comment="新颖度能量")
    is_archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否归档（0/1）")
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="是否软删除（0/1）")
    deleted_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, comment="软删除时间戳")
    created_at: Mapped[float] = mapped_column(Float, nullable=False, comment="创建时间戳")
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, comment="最后更新时间戳")
    last_activated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, comment="最近激活时间戳")
    activation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="激活次数")

    __table_args__ = (
        Index("idx_booku_memory_folder_bucket", "folder_id", "bucket", "is_archived"),
    )


class BookuMemoryTagModel(Base):
    """记忆标签表，对应 booku_memory_tags。"""

    __tablename__ = "booku_memory_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(Text, nullable=False, comment="关联的记忆 ID")
    tag_type: Mapped[str] = mapped_column(Text, nullable=False, comment="标签类型：tag/core/diffusion/opposing")
    tag_value: Mapped[str] = mapped_column(Text, nullable=False, comment="标签值")

    __table_args__ = (
        Index("idx_booku_memory_tags_memory", "memory_id", "tag_type"),
    )


__all__ = ["Base", "BookuMemoryRecordModel", "BookuMemoryTagModel"]
