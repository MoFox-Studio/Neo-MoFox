"""Tests for the optional trainable memory-controller boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from ..config import MnemosyneMemoryConfig
from ..vnext.learning.features import LearningCandidate, build_feature_batch
from ..vnext.learning.model import TrainableMemoryTransformer, torch
from ..vnext.learning.service import MemoryLearningService
from ..vnext.schema import SCHEMA_VERSION, VNextSchema


@pytest.fixture
async def schema(tmp_path: Path):
    """Provide an isolated vNext database with the current schema."""
    value = VNextSchema(str(tmp_path / "learning.db"))
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


def test_learning_defaults_are_safe() -> None:
    """Learning is disabled, shadow-only, and does not require torch by default."""
    config = MnemosyneMemoryConfig()
    learning = config.vnext.learning
    assert learning.enabled is False
    assert learning.shadow_mode is True
    assert learning.training_enabled is False
    assert learning.input_dim == 128
    assert learning.max_candidates == 32


def test_feature_batch_is_fixed_size_and_json_safe() -> None:
    """Feature snapshots align candidates, labels, masks, and padding slots."""
    batch = build_feature_batch(
        query_text="咖啡 失眠",
        candidates=(
            LearningCandidate(
                memory_id="memory-1",
                title="咖啡",
                content="用户喝咖啡会失眠",
                similarity=0.8,
                activation=1.2,
            ),
        ),
        selected_ids=frozenset({"memory-1"}),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert len(batch.tensor) == 1
    assert len(batch.tensor[0]) == 33
    assert len(batch.tensor[0][0]) == 128
    assert batch.candidate_mask == (tuple([True] + [False] * 31),)
    assert batch.candidate_ids[0][0] == "memory-1"
    assert batch.candidate_ids[0][1] == ""
    assert batch.labels == (tuple([1.0] + [0.0] * 31),)
    json.dumps(batch.to_json(), ensure_ascii=False)
    json.dumps(batch.labels_json(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_schema_creates_learning_tables(schema: VNextSchema) -> None:
    """The current schema includes all learning audit tables."""
    assert SCHEMA_VERSION == 8
    async with schema.database.session() as session:
        names = {
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name LIKE 'mnemosyne_vnext_memory_%'"
                    )
                )
            ).all()
        }
    assert {
        "mnemosyne_vnext_memory_feedback",
        "mnemosyne_vnext_memory_training_sample",
        "mnemosyne_vnext_memory_training_run",
        "mnemosyne_vnext_memory_checkpoint",
    } <= names


@pytest.mark.asyncio
async def test_feedback_and_training_sample_round_trip(schema: VNextSchema) -> None:
    """Feedback and frozen features are persisted without formal-memory writes."""
    service = MemoryLearningService(schema, MnemosyneMemoryConfig().vnext.learning)
    service.config.enabled = True
    feedback_id = await service.record_feedback(
        stream_id="stream-1",
        candidate_memory_id="memory-1",
        feedback_type="helpful",
        reward=1.0,
        source="test",
    )
    sample_id = await service.record_training_sample(
        stream_id="stream-1",
        candidate_ids=["memory-1", ""],
        features={"tensor": [[[0.0] * 128] * 33], "candidate_mask": [[True, False]]},
        labels={"recall": [1.0, 0.0]},
        source="test",
    )
    assert feedback_id
    assert sample_id
    assert await service.feedback_count() == 1


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_reward(schema: VNextSchema) -> None:
    """Feedback validation rejects values that cannot be training rewards."""
    service = MemoryLearningService(schema, MnemosyneMemoryConfig().vnext.learning)
    service.config.enabled = True
    with pytest.raises(ValueError, match="reward"):
        await service.record_feedback(
            stream_id="stream-1",
            candidate_memory_id="memory-1",
            feedback_type="helpful",
            reward=2.0,
            source="test",
        )


def test_missing_torch_is_guarded() -> None:
    """The optional model remains importable and fails clearly without torch."""
    if torch is not None:
        pytest.skip("selected environment provides optional torch")
    with pytest.raises(RuntimeError, match="requires PyTorch"):
        TrainableMemoryTransformer()


@pytest.mark.asyncio
async def test_training_only_skips_bad_samples_and_keeps_feedback_for_each_head(
    schema: VNextSchema, tmp_path: Path, monkeypatch
) -> None:
    """Exercise actual CPU training, queue recovery and checkpoint reload together."""
    if torch is None:
        pytest.skip("optional torch is not installed")
    config = MnemosyneMemoryConfig().vnext.learning.model_copy(update={
        "enabled": True, "inference_enabled": False, "training_enabled": True,
        "input_dim": 16, "d_model": 8, "nhead": 2, "num_layers": 1,
        "dim_feedforward": 16, "max_candidates": 2, "batch_size": 1,
        "min_feedback_samples": 1, "checkpoint_dir": str(tmp_path / "checkpoints"),
    })
    service = MemoryLearningService(schema, config)
    assert service.initialize() is True
    batches = [
        build_feature_batch(query_text="old", candidates=(LearningCandidate("old", "old", "old"),), input_dim=32, max_candidates=2),
        build_feature_batch(query_text="empty", candidates=(), input_dim=16, max_candidates=2),
        build_feature_batch(query_text="咖啡", candidates=(LearningCandidate("memory-1", "咖啡", "咖啡影响睡眠"),), selected_ids=frozenset({"memory-1"}), input_dim=16, max_candidates=2),
    ]
    sample_ids = []
    for batch in batches:
        sample_ids.append(await service.record_training_sample(
            stream_id="stream-1", candidate_ids=list(batch.candidate_ids[0]),
            features=batch.to_json(), labels=batch.labels_json(), source="test",
        ))
    for task, reward in (("recall", -1.0), ("conflict", 1.0)):
        await service.record_feedback(stream_id="stream-1", candidate_memory_id="memory-1",
            feedback_type=task, reward=reward, source="test", metadata={"training_sample_id": sample_ids[-1]})
    with pytest.raises(RuntimeError, match="disabled"):
        service.infer_batch(batches[-1])
    observed_targets = []
    original_loss = torch.nn.functional.binary_cross_entropy_with_logits
    def capture_loss(logits, targets, **kwargs):
        observed_targets.append(targets.detach().clone())
        return original_loss(logits, targets, **kwargs)
    monkeypatch.setattr(torch.nn.functional, "binary_cross_entropy_with_logits", capture_loss)
    summary = await service.train_once()
    assert summary is not None and summary.sample_count == 1
    assert len(observed_targets) == 2
    assert observed_targets[0][0, 0].item() == 0.0  # Explicit recall feedback overrides pseudo-label 1.
    assert observed_targets[1][0, 0].item() == 1.0  # Conflict feedback survives on the same candidate.
    async with schema.database.session() as session:
        assert await session.scalar(text("SELECT count(*) FROM mnemosyne_vnext_memory_training_sample WHERE consumed_at IS NOT NULL")) == 3
    restored = MemoryLearningService(schema, config)
    assert restored.initialize() is True
    assert await restored.load_active_checkpoint() is True
    assert restored.trained is True and restored.model_id == summary.model_id
    service.close()
    restored.close()
