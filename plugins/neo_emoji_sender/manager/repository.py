"""neo_emoji_sender 元数据仓储（基于 PluginDatabase）。"""

from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.plugin_system.api.storage_api import PluginDatabase

from .models import EmojiRecordModel, EmojiSuppressionModel, EmojiTagModel
from .types import EmojiRecord


def _to_record(row: EmojiRecordModel, tags: Iterable[str]) -> EmojiRecord:
    """把 ORM 行与标签集合组装成领域对象。

    Args:
        row: ``emoji_records`` 表的一行。
        tags: 该记录的全部标签。

    Returns:
        组装好的 :class:`EmojiRecord`，标签去重并排序。
    """
    return EmojiRecord(
        emoji_id=row.emoji_id,
        rel_path=row.rel_path,
        mime=row.mime,
        size_bytes=int(row.size_bytes),
        description=row.description,
        revision=row.revision,
        tags=tuple(sorted(set(tags))),
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        created_at=float(row.created_at),
        updated_at=float(row.updated_at),
    )


def _with_tags_stmt():
    """构造"记录 + 标签"单语句查询。

    主表与标签表用一条 outer join 一次读出：SQLite 的 SELECT 不会开启真正的
    读事务，分成两条 SELECT 时中间若发生 update，就会拼出"旧描述 + 新标签"
    这种从未存在过的混合记录。

    Returns:
        ``select(EmojiRecordModel, EmojiTagModel.tag)`` 语句（未加条件）。
    """
    return select(EmojiRecordModel, EmojiTagModel.tag).outerjoin(
        EmojiTagModel, EmojiTagModel.emoji_id == EmojiRecordModel.emoji_id
    )


def _rows_to_records(rows: Iterable[tuple[EmojiRecordModel, str | None]]) -> list[EmojiRecord]:
    """把 join 结果按记录聚合。

    Args:
        rows: ``(记录行, 标签或 None)`` 序列；无标签记录的标签列为 None。

    Returns:
        聚合后的记录列表，保持首次出现的顺序。
    """
    order: list[str] = []
    rows_by_id: dict[str, EmojiRecordModel] = {}
    tags_by_id: dict[str, list[str]] = {}
    for row, tag in rows:
        if row.emoji_id not in rows_by_id:
            rows_by_id[row.emoji_id] = row
            order.append(row.emoji_id)
        if tag is not None:
            tags_by_id.setdefault(row.emoji_id, []).append(str(tag))
    return [_to_record(rows_by_id[emoji_id], tags_by_id.get(emoji_id, [])) for emoji_id in order]


