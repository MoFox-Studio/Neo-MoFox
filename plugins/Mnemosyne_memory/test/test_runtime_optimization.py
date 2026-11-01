"""突发摄入、并发隔离、历史积压和派生向量缓存的回归验证。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text, update

from ..router.memory_admin_router import VNextMemoryAdminRouter
from ..vnext import runtime_owner
from ..vnext.claim_service import ClaimHypothesisService
from ..vnext.embedding_cache import EpisodeEmbeddingCache
from ..vnext.episode_service import EpisodeService
from ..vnext.models import ClaimHypothesisModel, EpisodeModel, MemoryUpdateProposalModel
from ..vnext.proposal_service import ProposalService
from ..vnext.schema import VNextSchema
from .test_plugin_runtime import owner  # noqa: F401 -- 复用隔离的 Owner fixture


def message(index: int, stream: str = "s1") -> dict[str, object]:
    return dict(message_id=f"m{index}", stream_id=stream, time=datetime.now(UTC),
                content=f"coffee {index}", processed_plain_text=f"coffee {index}",
                sender_name="example", person_id="person-example")


@pytest.fixture
async def schema(tmp_path: Path):
    value = VNextSchema(str(tmp_path / "optimization.db"))
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


@pytest.fixture
async def managed(monkeypatch: pytest.MonkeyPatch):
    """只创建本测试的 asyncio 任务，不连接运行实例的托管器。"""
    handles = {}

    def create(coro, **kwargs):
        key = str(len(handles))
        handle = SimpleNamespace(task_id=key, task=asyncio.create_task(coro))
        handles[key] = handle
        return handle

    monkeypatch.setattr(runtime_owner, "create_managed_task", create)
    monkeypatch.setattr(runtime_owner, "get_managed_task", lambda key: handles[key])
    monkeypatch.setattr(runtime_owner, "cancel_managed_task", lambda key: handles[key].task.cancel())
    try:
        yield handles
    finally:
        for handle in handles.values():
            handle.task.cancel()
        await asyncio.gather(*(handle.task for handle in handles.values()), return_exceptions=True)


async def test_burst_persists_every_input_once_even_when_recall_is_coalesced(owner, schema, managed):
    owner._initialized = True
    owner.episode_service = EpisodeService(schema)
    owner._flashback_for_stream = AsyncMock(return_value=())
    owner.flashback.next_turn_index = AsyncMock(return_value=0)
    await asyncio.gather(*(owner.observe_message(message(index)) for index in range(30)))
    await owner.observe_message(message(29))  # 重放事件不重复建立经历。
    for _ in range(4):
        await asyncio.gather(*(handle.task for handle in tuple(managed.values())))
        if all(handle.task.done() for handle in managed.values()):
            break
    async with schema.database.session() as session:
        refs = set((await session.scalars(select(EpisodeModel.source_ref))).all())
    assert refs == {f"message:s1:m{index}:INPUT" for index in range(30)}
    assert len(owner._recent_messages["s1"]) == owner.config.vnext.flashback.context_turns
    assert owner._latest_input_episodes["s1"].source_type == "MESSAGE"


async def test_cancelled_event_finishes_accepted_local_ingestion(owner, schema, managed):
    owner._initialized = True
    owner.episode_service = EpisodeService(schema)
    owner._schedule_flashback_prefetch = lambda *args, **kwargs: None
    await owner._sqlite_write_lock.acquire()
    task = asyncio.create_task(owner.observe_message(message(1)))
    while not owner._ingestion_tasks:
        await asyncio.sleep(0)
    task.cancel()
    owner._sqlite_write_lock.release()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with schema.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(EpisodeModel)) == 1


async def test_remote_review_does_not_block_another_stream_ingestion(owner, schema, managed):
    owner._initialized = True
    owner.episode_service = EpisodeService(schema)
    owner._schedule_flashback_prefetch = lambda *args, **kwargs: None
    owner.proposal_service.collect_consolidation_candidates = AsyncMock()
    owner.claim_service.sync_pending_proposals = AsyncMock()
    started, release = asyncio.Event(), asyncio.Event()

    async def reviewer(stream):
        assert not owner._sqlite_write_lock.locked()
        started.set()
        await release.wait()

    owner.claim_service.review_pending = reviewer
    review = asyncio.create_task(owner._run_claim_review("slow-stream"))
    try:
        await started.wait()
        await asyncio.wait_for(owner.observe_message(message(1, "other-stream")), timeout=1)
        assert not review.done()
    finally:
        release.set()
        await review


async def test_semantic_io_releases_database_sessions_before_waiting(schema):
    started, release = asyncio.Event(), asyncio.Event()

    async def embed(texts):
        assert not schema.database._sessions
        started.set()
        await release.wait()
        return [(1.0, 0.0)] * len(texts)

    service = EpisodeService(schema, embedding_function=embed, semantic_timeout_ms=2000)
    await service.record_episode(message(1))
    current = await service.record_episode(message(2))
    recall = asyncio.create_task(service.recall_working_memory(current))
    try:
        await started.wait()
        await asyncio.wait_for(service.record_episode(message(3, "another-stream")), timeout=1)
        assert not recall.done()
    finally:
        release.set()
        await recall
        await service.close()


async def test_background_review_rotates_and_recovers_unobserved_backlog(owner, monkeypatch):
    owner._initialized = True
    owner.config.vnext.claim_review.background_stream_limit = 2
    owner._recent_messages.update({"a": {}, "b": {}})
    owner.claim_service.pending_stream_ids = AsyncMock(return_value=("c", "d", "e"))
    reviewed = []

    async def review(stream):
        reviewed.append(stream)

    owner._run_claim_review = review
    monkeypatch.setattr(runtime_owner.asyncio, "sleep", AsyncMock(side_effect=[None, None, None, asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await owner._run_consolidation_loop()
    assert reviewed == ["a", "b", "c", "d", "e", "a"]


async def test_scope_switch_blocks_existing_cross_stream_edges_and_working_memory(schema):
    enabled = EpisodeService(schema, cross_stream_recall_enabled=True)
    private = await enabled.record_episode(message(1, "private"))
    group = await enabled.record_episode(message(2, "group"))
    working = await enabled.recall_working_memory(group)
    assert private.episode_id in {item["episode_id"] for item in working["selected_episodes"]}
    restricted = EpisodeService(schema)
    restored = await restricted.current_working_memory("group")
    assert restored is not None and restored["selected_episodes"] == []
    recalled = await restricted.recall_working_memory(group)
    assert recalled["selected_episodes"] == []
    async with schema.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(EpisodeModel)) == 2


async def test_cache_reuses_vectors_and_partitions_model_identity():
    model = ["model-a"]
    embed = AsyncMock(side_effect=lambda texts: [(1.0, 0.0)] * len(texts))
    cache = EpisodeEmbeddingCache(embed, namespace=lambda: model[0])
    try:
        await cache.vectors(("query-a", "history"))
        await cache.vectors(("query-b", "history"))
        assert embed.await_args_list[1].args[0] == ("query-b",)
        await cache.vectors(("query-b", "history"))
        assert embed.await_count == 2
        model[0] = "model-b"
        await cache.vectors(("query-b", "history"))
        assert embed.await_count == 3
        assert cache.status()["hits"] >= 3
    finally:
        await cache.close()


async def test_cache_singleflight_survives_one_waiter_cancellation():
    started, release = asyncio.Event(), asyncio.Event()

    async def embed(texts):
        started.set()
        await release.wait()
        return [(0.0, 1.0)] * len(texts)

    embedder = AsyncMock(side_effect=embed)
    cache = EpisodeEmbeddingCache(embedder)
    first = asyncio.create_task(cache.vectors(("same",)))
    await started.wait()
    second = asyncio.create_task(cache.vectors(("same",)))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == ((0.0, 1.0),)
    assert embedder.await_count == 1
    await cache.close()
    assert cache.status()["inflight"] == 0


async def test_cache_ttl_capacity_and_invalid_vector_retry():
    clock = [0.0]
    embed = AsyncMock(side_effect=lambda texts: [(1.0, 0.0)] * len(texts))
    cache = EpisodeEmbeddingCache(embed, capacity=1, ttl_seconds=10, clock=lambda: clock[0])
    await cache.vectors(("a",))
    await cache.vectors(("b",))
    await cache.vectors(("a",))
    assert embed.await_count == 3 and cache.status()["size"] == 1
    clock[0] = 11
    await cache.vectors(("a",))
    assert embed.await_count == 4
    embed.side_effect = None
    embed.return_value = [(float("nan"), 1)]
    assert await cache.vectors(("bad",)) == (None,)
    embed.return_value = [(1, 0)]
    assert await cache.vectors(("bad",)) == ((1.0, 0.0),)
    await cache.close()


async def test_cache_timeout_falls_back_and_can_close_inflight_requests():
    async def embed(texts):
        await asyncio.Event().wait()

    cache = EpisodeEmbeddingCache(embed, timeout_ms=20)
    assert await cache.vectors(("slow",)) == (None,)
    await cache.close()
    assert cache.status()["timeouts"] >= 1
    assert cache.status()["inflight"] == 0


async def test_old_v8_database_gains_indexes_and_uses_them(schema):
    names = ("episode_observed", "episode_relation_type_target", "cue_episode",
             "proposal_stream_status", "claim_queue", "claim_proposal")
    async with schema.database.session() as session:
        for name in names:
            await session.execute(text(f"DROP INDEX idx_mnemosyne_vnext_{name}"))
    await schema.close()
    await schema.initialize()
    async with schema.database.session() as session:
        observed = (await session.execute(text(
            "EXPLAIN QUERY PLAN SELECT * FROM mnemosyne_vnext_episode "
            "WHERE observed_at >= '2026-01-01' ORDER BY observed_at DESC LIMIT 100"
        ))).all()
        superseded = (await session.execute(text(
            "EXPLAIN QUERY PLAN SELECT target_episode_id FROM mnemosyne_vnext_episode_relation "
            "WHERE relation_type='DIARY_SUPERSEDES'"
        ))).all()
    assert "episode_observed" in str(observed) and "TEMP B-TREE" not in str(observed)
    assert "episode_relation_type_target" in str(superseded)


async def test_backlog_sync_advances_past_first_twenty_and_obeys_retry_budget(schema):
    source = await EpisodeService(schema).record_episode(message(1))
    proposals = ProposalService(schema)
    for index in range(25):
        await proposals.propose(stream_id="s1", claim=f"coffee preference {index}",
                                evidence_ids=(source.episode_id,), operation="uncertain")
    reviewer = AsyncMock(return_value=json.dumps(dict(
        decision="defer", evidence_ids=[source.episode_id], reason="needs review",
    )))
    service = ClaimHypothesisService(schema, proposals, reviewer=reviewer, max_per_run=1)
    assert await service.pending_stream_ids() == ("s1",)
    assert len(await service.sync_pending_proposals("s1")) == 20
    assert len(await service.sync_pending_proposals("s1")) == 5
    assert await service.sync_pending_proposals("s1") == ()
    await service.review_pending("s1")
    await service.review_pending("s1")
    assert reviewer.await_count == 2  # 不会反复重试刚刚延后的同一条。
    backlog = await service.backlog_status()
    assert backlog["claims"] == {"DEFERRED": 2, "PENDING": 23}
    assert backlog["proposals"] == {"PENDING": 25}
    assert backlog["oldest_pending_at"]


async def test_review_timeout_and_concurrent_review_do_not_duplicate_model_calls(schema):
    source = await EpisodeService(schema).record_episode(message(1))
    proposals = ProposalService(schema)
    await proposals.propose(stream_id="s1", claim="coffee", evidence_ids=(source.episode_id,), operation="uncertain")

    async def never(prompt, task):
        await asyncio.Event().wait()

    reviewer = AsyncMock(side_effect=never)
    service = ClaimHypothesisService(schema, proposals, reviewer=reviewer, review_timeout_seconds=.2)
    await service.sync_pending_proposals("s1")
    await asyncio.gather(service.review_pending("s1"), service.review_pending("s1"))
    assert reviewer.await_count == 1
    assert await service.pending_stream_ids() == ()
    async with schema.database.session() as session:
        await session.execute(update(ClaimHypothesisModel).values(
            reviewed_at=datetime.now(UTC) - timedelta(hours=1), review_attempts=3,
        ))
    assert (await service.backlog_status())["exhausted"] == 1
    assert await service.pending_stream_ids() == ()
