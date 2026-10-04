"""LM 记忆更新提案的校验与确认边界。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import (
    CueSetModel,
    EpisodeModel,
    MemoryModel,
    MemoryRevisionModel,
    MemoryRevisionSubjectModel,
    MemoryUpdateProposalModel,
    WorkingMemoryModel,
)
from .schema import VNextSchema

_OPERATIONS = frozenset({"support", "contradict", "supersede", "uncertain"})
_PENDING = "PENDING"
_CONFIRMED = "CONFIRMED"
_REJECTED = "REJECTED"
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_STOPWORDS = frozenset({"的", "了", "是", "我", "你", "他", "她", "很", "在", "有", "和"})


def _proposal_tokens(text: str) -> tuple[str, ...]:
    """提取用于保守纠正匹配的稳定低成本词元。"""
    return tuple(dict.fromkeys(
        token.casefold()
        for token in _TOKEN_PATTERN.findall(text)
        if token.casefold() not in _STOPWORDS
    ))


@dataclass(frozen=True, slots=True)
class MemoryUpdateProposal:
    """供 LM 和确认层之间传递的不可变提案。"""

    proposal_id: str
    stream_id: str
    claim: str
    evidence_ids: tuple[str, ...]
    operation: str
    target_memory_id: str | None
    confidence: float
    status: str


def _view(row: MemoryUpdateProposalModel) -> MemoryUpdateProposal:
    """转换 ORM 提案为不可变视图。"""
    return MemoryUpdateProposal(
        proposal_id=row.proposal_id,
        stream_id=row.stream_id,
        claim=row.claim,
        evidence_ids=tuple(str(item) for item in row.evidence_ids),
        operation=row.operation,
        target_memory_id=row.target_memory_id,
        confidence=float(row.confidence),
        status=row.status,
    )


class ProposalService:
    """校验证据并保存待确认更新，不直接执行正式记忆变更。"""

    def __init__(self, schema: VNextSchema) -> None:
        """绑定 Episode/Memory 数据库。"""
        self._schema = schema

    async def propose(
        self,
        *,
        stream_id: str,
        claim: str,
        evidence_ids: tuple[str, ...],
        operation: str,
        target_memory_id: str | None = None,
        confidence: float = 0.5,
    ) -> MemoryUpdateProposal:
        """验证提案并以 PENDING 状态保存。"""
        if not stream_id.strip() or not claim.strip():
            raise ValueError("提案必须提供 stream_id 和 claim")
        if operation not in _OPERATIONS:
            raise ValueError(f"不支持的提案 operation: {operation}")
        unique_evidence = tuple(dict.fromkeys(item.strip() for item in evidence_ids if item.strip()))
        if not unique_evidence:
            raise ValueError("提案必须至少引用一个 Episode evidence_id")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence 必须在 0-1 之间")
        if operation in {"contradict", "supersede"} and not target_memory_id:
            raise ValueError(f"{operation} 提案必须指定 target_memory_id")
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            episodes = tuple(
                (
                    await session.scalars(
                        select(EpisodeModel).where(
                            EpisodeModel.episode_id.in_(unique_evidence),
                            EpisodeModel.stream_id == stream_id,
                        )
                    )
                ).all()
            )
            if {item.episode_id for item in episodes} != set(unique_evidence):
                raise ValueError("提案 Evidence 必须是当前聊天流中的 Episode")
            if any(item.episode_kind in {"CUE", "OUTPUT"} for item in episodes):
                raise ValueError("查询线索和机器人输出不能作为事实提案 Evidence")
            if target_memory_id is not None:
                memory = await session.get(MemoryModel, target_memory_id)
                if memory is None:
                    raise ValueError("target_memory_id 不存在")
            row = MemoryUpdateProposalModel(
                proposal_id=str(uuid4()),
                stream_id=stream_id,
                claim=claim.strip(),
                evidence_ids=list(unique_evidence),
                operation=operation,
                target_memory_id=target_memory_id,
                confidence=confidence,
                status=_PENDING,
                created_at=now,
            )
            session.add(row)
            await session.flush()
            return _view(row)

    async def collect_consolidation_candidates(self, stream_id: str) -> tuple[str, ...]:
        """按纠正、显著性和跨轮激活筛选待审候选，不自动确认正式事实。"""
        now = datetime.now(UTC)
        collected: list[str] = []
        async with self._schema.database.session() as session:
            episodes = (await session.scalars(select(EpisodeModel).where(
                EpisodeModel.stream_id == stream_id,
                EpisodeModel.episode_kind.in_(("INPUT", "SUMMARY")),
            ).order_by(EpisodeModel.observed_at.desc()).limit(100))).all()
            workings = (await session.scalars(select(WorkingMemoryModel).where(
                WorkingMemoryModel.stream_id == stream_id,
            ).order_by(WorkingMemoryModel.created_at.desc()).limit(100))).all()
            cue_ids = {working.cue_id for working in workings}
            cues = (await session.scalars(select(CueSetModel).where(
                CueSetModel.stream_id == stream_id,
                CueSetModel.episode_id.in_([episode.episode_id for episode in episodes])
                | CueSetModel.cue_id.in_(cue_ids),
            ))).all()
            cues_by_id = {cue.cue_id: cue for cue in cues}
            corrected = {cue.episode_id for cue in cues if cue.hard_cues.get("explicit_correction")}
            active_memories = (await session.execute(
                select(
                    MemoryModel.memory_id,
                    MemoryRevisionSubjectModel.person_id,
                    MemoryRevisionModel.title,
                    MemoryRevisionModel.content,
                )
                .join(
                    MemoryRevisionModel,
                    MemoryRevisionModel.revision_id == MemoryModel.current_revision_id,
                )
                .join(
                    MemoryRevisionSubjectModel,
                    MemoryRevisionSubjectModel.revision_id == MemoryRevisionModel.revision_id,
                )
                .where(MemoryModel.status == "ACTIVE")
            )).all()
            activation_turns: dict[str, set[str]] = {}
            for working in workings:
                cue = cues_by_id.get(working.cue_id)
                if cue is None or cue.episode_id is None:
                    continue
                for selected in working.selected_episodes:
                    episode_id = str(selected.get("episode_id") or "")
                    activation_turns.setdefault(episode_id, set()).add(cue.episode_id)
            for episode in episodes:
                reasons = []
                if episode.episode_id in corrected:
                    reasons.append("explicit_correction_cue")
                if episode.salience >= 0.6:
                    reasons.append("high_salience")
                if len(activation_turns.get(episode.episode_id, set())) >= 3:
                    reasons.append("repeated_activation")
                if not reasons:
                    continue
                operation = "uncertain"
                target_memory_id = None
                if episode.episode_id in corrected and episode.participants:
                    episode_topics = set(episode.topics)
                    matches = [
                        row for row in active_memories
                        if row.person_id in episode.participants
                        and episode_topics
                        & set(_proposal_tokens(f"{row.title} {row.content}"))
                    ]
                    if len(matches) == 1:
                        operation = "supersede"
                        target_memory_id = matches[0].memory_id
                        reasons.append("unique_person_topic_match")
                proposal_id = str(uuid5(NAMESPACE_URL, f"engram:consolidation:{episode.episode_id}"))
                result = await session.execute(sqlite_insert(MemoryUpdateProposalModel).values(
                    proposal_id=proposal_id, stream_id=stream_id,
                    claim=episode.raw_text, evidence_ids=[episode.episode_id],
                    operation=operation, target_memory_id=target_memory_id,
                    confidence=min(float(episode.certainty), 0.5),
                    status=_PENDING, created_at=now,
                ).on_conflict_do_nothing(index_elements=["proposal_id"]))
                if result.rowcount != 1:
                    continue
                session.add(CueSetModel(
                    cue_id=str(uuid4()), episode_id=episode.episode_id,
                    stream_id=stream_id, hard_cues={},
                    soft_cues={"consolidation": {"proposal_id": proposal_id, "reasons": reasons}},
                    created_at=now,
                ))
                collected.append(proposal_id)
                if len(collected) >= 5:
                    break
        return tuple(collected)

    async def list_pending(self, stream_id: str, limit: int = 5) -> tuple[dict[str, object], ...]:
        """返回当前流待审核目录，保留来源 ID 和不确定状态。"""
        if not 1 <= limit <= 20:
            raise ValueError("limit 必须在 1-20 之间")
        async with self._schema.database.session() as session:
            rows = (await session.scalars(select(MemoryUpdateProposalModel).where(
                MemoryUpdateProposalModel.stream_id == stream_id,
                MemoryUpdateProposalModel.status == _PENDING,
            ).order_by(MemoryUpdateProposalModel.created_at.desc()).limit(limit))).all()
            return tuple({
                "proposal_id": row.proposal_id, "claim": row.claim,
                "evidence_ids": list(row.evidence_ids), "operation": row.operation,
                "confidence": row.confidence, "status": row.status,
            } for row in rows)

    async def get(self, proposal_id: str) -> MemoryUpdateProposal | None:
        """读取提案，不执行任何变更。"""
        async with self._schema.database.session() as session:
            row = await session.get(MemoryUpdateProposalModel, proposal_id)
            return _view(row) if row is not None else None

    async def reject(self, proposal_id: str) -> MemoryUpdateProposal:
        """拒绝待确认提案，保留审计记录。"""
        async with self._schema.database.session() as session:
            result = await session.execute(
                update(MemoryUpdateProposalModel)
                .where(
                    MemoryUpdateProposalModel.proposal_id == proposal_id,
                    MemoryUpdateProposalModel.status == _PENDING,
                )
                .values(status=_REJECTED, reviewed_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                raise ValueError("提案不存在或已被处理")
            row = await session.get(MemoryUpdateProposalModel, proposal_id)
            if row is None:
                raise ValueError("提案提交后无法读取")
            return _view(row)

    async def mark_confirmed(self, proposal_id: str) -> MemoryUpdateProposal:
        """在正式巩固成功后原子地标记提案已确认。"""
        async with self._schema.database.session() as session:
            result = await session.execute(
                update(MemoryUpdateProposalModel)
                .where(
                    MemoryUpdateProposalModel.proposal_id == proposal_id,
                    MemoryUpdateProposalModel.status == _PENDING,
                )
                .values(status=_CONFIRMED, reviewed_at=datetime.now(UTC))
            )
            if result.rowcount != 1:
                raise ValueError("提案不存在或已被处理")
            row = await session.get(MemoryUpdateProposalModel, proposal_id)
            if row is None:
                raise ValueError("提案提交后无法读取")
            return _view(row)
