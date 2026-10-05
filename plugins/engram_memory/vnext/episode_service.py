"""线索驱动的情景经历、关联扩散与工作记忆服务。"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import (
    CueSetModel,
    EpisodeModel,
    EpisodeRelationModel,
    MemoryEventModel,
    WorkingMemoryModel,
)
from .cognitive_engine import CognitiveRecallEngine
from .runtime import MessageLike, message_to_snapshot
from .schema import VNextSchema

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_CORRECTION_TERMS = frozenset(
    {"不是", "不对", "记错", "纠正", "其实", "已经不", "不再", "改成", "更正"}
)
_EMOTION_TERMS = {
    "累": "疲惫",
    "困": "疲惫",
    "失眠": "疲惫",
    "开心": "愉快",
    "高兴": "愉快",
    "难过": "低落",
    "生气": "愤怒",
    "害怕": "焦虑",
    "焦虑": "焦虑",
    "心慌": "不适",
}
_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "是",
        "我",
        "你",
        "他",
        "她",
        "这",
        "那",
        "很",
        "在",
        "有",
        "和",
        "也",
        "就",
        "又",
        "最近",
    }
)

EmbeddingFunction = Callable[
    [Sequence[str]], Awaitable[Sequence[Sequence[float]]]
]


@dataclass(frozen=True, slots=True)
class EpisodeView:
    """不依赖 ORM 生命周期的经历视图。"""

    episode_id: str
    stream_id: str
    observed_at: datetime
    raw_text: str
    compressed_text: str
    participants: tuple[str, ...]
    topics: tuple[str, ...]
    emotion: str | None
    salience: float
    certainty: float
    source_type: str
    episode_kind: str


@dataclass(frozen=True, slots=True)
class CueSetView:
    """当前上下文的硬线索与软线索。"""

    cue_id: str
    episode_id: str | None
    stream_id: str
    hard_cues: dict[str, object]
    soft_cues: dict[str, object]


def _tokens(text: str) -> tuple[str, ...]:
    """生成稳定的词元集合，兼容中文单字与拉丁词。"""
    values = [item.casefold() for item in _TOKEN_PATTERN.findall(text)]
    return tuple(dict.fromkeys(item for item in values if item not in _STOPWORDS))


def _view(row: EpisodeModel) -> EpisodeView:
    """把 ORM 经历转换成不可变视图。"""
    return EpisodeView(
        episode_id=row.episode_id,
        stream_id=row.stream_id,
        observed_at=row.observed_at,
        raw_text=row.raw_text,
        compressed_text=row.compressed_text,
        participants=tuple(str(item) for item in row.participants),
        topics=tuple(str(item) for item in row.topics),
        emotion=row.emotion,
        salience=float(row.salience),
        certainty=float(row.certainty),
        source_type=row.source_type,
        episode_kind=row.episode_kind,
    )


class EpisodeService:
    """保存经历痕迹并从当前线索构造有限工作记忆。"""

    def __init__(
        self,
        schema: VNextSchema,
        *,
        max_working_memory: int = 3,
        relation_hops: int = 2,
        reconstruction_noise: float = 0.06,
        working_memory_ttl_seconds: int = 900,
        cognitive_engine: CognitiveRecallEngine | None = None,
        embedding_function: EmbeddingFunction | None = None,
        semantic_candidate_limit: int = 50,
        semantic_min_similarity: float = 0.45,
    ) -> None:
        """绑定数据库与低成本在线召回参数。"""
        if max_working_memory <= 0:
            raise ValueError("max_working_memory 必须大于 0")
        if relation_hops not in {1, 2}:
            raise ValueError("relation_hops 只支持 1 或 2")
        if not 0 <= reconstruction_noise <= 0.2:
            raise ValueError("reconstruction_noise 必须在 0-0.2 之间")
        if working_memory_ttl_seconds <= 0:
            raise ValueError("working_memory_ttl_seconds 必须大于 0")
        if semantic_candidate_limit <= 0:
            raise ValueError("semantic_candidate_limit 必须大于 0")
        if not 0 <= semantic_min_similarity <= 1:
            raise ValueError("semantic_min_similarity 必须在 0-1 之间")
        self._schema = schema
        self._max_working_memory = max_working_memory
        self._relation_hops = relation_hops
        self._reconstruction_noise = reconstruction_noise
        self._working_memory_ttl = working_memory_ttl_seconds
        self._embedding_function = embedding_function
        self._semantic_candidate_limit = semantic_candidate_limit
        self._semantic_min_similarity = semantic_min_similarity
        self._cognitive_engine = cognitive_engine or CognitiveRecallEngine(
            reconstruction_budget=max_working_memory
        )

    @staticmethod
    def _cue_values(text: str, participants: tuple[str, ...]) -> tuple[dict[str, object], dict[str, object]]:
        """从原文提取低成本硬线索和软线索。"""
        tokens = _tokens(text)
        correction_terms = tuple(term for term in _CORRECTION_TERMS if term in text)
        emotions = tuple(dict.fromkeys(emotion for term, emotion in _EMOTION_TERMS.items() if term in text))
        hard = {
            "person_ids": list(dict.fromkeys(participants)),
            "topics": list(tokens),
            "correction_terms": list(correction_terms),
            "explicit_correction": bool(correction_terms),
            "language_cues": [token for token in ("又", "已经", "不再", "最近") if token in text],
        }
        soft = {
            "emotion": emotions[0] if emotions else None,
            "emotions": list(emotions),
            "scene": "sleep" if "睡" in text or "失眠" in text else None,
            "token_count": len(tokens),
        }
        return hard, soft

    @staticmethod
    def _salience(text: str, hard: dict[str, object], soft: dict[str, object]) -> float:
        """根据显式纠正、情绪和语言强调估计经历显著性。"""
        score = 0.35
        if hard.get("explicit_correction"):
            score += 0.35
        if soft.get("emotion"):
            score += 0.15
        if "!" in text or "！" in text:
            score += 0.1
        return min(1.0, score)

    async def record_episode(
        self,
        message: MessageLike,
        *,
        episode_kind: str = "INPUT",
        source_type: str = "MESSAGE",
        certainty: float = 0.9,
    ) -> EpisodeView:
        """把消息先保存为经历，不直接创建正式 Memory。"""
        snapshot = message_to_snapshot(message)
        if not 0 <= certainty <= 1:
            raise ValueError("certainty 必须在 0-1 之间")
        person_id = str(snapshot.snapshot.get("person_id") or "").strip()
        participants = (person_id,) if person_id else ()
        hard, soft = self._cue_values(snapshot.text, participants)
        source_ref = f"message:{snapshot.stream_id}:{snapshot.message_id}:{episode_kind}"
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            existing = await session.scalar(
                select(EpisodeModel).where(EpisodeModel.source_ref == source_ref)
            )
            if existing is not None:
                return _view(existing)
            episode = EpisodeModel(
                episode_id=str(uuid4()),
                source_ref=source_ref,
                stream_id=snapshot.stream_id,
                observed_at=snapshot.time,
                raw_text=snapshot.text,
                compressed_text=snapshot.text[:240],
                participants=list(participants),
                topics=list(hard["topics"]),
                emotion=soft.get("emotion"),
                scene=soft.get("scene"),
                salience=self._salience(snapshot.text, hard, soft),
                certainty=certainty,
                source_type=source_type,
                episode_kind=episode_kind,
                created_at=now,
            )
            session.add(episode)
            await session.flush()
            cue = CueSetModel(
                cue_id=str(uuid4()),
                episode_id=episode.episode_id,
                stream_id=episode.stream_id,
                hard_cues=hard,
                soft_cues=soft,
                created_at=now,
            )
            session.add(cue)
            await self._relate_to_recent(session, episode, now)
            return _view(episode)

    async def record_external_episode(
        self,
        *,
        title: str,
        content: str,
        stream_id: str,
        observed_at: datetime,
        source_ref: str,
        participants: tuple[str, ...] = (),
        source_type: str = "SYSTEM_SUMMARY",
        episode_kind: str = "SUMMARY",
        certainty: float = 0.55,
    ) -> EpisodeView:
        """保存没有当前消息快照的后台经历素材，不创建正式 Memory。"""
        if not title.strip() or not content.strip():
            raise ValueError("外部 Episode 的 title 和 content 不能为空")
        if not stream_id.strip() or not source_ref.strip():
            raise ValueError("外部 Episode 必须提供 stream_id 和 source_ref")
        if not 0 <= certainty <= 1:
            raise ValueError("certainty 必须在 0-1 之间")
        raw_text = f"{title.strip()}\n{content.strip()}"
        hard, soft = self._cue_values(raw_text, tuple(dict.fromkeys(participants)))
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            existing = await session.scalar(
                select(EpisodeModel).where(EpisodeModel.source_ref == source_ref)
            )
            if existing is not None:
                return _view(existing)
            episode = EpisodeModel(
                episode_id=str(uuid4()),
                source_ref=source_ref,
                stream_id=stream_id.strip(),
                observed_at=observed_at,
                raw_text=raw_text,
                compressed_text=content.strip()[:400],
                participants=list(dict.fromkeys(participants)),
                topics=list(hard["topics"]),
                emotion=soft.get("emotion"),
                scene=soft.get("scene"),
                salience=self._salience(raw_text, hard, soft),
                certainty=certainty,
                source_type=source_type,
                episode_kind=episode_kind,
                created_at=now,
            )
            session.add(episode)
            await session.flush()
            session.add(
                CueSetModel(
                    cue_id=str(uuid4()),
                    episode_id=episode.episode_id,
                    stream_id=episode.stream_id,
                    hard_cues=hard,
                    soft_cues=soft,
                    created_at=now,
                )
            )
            await self._relate_to_recent(session, episode, now)
            return _view(episode)

    async def _relate_to_recent(
        self,
        session: object,
        episode: EpisodeModel,
        now: datetime,
    ) -> None:
        """按人物和主题共现建立同流或受限跨流关联边。"""
        time_floor = episode.observed_at - timedelta(days=30)
        recent = (
            await session.scalars(  # type: ignore[union-attr]
                select(EpisodeModel)
                .where(
                    EpisodeModel.episode_id != episode.episode_id,
                    EpisodeModel.observed_at >= time_floor,
                )
                .order_by(EpisodeModel.observed_at.desc())
                .limit(100)
            )
        ).all()
        people = set(episode.participants)
        topics = set(episode.topics)
        for other in recent:
            shared_people = people & set(other.participants)
            shared_topics = topics & set(other.topics)
            if not shared_people and not shared_topics:
                continue
            cross_stream = other.stream_id != episode.stream_id
            if cross_stream and (not shared_people or not shared_topics):
                continue
            weight = min(
                1.0,
                0.45 * bool(shared_people)
                + 0.3 * min(len(shared_topics), 2)
                + (0.1 if cross_stream else 0.0),
            )
            relation_type = (
                "CROSS_STREAM_EVENT"
                if cross_stream
                else "SAME_PERSON"
                if shared_people
                else "TOPIC_OVERLAP"
            )
            await session.execute(
                sqlite_insert(EpisodeRelationModel)
                .values(
                    relation_id=str(uuid4()),
                    source_episode_id=episode.episode_id,
                    target_episode_id=other.episode_id,
                    relation_type=relation_type,
                    weight=weight,
                    created_at=now,
                )
                .prefix_with("OR IGNORE")
            )

    async def recall_working_memory(
        self,
        episode: EpisodeView,
        *,
        max_items: int | None = None,
        relation_hops: int | None = None,
    ) -> dict[str, object]:
        """从当前 Episode 的 CueSet 和关联边构造有来源的工作记忆。"""
        limit = self._max_working_memory if max_items is None else max_items
        hops = self._relation_hops if relation_hops is None else relation_hops
        if limit <= 0 or hops not in {1, 2}:
            raise ValueError("max_items 必须大于 0，relation_hops 必须为 1/2")
        now = datetime.now(UTC)
        async with self._schema.database.session() as session:
            direct = list(
                (
                    await session.scalars(
                        select(EpisodeModel)
                        .where(
                            EpisodeModel.episode_id != episode.episode_id,
                            EpisodeModel.stream_id == episode.stream_id,
                            EpisodeModel.episode_kind.notin_(("CUE", "OUTPUT")),
                        )
                        .order_by(EpisodeModel.observed_at.desc())
                        .limit(200)
                    )
                ).all()
            )
            relations = list(
                (
                    await session.scalars(
                        select(EpisodeRelationModel).where(
                            EpisodeRelationModel.source_episode_id == episode.episode_id
                        )
                    )
                ).all()
            )
            relation_ids = {item.target_episode_id: float(item.weight) for item in relations}
            related_stream_episodes = []
            if relation_ids:
                related_stream_episodes = list(
                    (
                        await session.scalars(
                            select(EpisodeModel).where(
                                EpisodeModel.episode_id.in_(tuple(relation_ids)),
                                EpisodeModel.episode_kind.notin_(("CUE", "OUTPUT")),
                            )
                        )
                    ).all()
                )
                known_ids = {item.episode_id for item in direct}
                direct.extend(
                    item
                    for item in related_stream_episodes
                    if item.episode_id not in known_ids
                )
            if episode.episode_kind == "CUE":
                for item in direct:
                    shared_people = set(episode.participants) & set(item.participants)
                    shared_topics = set(episode.topics) & set(item.topics)
                    if shared_people or shared_topics:
                        relation_ids[item.episode_id] = min(
                            1.0, 0.45 * bool(shared_people) + 0.3 * min(len(shared_topics), 2)
                        )
            first_hop_ids = tuple(relation_ids)
            if hops > 1 and relation_ids:
                second_hop = list(
                    (
                        await session.scalars(
                            select(EpisodeRelationModel).where(
                                EpisodeRelationModel.source_episode_id.in_(first_hop_ids)
                            )
                        )
                    ).all()
                )
                for relation in second_hop:
                    if relation.target_episode_id == episode.episode_id:
                        continue
                    propagated = relation_ids.get(relation.source_episode_id, 0.0)
                    relation_ids[relation.target_episode_id] = max(
                        relation_ids.get(relation.target_episode_id, 0.0),
                        propagated * float(relation.weight) * 0.55,
                    )
            candidates: dict[str, tuple[EpisodeModel, float, str]] = {}
            current_topics = set(episode.topics)
            current_people = set(episode.participants)
            semantic_scores = await self._semantic_scores(episode, direct)
            for item in direct:
                shared_topics = current_topics & set(item.topics)
                shared_people = current_people & set(item.participants)
                graph_weight = relation_ids.get(item.episode_id, 0.0)
                semantic_score = semantic_scores.get(item.episode_id, 0.0)
                if (
                    not shared_topics
                    and not shared_people
                    and not graph_weight
                    and semantic_score < self._semantic_min_similarity
                ):
                    continue
                reason = (
                    "人物关联"
                    if shared_people
                    else "主题关联"
                    if shared_topics
                    else "语义关联"
                )
                if graph_weight:
                    reason += " + 经历扩散"
                    if item.episode_id not in first_hop_ids:
                        reason += "（二跳衰减）"
                overlap = 0.25 * len(shared_topics) + 0.35 * bool(shared_people)
                age_days = max(0.0, (now - item.observed_at).total_seconds() / 86400)
                recency = math.exp(-math.log(2) * age_days / 30)
                score = (
                    overlap
                    + graph_weight
                    + 0.45 * semantic_score
                    + 0.2 * recency
                    + 0.15 * float(item.salience)
                    + 0.1 * float(item.certainty)
                )
                if "不再" in episode.raw_text or "已经不" in episode.raw_text:
                    score += 0.15 if "不" in item.raw_text else 0.0
                digest = sha256(f"{episode.episode_id}:{item.episode_id}".encode()).digest()
                stable_bias = int.from_bytes(digest[:4], "big") / 2**32
                score += (stable_bias - 0.5) * self._reconstruction_noise
                candidates[item.episode_id] = (item, score, reason)
            cognitive_scores = self._cognitive_engine.rank(
                episode.raw_text,
                tuple(
                    (item.episode_id, item.compressed_text, float(item.certainty))
                    for item, _, _ in candidates.values()
                ),
            )
            candidates = {
                episode_id: (item, score + 0.2 * cognitive_scores.get(episode_id, 0.0), reason)
                for episode_id, (item, score, reason) in candidates.items()
            }
            ranked = sorted(candidates.values(), key=lambda item: (-item[1], item[0].episode_id))
            selected: list[dict[str, object]] = []
            selected_topics: set[str] = set()
            while ranked and len(selected) < limit:
                ranked.sort(key=lambda candidate: (
                    -(candidate[1] - len(set(candidate[0].topics) & selected_topics) * 0.08),
                    candidate[0].episode_id,
                ))
                item, score, reason = ranked.pop(0)
                item_topics = set(item.topics)
                duplicate_penalty = len(item_topics & selected_topics) * 0.08
                effective = score - duplicate_penalty
                if selected and effective < 0:
                    continue
                selected.append(
                    {
                        "episode_id": item.episode_id,
                        "content": item.compressed_text,
                        "observed_at": item.observed_at.isoformat(),
                        "source_type": item.source_type,
                        "certainty": item.certainty,
                        "reason": reason,
                        "activation_score": round(max(0.0, effective), 6),
                        "historical": True,
                    }
                )
                selected_topics.update(item_topics)
                if len(selected) >= limit:
                    break
            cue = CueSetModel(
                cue_id=str(uuid4()),
                episode_id=None if episode.episode_kind == "CUE" else episode.episode_id,
                stream_id=episode.stream_id,
                hard_cues={
                    "person_ids": list(episode.participants),
                    "topics": list(episode.topics),
                    "explicit_correction": "不再" in episode.raw_text or "已经不" in episode.raw_text,
                },
                soft_cues={"emotion": episode.emotion, "scene": None},
                created_at=now,
            )
            session.add(cue)
            conflicts = [
                {
                    "episode_id": item["episode_id"],
                    "reason": "当前明确纠正优先，历史片段仅作背景",
                }
                for item in selected
                if episode.raw_text and (
                    "不再" in episode.raw_text
                    or "已经不" in episode.raw_text
                    or "不是" in episode.raw_text
                )
            ]
            working_memory = WorkingMemoryModel(
                working_memory_id=str(uuid4()),
                stream_id=episode.stream_id,
                cue_id=cue.cue_id,
                selected_episodes=selected,
                conflicts=conflicts,
                created_at=now,
                expires_at=now + timedelta(seconds=self._working_memory_ttl),
            )
            session.add(working_memory)
            return {
                "working_memory_id": working_memory.working_memory_id,
                "cue_id": cue.cue_id,
                "stream_id": episode.stream_id,
                "selected_episodes": selected,
                "conflicts": conflicts,
            }

    async def _semantic_scores(
        self,
        episode: EpisodeView,
        candidates: Sequence[EpisodeModel],
    ) -> dict[str, float]:
        """用真实 Embedding 为有限近期候选补充语义相似度。"""
        if self._embedding_function is None or not candidates:
            return {}
        selected = tuple(candidates[: self._semantic_candidate_limit])
        texts = (episode.raw_text, *(item.compressed_text for item in selected))
        try:
            vectors = tuple(await self._embedding_function(texts))
        except Exception:
            return {}
        if len(vectors) != len(texts):
            return {}
        query = self._normalized_vector(vectors[0])
        if query is None:
            return {}
        scores: dict[str, float] = {}
        for item, vector in zip(selected, vectors[1:], strict=True):
            normalized = self._normalized_vector(vector)
            if normalized is None or len(normalized) != len(query):
                continue
            scores[item.episode_id] = max(
                0.0,
                min(1.0, sum(left * right for left, right in zip(query, normalized, strict=True))),
            )
        return scores

    @staticmethod
    def _normalized_vector(value: Sequence[float]) -> tuple[float, ...] | None:
        """校验并归一化一个有限非零向量。"""
        try:
            values = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            return None
        if not values or any(not math.isfinite(item) for item in values):
            return None
        norm = math.sqrt(sum(item * item for item in values))
        if norm == 0:
            return None
        return tuple(item / norm for item in values)

    async def record_output_observation(self, message: MessageLike) -> EpisodeView:
        """登记输出及其可核验的激活上下文，不推断模型是否使用了回忆。"""
        episode = await self.record_episode(
            message, episode_kind="OUTPUT", source_type="ASSISTANT_MESSAGE", certainty=0.6
        )
        observation_id = str(uuid5(NAMESPACE_URL, f"engram:observation:{episode.episode_id}"))
        async with self._schema.database.session() as session:
            if await session.get(CueSetModel, observation_id) is not None:
                return episode
            working = await session.scalar(
                select(WorkingMemoryModel).where(
                    WorkingMemoryModel.stream_id == episode.stream_id,
                    WorkingMemoryModel.created_at <= episode.observed_at,
                    WorkingMemoryModel.expires_at > episode.observed_at,
                ).order_by(WorkingMemoryModel.created_at.desc()).limit(1)
            )
            activated = [
                str(item["episode_id"]) for item in working.selected_episodes
                if item.get("episode_id")
            ] if working is not None else []
            previous_output_at = await session.scalar(
                select(EpisodeModel.observed_at).where(
                    EpisodeModel.stream_id == episode.stream_id,
                    EpisodeModel.episode_kind == "OUTPUT",
                    EpisodeModel.episode_id != episode.episode_id,
                    EpisodeModel.observed_at < episode.observed_at,
                ).order_by(EpisodeModel.observed_at.desc()).limit(1)
            )
            injected_rows = tuple((await session.execute(
                select(
                    MemoryEventModel.memory_id,
                    MemoryEventModel.payload_json,
                ).where(
                    MemoryEventModel.event_type == "FLASHBACK_EXPOSED",
                    MemoryEventModel.stream_id == episode.stream_id,
                    MemoryEventModel.occurred_at <= episode.observed_at,
                    MemoryEventModel.occurred_at >= episode.observed_at - timedelta(minutes=30),
                    *(
                        (MemoryEventModel.occurred_at > previous_output_at,)
                        if previous_output_at is not None
                        else ()
                    ),
                ).order_by(MemoryEventModel.occurred_at.desc())
            )).all())
            injected: dict[str, dict[str, object]] = {}
            for row in injected_rows:
                payload = row.payload_json if isinstance(row.payload_json, dict) else {}
                if payload.get("stage") != "prompt_injected":
                    continue
                injected.setdefault(str(row.memory_id), payload)
            memory_evidence: list[dict[str, object]] = []
            for memory_id, payload in injected.items():
                if memory_id in episode.raw_text:
                    method = "explicit_memory_id"
                else:
                    content = str(payload.get("content") or "")
                    shared = set(_tokens(content)) & set(_tokens(episode.raw_text))
                    method = "lexical_overlap" if len(shared) >= 2 else "injected_without_evidence"
                memory_evidence.append({
                    "memory_id": memory_id,
                    "method": method,
                    "confidence": "direct_reference" if method == "explicit_memory_id" else "weak_textual" if method == "lexical_overlap" else "exposure_only",
                })
            observation = {
                "working_memory_id": working.working_memory_id if working else None,
                "activated_episode_ids": activated,
                "explicitly_referenced_episode_ids": [
                    episode_id for episode_id in activated if episode_id in episode.raw_text
                ],
                "conflicts": list(working.conflicts) if working else [],
                "reference_detection": "literal_id_only",
                "injected_memory_ids": list(injected),
                "memory_influence_evidence": memory_evidence,
                "causal_attribution": "not_proven",
            }
            await session.execute(sqlite_insert(CueSetModel).values(
                cue_id=observation_id, episode_id=episode.episode_id,
                stream_id=episode.stream_id, hard_cues={},
                soft_cues={"observation": observation}, created_at=datetime.now(UTC),
            ).on_conflict_do_nothing(index_elements=["cue_id"]))
        return episode

    async def current_working_memory(self, stream_id: str) -> dict[str, object] | None:
        """读取仍未过期的最近工作记忆。"""
        async with self._schema.database.session() as session:
            row = (
                await session.scalars(
                    select(WorkingMemoryModel)
                    .where(
                        WorkingMemoryModel.stream_id == stream_id,
                        WorkingMemoryModel.expires_at > datetime.now(UTC),
                    )
                    .order_by(WorkingMemoryModel.created_at.desc())
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            return {
                "working_memory_id": row.working_memory_id,
                "cue_id": row.cue_id,
                "stream_id": row.stream_id,
                "selected_episodes": list(row.selected_episodes),
                "conflicts": list(row.conflicts),
            }

    async def recall_association(
        self,
        *,
        stream_id: str,
        cue_text: str,
        max_hops: int = 2,
        limit: int = 5,
    ) -> tuple[dict[str, object], ...]:
        """按 CueSet 规则触发关联扩散，不读取或生成正式事实。"""
        if not cue_text.strip():
            raise ValueError("cue_text 不能为空")
        if max_hops not in {1, 2} or limit <= 0:
            raise ValueError("max_hops 必须为 1/2，limit 必须大于 0")
        hard, soft = self._cue_values(cue_text, ())
        cue_episode = EpisodeView(
            episode_id=f"cue:{sha256(f'{stream_id}:{cue_text}'.encode()).hexdigest()}",
            stream_id=stream_id,
            observed_at=datetime.now(UTC),
            raw_text=cue_text,
            compressed_text=cue_text[:240],
            participants=(),
            topics=_tokens(cue_text),
            emotion=str(soft["emotion"]) if soft["emotion"] else None,
            salience=self._salience(cue_text, hard, soft),
            episode_kind="CUE",
            source_type="CURRENT_CONTEXT",
            certainty=1.0,
        )
        result = await self.recall_working_memory(
            cue_episode, max_items=limit, relation_hops=max_hops
        )
        selected = result.get("selected_episodes")
        return tuple(item for item in selected if isinstance(item, dict)) if isinstance(selected, list) else ()

    @staticmethod
    def render_working_memory(data: dict[str, object]) -> str:
        """将有来源的工作记忆渲染为主 LLM 可理解的上下文。"""
        selected = data.get("selected_episodes")
        if not isinstance(selected, list) or not selected:
            return ""
        lines = [
            "以下是当前情境触发的经历片段，只能作为带来源的回忆线索，不是确定的新事实。",
            "不同片段可能属于不同时间或侧面；明确当前说法优先于历史片段。",
        ]
        for item in selected:
            if not isinstance(item, dict):
                continue
            episode_id = str(item.get("episode_id") or "").strip()
            content = str(item.get("content") or "").strip()
            reason = str(item.get("reason") or "相关经历").strip()
            if episode_id and content:
                lines.append(f"- [{episode_id}]（{reason}）{content}")
        return "\n".join(lines) if len(lines) > 2 else ""