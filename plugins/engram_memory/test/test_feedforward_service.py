"""前馈式记忆检索服务的端到端测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ..vnext.domain import (
    CreateMemoryInput,
    EvidenceInput,
    EvidenceMessageInput,
    MemoryLifecycleInput,
    RetrievalQuery,
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
from ..vnext.models import EvidenceMessageLinkModel, EvidenceMessageSnapshotModel
from ..vnext.retrieval_service import RetrievalService, VectorSearchBackend
from ..vnext.schema import VNextSchema


class _NoVector(VectorSearchBackend):
    """测试中只走词法与结构化通道。"""


class _BrokenVector(VectorSearchBackend):
    """模拟运行时不可用的向量服务。"""

    async def query_scored(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[tuple[str, float | None], ...]:
        """抛出后端异常，验证词法通道继续工作。"""
        del texts, top_k
        raise RuntimeError("embedding service unavailable")


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


async def test_vector_failure_keeps_lexical_recall(schema: VNextSchema) -> None:
    """向量服务异常时，正式记忆仍可由词法通道检索。"""
    memory_id = await _create(schema, "搬家计划", "下个月准备搬到新城市")
    results = await RetrievalService(schema, _BrokenVector()).search(
        RetrievalQuery(text="搬家计划 新城市", top_k=5)
    )
    assert [item.memory_id for item in results] == [memory_id]


async def test_person_scoped_cue_does_not_recall_another_person(schema: VNextSchema) -> None:
    """当前人物线索不能仅凭相同文本召回其他人物的记忆。"""
    await _create(schema, "共同安排", "周末一起去咖啡馆。", person_id="person-a")
    service = _service(schema)
    result = await service.feed_forward(_cue("周末一起去咖啡馆", person="person-b"))
    assert result.selected == ()


async def test_source_less_person_memory_is_private_only(schema: VNextSchema) -> None:
    """没有可验证来源流的人物记忆不进入群聊前馈。"""
    memory_id = await _create(schema, "私下安排", "周末一起去咖啡馆。")
    service = _service(schema)
    group = await service.feed_forward(
        FeedForwardCue(
            stream_id="group-stream",
            text="周末一起去咖啡馆",
            person_ids=("person-1",),
            chat_type="group",
            now=datetime.now(UTC),
            latest_text="周末一起去咖啡馆",
        )
    )
    private = await service.feed_forward(_cue("周末一起去咖啡馆"))
    assert group.selected == ()
    assert memory_id in [item.memory_id for item in private.selected]


async def test_private_source_is_not_recalled_in_group(schema: VNextSchema) -> None:
    """私聊来源记忆不能进入群聊前馈提示。"""
    now = datetime.now(UTC) - timedelta(days=1)
    writer = MemoryService(schema, "example-embedding")
    async def read_private_source(
        references: tuple[tuple[str, str], ...],
    ) -> tuple[dict[str, object], ...]:
        """返回隔离的私聊消息来源快照。"""
        return tuple(
            {
                "message_id": message_id,
                "stream_id": stream_id,
                "time": now.isoformat(),
                "platform": "qq",
                "chat_type": "private",
                "person_id": "person-1",
                "sender_id": "user-1",
                "content": "周末去咖啡馆",
                "reply_to": None,
            }
            for stream_id, message_id in references
        )

    writer._evidence_service._message_reader = read_private_source
    result = await writer.create_memory(
        CreateMemoryInput(
            title="私聊计划",
            content="用户计划周末去咖啡馆。",
            memory_kind=MemoryKind.EVENT,
            subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
            observed_at=now,
            evidence=(EvidenceInput(EvidenceSourceType.ADMIN, now, note="隔离测试来源"),),
        ),
        WriteContext(ActorType.ADMIN),
    )
    async with schema.database.session() as session:
        evidence_id = result.evidence_ids[0]
        session.add(
            EvidenceMessageLinkModel(
                evidence_id=evidence_id,
                stream_id="private-stream",
                message_id="private-message",
                ordinal=0,
            )
        )
        session.add(
            EvidenceMessageSnapshotModel(
                stream_id="private-stream",
                message_id="private-message",
                captured_at=now,
                payload={
                    "message_id": "private-message",
                    "stream_id": "private-stream",
                    "time": now.isoformat(),
                    "platform": "qq",
                    "chat_type": "private",
                    "person_id": "person-1",
                    "sender_id": "user-1",
                    "content": "周末去咖啡馆",
                },
            )
        )
    service = _service(schema)
    result = await service.feed_forward(
        FeedForwardCue(
            stream_id="group-stream",
            text="周末去咖啡馆",
            person_ids=("person-1",),
            chat_type="group",
            now=datetime.now(UTC),
            latest_text="周末去咖啡馆",
        )
    )
    assert result.selected == ()


async def test_group_memory_is_recalled_only_in_its_source_group(
    schema: VNextSchema,
) -> None:
    """群聊记忆只进入同一来源流，不能跨群前馈。"""
    now = datetime.now(UTC) - timedelta(days=1)
    writer = MemoryService(schema, "example-embedding")

    async def read_group_source(
        references: tuple[tuple[str, str], ...],
    ) -> tuple[dict[str, object], ...]:
        """返回一个群聊的隔离来源快照。"""
        return tuple(
            {
                "message_id": message_id,
                "stream_id": stream_id,
                "time": now.isoformat(),
                "platform": "qq",
                "chat_type": "group",
                "person_id": "person-1",
                "sender_id": "user-1",
                "content": "周末去咖啡馆",
                "reply_to": None,
            }
            for stream_id, message_id in references
        )

    writer._evidence_service._message_reader = read_group_source
    await writer.create_memory(
        CreateMemoryInput(
            title="群里约定",
            content="群里约好周末去咖啡馆。",
            memory_kind=MemoryKind.EVENT,
            subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
            observed_at=now,
            evidence=(
                EvidenceInput(
                    EvidenceSourceType.MESSAGE_SET,
                    now,
                    messages=(EvidenceMessageInput("group-message", "group-a"),),
                ),
            ),
        ),
        WriteContext(ActorType.ADMIN),
    )
    service = _service(schema)
    same_group = await service.feed_forward(
        FeedForwardCue(
            stream_id="group-a",
            text="周末去咖啡馆",
            person_ids=("person-1",),
            chat_type="group",
            now=datetime.now(UTC),
            latest_text="周末去咖啡馆",
        )
    )
    other_group = await service.feed_forward(
        FeedForwardCue(
            stream_id="group-b",
            text="周末去咖啡馆",
            person_ids=("person-1",),
            chat_type="group",
            now=datetime.now(UTC),
            latest_text="周末去咖啡馆",
        )
    )
    assert len(same_group.selected) == 1
    assert other_group.selected == ()


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
        source_scopes=(),
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
