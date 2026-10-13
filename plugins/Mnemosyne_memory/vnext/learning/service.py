"""Mnemosyne 可训练记忆控制器的生命周期与审计服务。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update

from ..config_sections import LearningSection
from ..models import (
    MemoryCheckpointModel,
    MemoryFeedbackModel,
    MemoryTrainingRunModel,
    MemoryTrainingSampleModel,
)
from ..schema import VNextSchema
from .features import LearningFeatureBatch
from .model import MODEL_FORMAT_VERSION, MemoryControllerOutput, TrainableMemoryTransformer

try:
    import torch
    from torch import Tensor
except ImportError:  # pragma: no cover - depends on optional environment
    torch = None  # type: ignore[assignment]
    Tensor = Any  # type: ignore[misc,assignment]


@dataclass(frozen=True, slots=True)
class LearningInference:
    """一次 shadow 推理的可持久化结果。"""

    recall: tuple[float, ...]
    conflict: tuple[float, ...]
    consolidation: tuple[float, ...]
    model_id: str


@dataclass(frozen=True, slots=True)
class TrainingSummary:
    """一次训练运行的摘要指标。"""

    run_id: str
    model_id: str
    sample_count: int
    train_loss: float
    status: str


class MemoryLearningService:
    """管理可选 Transformer，不拥有正式记忆的写权限。"""

    def __init__(self, schema: VNextSchema, config: LearningSection) -> None:
        """按配置准备学习服务；不会在构造阶段访问数据库或文件。"""
        self._schema = schema
        self.config = config
        self._model: TrainableMemoryTransformer | None = None
        self._optimizer: Any = None
        self._model_id = str(uuid4())
        self._loaded_checkpoint: Path | None = None

    @property
    def available(self) -> bool:
        """返回可训练依赖和模型是否已经就绪。"""
        return self._model is not None and torch is not None

    @property
    def model_id(self) -> str:
        """返回当前进程中的模型标识。"""
        return self._model_id

    def initialize(self) -> bool:
        """加载可选模型；依赖缺失时返回 False 而不是阻断插件。"""
        if not self.config.enabled or not self.config.inference_enabled:
            return False
        if torch is None:
            return False
        self._model = TrainableMemoryTransformer(
            input_dim=self.config.input_dim,
            d_model=self.config.d_model,
            nhead=self.config.nhead,
            num_layers=self.config.num_layers,
            dim_feedforward=self.config.dim_feedforward,
            dropout=self.config.dropout,
            max_candidates=self.config.max_candidates,
        )
        self._model.eval()
        if self.config.training_enabled:
            self._optimizer = torch.optim.AdamW(
                self._model.parameters(), lr=self.config.learning_rate
            )
        return True

    def close(self) -> None:
        """Release the in-process model and optimizer without touching the database."""
        self._model = None
        self._optimizer = None
        self._loaded_checkpoint = None

    def infer_batch(self, batch: LearningFeatureBatch) -> LearningInference:
        """Convert a serializable feature batch and run shadow inference."""
        if torch is None:
            raise RuntimeError("Mnemosyne learning requires PyTorch")
        if batch.input_dim != self.config.input_dim:
            raise ValueError("learning feature input_dim does not match configuration")
        if batch.max_candidates != self.config.max_candidates:
            raise ValueError("learning feature max_candidates does not match configuration")
        features = torch.tensor(batch.tensor, dtype=torch.float32)
        candidate_mask = torch.tensor(batch.candidate_mask, dtype=torch.bool)
        return self.infer(features, candidate_mask=candidate_mask)

    def infer(self, features: Tensor, candidate_mask: Tensor | None = None) -> LearningInference:
        """运行无梯度 shadow 推理并返回候选级别概率。"""
        if self._model is None or torch is None:
            raise RuntimeError("Mnemosyne learning model is not initialized")
        self._model.eval()
        with torch.no_grad():
            output = self._model(features, candidate_mask=candidate_mask)
            probabilities = output.probabilities()
        return LearningInference(
            recall=tuple(float(value) for value in probabilities["recall"].flatten()),
            conflict=tuple(float(value) for value in probabilities["conflict"].flatten()),
            consolidation=tuple(
                float(value) for value in probabilities["consolidation"].flatten()
            ),
            model_id=self._model_id,
        )

    async def record_feedback(
        self,
        *,
        stream_id: str,
        feedback_type: str,
        reward: float,
        source: str,
        query_episode_id: str | None = None,
        candidate_episode_id: str | None = None,
        candidate_memory_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str:
        """追加一条候选反馈，不修改正式记忆正文。"""
        if not stream_id.strip():
            raise ValueError("stream_id 不能为空")
        if not feedback_type.strip() or not source.strip():
            raise ValueError("feedback_type 和 source 不能为空")
        if not -1.0 <= reward <= 1.0:
            raise ValueError("reward 必须位于 [-1, 1]")
        feedback_id = str(uuid4())
        async with self._schema.database.session() as session:
            session.add(
                MemoryFeedbackModel(
                    feedback_id=feedback_id,
                    stream_id=stream_id,
                    query_episode_id=query_episode_id,
                    candidate_episode_id=candidate_episode_id,
                    candidate_memory_id=candidate_memory_id,
                    feedback_type=feedback_type,
                    reward=reward,
                    source=source,
                    model_id=self._model_id if self.available else None,
                    metadata_json=metadata,
                    created_at=datetime.now(UTC),
                )
            )
        return feedback_id

    async def record_training_sample(
        self,
        *,
        stream_id: str,
        candidate_ids: list[str],
        features: dict[str, object],
        labels: dict[str, object],
        source: str,
        query_episode_id: str | None = None,
    ) -> str:
        """追加一份冻结特征样本，供之后的本地训练消费。"""
        if not stream_id.strip() or not source.strip():
            raise ValueError("stream_id 和 source 不能为空")
        if not candidate_ids:
            raise ValueError("candidate_ids 不能为空")
        sample_id = str(uuid4())
        async with self._schema.database.session() as session:
            session.add(
                MemoryTrainingSampleModel(
                    sample_id=sample_id,
                    stream_id=stream_id,
                    query_episode_id=query_episode_id,
                    candidate_ids=candidate_ids,
                    features_json=features,
                    labels_json=labels,
                    source=source,
                    created_at=datetime.now(UTC),
                )
            )
        return sample_id

    async def feedback_count(self) -> int:
        """返回当前累计反馈数。"""
        async with self._schema.database.session() as session:
            result = await session.scalar(select(func.count()).select_from(MemoryFeedbackModel))
        return int(result or 0)

    async def save_checkpoint(
        self,
        *,
        training_run_id: str | None = None,
        metrics: dict[str, object] | None = None,
    ) -> MemoryCheckpointModel:
        """保存模型权重并登记 SHA-256 元数据。"""
        if self._model is None or torch is None:
            raise RuntimeError("Mnemosyne learning model is not initialized")
        target_dir = Path(self.config.checkpoint_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_model_id = str(uuid4())
        path = target_dir / f"{checkpoint_model_id}.pt"
        torch.save(self._model.state_dict(), path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        record = MemoryCheckpointModel(
            model_id=checkpoint_model_id,
            format_version=MODEL_FORMAT_VERSION,
            architecture_json=self._model.architecture(),
            checkpoint_path=str(path),
            checkpoint_sha256=digest,
            storage_mode="torch",
            training_run_id=training_run_id,
            metrics_json=metrics,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        async with self._schema.database.session() as session:
            await session.execute(
                update(MemoryCheckpointModel)
                .where(MemoryCheckpointModel.model_id != checkpoint_model_id)
                .values(is_active=False)
            )
            session.add(record)
        self._model_id = checkpoint_model_id
        self._loaded_checkpoint = path
        return record

    async def load_active_checkpoint(self) -> bool:
        """Load and hash-check the active local checkpoint, if one exists."""
        if self._model is None or torch is None:
            return False
        async with self._schema.database.session() as session:
            record = (
                await session.scalars(
                    select(MemoryCheckpointModel)
                    .where(MemoryCheckpointModel.is_active.is_(True))
                    .order_by(MemoryCheckpointModel.created_at.desc())
                )
            ).first()
        if record is None:
            return False
        path = Path(record.checkpoint_path)
        if not path.is_file():
            raise FileNotFoundError(f"学习 checkpoint 不存在: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record.checkpoint_sha256:
            raise ValueError(f"学习 checkpoint SHA-256 校验失败: {path}")
        architecture = self._model.architecture()
        if any(record.architecture_json.get(key) != value for key, value in architecture.items()):
            raise ValueError("学习 checkpoint 架构与当前配置不一致")
        try:
            state = torch.load(path, map_location="cpu", weights_only=True)
        except TypeError:
            state = torch.load(path, map_location="cpu")
        self._model.load_state_dict(state)
        self._model.eval()
        self._model_id = record.model_id
        self._loaded_checkpoint = path
        return True

    async def train_once(self) -> TrainingSummary | None:
        """消费已保存样本执行一次最小监督训练；样本不足时不训练。"""
        if not self.config.training_enabled or self._model is None or self._optimizer is None:
            return None
        if torch is None:
            return None
        async with self._schema.database.session() as session:
            feedback_count = await session.scalar(
                select(func.count()).select_from(MemoryFeedbackModel)
            )
            if int(feedback_count or 0) < self.config.min_feedback_samples:
                return None
            pending_count = await session.scalar(
                select(func.count())
                .select_from(MemoryTrainingSampleModel)
                .where(MemoryTrainingSampleModel.consumed_at.is_(None))
            )
            if int(pending_count or 0) < self.config.min_feedback_samples:
                return None
            rows = (
                await session.scalars(
                    select(MemoryTrainingSampleModel)
                    .where(MemoryTrainingSampleModel.consumed_at.is_(None))
                    .order_by(MemoryTrainingSampleModel.created_at)
                    .limit(self.config.batch_size)
                )
            ).all()
        if not rows:
            return None
        feedback_keys = {
            (row.stream_id, str(candidate_id))
            for row in rows
            for candidate_id in row.candidate_ids
            if candidate_id
        }
        feedback_map: dict[tuple[str, str], MemoryFeedbackModel] = {}
        if feedback_keys:
            async with self._schema.database.session() as session:
                feedback_rows = (
                    await session.scalars(
                        select(MemoryFeedbackModel)
                        .where(
                            MemoryFeedbackModel.stream_id.in_({key[0] for key in feedback_keys}),
                            MemoryFeedbackModel.candidate_memory_id.in_(
                                {key[1] for key in feedback_keys}
                            ),
                        )
                        .order_by(MemoryFeedbackModel.created_at)
                    )
                ).all()
            for feedback in feedback_rows:
                if feedback.candidate_memory_id is not None:
                    feedback_map[(feedback.stream_id, feedback.candidate_memory_id)] = feedback
        run_id = str(uuid4())
        serialized_features = [row.features_json["tensor"] for row in rows]
        if serialized_features and isinstance(serialized_features[0], list):
            serialized_features = [item[0] for item in serialized_features]
        features = torch.tensor(
            serialized_features, dtype=torch.float32
        )
        candidate_masks = torch.tensor(
            [
                row.features_json.get(
                    "candidate_mask",
                    [[True] * self.config.max_candidates],
                )[0]
                for row in rows
            ],
            dtype=torch.bool,
        )
        labels = torch.tensor(
            [
                [
                    (
                        1.0
                        if feedback_map.get((row.stream_id, candidate_id), None)
                        and feedback_map[(row.stream_id, candidate_id)].reward > 0
                        else 0.0
                        if feedback_map.get((row.stream_id, candidate_id), None)
                        else float(label)
                    )
                    for candidate_id, label in zip(
                        row.candidate_ids,
                        _recall_labels(row.labels_json),
                        strict=True,
                    )
                ]
                for row in rows
            ],
            dtype=torch.float32,
        )
        self._model.train()
        self._optimizer.zero_grad()
        output: MemoryControllerOutput = self._model(
            features,
            candidate_mask=candidate_masks,
        )
        loss_values = torch.nn.functional.binary_cross_entropy_with_logits(
            output.recall_logits,
            labels,
            reduction="none",
        )
        loss = loss_values.masked_select(candidate_masks).mean()
        loss.backward()
        self._optimizer.step()
        self._model.eval()
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            await session.execute(
                update(MemoryTrainingSampleModel)
                .where(MemoryTrainingSampleModel.sample_id.in_([row.sample_id for row in rows]))
                .values(consumed_at=now)
            )
            session.add(
                MemoryTrainingRunModel(
                    run_id=run_id,
                    model_id=self._model_id,
                    sample_count=len(rows),
                    train_loss=float(loss.detach().cpu().item()),
                    status="COMPLETED",
                    created_at=now,
                )
            )
        checkpoint = await self.save_checkpoint(
            training_run_id=run_id,
            metrics={"train_loss": float(loss.detach().cpu().item())},
        )
        async with self._schema.database.session() as session:
            await session.execute(
                update(MemoryTrainingRunModel)
                .where(MemoryTrainingRunModel.run_id == run_id)
                .values(model_id=checkpoint.model_id)
            )
        return TrainingSummary(
            run_id=run_id,
            model_id=checkpoint.model_id,
            sample_count=len(rows),
            train_loss=float(loss.detach().cpu().item()),
            status="COMPLETED",
        )


def _recall_labels(labels_json: dict[str, object]) -> list[object]:
    """Normalize single-sample and legacy nested recall label snapshots."""
    raw = labels_json.get("recall", [])
    if not isinstance(raw, list):
        raise ValueError("训练样本 recall 标签必须是数组")
    if raw and isinstance(raw[0], list):
        nested = raw[0]
        if not isinstance(nested, list):
            raise ValueError("训练样本 recall 标签格式无效")
        return nested
    return raw


__all__ = ["LearningInference", "MemoryLearningService", "TrainingSummary"]