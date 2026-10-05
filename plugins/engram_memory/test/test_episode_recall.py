"""Cue-Driven Reconstructive Memory Episode、工作记忆和提案回归测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from ..vnext.domain import CreateMemoryInput, EvidenceInput, SubjectInput, WriteContext
from ..vnext.enums import ActorType, EvidenceSourceType, MemoryEventType, MemoryKind, MemoryStatus, SubjectKind
from ..vnext.episode_service import EpisodeService
from ..vnext.models import (
    CueSetModel,
    EpisodeModel,
    EpisodeRelationModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRelationModel,
)
from ..vnext.proposal_service import ProposalService
from ..vnext.schema import VNextSchema
from ..vnext.tool_service import ToolContext, VNextToolService


@pytest.fixture
async def schema(tmp_path: Path):
    """提供独立 Episode/Memory 数据库。"""
    value = VNextSchema(str(tmp_path / "episode-recall.db"))
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.parametrize("text, emotion", [("最近失眠", "疲惫"), ("今天开心", "愉快"), ("有点心慌", "不适")])
def test_multi_character_emotion_cues(text: str, emotion: str) -> None:
    """多字情绪线索不依赖中文单字切分结果。"""
    _, soft = EpisodeService._cue_values(text, ())
    assert soft["emotion"] == emotion


async def test_episode_is_not_formal_memory_and_recall_has_sources(schema: VNextSchema) -> None:
    """输入先成为 Episode，工作记忆只携带已有 Episode 来源。"""
    service = EpisodeService(schema)
    first = await service.record_episode(
        {
            "message_id": "message-1",
            "stream_id": "stream-1",
            "time": "2026-10-05T10:00:00Z",
            "content": "我以前每天喝咖啡。",
            "processed_plain_text": "我以前每天喝咖啡。",
            "person_id": "person-1",
            "sender_name": "用户",
        }
    )
    second = await service.record_episode(
        {
            "message_id": "message-2",
            "stream_id": "stream-1",
            "time": "2026-10-05T10:01:00Z",
            "content": "咖啡让我失眠。",
            "processed_plain_text": "咖啡让我失眠。",
            "person_id": "person-1",
            "sender_name": "用户",
        }
    )
    working = await service.recall_working_memory(second)
    assert any(item["episode_id"] == first.episode_id for item in working["selected_episodes"])
    assert all(item["episode_id"] and item["content"] for item in working["selected_episodes"])
    async with schema.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryModel)) == 0
        assert await session.scalar(select(func.count()).select_from(EpisodeModel)) == 2


async def test_semantic_embedding_recalls_without_shared_topic_tokens(
    schema: VNextSchema,
) -> None:
    """真实语义相似度可以补入没有共同主题词的经历。"""
    service = EpisodeService(
        schema,
        embedding_function=lambda texts: _semantic_vectors(texts),
        semantic_min_similarity=0.9,
    )
    source = await service.record_external_episode(
        title="工作变化",
        content="我辞掉了原来的岗位，去了一家新公司。",
        stream_id="stream-1",
        observed_at=datetime(2026, 10, 5, tzinfo=UTC),
        source_ref="semantic:source",
        participants=(),
    )

    recalled = await service.recall_association(
        stream_id="stream-1",
        cue_text="我换了新的工作单位。",
        max_hops=1,
        limit=3,
    )

    assert recalled
    assert recalled[0]["episode_id"] == source.episode_id


async def test_same_person_event_recall_crosses_streams_without_copying_source(
    schema: VNextSchema,
) -> None:
    """同人物同主题经历可跨流联想，但 Episode 仍保留原聊天流。"""
    service = EpisodeService(schema, reconstruction_noise=0)
    source = await service.record_external_episode(
        title="咖啡偏好",
        content="我最近喜欢喝咖啡。",
        stream_id="private-stream",
        observed_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
        source_ref="cross-stream:source",
        participants=("person-1",),
    )
    current = await service.record_external_episode(
        title="咖啡习惯",
        content="今天又喝了咖啡。",
        stream_id="group-stream",
        observed_at=datetime(2026, 10, 5, 10, 1, tzinfo=UTC),
        source_ref="cross-stream:current",
        participants=("person-1",),
    )

    recalled = await service.recall_working_memory(current)

    assert any(item["episode_id"] == source.episode_id for item in recalled["selected_episodes"])
    async with schema.database.session() as session:
        persisted_source = await session.get(EpisodeModel, source.episode_id)
        relation = await session.scalar(
            select(EpisodeRelationModel).where(
                EpisodeRelationModel.source_episode_id == current.episode_id,
                EpisodeRelationModel.target_episode_id == source.episode_id,
                EpisodeRelationModel.relation_type == "CROSS_STREAM_EVENT",
            )
        )
        assert persisted_source is not None
        assert persisted_source.stream_id == "private-stream"
        assert relation is not None


@pytest.mark.parametrize(
    ("source_person", "current_person", "source_text", "current_text"),
    [
        ("person-1", "person-2", "喜欢咖啡", "今天喝了咖啡"),
        ("person-1", "person-1", "espresso", "gardening"),
    ],
)
async def test_cross_stream_relation_requires_same_person_and_topic(
    tmp_path: Path,
    source_person: str,
    current_person: str,
    source_text: str,
    current_text: str,
) -> None:
    """跨流关联必须同时匹配人物和主题。"""
    schema = VNextSchema(str(tmp_path / "cross-stream-negative.db"))
    await schema.initialize()
    service = EpisodeService(schema)
    source = await service.record_external_episode(
        title=source_text,
        content=source_text,
        stream_id="private-stream",
        observed_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
        source_ref=f"negative-cross-stream:source:{source_person}:{source_text}",
        participants=(source_person,),
    )
    current = await service.record_external_episode(
        title=current_text,
        content=current_text,
        stream_id="group-stream",
        observed_at=datetime(2026, 10, 5, 10, 1, tzinfo=UTC),
        source_ref=f"negative-cross-stream:current:{current_person}:{current_text}",
        participants=(current_person,),
    )

    async with schema.database.session() as session:
        relation = await session.scalar(
            select(EpisodeRelationModel).where(
                EpisodeRelationModel.source_episode_id == current.episode_id,
                EpisodeRelationModel.target_episode_id == source.episode_id,
                EpisodeRelationModel.relation_type == "CROSS_STREAM_EVENT",
            )
        )
        assert relation is None
    await schema.close()


async def test_cross_stream_relation_ignores_events_older_than_30_days(
    tmp_path: Path,
) -> None:
    """跨流 Episode 关联只检查最近 30 天。"""
    schema = VNextSchema(str(tmp_path / "cross-stream-expiry.db"))
    await schema.initialize()
    service = EpisodeService(schema)
    current_time = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    source = await service.record_external_episode(
        title="espresso",
        content="espresso preference",
        stream_id="private-stream",
        observed_at=current_time - timedelta(days=31),
        source_ref="cross-stream-expiry:source",
        participants=("person-1",),
    )
    current = await service.record_external_episode(
        title="espresso",
        content="espresso today",
        stream_id="group-stream",
        observed_at=current_time,
        source_ref="cross-stream-expiry:current",
        participants=("person-1",),
    )

    async with schema.database.session() as session:
        relation = await session.scalar(
            select(EpisodeRelationModel).where(
                EpisodeRelationModel.source_episode_id == current.episode_id,
                EpisodeRelationModel.target_episode_id == source.episode_id,
                EpisodeRelationModel.relation_type == "CROSS_STREAM_EVENT",
            )
        )
        assert relation is None
    await schema.close()


async def _semantic_vectors(texts: object) -> tuple[tuple[float, float], ...]:
    """为测试输入提供两个语义簇的确定向量。"""
    values = []
    for text in texts:  # type: ignore[union-attr]
        value = str(text)
        values.append((1.0, 0.0) if any(term in value for term in ("工作", "岗位", "公司", "单位")) else (0.0, 1.0))
    return tuple(values)


async def test_proposal_confirmation_supersedes_but_preserves_history(schema: VNextSchema) -> None:
    """明确纠正确认后建立新状态、保留旧状态并建立 SUPERSEDES 关系。"""
    tools = VNextToolService(
        schema,
        cast(Any, SimpleNamespace(search=AsyncMock(return_value=()))),
    )
    now = datetime.now(UTC)
    old = await tools._memory.create_memory(
        CreateMemoryInput(
            title="咖啡偏好",
            content="用户过去经常喝咖啡。",
            memory_kind=MemoryKind.PREFERENCE,
            subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
            observed_at=now,
            evidence=(EvidenceInput(EvidenceSourceType.ADMIN, now, note="旧状态"),),
        ),
        WriteContext(ActorType.ADMIN, actor_ref="test"),
    )
    episode = await EpisodeService(schema).record_external_episode(
        title="咖啡变化",
        content="用户已经不太想喝咖啡了。",
        stream_id="stream-1",
        observed_at=now,
        source_ref="summary:coffee-change",
        participants=("person-1",),
        certainty=0.95,
    )
    proposal = await ProposalService(schema).propose(
        stream_id="stream-1",
        claim="用户当前不太想喝咖啡",
        evidence_ids=(episode.episode_id,),
        operation="supersede",
        target_memory_id=old.memory_id,
        confidence=0.95,
    )
    result = await tools.confirm_memory_update(
        proposal.proposal_id,
        ToolContext(ActorType.ADMIN, actor_ref="test", stream_id="stream-1"),
    )
    assert result["status"] == "CONFIRMED"
    previous = await tools._repository.get_memory(old.memory_id)
    assert previous is not None and previous.status is MemoryStatus.TOMBSTONED
    confirmed = await tools._proposal_service.get(proposal.proposal_id)
    assert confirmed is not None and confirmed.status == "CONFIRMED"
    async with schema.database.session() as session:
        relation = (await session.scalars(select(MemoryRelationModel))).first()
        assert relation is not None and relation.relation_type.value == "SUPERSEDES"


async def test_output_observation_preserves_activation_and_reference(schema: VNextSchema) -> None:
    """输出观察区分激活和明确引用，重复事件保留首次观察。"""
    service = EpisodeService(schema)
    source = await service.record_external_episode(
        title="咖啡", content="喝咖啡后失眠", stream_id="stream-1",
        observed_at=datetime.now(UTC), source_ref="observation:coffee",
    )
    await service.recall_association(stream_id="stream-1", cue_text="咖啡")
    message = {
        "message_id": "output-1", "stream_id": "stream-1",
        "time": datetime.now(UTC), "content": f"回忆来源 [{source.episode_id}]",
        "person_id": "bot",
    }
    output = await service.record_output_observation(message)
    assert (await service.record_output_observation(message)).episode_id == output.episode_id
    async with schema.database.session() as session:
        cues = (await session.scalars(select(CueSetModel).where(CueSetModel.episode_id == output.episode_id))).all()
        observations = [cue.soft_cues["observation"] for cue in cues if "observation" in cue.soft_cues]
        assert len(observations) == 1
        observation = observations[0]
        assert observation["activated_episode_ids"] == [source.episode_id]
        assert observation["explicitly_referenced_episode_ids"] == [source.episode_id]
        assert await session.scalar(select(func.count()).select_from(MemoryModel)) == 0


@pytest.mark.parametrize(
    ("output_text", "expected_method"),
    [
        ("我记得 [memory-1]。", "explicit_memory_id"),
        ("你说过喜欢咖啡。", "lexical_overlap"),
        ("好的，我知道了。", "injected_without_evidence"),
        ("好的，我知道了。", "injected_without_evidence_expired"),
    ],
)
async def test_output_observation_classifies_memory_influence_evidence(
    schema: VNextSchema,
    output_text: str,
    expected_method: str,
) -> None:
    """输出记录注入记忆及可见证据等级，不把纯注入当成因果证据。"""
    service = EpisodeService(schema)
    injected_at = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    if expected_method.endswith("_expired"):
        injected_at -= timedelta(hours=1)
    tools = VNextToolService(
        schema,
        cast(Any, SimpleNamespace(search=AsyncMock(return_value=()))),
    )
    memory = await tools._memory.create_memory(
        CreateMemoryInput(
            title="咖啡偏好",
            content="你说过喜欢咖啡。",
            memory_kind=MemoryKind.PREFERENCE,
            subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
            observed_at=injected_at,
            evidence=(EvidenceInput(EvidenceSourceType.ADMIN, injected_at),),
        ),
        WriteContext(ActorType.ADMIN, actor_ref="test"),
    )
    async with schema.database.session() as session:
        session.add(
            MemoryEventModel(
                event_id="injected-memory-1",
                memory_id=memory.memory_id,
                revision_id=None,
                event_type=MemoryEventType.FLASHBACK_EXPOSED,
                actor_type=ActorType.SYSTEM,
                actor_ref=None,
                stream_id="stream-1",
                occurred_at=injected_at,
                payload_json={
                    "turn_index": 4,
                    "stage": "prompt_injected",
                    "content": "咖啡偏好: 你说过喜欢咖啡。",
                },
            )
        )
    message = {
        "message_id": f"output-{expected_method}",
        "stream_id": "stream-1",
        "time": injected_at + timedelta(
            hours=1 if expected_method.endswith("_expired") else 0,
            seconds=1,
        ),
        "content": output_text.replace("memory-1", memory.memory_id),
        "person_id": "bot",
    }
    if expected_method == "injected_without_evidence":
        await service.record_episode({
            "message_id": "previous-output",
            "stream_id": "stream-1",
            "time": injected_at - timedelta(seconds=1),
            "content": "上一轮回复结束。",
            "person_id": "bot",
        }, episode_kind="OUTPUT", source_type="ASSISTANT_MESSAGE")
        message["time"] = injected_at + timedelta(seconds=1)

    output = await service.record_output_observation(message)

    async with schema.database.session() as session:
        cues = (await session.scalars(
            select(CueSetModel).where(CueSetModel.episode_id == output.episode_id)
        )).all()
        cue = next(item for item in cues if "observation" in item.soft_cues)
        observation = cue.soft_cues["observation"]
        expected_injected = [] if expected_method.endswith("_expired") else [memory.memory_id]
        assert observation["injected_memory_ids"] == expected_injected
        if expected_injected:
            assert observation["memory_influence_evidence"][0]["method"] == expected_method
        assert observation["causal_attribution"] == "not_proven"


async def test_correction_candidate_targets_unique_matching_memory(schema: VNextSchema) -> None:
    """明确纠正只在唯一同人物同主题时提出 supersede 目标。"""
    tools = VNextToolService(
        schema,
        cast(Any, SimpleNamespace(search=AsyncMock(return_value=()))),
    )
    old = await tools._memory.create_memory(
        CreateMemoryInput(
            title="咖啡习惯",
            content="用户过去经常喝咖啡。",
            memory_kind=MemoryKind.PREFERENCE,
            subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
            observed_at=datetime.now(UTC),
            evidence=(EvidenceInput(
                EvidenceSourceType.ADMIN, datetime.now(UTC), note="旧状态"
            ),),
        ),
        WriteContext(ActorType.ADMIN, actor_ref="test", stream_id="stream-1"),
    )
    episode = await EpisodeService(schema).record_external_episode(
        title="咖啡习惯纠正",
        content="更正，我不再喝咖啡。",
        stream_id="stream-1",
        observed_at=datetime.now(UTC),
        source_ref="correction:unique-target",
        participants=("person-1",),
    )
    candidates = await ProposalService(schema).collect_consolidation_candidates("stream-1")
    assert len(candidates) == 1
    proposal = await ProposalService(schema).get(candidates[0])
    assert proposal is not None
    assert proposal.operation == "supersede"
    assert proposal.target_memory_id == old.memory_id


async def test_consolidation_is_conditional_idempotent_and_pending(schema: VNextSchema) -> None:
    """后台候选只处理触发经历，既不重复提案也不直接增加正式记忆。"""
    episodes = EpisodeService(schema)
    proposals = ProposalService(schema)
    for source_ref, content, stream_id, participants in (
        ("ordinary", "今天喝茶", "stream-1", ()),
        ("correction", "更正，我不再喝咖啡", "stream-1", ("person-1",)),
        ("other", "更正，我不再喝咖啡", "stream-2", ("person-2",)),
    ):
        await episodes.record_external_episode(
            title="记录", content=content, stream_id=stream_id,
            observed_at=datetime.now(UTC), source_ref=source_ref,
            participants=participants,
        )
    created = await proposals.collect_consolidation_candidates("stream-1")
    assert len(created) == 1
    proposal = await proposals.get(created[0])
    assert proposal is not None and proposal.status == "PENDING"
    assert proposal.operation == "uncertain"
    assert (await proposals.list_pending("stream-1"))[0]["proposal_id"] == created[0]
    assert await proposals.list_pending("stream-2") == ()
    tools = VNextToolService(schema, cast(Any, SimpleNamespace(search=AsyncMock(return_value=()))))
    with pytest.raises(ValueError, match="审核"):
        await tools.confirm_memory_update(
            created[0], ToolContext(ActorType.ADMIN, actor_ref="test", stream_id="stream-1")
        )
    assert await proposals.collect_consolidation_candidates("stream-1") == ()
    async with schema.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryModel)) == 0


async def test_association_does_not_persist_queries(schema: VNextSchema) -> None:
    """主动查询不成为经历，且不改变共享的跳数设置。"""
    service = EpisodeService(schema, reconstruction_noise=0)
    episode = await service.record_external_episode(
        title="咖啡", content="喝咖啡后失眠", stream_id="stream-1",
        observed_at=datetime.now(UTC), source_ref="coffee",
    )
    first = await service.recall_association(stream_id="stream-1", cue_text="咖啡", max_hops=1)
    second = await service.recall_association(stream_id="stream-1", cue_text="咖啡", max_hops=2)
    assert first[0]["episode_id"] == second[0]["episode_id"] == episode.episode_id
    assert service._relation_hops == 2
    async with schema.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(EpisodeModel)) == 1


@pytest.mark.parametrize("kind", ["CUE", "OUTPUT"])
async def test_non_evidence_episodes_are_excluded(schema: VNextSchema, kind: str) -> None:
    """历史查询和模型输出既不能召回为事实，也不能用于提案证据。"""
    service = EpisodeService(schema)
    episode = await service.record_external_episode(
        title="咖啡", content="喝咖啡后失眠", stream_id="stream-1",
        observed_at=datetime.now(UTC), source_ref=f"non-evidence:{kind}", episode_kind=kind,
    )
    assert await service.recall_association(stream_id="stream-1", cue_text="咖啡") == ()
    with pytest.raises(ValueError, match="不能作为"):
        await ProposalService(schema).propose(
            stream_id="stream-1", claim="咖啡事实", evidence_ids=(episode.episode_id,),
            operation="support",
        )


async def test_proposal_rejects_cross_stream_evidence(schema: VNextSchema) -> None:
    """提案不能引用其他聊天流的 Episode。"""
    episode = await EpisodeService(schema).record_external_episode(
        title="隔离来源",
        content="这条经历属于另一个流。",
        stream_id="stream-a",
        observed_at=datetime.now(UTC),
        source_ref="summary:other-stream",
        participants=("person-1",),
    )
    with pytest.raises(ValueError, match="当前聊天流"):
        await ProposalService(schema).propose(
            stream_id="stream-b",
            claim="越权提案",
            evidence_ids=(episode.episode_id,),
            operation="support",
        )
