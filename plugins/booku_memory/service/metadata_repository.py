"""Booku Memory 元数据仓储（基于 PluginDatabase）。

使用 :class:`~src.app.plugin_system.api.storage_api.PluginDatabase` 替代原有
直接操作 ``sqlite3`` 的实现，与核心数据库使用相同的 ORM 接口风格。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, distinct, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.app.plugin_system.api.storage_api import PluginDatabase

from .models import BookuMemoryRecordModel, BookuMemoryTagModel


@dataclass(slots=True)
class BookuMemoryRecord:
    """记忆元数据记录（公共接口，不变）。"""

    memory_id: str
    title: str
    folder_id: str
    bucket: str
    content: str
    source: str
    novelty_energy: float
    is_archived: bool
    is_deleted: bool
    deleted_at: float
    created_at: float
    updated_at: float
    last_activated_at: float
    activation_count: int
    tags: list[str]
    core_tags: list[str]
    diffusion_tags: list[str]
    opposing_tags: list[str]


class BookuMemoryMetadataRepository:
    """Booku Memory 的 SQLAlchemy + PluginDatabase 元数据仓储。"""

    def __init__(self, db_path: str) -> None:
        """初始化仓储。

        Args:
            db_path: SQLite 数据库文件路径。
        """
        self._db = PluginDatabase(db_path, [BookuMemoryRecordModel, BookuMemoryTagModel])

    async def initialize(self) -> None:
        """初始化数据库（建表）。"""
        await self._db.initialize()

    async def close(self) -> None:
        """关闭底层 PluginDatabase 连接并清理资源。

        在测试或插件卸载时建议调用，避免 aiosqlite 后台线程在事件循环关闭后回调。
        """

        await self._db.close()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _to_record(
        self,
        row: BookuMemoryRecordModel,
        tags_by_id: dict[str, dict[str, list[str]]] | None = None,
    ) -> BookuMemoryRecord:
        """将 ORM 实例转换为公共 dataclass。

        Args:
            row: SQLAlchemy ORM 记录实例。
            tags_by_id: 已预加载的标签映射表，格式为
                ``{memory_id: {tag_type: [tag_value, ...]}}``。
                为空时该记忆的所有标签列表将为空。

        Returns:
            含完整标签信息的 ``BookuMemoryRecord`` dataclass 实例。
        """
        td = (tags_by_id or {}).get(row.memory_id, {})
        return BookuMemoryRecord(
            memory_id=row.memory_id,
            title=row.title or "",
            folder_id=row.folder_id,
            bucket=row.bucket,
            content=row.content,
            source=row.source,
            novelty_energy=float(row.novelty_energy),
            is_archived=bool(row.is_archived),
            is_deleted=bool(row.is_deleted),
            deleted_at=float(row.deleted_at) if row.deleted_at else 0.0,
            created_at=float(row.created_at),
            updated_at=float(row.updated_at),
            last_activated_at=float(row.last_activated_at) if row.last_activated_at else 0.0,
            activation_count=int(row.activation_count) if row.activation_count else 0,
            tags=td.get("tag", []),
            core_tags=td.get("core", []),
            diffusion_tags=td.get("diffusion", []),
            opposing_tags=td.get("opposing", []),
        )

    # ------------------------------------------------------------------
    # 写入 / upsert
    # ------------------------------------------------------------------

    async def upsert_record(
        self,
        *,
        memory_id: str,
        title: str,
        folder_id: str,
        bucket: str,
        content: str,
        source: str,
        novelty_energy: float,
        tags: list[str],
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
    ) -> None:
        """写入或更新记忆元数据与标签。

        如果该 memory_id 已存在，则更新除 created_at 以外的所有环层字段，
        并全量重建标签表。

        Args:
            memory_id: 记忆唯一标识符。
            title: 记忆标题。
            folder_id: 所属文件夹 ID。
            bucket: 存储桶（emergent/archived/inherent）。
            content: 记忆全文内容。
            source: 来源标识字符串。
            novelty_energy: 写入时计算的新颖度能量比。
            tags: 通用标签列表。
            core_tags: 核心标签列表。
            diffusion_tags: 扩散标签列表。
            opposing_tags: 对立标签列表。
        """
        now = time.time()
        is_archived = 1 if bucket == "archived" else 0

        async with self._db.session() as s:
            # 保留已有记录的 created_at
            existing = await s.execute(
                select(BookuMemoryRecordModel.created_at).where(
                    BookuMemoryRecordModel.memory_id == memory_id
                )
            )
            row = existing.first()
            created_at = float(row[0]) if row else now

            stmt = sqlite_insert(BookuMemoryRecordModel).values(
                memory_id=memory_id,
                title=title,
                folder_id=folder_id,
                bucket=bucket,
                content=content,
                source=source,
                novelty_energy=novelty_energy,
                is_archived=is_archived,
                is_deleted=0,
                deleted_at=0.0,
                created_at=created_at,
                updated_at=now,
                last_activated_at=0.0,
                activation_count=0,
            ).on_conflict_do_update(
                index_elements=["memory_id"],
                set_=dict(
                    title=title,
                    folder_id=folder_id,
                    bucket=bucket,
                    content=content,
                    source=source,
                    novelty_energy=novelty_energy,
                    is_archived=is_archived,
                    is_deleted=0,
                    deleted_at=0.0,
                    updated_at=now,
                ),
            )
            await s.execute(stmt)

            # 重建标签
            await s.execute(
                delete(BookuMemoryTagModel).where(BookuMemoryTagModel.memory_id == memory_id)
            )
            tag_rows: list[dict[str, Any]] = []
            for tag_type, tag_values in [
                ("tag", tags),
                ("core", core_tags),
                ("diffusion", diffusion_tags),
                ("opposing", opposing_tags),
            ]:
                for v in tag_values:
                    if v:
                        tag_rows.append({"memory_id": memory_id, "tag_type": tag_type, "tag_value": v})
            if tag_rows:
                await s.execute(sqlite_insert(BookuMemoryTagModel), tag_rows)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_record(
        self, memory_id: str, *, include_deleted: bool = False
    ) -> BookuMemoryRecord | None:
        """按 memory_id 查询单条元数据（含完整标签）。

        Args:
            memory_id: 目标记忆的唯一 ID。
            include_deleted: 是否包含已软删的记录，默认 False。

        Returns:
            ``BookuMemoryRecord`` 实例；记录不存在时返回 ``None``。
        """
        records = await self.get_records_map([memory_id], include_deleted=include_deleted)
        return records.get(memory_id)

    async def get_records_map(
        self, memory_ids: list[str], *, include_deleted: bool = False
    ) -> dict[str, BookuMemoryRecord]:
        """按 memory_id 列表批量查询元数据（含完整标签）。

        一次 SQL 查询获取所有记录，再一次异履且查询所有标签。

        Args:
            memory_ids: 待查询的 memory_id 列表。为空时直接返回空字典。
            include_deleted: 是否包含已软删的记录，默认 False。

        Returns:
            ``{memory_id: BookuMemoryRecord}`` 字典，找不到的 id 不出现在返回值中。
        """
        if not memory_ids:
            return {}

        async with self._db.session() as s:
            R = BookuMemoryRecordModel
            stmt = select(R).where(R.memory_id.in_(memory_ids))
            if not include_deleted:
                stmt = stmt.where(R.is_deleted == 0)
            rows = (await s.execute(stmt)).scalars().all()

            if not rows:
                return {}

            found_ids = [r.memory_id for r in rows]

            # 批量获取标签
            T = BookuMemoryTagModel
            tag_rows = (
                await s.execute(select(T).where(T.memory_id.in_(found_ids)))
            ).scalars().all()

        # 按 memory_id + tag_type 分组
        tags_by_id: dict[str, dict[str, list[str]]] = {}
        for tag in tag_rows:
            mid = tag.memory_id
            if mid not in tags_by_id:
                tags_by_id[mid] = {}
            tags_by_id[mid].setdefault(tag.tag_type, []).append(tag.tag_value)

        return {r.memory_id: self._to_record(r, tags_by_id) for r in rows}

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    async def update_record(
        self,
        memory_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        source: str | None = None,
        bucket: str | None = None,
        folder_id: str | None = None,
        tags: list[str] | None = None,
        core_tags: list[str] | None = None,
        diffusion_tags: list[str] | None = None,
        opposing_tags: list[str] | None = None,
    ) -> bool:
        """按 memory_id 更新记录字段与标签。

        仅更新非 ``None`` 的字段（最小变更原则）。
        标签参数任一不为 ``None`` 时，将全量重建标签表。

        Args:
            memory_id: 目标记忆的 ID。
            title: 新标题（可选）。
            content: 新内容（可选）。
            source: 新来源标识（可选）。
            bucket: 新存储桶（可选），同时自动更新 is_archived 字段。
            folder_id: 新文件夹 ID（可选）。
            tags: 新通用标签列表（可选）。
            core_tags: 新核心标签列表（可选）。
            diffusion_tags: 新扩散标签列表（可选）。
            opposing_tags: 新对立标签列表（可选）。

        Returns:
            True 表示更新成功；False 表示记录不存在或已被删除。
        """
        now = time.time()
        R = BookuMemoryRecordModel

        async with self._db.session() as s:
            existing = (
                await s.execute(
                    select(R).where(R.memory_id == memory_id, R.is_deleted == 0)
                )
            ).scalar_one_or_none()
            if existing is None:
                return False

            update_vals: dict[str, Any] = {"updated_at": now}
            if title is not None:
                update_vals["title"] = title
            if content is not None:
                update_vals["content"] = content
            if source is not None:
                update_vals["source"] = source
            if bucket is not None:
                update_vals["bucket"] = bucket
                update_vals["is_archived"] = 1 if bucket == "archived" else 0
            if folder_id is not None:
                update_vals["folder_id"] = folder_id

            await s.execute(
                update(R).where(R.memory_id == memory_id).values(**update_vals)
            )

            if any(v is not None for v in [tags, core_tags, diffusion_tags, opposing_tags]):
                T = BookuMemoryTagModel
                await s.execute(delete(T).where(T.memory_id == memory_id))
                tag_rows: list[dict[str, Any]] = []
                for tag_type, tag_vals in [
                    ("tag", tags or []),
                    ("core", core_tags or []),
                    ("diffusion", diffusion_tags or []),
                    ("opposing", opposing_tags or []),
                ]:
                    for v in tag_vals:
                        if v:
                            tag_rows.append({"memory_id": memory_id, "tag_type": tag_type, "tag_value": v})
                if tag_rows:
                    await s.execute(sqlite_insert(BookuMemoryTagModel), tag_rows)

        return True

    async def mark_archived(
        self, memory_ids: list[str], folder_id: str | None = None
    ) -> int:
        """将给定记忆在元数据库中标记为归档。

        仅更新元数据库，不操作向量库。

        Args:
            memory_ids: 待归档的 memory_id 列表。
            folder_id: 如果指定则仅更新该 folder 内的记录，默认 None 则操作全部。

        Returns:
            实际被更新的记录数量。
        """
        if not memory_ids:
            return 0
        now = time.time()
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            stmt = update(R).where(R.memory_id.in_(memory_ids), R.is_deleted == 0)
            if folder_id is not None:
                stmt = stmt.where(R.folder_id == folder_id)
            stmt = stmt.values(bucket="archived", is_archived=1, updated_at=now)
            result = await s.execute(stmt)
            return int(getattr(result, "rowcount", 0) or 0)

    async def move_records(
        self,
        memory_ids: list[str],
        *,
        to_bucket: str | None = None,
        to_folder_id: str | None = None,
    ) -> int:
        """批量移动记忆到新 bucket 或 folder（仅元数据库）。

        仅更新元数据库中的字段，不操作向量库。向量库的迁移由 service 层负责。
        ``to_bucket`` 与 ``to_folder_id`` 同时为 ``None`` 时无操作直接返回 0。

        Args:
            memory_ids: 待移动的 memory_id 列表。
            to_bucket: 目标 bucket（可选）。
            to_folder_id: 目标 folder_id（可选）。

        Returns:
            实际被更新的记录数量。
        """
        if not memory_ids:
            return 0
        now = time.time()
        update_vals: dict[str, Any] = {"updated_at": now}
        if to_bucket is not None:
            update_vals["bucket"] = to_bucket
            update_vals["is_archived"] = 1 if to_bucket == "archived" else 0
        if to_folder_id is not None:
            update_vals["folder_id"] = to_folder_id
        if len(update_vals) == 1:
            return 0

        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            result = await s.execute(
                update(R)
                .where(R.memory_id.in_(memory_ids), R.is_deleted == 0)
                .values(**update_vals)
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def soft_delete_records(self, memory_ids: list[str]) -> int:
        """将指定记忆标记为软删除（is_deleted=1）。

        软删除仓保数据，对向量库无影响。

        Args:
            memory_ids: 待软删的 memory_id 列表。

        Returns:
            实际被标记为删除的记录数量。
        """
        if not memory_ids:
            return 0
        now = time.time()
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            result = await s.execute(
                update(R)
                .where(R.memory_id.in_(memory_ids), R.is_deleted == 0)
                .values(is_deleted=1, deleted_at=now, updated_at=now)
            )
            return int(getattr(result, "rowcount", 0) or 0)

    async def hard_delete_records(self, memory_ids: list[str]) -> int:
        """硬删除指定记忆（元数据 + 全部标签）。

        删除操作不可逆，同时删除 ``booku_memory_tags`` 中并行的标签行。

        Args:
            memory_ids: 待硬删的 memory_id 列表。

        Returns:
            实际被删除的主表记录数量。
        """
        if not memory_ids:
            return 0
        T = BookuMemoryTagModel
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            await s.execute(delete(T).where(T.memory_id.in_(memory_ids)))
            result = await s.execute(delete(R).where(R.memory_id.in_(memory_ids)))
            return int(getattr(result, "rowcount", 0) or 0)

    async def update_activated(self, memory_id: str) -> None:
        """原子将指定记忆的激活计数 +1 并更新最近激活时间。

        使用 SQL 表达式更新避免读-改写竞争。

        Args:
            memory_id: 目标记忆的 ID。
        """
        now = time.time()
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            await s.execute(
                update(R)
                .where(R.memory_id == memory_id)
                .values(
                    activation_count=R.activation_count + 1,
                    last_activated_at=now,
                )
            )

    # ------------------------------------------------------------------
    # 特殊查询
    # ------------------------------------------------------------------

    async def get_stale_emergent(
        self, folder_id: str, before_timestamp: float
    ) -> list[BookuMemoryRecord]:
        """查询指定 folder 中创建时间早于给定时间戳的 emergent 层记录。

        用于 ``promote_stale_emergent`` 中逐剪窗口的隐现记忆。

        Args:
            folder_id: 限定查询的文件夹 ID。
            before_timestamp: Unix 时间戳，吾创建比此戳早的记录才会被返回。

        Returns:
            滿足条件的 ``BookuMemoryRecord`` 列表，不包含已删除条目。
        """
        rows = await (
            self._db.query(BookuMemoryRecordModel)
            .filter(
                folder_id=folder_id,
                bucket="emergent",
                created_at__lt=before_timestamp,
                is_deleted=0,
            )
            .all()
        )
        return [self._to_record(r) for r in rows]  # type: ignore[arg-type]

    async def get_bucket_counts(
        self,
        folder_id: str | None = None,
        *,
        include_deleted: bool = False,
    ) -> dict[str, int]:
        """统计元数据库中各 bucket 的记忆数量。

        Args:
            folder_id: 限定统计范围的 folder，``None`` 时统计所有 folder。
            include_deleted: 是否包含软删除的记录，默认 False。

        Returns:
            ``{bucket_name: count}`` 字典，默认包含 inherent、emergent、archived三个键。
        """
        counts: dict[str, int] = {"inherent": 0, "emergent": 0, "archived": 0}
        R = BookuMemoryRecordModel
        async with self._db.session() as s:
            stmt = (
                select(R.bucket, func.count())
                .select_from(R)
                .group_by(R.bucket)
            )
            if folder_id is not None:
                stmt = stmt.where(R.folder_id == folder_id)
            if not include_deleted:
                stmt = stmt.where(R.is_deleted == 0)
            for bucket, cnt in (await s.execute(stmt)).all():
                key = str(bucket)
                counts[key] = counts.get(key, 0) + int(cnt)
        return counts

    async def get_recent_records(
        self,
        *,
        limit: int = 10,
        folder_id: str | None = None,
        include_archived: bool = True,
        include_deleted: bool = False,
    ) -> list[BookuMemoryRecord]:
        """获取最近更新的记忆列表，按 updated_at 倒序排序。

        Args:
            limit: 返回的最大条数，默认 10。
            folder_id: 限定范围的 folder，``None`` 时不限定。
            include_archived: 是否包含归档层记录，默认 True。
            include_deleted: 是否包含软删除的记录，默认 False。

        Returns:
            按时间倒序排序的 ``BookuMemoryRecord`` 列表。
        """
        qb = self._db.query(BookuMemoryRecordModel)
        if folder_id is not None:
            qb = qb.filter(folder_id=folder_id)
        if not include_archived:
            qb = qb.filter(bucket__ne="archived")
        if not include_deleted:
            qb = qb.filter(is_deleted=0)
        rows = await qb.order_by("-updated_at").limit(max(1, limit)).all()
        return [self._to_record(r) for r in rows]  # type: ignore[arg-type]

    async def list_knowledge_chunk_titles(
        self,
        *,
        folder_id: str = "default",
    ) -> list[str]:
        """查询知识库中所有 chunk 的去重标题，不受数量限制。

        与 list_records_by_bucket 不同，此方法使用 SELECT DISTINCT title 直接去重，
        不会因 limit 截断而遗漏旧文档，专用于 export_document_titles 等需要枚举
        知识库全集的场景。

        Args:
            folder_id: 限定文件夹 ID，默认 "default"。

        Returns:
            去重后的 chunk 标题字符串列表（包含"-片段N"后缀，由调用方规整）。
        """
        R = BookuMemoryRecordModel
        stmt = (
            select(distinct(R.title))
            .where(R.bucket == "knowledge")
            .where(R.folder_id == folder_id)
            .where(R.is_deleted == 0)
        )
        async with self._db.session() as s:
            result = await s.execute(stmt)
            return [str(row[0]) for row in result.fetchall()]

    async def list_records_by_bucket(
        self,
        *,
        bucket: str,
        folder_id: str | None = None,
        limit: int = 300,
        include_deleted: bool = False,
    ) -> list[BookuMemoryRecord]:
        """按 bucket 列出记忆记录列表。

        主要用于 prompt 注入类功能（如“记忆闪回”）快速加载候选。
        为避免全表扫描，结果按 updated_at 倒序并截断到 limit。

        Args:
            bucket: 目标 bucket（如 "emergent"、"archived"、"inherent"）。
            folder_id: 限定文件夹；为 None 时不限定。
            limit: 最大返回条数，至少为 1。
            include_deleted: 是否包含已删除记录，默认 False。

        Returns:
            满足条件的 ``BookuMemoryRecord`` 列表（按 updated_at 倒序）。
        """

        qb = self._db.query(BookuMemoryRecordModel).filter(bucket=bucket)
        if folder_id is not None:
            qb = qb.filter(folder_id=folder_id)
        if not include_deleted:
            qb = qb.filter(is_deleted=0)
        rows = await qb.order_by("-updated_at").limit(max(1, int(limit))).all()
        return [self._to_record(r) for r in rows]  # type: ignore[arg-type]

    async def list_memory_ids_by_folder(
        self,
        *,
        folder_id: str,
        include_archived: bool = True,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> list[str]:
        """列出指定 folder 中所有内容的 memory_id 列表。

        用于判断某个 folder 是否为空、防止在空 folder 上进行无意义检索。

        Args:
            folder_id: 目标文件夹 ID。
            include_archived: 是否包含归档层记录，默认 True。
            include_deleted: 是否包含已删除记录，默认 False。
            limit: 最大返回条数，默认 200。

        Returns:
            按 updated_at 倒序排序的 memory_id 字符串列表。
        """
        qb = self._db.query(BookuMemoryRecordModel).filter(folder_id=folder_id)
        if not include_archived:
            qb = qb.filter(bucket__ne="archived")
        if not include_deleted:
            qb = qb.filter(is_deleted=0)
        rows = await qb.order_by("-updated_at").limit(max(1, limit)).all()
        return [r.memory_id for r in rows]  # type: ignore[union-attr]

    async def search_records_grep(
        self,
        *,
        query: str,
        search_fields: list[str],
        folder_id: str | None = None,
        include_archived: bool = False,
        include_deleted: bool = False,
        limit: int = 20,
        use_regex: bool = False,
    ) -> list[str]:
        """在指定字段中匹配关键词或正则表达式，返回 memory_id 列表。

        支持两种匹配模式：
        - ``use_regex=False``（默认）：SQL ``LIKE '%keyword%'`` 子串匹配，速度快。
        - ``use_regex=True``：Python ``re.search()`` 正则匹配，先拉取候选行再在内存中过滤。

        支持的范围标识：
        - ``title``：匹配记忆标题。
        - ``summary``/``content``：匹配正文内容。
        - ``tags``：匹配标签值（通过子查询 tag 表）。
        - ``metadata``：匹配 memory_id、source、folder_id、bucket 字段。
        不支持的范围标识将被忽略，默认回落到 ``title``+``content``。

        Args:
            query: 关键词字符串或正则表达式，为空时直接返回空列表。
            search_fields: 搜索范围列表。
            folder_id: 限定 folder，``None`` 时全局搜索。
            include_archived: 是否包含归档层，默认 False。
            include_deleted: 是否包含已删除记录，默认 False。
            limit: 最大返回条数，默认 20。
            use_regex: 为 ``True`` 时启用 Python 正则匹配，默认 False（LIKE 匹配）。

        Returns:
            按 updated_at 倒序排序的 memory_id 列表，全局唯一。

        Raises:
            ValueError: ``use_regex=True`` 且 query 不是合法正则表达式时抛出。
        """
        keyword = query.strip()
        if not keyword:
            return []

        allowed_fields = {"title", "summary", "content", "tags", "metadata"}
        normalized_fields = [f for f in search_fields if f in allowed_fields] or ["title", "content"]

        R = BookuMemoryRecordModel
        T = BookuMemoryTagModel

        # ------------------------------------------------------------------ #
        # 正则模式：在 Python 层过滤，避免依赖 SQLite REGEXP 扩展              #
        # ------------------------------------------------------------------ #
        if use_regex:
            try:
                pattern = re.compile(keyword)
            except re.error as exc:
                raise ValueError(f"无效的正则表达式: {exc}") from exc

            need_tags = "tags" in normalized_fields

            async with self._db.session() as s:
                where_parts: list[Any] = []
                if folder_id is not None:
                    where_parts.append(R.folder_id == folder_id)
                if not include_archived:
                    where_parts.append(R.bucket != "archived")
                if not include_deleted:
                    where_parts.append(R.is_deleted == 0)

                # 拉取所有候选行的可检索字段（不做文本过滤，交由 Python 处理）
                cand_stmt = (
                    select(R.memory_id, R.title, R.content, R.source, R.folder_id, R.bucket)
                    .where(*where_parts)
                    .order_by(R.updated_at.desc())
                )
                cand_result = await s.execute(cand_stmt)
                rows = cand_result.all()

                # 预构建 tags 映射（仅在需要匹配 tags 字段时才查询）
                tag_map: dict[str, list[str]] = {}
                if need_tags and rows:
                    all_ids = [str(row[0]) for row in rows]
                    tag_stmt = select(T.memory_id, T.tag_value).where(T.memory_id.in_(all_ids))
                    tag_result = await s.execute(tag_stmt)
                    for tmid, tval in tag_result.all():
                        tag_map.setdefault(str(tmid), []).append(str(tval))

                matched: list[str] = []
                for row in rows:
                    mid, title, content, source, fid, bucket = (
                        str(row[0]), str(row[1] or ""), str(row[2] or ""),
                        str(row[3] or ""), str(row[4] or ""), str(row[5] or ""),
                    )
                    hit = False
                    if "title" in normalized_fields and pattern.search(title):
                        hit = True
                    if not hit and ("summary" in normalized_fields or "content" in normalized_fields):
                        hit = bool(pattern.search(content))
                    if not hit and "metadata" in normalized_fields:
                        hit = any(pattern.search(v) for v in (mid, source, fid, bucket))
                    if not hit and need_tags:
                        hit = any(pattern.search(tag) for tag in tag_map.get(mid, []))
                    if hit:
                        matched.append(mid)
                        if len(matched) >= max(1, limit):
                            break

                return matched

        # ------------------------------------------------------------------ #
        # LIKE 模式（默认）：全部在 SQL 层完成，性能更优                       #
        # ------------------------------------------------------------------ #
        like_value = f"%{keyword}%"

        async with self._db.session() as s:
            where_parts_like: list[Any] = []
            if folder_id is not None:
                where_parts_like.append(R.folder_id == folder_id)
            if not include_archived:
                where_parts_like.append(R.bucket != "archived")
            if not include_deleted:
                where_parts_like.append(R.is_deleted == 0)

            matchers: list[Any] = []
            if "title" in normalized_fields:
                matchers.append(R.title.like(like_value))
            if "summary" in normalized_fields or "content" in normalized_fields:
                matchers.append(R.content.like(like_value))
            if "metadata" in normalized_fields:
                matchers.extend([
                    R.memory_id.like(like_value),
                    R.source.like(like_value),
                    R.folder_id.like(like_value),
                    R.bucket.like(like_value),
                ])
            if "tags" in normalized_fields:
                matchers.append(
                    select(T.memory_id)
                    .where(T.memory_id == R.memory_id, T.tag_value.like(like_value))
                    .exists()
                )

            if not matchers:
                return []

            stmt = (
                select(distinct(R.memory_id))
                .where(*where_parts_like, or_(*matchers))
                .order_by(R.updated_at.desc())
                .limit(max(1, limit))
            )
            result = await s.execute(stmt)
            return [str(row[0]) for row in result.all()]
