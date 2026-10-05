"""正式记忆强度衰减与遗忘候选服务。"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from sqlalchemy import select

from .domain import MemoryDecayCandidate, MemoryStrength
from .enums import EvidenceSourceType, MemoryEventType, MemoryStatus
from .models import (
    EvidenceModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRevisionModel,
    RevisionEvidenceModel,
)
from .schema import VNextSchema


class MemoryDecayService:
    """计算正式记忆强度，并返回需要后续审核的遗忘候选。"""

    _SOURCE_WEIGHTS: dict[EvidenceSourceType, float] = {
        EvidenceSourceType.ACTOR_WRITE: 1.0,
        EvidenceSourceType.ADMIN: 1.0,
        EvidenceSourceType.MESSAGE_SET: 0.85,
        EvidenceSourceType.EXTERNAL: 0.70,
        EvidenceSourceType.SYSTEM_EVENT: 0.60,
        EvidenceSourceType.LEGACY_RECORD: 0.45,
    }
    _SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？.!?])\s*")

    def __init__(
        self,
        schema: VNextSchema,
        *,
        half_life_days: float = 90.0,
        recall_half_life_days: float = 30.0,
        forget_threshold: float = 0.12,
        min_age_days: float = 30.0,
    ) -> None:
        """绑定数据库与衰减参数。"""
        if half_life_days <= 0 or recall_half_life_days <= 0:
            raise ValueError("衰减半衰期必须大于 0")
        if not 0 <= forget_threshold <= 1:
            raise ValueError("forget_threshold 必须在 0-1 之间")
        if min_age_days < 0:
            raise ValueError("min_age_days 不能为负")
        self._schema = schema
        self._half_life_days = half_life_days
        self._recall_half_life_days = recall_half_life_days
        self._forget_threshold = forget_threshold
        self._min_age_days = min_age_days

    async def strength(
        self,
        memory_id: str,
        *,
        now: datetime | None = None,
    ) -> MemoryStrength | None:
        """返回一条 ACTIVE 记忆的当前强度及组成数据。"""
        values = await self._load_strength_rows((memory_id,))
        if not values:
            return None
        return self._calculate(values[0], now=now)

    async def list_decay_candidates(
        self,
        *,
        limit: int = 20,
        now: datetime | None = None,
    ) -> tuple[MemoryDecayCandidate, ...]:
        """返回低强度且已过最小年龄的候选，不自动作废记忆。"""
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1-100 之间")
        rows = await self._load_strength_rows(())
        current = now or datetime.now(UTC)
        candidates: list[MemoryDecayCandidate] = []
        for row in rows:
            strength = self._calculate(row, now=current)
            if strength.age_days < self._min_age_days:
                continue
            if strength.strength > self._forget_threshold:
                continue
            candidates.append(
                MemoryDecayCandidate(
                    memory_id=strength.memory_id,
                    title=row["title"],
                    strength=strength.strength,
                    reason=self._reason(strength),
                    last_experienced_at=strength.last_experienced_at,
                )
            )
        candidates.sort(key=lambda item: (item.strength, item.memory_id))
        return tuple(candidates[:limit])

    @classmethod
    def recall_view(
        cls,
        *,
        title: str,
        content: str,
        strength: float,
    ) -> dict[str, object]:
        """按强度生成只用于回忆展示的渐进模糊视图，不改写原文。"""
        if not 0 <= strength <= 1:
            raise ValueError("strength 必须在 0-1 之间")
        normalized_title = title.strip()
        normalized_content = content.strip()
        if strength >= 0.65:
            return {
                "detail_level": "detailed",
                "content": normalized_content,
                "blurred": False,
            }

        sentences = tuple(
            part.strip()
            for part in cls._SENTENCE_BOUNDARY.split(normalized_content)
            if part.strip()
        )
        if strength >= 0.40:
            retained = " ".join(sentences[:2]) or normalized_content[:240]
            return {
                "detail_level": "partial",
                "content": retained,
                "blurred": True,
                "notice": "较久未想起，部分细节可能已模糊；不要补全未提供的信息。",
            }
        if strength >= 0.20:
            retained = sentences[0] if sentences else normalized_content[:120]
            return {
                "detail_level": "gist",
                "content": retained,
                "blurred": True,
                "notice": "只剩大意，时间、数量和具体经过不应当作精确回忆。",
            }
        return {
            "detail_level": "faint",
            "content": normalized_title or "一段较模糊的旧经历",
            "blurred": True,
            "notice": "这段记忆已经很模糊，只能确认曾有相关经历，不能据此断言细节。",
        }

    async def _load_strength_rows(
        self,
        memory_ids: tuple[str, ...],
    ) -> list[dict[str, object]]:
        """读取计算强度所需的当前版本、证据和事件聚合数据。"""
        async with self._schema.database.session() as session:
            statement = (
                select(
                    MemoryModel.memory_id,
                    MemoryModel.last_experienced_at,
                    MemoryModel.created_at,
                    MemoryRevisionModel.title,
                    MemoryRevisionModel.observed_at,
                    EvidenceModel.evidence_id,
                    EvidenceModel.source_type,
                    EvidenceModel.observed_at.label("evidence_observed_at"),
                    MemoryEventModel.event_id,
                    MemoryEventModel.event_type,
                    MemoryEventModel.occurred_at.label("event_occurred_at"),
                )
                .join(
                    MemoryRevisionModel,
                    MemoryRevisionModel.revision_id == MemoryModel.current_revision_id,
                )
                .outerjoin(
                    RevisionEvidenceModel,
                    RevisionEvidenceModel.revision_id == MemoryRevisionModel.revision_id,
                )
                .outerjoin(
                    EvidenceModel,
                    EvidenceModel.evidence_id == RevisionEvidenceModel.evidence_id,
                )
                .outerjoin(
                    MemoryEventModel,
                    MemoryEventModel.memory_id == MemoryModel.memory_id,
                )
                .where(MemoryModel.status == MemoryStatus.ACTIVE)
            )
            if memory_ids:
                statement = statement.where(MemoryModel.memory_id.in_(memory_ids))
            rows = (await session.execute(statement)).all()

        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            item = grouped.setdefault(
                row.memory_id,
                {
                    "memory_id": row.memory_id,
                    "title": str(row.title or ""),
                    "last_experienced_at": row.last_experienced_at,
                    "created_at": row.created_at,
                    "observed_at": row.observed_at,
                    "sources": set(),
                    "evidence_ids": set(),
                    "recall_events": {},
                },
            )
            if row.source_type is not None:
                item["sources"].add(row.source_type)
            if row.evidence_id is not None:
                item["evidence_ids"].add(row.evidence_id)
            if (
                row.event_id is not None
                and row.event_type is MemoryEventType.RECALLED
                and row.event_occurred_at is not None
            ):
                item["recall_events"].setdefault(row.event_id, row.event_occurred_at)
        return list(grouped.values())

    def _calculate(
        self,
        row: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> MemoryStrength:
        """根据聚合行计算有界、可解释的强度分数。"""
        current = now or datetime.now(UTC)
        experienced = row["last_experienced_at"] or row["observed_at"] or row["created_at"]
        assert isinstance(experienced, datetime)
        age_days = max(0.0, (current - experienced).total_seconds() / 86400.0)
        age_factor = math.exp(-math.log(2.0) * age_days / self._half_life_days)

        recall_dates = [
            value
            for value in row["recall_events"].values()
            if isinstance(value, datetime)
        ]
        recall_factor = sum(
            math.exp(
                -math.log(2.0)
                * max(0.0, (current - recalled).total_seconds() / 86400.0)
                / self._recall_half_life_days
            )
            for recalled in recall_dates
        )
        recall_bonus = min(0.35, 0.07 * recall_factor)
        source_factor = max(
            (self._SOURCE_WEIGHTS.get(source, 0.5) for source in row["sources"]),
            default=0.5,
        )
        evidence_bonus = min(0.20, max(0, len(row["evidence_ids"]) - 1) * 0.05)
        strength = min(1.0, max(0.0, age_factor * (0.65 + 0.35 * source_factor) + recall_bonus + evidence_bonus))
        last_recalled_at = max(recall_dates, default=None)
        return MemoryStrength(
            memory_id=str(row["memory_id"]),
            strength=strength,
            age_days=age_days,
            evidence_count=len(row["evidence_ids"]),
            recall_count=len(recall_dates),
            last_recalled_at=last_recalled_at,
            last_experienced_at=experienced,
        )

    @staticmethod
    def _reason(strength: MemoryStrength) -> str:
        """生成供审核器阅读的候选原因。"""
        return (
            f"强度 {strength.strength:.3f}，距最近经历 {strength.age_days:.1f} 天，"
            f"证据 {strength.evidence_count} 条，召回 {strength.recall_count} 次"
        )


__all__ = ["MemoryDecayService"]