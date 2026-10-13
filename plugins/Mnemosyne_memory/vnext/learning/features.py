"""Deterministic feature snapshots for the optional memory controller."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime

FEATURE_DIM = 128


@dataclass(frozen=True, slots=True)
class LearningCandidate:
    """A candidate memory represented by controller-safe scalar features."""

    memory_id: str
    title: str
    content: str
    similarity: float = 0.0
    lexical: float = 0.0
    working: float = 0.0
    kda: float = 0.0
    recency: float = 0.0
    activation: float = 0.0
    probability: float = 0.0
    transformer_score: float = 0.0
    person_match: float = 0.0
    age_days: float = 0.0


@dataclass(frozen=True, slots=True)
class LearningFeatureBatch:
    """Fixed-size query/candidate features and their JSON representation."""

    tensor: tuple[tuple[tuple[float, ...], ...], ...]
    candidate_mask: tuple[tuple[bool, ...], ...]
    candidate_ids: tuple[tuple[str, ...], ...]
    labels: tuple[tuple[float, ...], ...]
    input_dim: int = FEATURE_DIM
    max_candidates: int = 32

    def to_json(self) -> dict[str, object]:
        """Return a JSON-safe snapshot for the training-sample table."""
        return {
            "tensor": [[list(vector) for vector in sequence] for sequence in self.tensor],
            "candidate_mask": [list(mask) for mask in self.candidate_mask],
            "input_dim": self.input_dim,
            "max_candidates": self.max_candidates,
        }

    def labels_json(self) -> dict[str, object]:
        """Return candidate labels in the same JSON-safe shape as the tensor."""
        values: object = (
            list(self.labels[0])
            if len(self.labels) == 1
            else [list(labels) for labels in self.labels]
        )
        return {"recall": values}


def build_feature_batch(
    *,
    query_text: str,
    candidates: tuple[LearningCandidate, ...],
    selected_ids: frozenset[str] = frozenset(),
    max_candidates: int = 32,
    input_dim: int = FEATURE_DIM,
    now: datetime | None = None,
) -> LearningFeatureBatch:
    """Build a deterministic batch shaped as ``[1, 1 + candidates, input_dim]``.

    The first slot is the query and remaining slots are candidates. Unused slots
    are zero-padded and marked false in ``candidate_mask``. Text contributes only
    hashed signs, while retrieval and activation values occupy stable scalar
    positions, so the snapshot contains no formal memory payload.
    """
    if input_dim < 16:
        raise ValueError("input_dim must be at least 16")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    current = now or datetime.now().astimezone()
    chosen = candidates[:max_candidates]
    query_vector = _text_vector(query_text, input_dim, salt="query")
    sequence = [query_vector]
    mask = []
    candidate_ids = []
    labels = []
    for candidate in chosen:
        sequence.append(_candidate_vector(candidate, input_dim, current))
        mask.append(True)
        candidate_ids.append(candidate.memory_id)
        labels.append(1.0 if candidate.memory_id in selected_ids else 0.0)
    zero = tuple(0.0 for _ in range(input_dim))
    while len(sequence) < max_candidates + 1:
        sequence.append(zero)
        mask.append(False)
        candidate_ids.append("")
        labels.append(0.0)
    return LearningFeatureBatch(
        tensor=(tuple(sequence),),
        candidate_mask=(tuple(mask),),
        candidate_ids=(tuple(candidate_ids),),
        labels=(tuple(labels),),
        input_dim=input_dim,
        max_candidates=max_candidates,
    )


def _candidate_vector(
    candidate: LearningCandidate,
    input_dim: int,
    now: datetime,
) -> tuple[float, ...]:
    """Encode candidate text and bounded retrieval scalars."""
    del now
    vector = list(
        _text_vector(f"{candidate.title}\n{candidate.content}", input_dim, salt="candidate")
    )
    scalar_values = (
        candidate.similarity,
        candidate.lexical,
        candidate.working,
        candidate.kda,
        candidate.recency,
        candidate.activation,
        candidate.probability,
        candidate.transformer_score,
        candidate.person_match,
        candidate.age_days / 365.0,
    )
    for index, value in enumerate(scalar_values):
        vector[index] = _bounded(value)
    return tuple(vector)


def _text_vector(text: str, input_dim: int, *, salt: str) -> tuple[float, ...]:
    """Map text to deterministic signed features without external embeddings."""
    values = [0.0] * input_dim
    tokens = tuple(part for part in text.casefold().split() if part)
    if not tokens:
        return tuple(values)
    for token in tokens:
        digest = hashlib.sha256(f"{salt}\0{token}".encode("utf-8")).digest()
        for offset in range(0, len(digest), 4):
            index = int.from_bytes(digest[offset : offset + 2], "big") % input_dim
            sign = 1.0 if digest[offset + 2] & 1 else -1.0
            values[index] += sign
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / norm for value in values)


def _bounded(value: float) -> float:
    """Keep scalar controller inputs finite and within a stable range."""
    if not math.isfinite(value):
        return 0.0
    return math.tanh(value)


__all__ = [
    "FEATURE_DIM",
    "LearningCandidate",
    "LearningFeatureBatch",
    "build_feature_batch",
]
