"""原子保存每流的处理位置与按自然日组织的完整日记。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import Float, Integer, String, Text, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.app.plugin_system.api.storage_api import PluginDatabase


class DiaryBase(DeclarativeBase):
    """日记独立数据库的声明基类。"""


class StreamProgress(DiaryBase):
    """一个聊天流的成功位置及首次回填边界。"""

    __tablename__ = "chat_diary_progress"
    stream_id: Mapped[str] = mapped_column(String, primary_key=True)
    chat_type: Mapped[str] = mapped_column(String, nullable=False)
    cursor_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    bootstrap_through: Mapped[int] = mapped_column(Integer, nullable=False)
    initialized_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_success_at: Mapped[float | None] = mapped_column(Float)
    cursor_message_id: Mapped[str] = mapped_column(String, nullable=False, default="")


class DailyDiary(DiaryBase):
    """一流一天的完整正文与程序记录的覆盖边界。"""

    __tablename__ = "chat_diary_days"
    stream_id: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    through_id: Mapped[int] = mapped_column(Integer, nullable=False)
    through_time: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


@dataclass(frozen=True, slots=True)
class Progress:
    """不持有数据库会话的流进度快照。"""

    stream_id: str
    chat_type: str
    cursor_id: int
    start_time: float
    bootstrap_through: int
    initialized_at: float
    last_success_at: float | None
    cursor_message_id: str = ""


@dataclass(frozen=True, slots=True)
class Diary:
    """一个自然日的日记快照。"""

    stream_id: str
    day: str
    body: str
    through_id: int
    through_time: float
    updated_at: float


class DiaryStore:
    """将日记替换与成功位置推进绑定到同一个事务。"""

    def __init__(self, database_path: str) -> None:
        """使用公开插件存储 API 创建独立数据库。"""
        self.database = PluginDatabase(database_path, [StreamProgress, DailyDiary])

    async def initialize(self) -> None:
        """初始化日记库，不连接正式记忆库。"""
        await self.database.initialize()

    async def close(self) -> None:
        """释放日记连接。"""
        await self.database.close()

    async def progress(self, stream_id: str) -> Progress | None:
        """读取已保存的处理边界。"""
        async with self.database.session() as session:
            row = await session.get(StreamProgress, stream_id)
            return (
                Progress(
                    row.stream_id,
                    row.chat_type,
                    row.cursor_id,
                    row.start_time,
                    row.bootstrap_through,
                    row.initialized_at,
                    row.last_success_at,
                    row.cursor_message_id,
                )
                if row is not None
                else None
            )

    async def ensure_stream(
        self,
        stream_id: str,
        chat_type: str,
        *,
        start_time: float,
        bootstrap_through: int,
        now: float,
    ) -> Progress:
        """仅首次保存回填起点与上界，重启不会重新设置已有进度。"""
        async with self.database.session() as session:
            await session.execute(
                insert(StreamProgress)
                .values(
                    stream_id=stream_id,
                    chat_type=chat_type,
                    cursor_id=0,
                    start_time=start_time,
                    bootstrap_through=bootstrap_through,
                    initialized_at=now,
                    last_success_at=None,
                    cursor_message_id="",
                )
                .on_conflict_do_nothing(index_elements=["stream_id"])
            )
        result = await self.progress(stream_id)
        if result is None or result.chat_type != chat_type:
            raise RuntimeError("日记流身份或聊天类型不一致")
        return result

    async def list_streams(self) -> tuple[Progress, ...]:
        """读取已有流，供启动恢复使用。"""
        async with self.database.session() as session:
            rows = (await session.scalars(select(StreamProgress))).all()
            return tuple(
                Progress(
                    row.stream_id,
                    row.chat_type,
                    row.cursor_id,
                    row.start_time,
                    row.bootstrap_through,
                    row.initialized_at,
                    row.last_success_at,
                    row.cursor_message_id,
                )
                for row in rows
            )

    async def diaries(self, stream_id: str, since_day: str) -> tuple[Diary, ...]:
        """仅取指定流的日期范围，不按篇数向更早日期补齐。"""
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(DailyDiary)
                    .where(
                        DailyDiary.stream_id == stream_id,
                        DailyDiary.day >= since_day,
                    )
                    .order_by(DailyDiary.day)
                )
            ).all()
            return tuple(
                Diary(
                    row.stream_id,
                    row.day,
                    row.body,
                    row.through_id,
                    row.through_time,
                    row.updated_at,
                )
                for row in rows
            )

    async def get_day(self, stream_id: str, day: str) -> Diary | None:
        """读取当天底稿，不把其他日期当成可重写正文。"""
        async with self.database.session() as session:
            row = await session.get(DailyDiary, (stream_id, day))
            return (
                Diary(
                    row.stream_id,
                    row.day,
                    row.body,
                    row.through_id,
                    row.through_time,
                    row.updated_at,
                )
                if row is not None
                else None
            )

    async def commit_batch(
        self,
        progress: Progress,
        *,
        through_id: int,
        now: float,
        diary: Diary | None,
        message_id: str = "",
    ) -> None:
        """比较原位置后同时替换正文和推进游标，异常会整批回滚。"""
        if through_id <= progress.cursor_id:
            raise ValueError("日记处理位置必须向前推进")
        if diary is not None and (
            diary.stream_id != progress.stream_id or diary.through_id != through_id
        ):
            raise ValueError("日记正文和处理位置不属于同一批次")
        async with self.database.session() as session:
            result = await session.execute(
                update(StreamProgress)
                .where(
                    StreamProgress.stream_id == progress.stream_id,
                    StreamProgress.cursor_id == progress.cursor_id,
                )
                .values(
                    cursor_id=through_id,
                    cursor_message_id=message_id,
                    last_success_at=now,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise RuntimeError("日记处理位置已变化，拒绝过期结果")
            if diary is not None:
                values = {
                    "stream_id": diary.stream_id,
                    "day": diary.day,
                    "body": diary.body,
                    "through_id": diary.through_id,
                    "through_time": diary.through_time,
                    "updated_at": now,
                }
                await session.execute(
                    insert(DailyDiary)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["stream_id", "day"],
                        set_=values,
                    )
                )
