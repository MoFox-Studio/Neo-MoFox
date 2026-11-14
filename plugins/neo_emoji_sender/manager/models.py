"""neo_emoji_sender SQLAlchemy 模型。

使用独立 declarative_base，由 PluginDatabase 在插件自有 SQLite 中建表。
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

Base = declarative_base()


class EmojiRecordModel(Base):
    """表情包主表。"""

    __tablename__ = "emoji_records"

    emoji_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="内容 sha256")
    rel_path: Mapped[str] = mapped_column(Text, nullable=False, comment="相对数据根目录的路径")
    mime: Mapped[str] = mapped_column(Text, nullable=False, default="image/png")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revision: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("idx_emoji_records_updated_at", "updated_at"),
        Index("idx_emoji_records_source", "source_kind", "source_ref"),
    )


class EmojiTagModel(Base):
    """表情包标签表。"""

    __tablename__ = "emoji_tags"

    emoji_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tag: Mapped[str] = mapped_column(Text, primary_key=True)

    __table_args__ = (Index("idx_emoji_tags_tag", "tag"),)


class EmojiSuppressionModel(Base):
    """拒收 / 删除抑制表。"""

    __tablename__ = "emoji_suppressions"

    content_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_kind: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (Index("idx_emoji_suppressions_source", "source_kind", "source_ref"),)


__all__ = [
    "Base",
    "EmojiRecordModel",
    "EmojiTagModel",
    "EmojiSuppressionModel",
]
