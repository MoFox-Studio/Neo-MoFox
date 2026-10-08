"""Engram vNext 与 Neo-MoFox 内部运行能力的集中适配层。

现有插件公开 API 尚未覆盖任务、向量数据库、全局 Reminder 删除
和按消息 ID 批量读取。本模块只在插件边缘封装这些只读或生命周期能力，
不向领域层泄漏框架 Manager、数据库 Session 或可变 ORM 对象。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import and_, desc, or_, select, text

from src.core.models.sql_alchemy import ChatStreams, Messages, PersonInfo
from src.core.prompt.system_reminder import get_system_reminder_store
from src.kernel.concurrency import TaskNotFoundError, get_task_manager
from src.kernel.db import get_db_session
from src.kernel.vector_db import get_vector_db_service


@dataclass(frozen=True, slots=True)
class ManagedTaskHandle:
    """Engram 创建的单个框架任务句柄。"""

    task_id: str
    task: asyncio.Task[Any] | None


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    """从框架消息表复制出的不可变证据快照。"""

    message_id: str
    stream_id: str
    time: float | datetime | None
    sender_id: str | None
    sender_name: str | None
    sender_cardname: str | None
    person_id: str | None
    platform: str | None
    message_type: str | None
    content: str
    processed_plain_text: str
    reply_to: str | None

    def to_dict(self) -> dict[str, object]:
        """返回不含 ORM 或 Session 的普通字典。"""
        return {
            "message_id": self.message_id,
            "stream_id": self.stream_id,
            "time": self.time,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sender_cardname": self.sender_cardname,
            "person_id": self.person_id,
            "platform": self.platform,
            "message_type": self.message_type,
            "content": self.content,
            "processed_plain_text": self.processed_plain_text,
            "reply_to": self.reply_to,
        }


@dataclass(frozen=True, slots=True)
class MigrationPersonCandidate:
    """迁移 Resolver 使用的只读人物候选。"""

    person_id: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class MigrationStreamCandidate:
    """迁移 Resolver 使用的只读聊天流候选。"""

    stream_id: str


@dataclass(frozen=True, slots=True)
class MigrationMessageCandidate:
    """迁移 Resolver 使用的只读消息候选。"""

    message_id: str
    stream_id: str
    person_id: str | None
    time: float
    content: str


async def _set_transaction_read_only(session: Any) -> None:
    """将 Bridge 查询事务显式限制为只读。"""
    bind = session.get_bind()
    dialect_name = str(bind.dialect.name)
    if dialect_name == "postgresql":
        await session.execute(text("SET TRANSACTION READ ONLY"))
    elif dialect_name == "sqlite":
        await session.execute(text("PRAGMA query_only=ON"))
    else:
        raise RuntimeError(f"Migration Resolver 不支持数据库方言: {dialect_name}")


@asynccontextmanager
async def _read_only_db_session() -> AsyncIterator[Any]:
    """为单次来源查询启用只读事务，并复位 SQLite 连接级开关。"""
    async with get_db_session() as session:
        is_sqlite = str(session.get_bind().dialect.name) == "sqlite"
        await _set_transaction_read_only(session)
        try:
            yield session
        finally:
            if is_sqlite:
                await session.execute(text("PRAGMA query_only=OFF"))


async def read_migration_person_candidates(
    source_ref: str,
) -> tuple[MigrationPersonCandidate, ...]:
    """返回人物标识的全部精确候选，不折叠重复。"""
    normalized = source_ref.strip()
    if not normalized:
        return ()
    conditions = [PersonInfo.person_id == normalized]
    if ":" in normalized:
        platform, user_id = normalized.split(":", 1)
        if platform and user_id:
            conditions.append(
                (PersonInfo.platform == platform) & (PersonInfo.user_id == user_id)
            )
    async with _read_only_db_session() as session:
        result = await session.execute(select(PersonInfo).where(or_(*conditions)))
        rows = result.scalars().all()
        return tuple(
            MigrationPersonCandidate(
                person_id=str(row.person_id),
                display_name=str(row.cardname or row.nickname or "").strip() or None,
            )
            for row in rows
        )


async def read_migration_stream_candidates(
    source_ref: str,
    *,
    primary_person_id: str | None = None,
    start_timestamp: float | None = None,
    end_timestamp: float | None = None,
) -> tuple[MigrationStreamCandidate, ...]:
    """按旧 ID 或主人物时间窗返回确定性聊天流候选。"""
    normalized = source_ref.strip()
    has_window = (
        primary_person_id is not None
        and start_timestamp is not None
        and end_timestamp is not None
        and start_timestamp <= end_timestamp
    )
    if not normalized and not has_window:
        return ()
    async with _read_only_db_session() as session:
        if normalized:
            result = await session.execute(
                select(ChatStreams.stream_id).where(ChatStreams.stream_id == normalized)
            )
            exact = tuple(str(item) for item in result.scalars().all())
            if exact:
                return tuple(MigrationStreamCandidate(stream_id=item) for item in exact)
            result = await session.execute(
                select(Messages.stream_id)
                .where(Messages.stream_id == normalized)
                .distinct()
            )
            preserved = tuple(str(item) for item in result.scalars().all())
            if preserved:
                return tuple(
                    MigrationStreamCandidate(stream_id=item) for item in preserved
                )
        if not has_window:
            return ()
        result = await session.execute(
            select(Messages.stream_id)
            .where(
                Messages.person_id == primary_person_id,
                Messages.time >= start_timestamp,
                Messages.time <= end_timestamp,
            )
            .distinct()
            .order_by(Messages.stream_id.asc())
        )
        return tuple(
            MigrationStreamCandidate(stream_id=str(item))
            for item in result.scalars().all()
        )


async def read_migration_message_candidates(
    *,
    stream_id: str,
    start_timestamp: float,
    end_timestamp: float,
    person_id: str | None = None,
    limit: int = 500,
) -> tuple[MigrationMessageCandidate, ...]:
    """返回显式 UTC 时间窗内的全部消息候选，不使用当前年份。"""
    normalized_stream = stream_id.strip()
    if not normalized_stream or start_timestamp > end_timestamp:
        return ()
    if limit < 1 or limit > 2_000:
        raise ValueError("limit 必须在 1..2000")
    statement = select(Messages).where(
        Messages.stream_id == normalized_stream,
        Messages.time >= start_timestamp,
        Messages.time <= end_timestamp,
    )
    if person_id:
        statement = statement.where(Messages.person_id == person_id)
    statement = statement.order_by(Messages.time.asc(), Messages.id.asc()).limit(limit)
    async with _read_only_db_session() as session:
        result = await session.execute(statement)
        return tuple(
            MigrationMessageCandidate(
                message_id=str(row.message_id),
                stream_id=str(row.stream_id),
                person_id=str(row.person_id) if row.person_id is not None else None,
                time=float(row.time),
                content=str(row.processed_plain_text or row.content or ""),
            )
            for row in result.scalars().all()
        )


class VectorDatabase(Protocol):
    """Engram 派生向量适配器实际使用的最小框架协议。"""

    async def add(
        self,
        collection_name: str,
        embeddings: list[list[float]],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        """向 Engram 专属 collection 添加派生入口。"""

    async def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]],
        n_results: int = 1,
        where: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, list[Any]]:
        """查询 Engram 专属 collection。"""
        ...

    async def delete(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        """删除 Engram 专属 collection 中指定入口。"""

    async def get(
        self,
        collection_name: str,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        where_document: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """读取 Engram 专属 collection 的派生入口。"""
        ...


def create_managed_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str,
    daemon: bool = True,
) -> ManagedTaskHandle:
    """创建并返回仅属于 Engram 的受管任务句柄。"""
    task_info = get_task_manager().create_task(coro, name=name, daemon=daemon)
    return ManagedTaskHandle(task_info.task_id, task_info.task)


def get_managed_task(task_id: str) -> ManagedTaskHandle:
    """按 Engram 保存的 ID 读取受管任务，不枚举或修改其他任务。"""
    task_info = get_task_manager().get_task(task_id)
    return ManagedTaskHandle(task_info.task_id, task_info.task)


def cancel_managed_task(task_id: str) -> bool:
    """精确取消一个由 Engram 保存 ID 的受管任务。"""
    return get_task_manager().cancel_task(task_id)


def delete_owned_reminder(bucket: str, name: str) -> bool:
    """只删除 Engram 自己命名空间下的一条全局 Reminder。"""
    if not name.startswith("engram_memory_"):
        raise ValueError("Engram reminder 名称必须使用 engram_memory_ 前缀")
    return get_system_reminder_store().delete(bucket, name)


def get_vector_database(db_path: str) -> VectorDatabase:
    """取得供 Engram 派生索引适配器使用的框架向量服务。"""
    if not db_path.strip():
        raise ValueError("db_path 不能为空")
    return cast(VectorDatabase, get_vector_db_service(db_path))


async def _message_snapshots_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[MessageSnapshot, ...]:
    """将只读消息行转换为包含原始人物元数据的快照。"""
    person_ids = tuple(
        dict.fromkeys(
            str(row.get("person_id") or "")
            for row in rows
            if str(row.get("person_id") or "").strip()
        )
    )
    people: dict[str, Mapping[str, Any]] = {}
    if person_ids:
        async with _read_only_db_session() as session:
            person_rows = [
                dict(row)
                for row in (
                    await session.execute(
                        select(PersonInfo.__table__).where(
                            PersonInfo.person_id.in_(person_ids)
                        )
                    )
                )
                .mappings()
                .all()
            ]
        people = {
            str(row.get("person_id") or ""): row
            for row in person_rows
            if str(row.get("person_id") or "").strip()
        }

    snapshots: list[MessageSnapshot] = []
    for row in rows:
        message_id = str(row.get("message_id") or "")
        if not message_id:
            continue
        stream_id = str(row.get("stream_id") or "")
        person_id = str(row.get("person_id") or "") or None
        person = people.get(person_id or "", {})
        sender_id = str(person.get("user_id") or person_id or "") or None
        snapshots.append(
            MessageSnapshot(
                message_id=message_id,
                stream_id=stream_id,
                time=row.get("time"),
                sender_id=sender_id,
                sender_name=(
                    str(person.get("nickname") or "").strip()
                    or str(row.get("sender_name") or "").strip()
                    or sender_id
                ),
                sender_cardname=str(person.get("cardname") or "").strip() or None,
                person_id=person_id,
                platform=str(row.get("platform") or "").strip() or None,
                message_type=str(row.get("message_type") or "").strip() or None,
                content=str(row.get("content") or ""),
                processed_plain_text=str(
                    row.get("processed_plain_text") or row.get("content") or ""
                ),
                reply_to=str(row.get("reply_to") or "").strip() or None,
            )
        )
    return tuple(snapshots)


async def read_message_snapshots(
    message_refs: Sequence[tuple[str, str]],
) -> tuple[MessageSnapshot, ...]:
    """按 ``(stream_id, message_id)`` 顺序批量只读消息。

    不存在或与指定 stream 不匹配的消息会被忽略；返回顺序严格遵循
    ``message_refs`` 的首次出现顺序。
    """
    normalized_refs = tuple(
        dict.fromkeys(
            (str(stream_id or "").strip(), str(message_id or "").strip())
            for stream_id, message_id in message_refs
            if str(message_id or "").strip()
        )
    )
    if not normalized_refs:
        return ()
    message_ids = tuple(dict.fromkeys(message_id for _, message_id in normalized_refs))
    async with _read_only_db_session() as session:
        rows = [
            dict(row)
            for row in (
                await session.execute(
                    select(Messages.__table__).where(
                        Messages.message_id.in_(message_ids)
                    )
                )
            )
            .mappings()
            .all()
        ]
    exact: dict[tuple[str, str], MessageSnapshot] = {}
    by_id: dict[str, list[MessageSnapshot]] = {}
    for snapshot in await _message_snapshots_from_rows(rows):
        message_id = snapshot.message_id
        stream_id = snapshot.stream_id
        exact[(stream_id, message_id)] = snapshot
        by_id.setdefault(message_id, []).append(snapshot)

    ordered: list[MessageSnapshot] = []
    for stream_id, message_id in normalized_refs:
        snapshot = exact.get((stream_id, message_id)) if stream_id else None
        if snapshot is None and not stream_id:
            matches = by_id.get(message_id, ())
            snapshot = matches[0] if len(matches) == 1 else None
        if snapshot is not None:
            ordered.append(snapshot)
    return tuple(ordered)


async def read_message_context_snapshots(
    stream_id: str,
    anchor_message_id: str,
    before: int,
    after: int,
    *,
    reply_to_message_id: str | None = None,
    use_core_reply_to: bool = True,
) -> tuple[MessageSnapshot, ...]:
    """读取 anchor 的同流窗口及可获取的回复目标，按核心顺序返回。"""
    if before < 0 or after < 0:
        raise ValueError("上下文数量不能为负数")
    if not stream_id.strip() or not anchor_message_id.strip():
        raise ValueError("上下文查询必须指定 stream_id 与 anchor_message_id")

    async with _read_only_db_session() as session:
        anchor = (
            (
                await session.execute(
                    select(Messages.__table__).where(
                        Messages.stream_id == stream_id,
                        Messages.message_id == anchor_message_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if anchor is None:
            return ()

        anchor_time = float(anchor["time"])
        anchor_row_id = int(anchor["id"])
        rows: list[dict[str, Any]] = [dict(anchor)]
        if before:
            earlier = [
                dict(row)
                for row in (
                    await session.execute(
                        select(Messages.__table__)
                        .where(
                            Messages.stream_id == stream_id,
                            or_(
                                Messages.time < anchor_time,
                                and_(
                                    Messages.time == anchor_time,
                                    Messages.id < anchor_row_id,
                                ),
                            ),
                        )
                        .order_by(desc(Messages.time), desc(Messages.id))
                        .limit(before)
                    )
                )
                .mappings()
                .all()
            ]
            rows.extend(reversed(earlier))
        if after:
            rows.extend(
                dict(row)
                for row in (
                    await session.execute(
                        select(Messages.__table__)
                        .where(
                            Messages.stream_id == stream_id,
                            or_(
                                Messages.time > anchor_time,
                                and_(
                                    Messages.time == anchor_time,
                                    Messages.id > anchor_row_id,
                                ),
                            ),
                        )
                        .order_by(Messages.time, Messages.id)
                        .limit(after)
                    )
                )
                .mappings()
                .all()
            )

        reply_to = (
            str(anchor.get("reply_to") or "").strip()
            if use_core_reply_to
            else str(reply_to_message_id or "").strip()
        )
        if reply_to and not any(row.get("message_id") == reply_to for row in rows):
            reply_row = (
                (
                    await session.execute(
                        select(Messages.__table__).where(
                            Messages.stream_id == stream_id,
                            Messages.message_id == reply_to,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if reply_row is not None:
                rows.append(dict(reply_row))

    rows.sort(key=lambda row: (float(row["time"]), int(row["id"])))
    return await _message_snapshots_from_rows(rows)


__all__ = [
    "ManagedTaskHandle",
    "MessageSnapshot",
    "MigrationMessageCandidate",
    "MigrationPersonCandidate",
    "MigrationStreamCandidate",
    "TaskNotFoundError",
    "VectorDatabase",
    "cancel_managed_task",
    "create_managed_task",
    "delete_owned_reminder",
    "get_managed_task",
    "get_vector_database",
    "read_message_context_snapshots",
    "read_message_snapshots",
    "read_migration_message_candidates",
    "read_migration_person_candidates",
    "read_migration_stream_candidates",
]
