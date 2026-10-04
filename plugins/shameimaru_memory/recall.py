"""线索驱动的重构式记忆召回。"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Iterable

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


@dataclass(frozen=True, slots=True)
class RecallCandidate:
    """参与一次前馈召回的记忆候选。"""

    memory_id: str
    text: str
    timestamp: float
    person_ids: frozenset[str]


def _tokens(text: str) -> frozenset[str]:
    """提取可解释的中英文线索词。"""
    return frozenset(match.group(0).lower() for match in _TOKEN_RE.finditer(text or ""))


def _semantic_overlap(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokens(text)) / len(query_tokens)


def _recency(timestamp: float, now: float, decay_seconds: float) -> float:
    if timestamp <= 0.0 or decay_seconds <= 0.0:
        return 0.0
    age = max(0.0, now - timestamp)
    return math.exp(-age / decay_seconds)


def _inhibition(
    memory_id: str,
    state: dict[str, Any],
    now: float,
    inhibition_seconds: float,
) -> float:
    raw = state.get(memory_id)
    if not isinstance(raw, dict):
        return 0.0
    last_recalled_at = float(raw.get("last_recalled_at") or 0.0)
    if last_recalled_at <= 0.0 or inhibition_seconds <= 0.0:
        return 0.0
    age = max(0.0, now - last_recalled_at)
    return max(0.0, 1.0 - age / inhibition_seconds)


def select_candidates(
    candidates: Iterable[RecallCandidate],
    *,
    query: str,
    person_ids: set[str],
    state: dict[str, Any],
    stream_id: str,
    limit: int,
    now: float,
    noise: float = 0.12,
    inhibition_seconds: float = 1800.0,
    decay_seconds: float = 7 * 24 * 3600.0,
) -> list[RecallCandidate]:
    """一次性计算激活并选择具有多样性的记忆候选。

    随机性只改变已有候选的选择顺序，不生成新的记忆内容。
    ``state`` 只作为上一轮召回的抑制输入，不触发模型反馈循环。
    """
    if limit <= 0:
        return []
    query_tokens = _tokens(query)
    nonce = int((state.get("__meta__") or {}).get("nonce", 0))
    rng = random.Random(f"{stream_id}:{nonce}:{len(state)}")
    scored: list[tuple[float, RecallCandidate]] = []

    for candidate in candidates:
        person_match = bool(person_ids & candidate.person_ids)
        semantic_match = bool(query_tokens & _tokens(candidate.text))
        if not person_match and not semantic_match:
            continue
        person_score = 1.0 if person_match else 0.0
        semantic_score = _semantic_overlap(query, candidate.text)
        activation = (
            0.55 * person_score
            + 0.30 * semantic_score
            + 0.15 * _recency(candidate.timestamp, now, decay_seconds)
            - 0.90
            * _inhibition(
                candidate.memory_id, state, now, inhibition_seconds
            )
            + max(0.0, noise) * (rng.random() - 0.5)
        )
        scored.append((activation, candidate))

    scored.sort(key=lambda item: (-item[0], item[1].memory_id))
    selected: list[RecallCandidate] = []
    selected_tokens: list[frozenset[str]] = []
    while scored and len(selected) < limit:
        best_index = 0
        best_score = float("-inf")
        for index, (score, candidate) in enumerate(scored):
            candidate_tokens = _tokens(candidate.text)
            duplicate_penalty = max(
                (
                    len(candidate_tokens & previous) / max(1, len(candidate_tokens | previous))
                    for previous in selected_tokens
                ),
                default=0.0,
            )
            adjusted = score - 0.25 * duplicate_penalty
            if adjusted > best_score:
                best_score = adjusted
                best_index = index
        _, chosen = scored.pop(best_index)
        selected.append(chosen)
        selected_tokens.append(_tokens(chosen.text))
    return selected