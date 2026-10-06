"""前馈式记忆检索服务的端到端测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ..vnext.domain import (
    CreateMemoryInput,
    EvidenceInput,
    MemoryLifecycleInput,
    SubjectInput,
    WriteContext,
)
from ..vnext.enums import ActorType, EvidenceSourceType, MemoryKind, SubjectKind
from ..vnext.feedforward_service import (
    CHANNEL_EMOTIONAL,
    CHANNEL_PRIVATE,
    FeedForwardCue,
    FeedForwardRetrievalService,
    FeedForwardSettings,
)
from ..vnext.memory_service import MemoryService
from ..vnext.retrieval_service import RetrievalService, VectorSearchBackend
from ..vnext.schema import VNextSchema


class _NoVector(VectorSearchBackend):
    """测试中只走词法与结构化通道。"""


@pytest.fixture
async def schema(tmp_path: Path):
    """提供隔离数据库。"""
    value = VNextSchema(str(tmp_path / "feedforward.db"))
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


async def _create(
    schema: VNextSchema,
    title: str,
    content: str,
    *,
    kind: MemoryKind = MemoryKind.FACT,
    person_id: str = "person-1",
    observed_at: datetime | None = None,
) -> str:
    """写入一条带来源的正式记忆。"""
    moment = observed_at or datetime.now(UTC) - timedelta(days=1)
    result = await MemoryService(schema, "example-embedding").create_memory(
        CreateMemoryInput(
            title=title,
            content=content,
            memory_kind=kind,
            subject=SubjectInput(SubjectKind.PERSON, person_id=person_id),
            observed_at=moment,
            evidence=(EvidenceInput(EvidenceSourceType.ACTOR_WRITE, moment, note="测试"),),
        ),
        WriteContext(ActorType.ADMIN),
    )
    return result.memory_id


def _service(schema: VNextSchema, **overrides: object) -> FeedForwardRetrievalService:
    """构造无向量后端、无噪声的前馈服务。"""
    settings = FeedForwardSettings(noise_scale=0.0, **overrides)  # type: ignore[arg-type]
    return FeedForwardRetrievalService(schema, RetrievalService(schema, _NoVector()), settings)


def _cue(text: str, *, person: str | None = "person-1", now: datetime | None = None) -> FeedForwardCue:
    """构造私聊线索。"""
    return FeedForwardCue(
        stream_id="stream-1",
        text=text,
        person_ids=(person,) if person else (),
        chat_type="private",
        now=now or datetime.now(UTC),
    )


async def test_every_turn_feeds_forward_relevant_memory(schema: VNextSchema) -> None:
    """无需概率触发，相关记忆每轮都能前馈。"""
    coffee = await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    await _create(schema, "宠物", "用户养了一只猫。", person_id="person-2")
    service = _service(schema)
    for _ in range(3):
        result = await service.feed_forward(_cue("我今天又喝咖啡了"))
        assert coffee in [item.memory_id for item in result.selected]
        assert result.candidate_count >= 1


async def test_unrelated_cue_injects_nothing(schema: VNextSchema) -> None:
    """与线索无真实关联的记忆不会因为新近而被注入。"""
    await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    service = _service(schema)
    result = await service.feed_forward(_cue("天气怎么样", person=None))
    assert result.selected == ()


async def test_rehearsal_keeps_memory_in_working_set(schema: VNextSchema) -> None:
    """被选中的记忆进入 L1，话题转开后仍凭工作记忆参与竞争。"""
    coffee = await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    service = _service(schema)
    await service.feed_forward(_cue("我今天又喝咖啡了"))
    assert service.layers.rehearsal_counts("stream-1").get(coffee) == 1
    follow_up = await service.feed_forward(_cue("所以晚上怎么办", person=None))
    assert coffee in [item.memory_id for item in follow_up.selected]


async def test_kda_learns_cue_association(schema: VNextSchema) -> None:
    """选中后写入 KDA，同一线索的读出指向该记忆。"""
    coffee = await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    service = _service(schema)
    cue = _cue("我今天又喝咖啡了")
    await service.feed_forward(cue)
    assert service.kda.energy(CHANNEL_PRIVATE) > 0
    from ..vnext.backends.kda_memory import hashed_features
    from ..vnext.retrieval_service import _tokenize

    key = hashed_features(_tokenize(cue.text), service.settings.feature_dim, salt="cue")
    scores = service.kda.scores(key, {coffee: service._value_vector(coffee)}, at=cue.now)
    assert scores[coffee] > 0.5


async def test_emotional_memory_written_to_emotional_channel(schema: VNextSchema) -> None:
    """偏好类记忆的候选事实带 PREFERENCE 类型，通道识别为情感通道。"""
    from ..vnext.feedforward_service import _CandidateFacts

    now = datetime.now(UTC)
    fact = _CandidateFacts(
        memory_id="mem-pref",
        title="喜欢茶",
        content="用户很喜欢喝茶。",
        memory_kind="PREFERENCE",
        person_ids=("person-1",),
        chat_types=("private",),
        source_types=("ACTOR_WRITE",),
        access_times=(now - timedelta(days=1),),
        experienced_at=now - timedelta(days=1),
    )
    channels = fact.channels()
    assert CHANNEL_EMOTIONAL in channels
    assert CHANNEL_PRIVATE in channels


async def test_preference_selection_writes_emotional_kda(schema: VNextSchema) -> None:
    """偏好类记忆被选中后同时写入情感通道 KDA。"""
    tea = await _create(schema, "喜欢茶", "用户很喜欢喝茶。", kind=MemoryKind.PREFERENCE)
    service = _service(schema)
    result = await service.feed_forward(_cue("我还是喜欢喝茶"))
    assert [item.memory_id for item in result.selected] == [tea]
    assert CHANNEL_EMOTIONAL in result.selected[0].channels
    assert service.kda.energy(CHANNEL_EMOTIONAL) > 0


async def test_hot_cache_serves_second_turn(schema: VNextSchema) -> None:
    """第二轮命中 L2 热缓存，不重复回源冷库。"""
    await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    service = _service(schema)
    await service.feed_forward(_cue("我今天又喝咖啡了"))
    hits_before = service.layers.stats.l2_hits
    await service.feed_forward(_cue("咖啡真好喝"))
    assert service.layers.stats.l2_hits > hits_before


async def test_tombstoned_memory_no_longer_injected(schema: VNextSchema) -> None:
    """作废后清除缓存，即使仍在工作集中也不再注入。"""
    coffee = await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    service = _service(schema)
    await service.feed_forward(_cue("我今天又喝咖啡了"))
    await MemoryService(schema, "example-embedding").tombstone_memory(
        MemoryLifecycleInput(memory_id=coffee, reason="用户纠正"),
        WriteContext(ActorType.ADMIN),
    )
    service.forget(coffee)
    result = await service.feed_forward(_cue("我今天又喝咖啡了"))
    assert coffee not in [item.memory_id for item in result.selected]


async def test_topic_switch_prefers_latest_input(schema: VNextSchema) -> None:
    """话题转向新内容时，最新输入指向的记忆排在已反复复述的旧记忆前。"""
    coffee = await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    tea = await _create(schema, "喜欢茶", "用户很喜欢喝茶。", kind=MemoryKind.PREFERENCE)
    service = _service(schema)
    now = datetime.now(UTC)
    for offset, text in enumerate(("我又喝咖啡了", "所以晚上怎么办", "还是喜欢喝茶")):
        moment = now + timedelta(minutes=offset)
        service.perceive("stream-1", text, person_id="person-1", observed_at=moment)
        cue = service.build_cue("stream-1", chat_type="private", now=moment)
        assert cue is not None
        result = await service.feed_forward(cue)
    assert result.selected[0].memory_id == tea
    assert coffee != result.selected[0].memory_id


async def test_max_memories_zero_disables(schema: VNextSchema) -> None:
    """max_memories 为 0 时不执行检索。"""
    await _create(schema, "咖啡失眠", "用户喝咖啡之后会失眠。")
    service = _service(schema, max_memories=0)
    result = await service.feed_forward(_cue("我今天又喝咖啡了"))
    assert result.selected == ()
    assert result.candidate_count == 0


async def test_cue_built_from_sensory_buffer(schema: VNextSchema) -> None:
    """L0 感知缓冲构造线索，排除 bot 自身发言人。"""
    service = _service(schema)
    now = datetime.now(UTC)
    service.perceive("stream-1", "第一句", person_id="person-1", observed_at=now)
    service.perceive("stream-1", "我的回复", person_id="bot", observed_at=now)
    cue = service.build_cue("stream-1", chat_type="group", now=now)
    assert cue is not None
    assert cue.person_ids == ("person-1",)
    assert "第一句" in cue.text
    assert service.build_cue("empty", chat_type="group", now=now) is None
