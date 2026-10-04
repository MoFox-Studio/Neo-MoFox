"""Engram Memory vNext 规范数据只读仓储。"""

from __future__ import annotations

import re

from sqlalchemy import select

from src.app.plugin_system.api import person_api

from .models import (
    EvidenceMessageSnapshotModel,
    EvidenceModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRevisionModel,
    RevisionEvidenceModel,
)
from .schema import VNextSchema


class MemoryRepository:
    """提供不绕过领域约束的正式记忆读取能力。"""

    def __init__(self, schema: VNextSchema) -> None:
        """绑定 vNext Schema。"""
        self._schema = schema

    async def get_memory(self, memory_id: str) -> MemoryModel | None:
        """按稳定标识读取记忆身份。"""
        async with self._schema.database.session() as session:
            return await session.get(MemoryModel, memory_id)

    async def get_current_revision(self, memory_id: str) -> MemoryRevisionModel | None:
        """读取正式记忆当前版本。"""
        statement = (
            select(MemoryRevisionModel)
            .join(
                MemoryModel,
                MemoryModel.current_revision_id == MemoryRevisionModel.revision_id,
            )
            .where(MemoryModel.memory_id == memory_id)
        )
        async with self._schema.database.session() as session:
            return (await session.scalars(statement)).one_or_none()

    async def list_revisions(self, memory_id: str) -> tuple[MemoryRevisionModel, ...]:
        """按版本号升序读取完整 Revision 历史。"""
        statement = (
            select(MemoryRevisionModel)
            .where(MemoryRevisionModel.memory_id == memory_id)
            .order_by(MemoryRevisionModel.revision_no)
        )
        async with self._schema.database.session() as session:
            return tuple((await session.scalars(statement)).all())

    async def list_evidence(
        self, memory_id: str, revision_id: str | None = None
    ) -> tuple[EvidenceModel, ...]:
        """读取记忆全部版本或指定版本关联的去重 Evidence。"""
        conditions = [MemoryRevisionModel.memory_id == memory_id]
        if revision_id is not None:
            conditions.append(RevisionEvidenceModel.revision_id == revision_id)
        statement = (
            select(EvidenceModel)
            .join(
                RevisionEvidenceModel,
                RevisionEvidenceModel.evidence_id == EvidenceModel.evidence_id,
            )
            .join(
                MemoryRevisionModel,
                MemoryRevisionModel.revision_id == RevisionEvidenceModel.revision_id,
            )
            .where(*conditions)
            .distinct()
            .order_by(EvidenceModel.created_at, EvidenceModel.evidence_id)
        )
        async with self._schema.database.session() as session:
            return tuple((await session.scalars(statement)).all())

    async def list_events(self, memory_id: str) -> tuple[MemoryEventModel, ...]:
        """按发生时间稳定排序读取追加式事件历史。"""
        statement = (
            select(MemoryEventModel)
            .where(MemoryEventModel.memory_id == memory_id)
            .order_by(MemoryEventModel.occurred_at, MemoryEventModel.event_id)
        )
        async with self._schema.database.session() as session:
            return tuple((await session.scalars(statement)).all())

    async def resolve_person_aliases(self, person_id: str) -> tuple[str, ...]:
        """解析确定的人物别名；无消息快照时使用框架的平台身份生成规则。"""
        normalized = person_id.strip()
        if not normalized or normalized == "bot":
            return (person_id,)
        rows = await self._person_snapshot_rows(normalized)
        if not rows and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*:[^\s:]+", normalized):
            platform, user_id = normalized.split(":", 1)
            return (person_api.generate_person_id(platform, user_id), normalized)
        aliases = self._person_aliases_from_rows(normalized, rows)
        if len(aliases) != 2:
            return aliases
        core_id, legacy_id = aliases
        legacy_rows = await self._person_snapshot_rows(legacy_id)
        if self._person_aliases_from_rows(legacy_id, legacy_rows) != aliases:
            return (person_id,)
        return aliases

    async def get_person_metadata(self, person_id: str) -> dict[str, object] | None:
        """从未删除的消息快照中读取基本人物标识信息。"""
        normalized = person_id.strip()
        if not normalized or normalized == "bot":
            return None
        aliases = await self.resolve_person_aliases(normalized)
        if len(aliases) != 2:
            return None
        core_id, legacy_id = aliases
        platform, _, sender_id = legacy_id.partition(":")
        matching_rows = await self._person_snapshot_rows(legacy_id)
        matching = tuple(
            row
            for row in matching_rows
            if row.get("person_id") == core_id
            and row.get("platform") == platform
            and row.get("sender_id") == sender_id
        )
        if not matching:
            return None
        latest = matching[0]
        return {
            "platform": platform,
            "user_id": sender_id,
            "nickname": latest.get("nickname"),
            "cardname": latest.get("cardname"),
        }

    async def _person_snapshot_rows(
        self, person_id: str
    ) -> tuple[dict[str, object], ...]:
        """仅读取匹配且未删除的本地快照人物元数据。"""
        payload = EvidenceMessageSnapshotModel.payload
        conditions = [EvidenceMessageSnapshotModel.redacted_at.is_(None)]
        if ":" in person_id:
            platform, sender_id = person_id.split(":", 1)
            if not platform or not sender_id:
                return ()
            conditions.extend(
                (
                    payload["platform"].as_string() == platform,
                    payload["sender_id"].as_string() == sender_id,
                )
            )
        else:
            conditions.append(payload["person_id"].as_string() == person_id)
        statement = (
            select(
                payload["platform"].as_string().label("platform"),
                payload["sender_id"].as_string().label("sender_id"),
                payload["person_id"].as_string().label("person_id"),
                payload["sender_name"].as_string().label("nickname"),
                payload["sender_cardname"].as_string().label("cardname"),
                EvidenceMessageSnapshotModel.captured_at.label("captured_at"),
            )
            .where(*conditions)
            .order_by(
                EvidenceMessageSnapshotModel.captured_at.desc(),
                EvidenceMessageSnapshotModel.stream_id,
                EvidenceMessageSnapshotModel.message_id,
            )
        )
        async with self._schema.database.session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _person_aliases_from_rows(
        person_id: str,
        rows: tuple[dict[str, object], ...],
    ) -> tuple[str, ...]:
        """仅在快照元数据唯一确定人物身份时返回别名。"""
        if not person_id or person_id == "bot":
            return (person_id,)
        if ":" in person_id:
            core_ids = {
                row["person_id"]
                for row in rows
                if isinstance(row.get("person_id"), str) and row["person_id"].strip()
            }
            if "bot" in core_ids or len(core_ids) != 1:
                return (person_id,)
            core_id = next(iter(core_ids))
            if (
                not isinstance(core_id, str)
                or core_id == person_id
                or not core_id.strip()
            ):
                return (person_id,)
            if not any(
                row.get("platform") == person_id.partition(":")[0]
                and row.get("sender_id") == person_id.partition(":")[2]
                for row in rows
            ):
                return (person_id,)
            return (core_id, person_id)
        aliases = {
            (row["platform"], row["sender_id"])
            for row in rows
            if row.get("person_id") == person_id
            and isinstance(row.get("platform"), str)
            and row["platform"].strip()
            and isinstance(row.get("sender_id"), str)
            and row["sender_id"].strip()
        }
        if len(aliases) != 1:
            return (person_id,)
        platform, sender_id = next(iter(aliases))
        alias = f"{platform}:{sender_id}"
        return (person_id, alias) if alias != person_id else (person_id,)
