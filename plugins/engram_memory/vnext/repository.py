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
        """将核心人物哈希归一为平台账号，仅返回单一人物标识。"""
        normalized = person_id.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized):
            return (normalized,)
        person = await person_api.get_person_by_id(normalized)
        if person is not None:
            if person_api.generate_person_id(person.platform, person.user_id) != normalized:
                raise ValueError("人物哈希与平台账号不一致")
            return (person_api.generate_raw_person_id(person.platform, person.user_id),)
        rows = await self._person_snapshot_rows(normalized)
        aliases = self._person_aliases_from_rows(normalized, rows)
        if aliases == (normalized,):
            raise ValueError("人物哈希缺少可核实的平台账号")
        return aliases

    async def get_person_metadata(self, person_id: str) -> dict[str, object] | None:
        """从未删除的消息快照中读取基本人物标识信息。"""
        normalized = person_id.strip()
        if not normalized or normalized == "bot":
            return None
        if ":" not in normalized:
            rows = await self._person_snapshot_rows(normalized)
            aliases = self._person_aliases_from_rows(normalized, rows)
            if aliases == (normalized,):
                return None
            normalized = aliases[0]
        platform, _, sender_id = normalized.partition(":")
        if not platform or not sender_id:
            return None
        matching_rows = await self._person_snapshot_rows(normalized)
        matching = tuple(
            row
            for row in matching_rows
            if row.get("platform") == platform
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
        """仅在账号唯一且哈希校验通过时返回平台标识。"""
        if not person_id or person_id == "bot":
            return (person_id,)
        if ":" in person_id:
            return (person_id,)
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
        if person_api.generate_person_id(platform, sender_id) != person_id:
            return (person_id,)
        return (person_api.generate_raw_person_id(platform, sender_id),)
