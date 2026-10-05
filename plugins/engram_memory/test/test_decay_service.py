"""正式记忆强度衰减与遗忘候选测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ..vnext.decay_service import MemoryDecayService
from ..vnext.domain import CreateMemoryInput, EvidenceInput, SubjectInput, WriteContext
from ..vnext.enums import ActorType, EvidenceSourceType, MemoryEventType, MemoryKind, SubjectKind
from ..vnext.memory_service import MemoryService
from ..vnext.models import MemoryEventModel, MemoryModel
from ..vnext.schema import VNextSchema


@pytest.fixture
async def schema(tmp_path: Path):
    """提供隔离的 Engram 数据库。"""
    value = VNextSchema(str(tmp_path / "decay.db"))
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


async def _create_memory(schema: VNextSchema, observed_at: datetime) -> str:
    """创建带稳定来源的测试记忆。"""
    result = await MemoryService(schema, "example-embedding").create_memory(
        CreateMemoryInput(
            title="旧偏好",
            content="用户过去喜欢喝茶。",
            memory_kind=MemoryKind.PREFERENCE,
            subject=SubjectInput(SubjectKind.PERSON, person_id="person-1"),
            observed_at=observed_at,
            evidence=(
                EvidenceInput(
                    EvidenceSourceType.LEGACY_RECORD,
                    observed_at,
                    note="测试来源",
                ),
            ),
        ),
        WriteContext(ActorType.ADMIN),
    )
    return result.memory_id


async def test_strength_decays_and_recall_events_are_counted_once(
    schema: VNextSchema,
) -> None:
    """年龄降低强度，重复联表不会把一次召回重复计算。"""
    now = datetime(2026, 10, 5, tzinfo=UTC)
    memory_id = await _create_memory(schema, now - timedelta(days=180))
    async with schema.database.session() as session:
        for index, occurred_at in enumerate(
            (now - timedelta(days=2), now - timedelta(days=1)),
            start=1,
        ):
            session.add(
                MemoryEventModel(
                    event_id=f"recall-{index}",
                    memory_id=memory_id,
                    revision_id=None,
                    event_type=MemoryEventType.RECALLED,
                    actor_type=ActorType.ACTOR,
                    actor_ref="test",
                    stream_id="stream-1",
                    occurred_at=occurred_at,
                    payload_json={},
                )
            )

    strength = await MemoryDecayService(schema).strength(memory_id, now=now)

    assert strength is not None
    assert strength.age_days == pytest.approx(180.0)
    assert strength.recall_count == 2
    assert strength.last_recalled_at == now - timedelta(days=1)
    assert 0 < strength.strength < 1


async def test_low_strength_is_a_candidate_but_not_automatic_tombstone(
    schema: VNextSchema,
) -> None:
    """低强度记忆只进入可审查候选，不自动删除正式记忆。"""
    now = datetime(2026, 10, 5, tzinfo=UTC)
    memory_id = await _create_memory(schema, now - timedelta(days=720))
    service = MemoryDecayService(schema, min_age_days=30, forget_threshold=0.12)

    candidates = await service.list_decay_candidates(now=now)

    assert [item.memory_id for item in candidates] == [memory_id]
    assert candidates[0].strength <= 0.12
    async with schema.database.session() as session:
        memory = await session.get(MemoryModel, memory_id)
        assert memory is not None
        assert memory.status.value == "ACTIVE"


def test_recall_view_blurs_details_progressively_without_mutating_source() -> None:
    """强度降低时回忆视图逐步丢失细节，输入正文保持不变。"""
    source = "去年三月，我在周五下午五点买了三杯咖啡。后来把其中两杯送给同事。之后我们一起喝了最后一杯。"

    detailed = MemoryDecayService.recall_view(
        title="咖啡",
        content=source,
        strength=0.8,
    )
    partial = MemoryDecayService.recall_view(
        title="咖啡",
        content=source,
        strength=0.5,
    )
    gist = MemoryDecayService.recall_view(
        title="咖啡",
        content=source,
        strength=0.3,
    )
    faint = MemoryDecayService.recall_view(
        title="咖啡",
        content=source,
        strength=0.1,
    )

    assert detailed["content"] == source
    assert partial["content"] != source
    assert gist["content"] == "去年三月，我在周五下午五点买了三杯咖啡。"
    assert faint["content"] == "咖啡"
    assert "周五下午五点" in detailed["content"]
    assert "周五下午五点" in partial["content"]
    assert "周五下午五点" in gist["content"]
    assert "周五下午五点" not in faint["content"]
    assert "之后我们" not in partial["content"]
    assert "两杯" not in gist["content"]