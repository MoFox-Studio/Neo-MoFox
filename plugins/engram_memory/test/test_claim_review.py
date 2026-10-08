"""Claim/Hypothesis 审核、语义抽取和可选后端测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from ..vnext.backends.hopfield import ModernHopfieldMemory
from ..vnext.backends.kda import KDAAttention
from ..vnext.backends.layersplit import LayerSplitRouter, LayerSplitStage
from ..vnext.backends.neo4j import Neo4jEpisodeGraph, Neo4jUnavailableError
from ..vnext.claim_service import ClaimHypothesisService
from ..vnext.episode_service import EpisodeService
from ..vnext.models import ClaimHypothesisModel, EpisodeSemanticModel
from ..vnext.proposal_service import ProposalService
from ..vnext.schema import VNextSchema


@pytest.fixture
async def schema(tmp_path: Path):
    """提供独立审核数据库。"""
    value = VNextSchema(str(tmp_path / "claim-review.db"))
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


async def test_semantic_extraction_is_same_stream_and_idempotent(schema: VNextSchema) -> None:
    """显式 Episode ID 和完整引用只解析同流历史，并重复读取同一语义记录。"""
    episodes = EpisodeService(schema)
    source = await episodes.record_external_episode(
        title="咖啡", content="用户喝咖啡后失眠", stream_id="stream-1",
        observed_at=datetime(2026, 10, 5, tzinfo=UTC), source_ref="semantic:source",
        participants=("person-1",),
    )
    current = await episodes.record_external_episode(
        title="引用", content=f"我记得“用户喝咖啡后失眠” [{source.episode_id}]",
        stream_id="stream-1", observed_at=datetime(2026, 10, 5, 0, 1, tzinfo=UTC),
        source_ref="semantic:current", participants=("person-1",),
    )
    service = ClaimHypothesisService(schema, ProposalService(schema))
    first = await service.extract_semantics(current.episode_id)
    second = await service.extract_semantics(current.episode_id)
    assert first.semantic_id == second.semantic_id
    assert source.episode_id in first.referenced_episode_ids
    assert first.entities[0]["person_id"] == "person-1"
    assert first.events[0]["type"] == "observation"
    async with schema.database.session() as session:
        assert len((await session.scalars(select(EpisodeSemanticModel))).all()) == 1


async def test_review_rejects_model_evidence_injection(schema: VNextSchema) -> None:
    """模型不能把未提供的 Evidence ID 注入审核结果。"""
    episodes = EpisodeService(schema)
    episode = await episodes.record_external_episode(
        title="纠正", content="更正，我不再喝咖啡", stream_id="stream-1",
        observed_at=datetime.now(UTC), source_ref="review:source", participants=("person-1",),
    )
    proposals = ProposalService(schema)
    await proposals.propose(
        stream_id="stream-1", claim=episode.raw_text, evidence_ids=(episode.episode_id,),
        operation="uncertain",
    )
    service = ClaimHypothesisService(
        schema, proposals, reviewer=lambda prompt, task: _review("forged-evidence"),
    )
    await service.sync_pending_proposals("stream-1")
    await service.review_pending("stream-1")
    async with schema.database.session() as session:
        row = await session.scalar(select(ClaimHypothesisModel))
        assert row is not None and row.status == "DEFERRED"


async def test_review_accepts_only_known_evidence_and_updates_proposal(schema: VNextSchema) -> None:
    """合法模型审核只接受已有来源，并把提案变为可人工确认的 support。"""
    episodes = EpisodeService(schema)
    episode = await episodes.record_external_episode(
        title="事实", content="用户喜欢茶", stream_id="stream-1",
        observed_at=datetime.now(UTC), source_ref="review:accepted", participants=("person-1",),
    )
    proposals = ProposalService(schema)
    proposal = await proposals.propose(
        stream_id="stream-1", claim=episode.raw_text, evidence_ids=(episode.episode_id,),
        operation="uncertain",
    )

    async def reviewer(prompt: str, task: str) -> str:
        """返回只引用已提供 Evidence 的审核结果。"""
        del task
        assert episode.episode_id in prompt
        return '{"decision":"accept","operation":"support","confidence":0.91,' \
            f'"evidence_ids":["{episode.episode_id}"],"reason":"来源直接支持"}}'

    service = ClaimHypothesisService(schema, proposals, reviewer=reviewer)
    await service.sync_pending_proposals("stream-1")
    await service.review_pending("stream-1")
    updated = await proposals.get(proposal.proposal_id)
    assert updated is not None
    assert updated.operation == "support"
    assert updated.status == "PENDING"
    status = (await service.pending_status("stream-1"))[0]
    assert status["status"] == "ACCEPTED"


async def test_pending_status_exposes_structured_sources(schema: VNextSchema) -> None:
    """审核状态目录同时提供实体、事件和引用来源。"""
    episodes = EpisodeService(schema)
    episode = await episodes.record_external_episode(
        title="人物", content="我叫小明，准备旅行", stream_id="stream-1",
        observed_at=datetime.now(UTC), source_ref="status:source", participants=("person-1",),
    )
    proposals = ProposalService(schema)
    proposal = await proposals.propose(
        stream_id="stream-1", claim=episode.raw_text, evidence_ids=(episode.episode_id,),
        operation="uncertain",
    )
    service = ClaimHypothesisService(schema, proposals)
    await service.sync_pending_proposals("stream-1")
    status = (await service.pending_status("stream-1"))[0]
    assert status["proposal_id"] == proposal.proposal_id
    assert status["entities"]
    assert status["events"][0]["type"] == "plan"


async def _review(kind: str) -> str:
    """返回故意不合法的模型结果。"""
    del kind
    return '{"decision":"accept","operation":"support","evidence_ids":["forged"]}'


def test_modern_hopfield_retrieves_softmax_patterns() -> None:
    """现代 Hopfield 返回归一化竞争权重。"""
    memory = ModernHopfieldMemory(beta=10)
    memory.store("a", (1, 0))
    memory.store("b", (0, 1))
    result = memory.retrieve((1, 0), limit=2)
    assert result[0][0] == "a"
    assert sum(weight for _, weight in result) == pytest.approx(1.0)


def test_kda_attention_respects_reliability() -> None:
    """KDA 评分不能让低可靠性候选无条件超过高可靠性候选。"""
    result = KDAAttention().rank(
        (1, 0), (("reliable", (1, 0), 1.0), ("weak", (1, 0), 0.1)), limit=2
    )
    assert result[0][0] == "reliable"


def test_layersplit_routes_unique_bounded_candidates() -> None:
    """LayerSplit 各阶段保持来源 ID 唯一且受预算限制。"""
    router = LayerSplitRouter(association_budget=2, reconstruction_budget=1)
    result = router.route(LayerSplitStage.ASSOCIATION, ("a", "a", "b", "c"))
    assert result.candidate_ids == ("a", "b")
    assert router.route(LayerSplitStage.RECONSTRUCTION, ("a", "b")).budget == 1


@pytest.mark.asyncio
async def test_neo4j_is_lazy_and_reports_driver_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neo4j 适配器构造不连接网络，驱动不可用时给出明确错误。"""
    import sys
    monkeypatch.setitem(sys.modules, "neo4j", None)
    graph = Neo4jEpisodeGraph("bolt://localhost:7687", "neo4j", "password")
    with pytest.raises(Neo4jUnavailableError):
        await graph.connect()