class EmojiRepository:
    """表情包元数据仓储。"""

    def __init__(self, db_path: str) -> None:
        """初始化仓储。

        Args:
            db_path: 插件自有 SQLite 文件路径。
        """
        self._db = PluginDatabase(
            db_path,
            [EmojiRecordModel, EmojiTagModel, EmojiSuppressionModel],
        )

    async def initialize(self) -> None:
        """建表。"""
        await self._db.initialize()
    async def close(self) -> None:
        """关闭数据库连接。"""
        await self._db.close()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    async def get(self, emoji_id: str) -> EmojiRecord | None:
        """按完整 ID 获取记录。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            记录；不存在时返回 None。
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    _with_tags_stmt().where(EmojiRecordModel.emoji_id == emoji_id)
                )
            ).all()
        records = _rows_to_records(rows)
        return records[0] if records else None

    async def get_many(self, emoji_ids: list[str]) -> dict[str, EmojiRecord]:
        """批量获取记录。

        一次查询取回全部记录与标签，避免逐条访问数据库。

        Args:
            emoji_ids: 表情包完整 ID 列表。

        Returns:
            ID 到记录的映射；不存在的 ID 不会出现在结果中。
        """
        if not emoji_ids:
            return {}
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    _with_tags_stmt().where(EmojiRecordModel.emoji_id.in_(emoji_ids))
                )
            ).all()
        return {record.emoji_id: record for record in _rows_to_records(rows)}

    async def exists(self, emoji_id: str) -> bool:
        """判断记录是否存在。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            存在时返回 True。
        """
        async with self._db.session() as session:
            row = await session.get(EmojiRecordModel, emoji_id)
            return row is not None

    async def find_by_short_id(self, prefix: str) -> EmojiRecord | None:
        """按完整 ID 或前缀查找记录。

        先按主键精确命中，未命中再退化为前缀匹配，便于模型用短 ID 指定。
        前缀命中多条时返回 None——短 ID 的含义必须唯一，否则可能发错表情包。

        Args:
            prefix: 完整 ID 或前缀。

        Returns:
            唯一匹配到的记录；无匹配或前缀有歧义时返回 None。
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    _with_tags_stmt().where(EmojiRecordModel.emoji_id == prefix)
                )
            ).all()
            records = _rows_to_records(rows)
            if records:
                return records[0]
            matched = (
                await session.execute(
                    select(EmojiRecordModel.emoji_id)
                    .where(EmojiRecordModel.emoji_id.like(f"{prefix}%"))
                    .limit(2)
                )
            ).scalars().all()
            if len(matched) != 1:
                return None
            rows = (
                await session.execute(
                    _with_tags_stmt().where(EmojiRecordModel.emoji_id == matched[0])
                )
            ).all()
        records = _rows_to_records(rows)
        return records[0] if records else None

    async def list_records(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        tag: str | None = None,
        source_kind: str | None = None,
    ) -> tuple[list[EmojiRecord], int]:
        """分页列出记录，按创建时间倒序。

        Args:
            page: 页码，从 1 开始。
            page_size: 每页数量。
            tag: 仅返回带该标签的记录。
            source_kind: 仅返回来自该来源的记录。

        Returns:
            (当前页记录, 满足条件的总数)。
        """
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        async with self._db.session() as session:
            stmt = select(EmojiRecordModel.emoji_id)
            count_stmt = select(func.count()).select_from(EmojiRecordModel)
            if tag:
                id_subquery = select(EmojiTagModel.emoji_id).where(EmojiTagModel.tag == tag)
                stmt = stmt.where(EmojiRecordModel.emoji_id.in_(id_subquery))
                count_stmt = count_stmt.where(EmojiRecordModel.emoji_id.in_(id_subquery))
            if source_kind:
                stmt = stmt.where(EmojiRecordModel.source_kind == source_kind)
                count_stmt = count_stmt.where(EmojiRecordModel.source_kind == source_kind)
            total = int((await session.execute(count_stmt)).scalar_one())
            page_ids = (
                await session.execute(
                    stmt.order_by(EmojiRecordModel.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            # 本页记录与标签用一条 join 语句取回，避免与并发 update 拼出混合记录
            rows = (
                await session.execute(
                    _with_tags_stmt().where(EmojiRecordModel.emoji_id.in_(page_ids))
                )
            ).all() if page_ids else []
        order = {emoji_id: index for index, emoji_id in enumerate(page_ids)}
        records = sorted(
            _rows_to_records(rows), key=lambda record: order.get(record.emoji_id, len(order))
        )
        return records, total

    async def list_all(self) -> list[EmojiRecord]:
        """列出全部记录。

        对账器需要全量快照来与文件、向量逐条比对，因此不分页。

        Returns:
            全部记录。
        """
        async with self._db.session() as session:
            rows = (await session.execute(_with_tags_stmt())).all()
        return _rows_to_records(rows)

    async def list_source_refs(self, source_kind: str) -> set[str]:
        """列出某来源已注册的 source_ref。

        Args:
            source_kind: 来源类型。

        Returns:
            source_ref 集合。
        """
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(EmojiRecordModel.source_ref).where(
                        EmojiRecordModel.source_kind == source_kind
                    )
                )
            ).scalars().all()
        return {str(item) for item in rows if item}

    async def has_source_ref(self, source_kind: str, source_ref: str) -> bool:
        """判断某来源的 source_ref 是否已注册。

        Args:
            source_kind: 来源类型。
            source_ref: 来源稳定标识。

        Returns:
            已存在时返回 True。
        """
        if not source_ref:
            return False
        async with self._db.session() as session:
            row = (
                await session.execute(
                    select(EmojiRecordModel.emoji_id)
                    .where(EmojiRecordModel.source_kind == source_kind)
                    .where(EmojiRecordModel.source_ref == source_ref)
                    .limit(1)
                )
            ).scalars().first()
        return row is not None

    async def count(self) -> int:
        """统计记录数。

        Returns:
            记录总数。
        """
        async with self._db.session() as session:
            return int(
                (await session.execute(select(func.count()).select_from(EmojiRecordModel))).scalar_one()
            )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def insert(self, record: EmojiRecord) -> bool:
        """插入记录与标签。

        记录与标签在同一事务内写入，避免出现有记录无标签的中间态。

        Args:
            record: 待插入的记录。

        Returns:
            插入成功返回 True；ID 已存在返回 False。
        """
        async with self._db.session() as session:
            exists = await session.get(EmojiRecordModel, record.emoji_id)
            if exists is not None:
                return False
            session.add(
                EmojiRecordModel(
                    emoji_id=record.emoji_id,
                    rel_path=record.rel_path,
                    mime=record.mime,
                    size_bytes=record.size_bytes,
                    description=record.description,
                    revision=record.revision,
                    source_kind=record.source_kind,
                    source_ref=record.source_ref,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
            # 标签是主键的一部分，重复值会触发唯一约束导致整条注册失败，
            # 因此入库前去重；排序保证同一集合写入顺序稳定
            for tag in sorted(set(record.tags)):
                session.add(EmojiTagModel(emoji_id=record.emoji_id, tag=tag))
        return True

    async def update(
        self,
        emoji_id: str,
        *,
        description: str,
        tags: list[str],
        revision: str,
        updated_at: float,
    ) -> bool:
        """更新描述与标签。

        标签采用整体替换策略（先删后插），与"标签集合是完整事实"的语义一致。

        Args:
            emoji_id: 表情包完整 ID。
            description: 新描述。
            tags: 新标签列表。
            revision: 新内容指纹。
            updated_at: 更新时刻。

        Returns:
            更新成功返回 True；记录不存在返回 False。
        """
        async with self._db.session() as session:
            row = await session.get(EmojiRecordModel, emoji_id)
            if row is None:
                return False
            row.description = description
            row.revision = revision
            row.updated_at = updated_at
            await session.execute(delete(EmojiTagModel).where(EmojiTagModel.emoji_id == emoji_id))
            for tag in tags:
                session.add(EmojiTagModel(emoji_id=emoji_id, tag=tag))
        return True

    async def update_file_meta(
        self,
        emoji_id: str,
        *,
        rel_path: str,
        mime: str,
        size_bytes: int,
        updated_at: float,
    ) -> bool:
        """更新文件相关信息。

        只改路径、MIME 与大小，不动描述与 revision，因此不会触发向量重建。

        Args:
            emoji_id: 表情包完整 ID。
            rel_path: 新的相对路径。
            mime: 新的 MIME。
            size_bytes: 新的文件大小。
            updated_at: 更新时刻。

        Returns:
            更新成功返回 True；记录不存在返回 False。
        """
        async with self._db.session() as session:
            row = await session.get(EmojiRecordModel, emoji_id)
            if row is None:
                return False
            row.rel_path = rel_path
            row.mime = mime
            row.size_bytes = size_bytes
            row.updated_at = updated_at
        return True

    async def delete(self, emoji_id: str) -> bool:
        """删除记录与标签。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            删除成功返回 True；记录不存在返回 False。
        """
        async with self._db.session() as session:
            row = await session.get(EmojiRecordModel, emoji_id)
            if row is None:
                return False
            await session.execute(delete(EmojiTagModel).where(EmojiTagModel.emoji_id == emoji_id))
            await session.delete(row)
        return True

    # ------------------------------------------------------------------
    # 抑制表
    # ------------------------------------------------------------------

    async def get_suppression(self, content_hash: str) -> EmojiSuppressionModel | None:
        """读取抑制记录。

        Args:
            content_hash: 内容哈希。

        Returns:
            抑制记录；未被抑制时返回 None。
        """
        async with self._db.session() as session:
            return await session.get(EmojiSuppressionModel, content_hash)

    async def is_suppressed(self, content_hash: str) -> bool:
        """判断该内容是否被抑制。

        Args:
            content_hash: 内容哈希。

        Returns:
            被抑制时返回 True。
        """
        return await self.get_suppression(content_hash) is not None

    async def add_suppression(
        self,
        content_hash: str,
        *,
        reason: str,
        source_kind: str = "",
        source_ref: str = "",
        note: str = "",
    ) -> None:
        """写入或更新抑制记录。

        同一内容哈希重复写入会覆盖旧记录，因此可幂等调用。

        多个来源任务以及"来源拒收"与"删除"可以并发写同一内容哈希
        （来源拒收不持 ``mutation_lock``），先查后插会在提交时撞主键。
        因此使用 ``INSERT ... ON CONFLICT DO UPDATE`` 由 SQLite 原子完成。

        Args:
            content_hash: 内容哈希。
            reason: 抑制原因（如 ``user_deleted`` / ``vlm_rejected``）。
            source_kind: 来源类型。
            source_ref: 来源稳定标识。
            note: 备注。
        """
        now = time.time()
        values = {
            "content_hash": content_hash,
            "reason": reason,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "note": note,
            "created_at": now,
        }
        stmt = sqlite_insert(EmojiSuppressionModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[EmojiSuppressionModel.content_hash],
            set_={
                "reason": reason,
                "source_kind": source_kind,
                "source_ref": source_ref,
                "note": note,
                "created_at": now,
            },
        )
        async with self._db.session() as session:
            await session.execute(stmt)

    async def remove_suppression(self, content_hash: str) -> bool:
        """移除抑制记录。

        Args:
            content_hash: 内容哈希。

        Returns:
            移除成功返回 True；原本不存在返回 False。
        """
        async with self._db.session() as session:
            row = await session.get(EmojiSuppressionModel, content_hash)
            if row is None:
                return False
            await session.delete(row)
        return True

    async def list_suppressions(self) -> list[EmojiSuppressionModel]:
        """列出全部抑制记录。

        Returns:
            抑制记录列表。
        """
        async with self._db.session() as session:
            return list((await session.execute(select(EmojiSuppressionModel))).scalars().all())
