"""Claim/Hypothesis 解析、模型审核与待确认提案桥接。"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update

from src.app.plugin_system.api import llm_api
from src.kernel.llm import LLMPayload, ROLE, Text

from .models import (
    ClaimHypothesisModel,
    EpisodeModel,
    EpisodeSemanticModel,
    MemoryModel,
    MemoryUpdateProposalModel,
)
from .proposal_service import ProposalService
from .schema import VNextSchema

_UUID_PATTERN = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
    re.IGNORECASE,
)
_QUOTE_PATTERN = re.compile(r"[\"“「『](.{2,160})[\"”」』]")
_NAME_PATTERN = re.compile(r"(?:我叫|我名叫|名字是|叫做|名叫)([\u4e00-\u9fffA-Za-z0-9_]{2,24})")
_CORRECTION_TERMS = ("不是", "不对", "纠正", "更正", "记错", "不再", "已经不")
_PLAN_TERMS = ("计划", "打算", "准备", "想要", "将要", "以后")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_STOPWORDS = frozenset({"的", "了", "是", "我", "你", "他", "她", "很", "在", "有", "和"})


@dataclass(frozen=True, slots=True)
class SemanticView:
    """一条可审计的 Episode 语义解析结果。"""

    semantic_id: str
    episode_id: str
    referenced_episode_ids: tuple[str, ...]
    entities: tuple[dict[str, object], ...]
    events: tuple[dict[str, object], ...]
    extraction_method: str
    confidence: float


def _tokens(text: str) -> set[str]:
    """提取用于保守引用匹配的词元集合。"""
    return {
        token.casefold()
        for token in _TOKEN_PATTERN.findall(text)
        if token.casefold() not in _STOPWORDS
    }


def _parse_json_object(text: str) -> dict[str, object]:
    """解析模型 JSON 对象，拒绝围栏外的额外语义。"""
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_+-]*\s*|\s*```$", "", value).strip()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("审核模型返回无效 JSON") from None
        decoded = json.loads(value[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError("审核模型结果必须是 JSON 对象")
    return decoded


class ClaimHypothesisService:
    """维护 Claim/Hypothesis 生命周期，并把模型审核限制在来源边界内。"""

    def __init__(
        self,
        schema: VNextSchema,
        proposal_service: ProposalService,
        *,
        enabled: bool = True,
        model_task: str = "actor",
        max_per_run: int = 2,
        reviewer: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> None:
        """绑定存储、审核配置和可替换的测试模型调用器。"""
        if max_per_run <= 0:
            raise ValueError("max_per_run 必须大于 0")
        self._schema = schema
        self._proposals = proposal_service
        self._enabled = enabled
        self._model_task = model_task
        self._max_per_run = max_per_run
        self._reviewer = reviewer

    async def extract_semantics(self, episode_id: str) -> SemanticView:
        """以显式 ID、完整引号和高重合度规则解析 Episode 引用、实体和事件。"""
        async with self._schema.database.session() as session:
            episode = await session.get(EpisodeModel, episode_id)
            if episode is None:
                raise ValueError("Episode 不存在")
            existing = await session.scalar(
                select(EpisodeSemanticModel)
                .where(EpisodeSemanticModel.episode_id == episode_id)
                .order_by(EpisodeSemanticModel.created_at.asc())
                .limit(1)
            )
            if existing is not None:
                return self._semantic_view(existing)
            recent = tuple(
                (
                    await session.scalars(
                        select(EpisodeModel)
                        .where(
                            EpisodeModel.stream_id == episode.stream_id,
                            EpisodeModel.episode_id != episode_id,
                            EpisodeModel.observed_at <= episode.observed_at,
                        )
                        .order_by(EpisodeModel.observed_at.desc())
                        .limit(100)
                    )
                ).all()
            )
            explicit_ids = set(_UUID_PATTERN.findall(episode.raw_text))
            referenced: list[EpisodeModel] = [
                item for item in recent if item.episode_id in explicit_ids
            ]
            quotes = _QUOTE_PATTERN.findall(episode.raw_text)
            for item in recent:
                if item in referenced:
                    continue
                if any(
                    quote.strip() and quote.strip() in item.raw_text
                    for quote in quotes
                ):
                    referenced.append(item)
            entities = [
                {
                    "type": "person",
                    "person_id": person_id,
                    "source": "episode_participant",
                    "confidence": 1.0,
                }
                for person_id in episode.participants
            ]
            entities.extend(
                {
                    "type": "person_label",
                    "label": label,
                    "source": "name_pattern",
                    "confidence": 0.7,
                }
                for label in _NAME_PATTERN.findall(episode.raw_text)
            )
            if any(term in episode.raw_text for term in _CORRECTION_TERMS):
                event_type, temporal = "correction", "present"
            elif any(term in episode.raw_text for term in _PLAN_TERMS):
                event_type, temporal = "plan", "future"
            else:
                event_type, temporal = "observation", "past_or_present"
            events = ({
                "type": event_type,
                "temporal": temporal,
                "text": episode.compressed_text,
                "confidence": 0.75 if event_type != "observation" else 0.55,
            },)
            reference_confidence = 1.0 if explicit_ids else 0.85 if referenced else 0.45
            semantic = EpisodeSemanticModel(
                semantic_id=str(uuid5(NAMESPACE_URL, f"engram:semantic:{episode_id}")),
                episode_id=episode_id,
                referenced_episode_ids=[item.episode_id for item in referenced],
                entities=entities,
                events=list(events),
                extraction_method="RULES_V1",
                confidence=reference_confidence,
                created_at=datetime.now(UTC),
            )
            session.add(semantic)
            await session.flush()
            return self._semantic_view(semantic)

    async def sync_pending_proposals(self, stream_id: str) -> tuple[str, ...]:
        """为待审 MemoryUpdateProposal 建立对应 Claim/Hypothesis 对象。"""
        proposals = await self._proposals.list_pending(stream_id, limit=20)
        semantic_by_proposal: dict[str, tuple[SemanticView, ...]] = {}
        for proposal in proposals:
            semantic_by_proposal[str(proposal["proposal_id"])] = tuple(
                [
                    await self.extract_semantics(str(evidence_id))
                    for evidence_id in proposal["evidence_ids"]
                ]
            )
        created: list[str] = []
        async with self._schema.database.session() as session:
            for proposal in proposals:
                item_id = str(uuid5(NAMESPACE_URL, f"engram:claim:{proposal['proposal_id']}"))
                if await session.get(ClaimHypothesisModel, item_id) is not None:
                    continue
                semantics = semantic_by_proposal[str(proposal["proposal_id"])]
                session.add(ClaimHypothesisModel(
                    item_id=item_id,
                    stream_id=stream_id,
                    kind="HYPOTHESIS" if proposal["operation"] == "uncertain" else "CLAIM",
                    statement=str(proposal["claim"]),
                    evidence_ids=list(proposal["evidence_ids"]),
                    referenced_episode_ids=[
                        episode_id
                        for semantic in semantics
                        for episode_id in semantic.referenced_episode_ids
                    ],
                    entity_refs=[
                        entity for semantic in semantics for entity in semantic.entities
                    ],
                    event_refs=[event for semantic in semantics for event in semantic.events],
                    status="PENDING",
                    confidence=float(proposal["confidence"]),
                    target_memory_id=proposal.get("target_memory_id"),
                    proposal_id=str(proposal["proposal_id"]),
                    review_attempts=0,
                    created_at=datetime.now(UTC),
                ))
                created.append(item_id)
        return tuple(created)

    async def review_pending(self, stream_id: str) -> tuple[str, ...]:
        """自动审核有限数量候选；审核结果只推进提案，不确认正式 Memory。"""
        if not self._enabled:
            return ()
        async with self._schema.database.session() as session:
            rows = tuple((await session.scalars(
                select(ClaimHypothesisModel)
                .where(
                    ClaimHypothesisModel.stream_id == stream_id,
                    ClaimHypothesisModel.status.in_(("PENDING", "DEFERRED")),
                    ClaimHypothesisModel.review_attempts < 3,
                )
                .order_by(ClaimHypothesisModel.created_at.asc())
                .limit(self._max_per_run)
            )).all())
        completed: list[str] = []
        for row in rows:
            try:
                result = await self._review_one(row)
            except Exception as error:  # noqa: BLE001
                await self._defer(row.item_id, f"审核异常：{type(error).__name__}")
                continue
            completed.append(result)
        return tuple(completed)

    async def pending_status(self, stream_id: str, limit: int = 20) -> tuple[dict[str, object], ...]:
        """返回当前流 Claim/Hypothesis 的审核状态和结构化来源。"""
        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1-50 之间")
        async with self._schema.database.session() as session:
            rows = (await session.scalars(select(ClaimHypothesisModel).where(
                ClaimHypothesisModel.stream_id == stream_id,
            ).order_by(ClaimHypothesisModel.created_at.desc()).limit(limit))).all()
            return tuple({
                "item_id": row.item_id,
                "kind": row.kind,
                "statement": row.statement,
                "status": row.status,
                "confidence": row.confidence,
                "evidence_ids": list(row.evidence_ids),
                "referenced_episode_ids": list(row.referenced_episode_ids),
                "entities": list(row.entity_refs),
                "events": list(row.event_refs),
                "proposal_id": row.proposal_id,
                "target_memory_id": row.target_memory_id,
                "review_reason": row.review_reason,
            } for row in rows)

    async def _review_one(self, row: ClaimHypothesisModel) -> str:
        """审核单条 Claim，严格限制模型返回的操作和证据 ID。"""
        prompt = await self._review_prompt(row)
        raw = await (self._reviewer(prompt, self._model_task)
                     if self._reviewer is not None else self._call_model(prompt))
        result = _parse_json_object(raw)
        decision = str(result.get("decision") or "defer").lower()
        reason = str(result.get("reason") or "").strip()[:500]
        confidence = float(result.get("confidence", row.confidence))
        if not 0 <= confidence <= 1:
            raise ValueError("审核 confidence 越界")
        allowed_ids = set(row.evidence_ids)
        evidence_ids = tuple(
            item for item in result.get("evidence_ids", row.evidence_ids)
            if isinstance(item, str) and item in allowed_ids
        )
        if set(evidence_ids) != set(row.evidence_ids):
            raise ValueError("审核结果丢失或伪造 Evidence ID")
        if decision == "defer":
            await self._defer(row.item_id, reason or "模型要求延后审核")
            return row.item_id
        if decision not in {"accept", "reject"}:
            raise ValueError("审核 decision 必须为 accept/reject/defer")
        async with self._schema.database.session() as session:
            if decision == "reject":
                await session.execute(update(ClaimHypothesisModel).where(
                    ClaimHypothesisModel.item_id == row.item_id
                ).values(
                    status="REJECTED", confidence=confidence, review_reason=reason,
                    review_payload=result, review_attempts=ClaimHypothesisModel.review_attempts + 1,
                    reviewed_at=datetime.now(UTC),
                ))
                if row.proposal_id:
                    await session.execute(update(MemoryUpdateProposalModel).where(
                        MemoryUpdateProposalModel.proposal_id == row.proposal_id,
                        MemoryUpdateProposalModel.status == "PENDING",
                    ).values(status="REJECTED", reviewed_at=datetime.now(UTC)))
                return row.item_id
            operation = str(result.get("operation") or "uncertain")
            if operation not in {"support", "contradict", "supersede", "uncertain"}:
                raise ValueError("审核 operation 不受支持")
            target = result.get("target_memory_id")
            target_id = str(target).strip() if isinstance(target, str) and target.strip() else None
            if operation in {"contradict", "supersede"}:
                if target_id is None or await session.get(MemoryModel, target_id) is None:
                    raise ValueError("审核纠正操作缺少有效目标 Memory")
            if row.proposal_id:
                await session.execute(update(MemoryUpdateProposalModel).where(
                    MemoryUpdateProposalModel.proposal_id == row.proposal_id,
                    MemoryUpdateProposalModel.status == "PENDING",
                ).values(
                    operation=operation, target_memory_id=target_id,
                    confidence=confidence,
                ))
            await session.execute(update(ClaimHypothesisModel).where(
                ClaimHypothesisModel.item_id == row.item_id
            ).values(
                status="ACCEPTED", confidence=confidence, review_reason=reason,
                review_payload=result, target_memory_id=target_id,
                review_attempts=ClaimHypothesisModel.review_attempts + 1,
                reviewed_at=datetime.now(UTC),
            ))
        return row.item_id

    async def _defer(self, item_id: str, reason: str) -> None:
        """将无法安全审核的候选保留为可重试 DEFERRED。"""
        async with self._schema.database.session() as session:
            await session.execute(update(ClaimHypothesisModel).where(
                ClaimHypothesisModel.item_id == item_id
            ).values(
                status="DEFERRED", review_reason=reason[:500],
                review_attempts=ClaimHypothesisModel.review_attempts + 1,
                reviewed_at=datetime.now(UTC),
            ))

    async def _review_prompt(self, row: ClaimHypothesisModel) -> str:
        """构造只含已保存来源的审核材料。"""
        async with self._schema.database.session() as session:
            episodes = tuple((await session.scalars(
                select(EpisodeModel).where(EpisodeModel.episode_id.in_(row.evidence_ids))
            )).all())
        evidence = [
            {"episode_id": item.episode_id, "text": item.raw_text,
             "participants": item.participants, "kind": item.episode_kind}
            for item in episodes
        ]
        return json.dumps({
            "instruction": "只审核来源支持程度，不创造来源外事实。输出 JSON：decision、operation、target_memory_id、confidence、evidence_ids、reason。",
            "claim": row.statement,
            "kind": row.kind,
            "evidence": evidence,
            "allowed_evidence_ids": list(row.evidence_ids),
            "current_target_memory_id": row.target_memory_id,
        }, ensure_ascii=False)

    async def _call_model(self, prompt: str) -> str:
        """通过框架 actor 任务调用审核模型。"""
        model_set = llm_api.get_model_set_by_task(self._model_task)
        request = llm_api.create_llm_request(
            model_set=model_set, request_name="engram_claim_hypothesis_review"
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(
            "你是记忆审核器。只使用输入中的 Evidence ID 和文本。不要确认无来源事实。"
            "必须只返回 JSON 对象。"
        )))
        request.add_payload(LLMPayload(ROLE.USER, Text(prompt)))
        response = await request.send(stream=False)
        return str(await response or "").strip()

    @staticmethod
    def _semantic_view(row: EpisodeSemanticModel) -> SemanticView:
        """转换追加式语义记录为不可变视图。"""
        return SemanticView(
            semantic_id=row.semantic_id,
            episode_id=row.episode_id,
            referenced_episode_ids=tuple(row.referenced_episode_ids),
            entities=tuple(row.entities),
            events=tuple(row.events),
            extraction_method=row.extraction_method,
            confidence=float(row.confidence),
        )