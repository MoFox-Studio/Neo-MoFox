"""Engram Memory vNext 混合检索领域服务。"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import RetrievalQuery, ScoredMemory, WriteContext
from .enums import (
    EvidenceSourceType,
    MemoryEventType,
    MemoryStatus,
    RevisionChangeReason,
)
from .models import (
    MemoryEventModel,
    EvidenceModel,
    MemoryModel,
    MemoryRetrievalEntryModel,
    MemoryRevisionModel,
    MemoryRevisionParticipantModel,
    MemoryRevisionSubjectModel,
    RevisionEvidenceModel,
)
from .repository import MemoryRepository
from .schema import VNextSchema

RRF_K = 60
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _new_id() -> str:
    """生成 UUID 字符串。"""
    return str(uuid4())


def _tokenize(text: str) -> tuple[str, ...]:
    """切分文本为归一化词元。

    拉丁字母数字串按整词小写处理；CJK 连续段按相邻双字 bigram 切分，
    保证“换工作”这类查询能命中“某人开始考虑换工作”。
    """
    tokens: list[str] = []
    latin: list[str] = []
    cjk: list[str] = []
    for char in text:
        if _CJK_PATTERN.match(char):
            if latin:
                tokens.append("".join(latin).lower())
                latin = []
            cjk.append(char)
        elif char.isalnum():
            if cjk:
                tokens.extend(_bigrams("".join(cjk)))
                cjk = []
            latin.append(char.lower())
        else:
            if latin:
                tokens.append("".join(latin))
                latin = []
            if cjk:
                tokens.extend(_bigrams("".join(cjk)))
                cjk = []
    if latin:
        tokens.append("".join(latin))
    if cjk:
        tokens.extend(_bigrams("".join(cjk)))
    return tuple(dict.fromkeys(tokens))


def _bigrams(text: str) -> tuple[str, ...]:
    """生成 CJK 文本的相邻双字组合。"""
    if len(text) == 1:
        return (text,)
    return tuple(text[i : i + 2] for i in range(len(text) - 1))


class LexicalIndex:
    """检索入口的词法命中索引，支持 CJK bigram 匹配。"""

    def __init__(self) -> None:
        """初始化空索引。"""
        self._entries: dict[str, tuple[str, frozenset[str]]] = {}

    def add(self, entry_id: str, text: str) -> None:
        """登记一个检索入口文本。"""
        self._entries[entry_id] = (text, frozenset(_tokenize(text)))

    def search(self, text: str) -> tuple[str, ...]:
        """按查询词元命中数降序返回入口 ID。"""
        tokens = _tokenize(text)
        counts: dict[str, int] = defaultdict(int)
        for entry_id, (_, entry_tokens) in self._entries.items():
            for token in tokens:
                if token in entry_tokens:
                    counts[entry_id] += 1
        return tuple(
            entry_id
            for entry_id, _ in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        )


class VectorSearchBackend:
    """向量检索后端接口。"""

    async def query(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[str, ...]:
        """按 (entry_id, text) 计算相似度并返回入口排位。

        参数:
            texts: 参与检索的入口标识与文本。
            top_k: 返回的最大入口数量。

        返回:
            依相关性降序的 entry_id 元组。
        """
        raise NotImplementedError

    async def query_scored(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[tuple[str, float | None], ...]:
        """返回入口排位及余弦相似度；仅有排位的后端不提供相关性分。"""
        return tuple((entry_id, None) for entry_id in await self.query(texts, top_k))


class EmbeddingVectorBackend(VectorSearchBackend):
    """基于注入的嵌入函数进行余弦相似度向量检索。"""

    def __init__(self, embedder: object) -> None:
        """绑定接收文本序列、返回向量元组的同步或异步嵌入函数。"""
        self._embedder = embedder

    async def query(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[str, ...]:
        """将查询与入口文本共同嵌入并返回余弦相似度排位。"""
        return tuple(entry_id for entry_id, _ in await self.query_scored(texts, top_k))

    async def query_scored(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[tuple[str, float | None], ...]:
        """保留查询与入口文本的真实余弦相似度。"""
        if not texts:
            return ()
        query_is_explicit = texts[0][0] == ""
        query_text = texts[0][1]
        entries = texts[1:] if query_is_explicit else texts
        entry_texts = tuple(text for _, text in entries)
        entry_ids = tuple(entry_id for entry_id, _ in entries)
        embeddings_result = self._embedder((query_text, *entry_texts))
        embeddings = (
            await embeddings_result
            if inspect.isawaitable(embeddings_result)
            else embeddings_result
        )
        if not isinstance(embeddings, tuple):
            raise ValueError("embedder 必须返回 tuple")
        if len(embeddings) != len(entry_texts) + 1:
            raise ValueError("embedder 返回维度与输入不一致")
        query_vector = embeddings[0]
        scores: list[tuple[float, str]] = []
        for entry_id, vector in zip(entry_ids, embeddings[1:], strict=True):
            score = self._cosine(query_vector, vector)
            scores.append((score, entry_id))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return tuple((entry_id, score) for score, entry_id in scores[:top_k])

    @staticmethod
    def _cosine(left: object, right: object) -> float:
        """计算两个向量的余弦相似度。"""
        left_values = tuple(float(value) for value in left)  # type: ignore[arg-type]
        right_values = tuple(float(value) for value in right)  # type: ignore[arg-type]
        if not left_values or not right_values or len(left_values) != len(right_values):
            raise ValueError("余弦相似度要求向量维度一致且非空")
        dot = sum(a * b for a, b in zip(left_values, right_values, strict=True))
        norm_left = sum(a * a for a in left_values) ** 0.5
        norm_right = sum(b * b for b in right_values) ** 0.5
        if norm_left == 0.0 or norm_right == 0.0:
            return 0.0
        return dot / (norm_left * norm_right)


class RetrievalService:
    """提供词法、向量与 RRF 融合的混合检索。"""

    def __init__(
        self,
        schema: VNextSchema,
        vector_backend: VectorSearchBackend,
        *,
        rrf_k: int = RRF_K,
        activation_half_life_days: float = 30.0,
        activation_weight: float = 0.08,
        activation_noise: float = 0.02,
    ) -> None:
        """绑定 Schema 与向量检索后端。"""
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k <= 0:
            raise ValueError("rrf_k 必须是正整数")
        if activation_half_life_days <= 0:
            raise ValueError("activation_half_life_days 必须大于 0")
        if not 0 <= activation_weight <= 1:
            raise ValueError("activation_weight 必须在 0-1 之间")
        if not 0 <= activation_noise <= 1:
            raise ValueError("activation_noise 必须在 0-1 之间")
        self._schema = schema
        self._vector_backend = vector_backend
        self._rrf_k = rrf_k
        self._activation_half_life_days = activation_half_life_days
        self._activation_weight = activation_weight
        self._activation_noise = activation_noise
        self._repository = MemoryRepository(schema)

    async def search(
        self,
        query: RetrievalQuery,
        context: WriteContext | None = None,
        *,
        min_similarity: float | None = None,
    ) -> tuple[ScoredMemory, ...]:
        """执行三路混合检索并按 memory_id 聚合结果。

        参数:
            query: 查询文本、结构化过滤与限制。
            context: 触发检索的执行上下文；非 None 时对命中记录 RECALLED 事件。
            min_similarity: 在结果限量前过滤未达到余弦相关性门槛的记忆。

        返回:
            按 RRF 融合分降序排列的聚合结果元组。
        """
        query.validate()
        person_ids = await self._resolve_person_ids(query.person_ids)
        async with self._schema.database.session() as session:
            candidates = await self._load_candidates(session)
            if not candidates:
                return ()
            structured_hits: set[str] = set()
            has_structured = bool(
                person_ids or query.memory_kinds or query.start_time or query.end_time
            )
            if has_structured:
                structured_hits = await self._structured_hits(
                    session,
                    query,
                    candidates,
                    person_ids=person_ids,
                )
                if not structured_hits:
                    return ()
            lexical = LexicalIndex()
            for entry in candidates:
                lexical.add(entry.entry_id, entry.text)
            lexical_ranks = tuple(
                entry_id
                for entry_id in lexical.search(query.text)
                if not structured_hits or entry_id in structured_hits
            )
            try:
                vector_results = await self._vector_backend.query_scored(
                    (("", query.text),)
                    + tuple((entry.entry_id, entry.text) for entry in candidates),
                    min(len(candidates), max(24, query.top_k * 4)),
                )
            except (NotImplementedError, Exception):
                vector_results = ()
            allowed_entry_ids = {entry.entry_id for entry in candidates}
            vector_similarities = {
                entry_id: score
                for entry_id, score in vector_results
                if entry_id in allowed_entry_ids and score is not None
            }
            vector_ranks = tuple(
                dict.fromkeys(
                    entry_id
                    for entry_id, _ in vector_results
                    if entry_id in allowed_entry_ids
                )
            )
            if structured_hits:
                vector_ranks = tuple(
                    entry_id for entry_id in vector_ranks if entry_id in structured_hits
                )
            # 结构化条件本身即第三路召回通道：与词法/向量同权参与 RRF
            structured_ranks = tuple(sorted(structured_hits)) if has_structured else ()
            fused = self._fuse(
                lexical_ranks,
                vector_ranks,
                structured_ranks,
                None,
                self._rrf_k,
            )
            entries_by_id = {entry.entry_id: entry for entry in candidates}
            current_titles = await self._current_titles(
                session, {entry.memory_id for entry in candidates}
            )
            activation_scores = await self._activation_scores(
                session, {entry.memory_id for entry in candidates}, query.text
            )
            results = self._aggregate(
                fused,
                entries_by_id,
                current_titles,
                lexical_ranks,
                vector_ranks,
                structured_hits,
                query.top_k,
                vector_similarities,
                min_similarity,
                activation_scores,
            )
            for scored in results:
                await self._record_recall(session, scored, context)
            return results

    async def _structured_hits(
        self,
        session: AsyncSession,
        query: RetrievalQuery,
        candidates: list[MemoryRetrievalEntryModel],
        *,
        person_ids: tuple[str, ...] | None = None,
    ) -> set[str]:
        """计算显式结构化条件命中的入口集合（硬过滤语义）。

        person_ids 命中 Subject 或 Participants、memory_kinds 命中当前
        Revision 类型、时间范围校验 last_experienced_at（缺省 created_at）
        的记忆，其全部检索入口进入命中集合。
        """
        memory_ids = {entry.memory_id for entry in candidates}
        if not memory_ids:
            return set()
        person_allowed: set[str] | None = None
        selected_person_ids = query.person_ids if person_ids is None else person_ids
        if selected_person_ids:
            subject_memories = set(
                (
                    await session.scalars(
                        select(MemoryModel.memory_id)
                        .join(
                            MemoryRevisionSubjectModel,
                            MemoryRevisionSubjectModel.revision_id
                            == MemoryModel.current_revision_id,
                        )
                        .where(
                            MemoryModel.memory_id.in_(memory_ids),
                            MemoryRevisionSubjectModel.person_id.in_(
                                selected_person_ids
                            ),
                        )
                    )
                ).all()
            )
            participant_memories = set(
                (
                    await session.scalars(
                        select(MemoryModel.memory_id)
                        .join(
                            MemoryRevisionParticipantModel,
                            MemoryRevisionParticipantModel.revision_id
                            == MemoryModel.current_revision_id,
                        )
                        .where(
                            MemoryModel.memory_id.in_(memory_ids),
                            MemoryRevisionParticipantModel.person_id.in_(
                                selected_person_ids
                            ),
                        )
                    )
                ).all()
            )
            person_allowed = subject_memories | participant_memories
        kind_allowed: set[str] | None = None
        if query.memory_kinds:
            allowed = await self._current_revision_kinds(session, memory_ids)
            kind_allowed = {
                memory_id
                for memory_id, kind in allowed.items()
                if kind in query.memory_kinds
            }
        rows = tuple(
            (
                await session.scalars(
                    select(MemoryModel).where(MemoryModel.memory_id.in_(memory_ids))
                )
            ).all()
        )
        qualified: set[str] = set()
        for row in rows:
            if person_allowed is not None and row.memory_id not in person_allowed:
                continue
            if kind_allowed is not None and row.memory_id not in kind_allowed:
                continue
            reference = row.last_experienced_at or row.created_at
            if query.start_time is not None and reference < query.start_time:
                continue
            if query.end_time is not None and reference > query.end_time:
                continue
            qualified.add(row.memory_id)
        return {entry.entry_id for entry in candidates if entry.memory_id in qualified}

    async def _resolve_person_ids(self, person_ids: tuple[str, ...]) -> tuple[str, ...]:
        """结构化人物过滤前，展开已保存快照中的人物别名并去重。"""
        resolved: list[str] = []
        for person_id in person_ids:
            resolved.extend(await self._repository.resolve_person_aliases(person_id))
        return tuple(dict.fromkeys(resolved))

    async def _load_candidates(
        self,
        session: AsyncSession,
    ) -> list[MemoryRetrievalEntryModel]:
        """加载 ACTIVE 记忆的全部检索入口。"""
        candidates = list(
            (
                await session.scalars(
                    select(MemoryRetrievalEntryModel)
                    .join(
                        MemoryModel,
                        MemoryModel.memory_id == MemoryRetrievalEntryModel.memory_id,
                    )
                    .where(MemoryModel.status == MemoryStatus.ACTIVE)
                )
            ).all()
        )
        return candidates

    @staticmethod
    async def _current_titles(
        session: AsyncSession,
        memory_ids: set[str],
    ) -> dict[str, str]:
        """读取当前 Revision 标题映射。"""
        if not memory_ids:
            return {}
        rows = tuple(
            (
                await session.execute(
                    select(MemoryModel.memory_id, MemoryRevisionModel.title)
                    .join(
                        MemoryRevisionModel,
                        MemoryRevisionModel.revision_id
                        == MemoryModel.current_revision_id,
                    )
                    .where(
                        MemoryModel.memory_id.in_(memory_ids),
                        MemoryModel.status == MemoryStatus.ACTIVE,
                    )
                )
            ).all()
        )
        return {row.memory_id: row.title for row in rows}

    async def _activation_scores(
        self,
        session: AsyncSession,
        memory_ids: set[str],
        query_text: str,
    ) -> dict[str, float]:
        """计算时间基线、来源层级、纠正优先级与有界噪声。"""
        if not memory_ids:
            return {}
        rows = tuple(
            (
                await session.execute(
                    select(
                        MemoryModel.memory_id,
                        MemoryModel.last_experienced_at,
                        MemoryRevisionModel.observed_at,
                        MemoryRevisionModel.change_reason,
                        EvidenceModel.source_type,
                    )
                    .join(
                        MemoryRevisionModel,
                        MemoryRevisionModel.revision_id
                        == MemoryModel.current_revision_id,
                    )
                    .outerjoin(
                        RevisionEvidenceModel,
                        RevisionEvidenceModel.revision_id
                        == MemoryRevisionModel.revision_id,
                    )
                    .outerjoin(
                        EvidenceModel,
                        EvidenceModel.evidence_id == RevisionEvidenceModel.evidence_id,
                    )
                    .where(MemoryModel.memory_id.in_(memory_ids))
                )
            ).all()
        )
        by_memory: dict[str, list[object]] = defaultdict(list)
        for row in rows:
            by_memory[row.memory_id].append(row)
        source_weights = {
            EvidenceSourceType.ACTOR_WRITE: 1.0,
            EvidenceSourceType.ADMIN: 0.95,
            EvidenceSourceType.MESSAGE_SET: 0.85,
            EvidenceSourceType.EXTERNAL: 0.70,
            EvidenceSourceType.SYSTEM_EVENT: 0.60,
            EvidenceSourceType.LEGACY_RECORD: 0.45,
        }
        now = datetime.now(UTC)
        scores: dict[str, float] = {}
        for memory_id, memory_rows in by_memory.items():
            first = memory_rows[0]
            observed = first.last_experienced_at or first.observed_at or now
            age_days = max(0.0, (now - observed).total_seconds() / 86400.0)
            base = math.exp(-math.log(2.0) * age_days / self._activation_half_life_days)
            sources = {
                row.source_type
                for row in memory_rows
                if isinstance(row.source_type, EvidenceSourceType)
            }
            source_score = max(
                (source_weights.get(source, 0.5) for source in sources),
                default=0.5,
            )
            evidence_bonus = min(0.15, max(0, len(sources) - 1) * 0.05)
            correction_bonus = (
                0.20
                if first.change_reason
                in {
                    RevisionChangeReason.EXPLICIT_CORRECTION,
                    RevisionChangeReason.ADMIN_CORRECTION,
                }
                else 0.0
            )
            digest = hashlib.sha256(f"{query_text}\0{memory_id}".encode()).digest()
            noise_unit = int.from_bytes(digest[:8], "big") / 2**64
            bounded_noise = (noise_unit * 2.0 - 1.0) * self._activation_noise
            scores[memory_id] = max(
                0.0,
                base * source_score + evidence_bonus + correction_bonus + bounded_noise,
            )
        return scores

    def _aggregate(
        self,
        fused: tuple[tuple[str, float], ...],
        entries_by_id: dict[str, MemoryRetrievalEntryModel],
        current_titles: dict[str, str],
        lexical_ranks: tuple[str, ...],
        vector_ranks: tuple[str, ...],
        structured_hits: set[str],
        top_k: int,
        vector_similarities: dict[str, float] | None = None,
        min_similarity: float | None = None,
        activation_scores: dict[str, float] | None = None,
    ) -> tuple[ScoredMemory, ...]:
        """按 memory_id 聚合入口级融合结果。

        同一 Memory 的多个入口命中只返回一个 memory_id，
        全部命中来源列进 matched_by（含 STRUCTURED）。
        """
        lexical_positions = {
            entry_id: rank for rank, entry_id in enumerate(lexical_ranks)
        }
        vector_positions = {
            entry_id: rank for rank, entry_id in enumerate(vector_ranks)
        }
        memory_scores: dict[str, float] = defaultdict(float)
        memory_sources: dict[str, set[str]] = defaultdict(set)
        memory_similarities: dict[str, float] = {}
        best_lexical: dict[str, int | None] = {}
        best_vector: dict[str, int | None] = {}
        for entry_id, score in fused:
            entry = entries_by_id[entry_id]
            similarity = (vector_similarities or {}).get(entry_id)
            if similarity is not None:
                memory_similarities[entry.memory_id] = max(
                    memory_similarities.get(entry.memory_id, -1.0), similarity
                )
            memory_scores[entry.memory_id] += score
            if entry_id in lexical_positions or entry_id in vector_positions:
                memory_sources[entry.memory_id].add(entry.entry_type.value)
            lexical_rank = lexical_positions.get(entry_id)
            vector_rank = vector_positions.get(entry_id)
            if entry.memory_id not in best_lexical:
                best_lexical[entry.memory_id] = None
            if lexical_rank is not None and (
                best_lexical[entry.memory_id] is None
                or lexical_rank < best_lexical[entry.memory_id]
            ):
                best_lexical[entry.memory_id] = lexical_rank
            if entry.memory_id not in best_vector:
                best_vector[entry.memory_id] = None
            if vector_rank is not None and (
                best_vector[entry.memory_id] is None
                or vector_rank < best_vector[entry.memory_id]
            ):
                best_vector[entry.memory_id] = vector_rank
        for entry_id in structured_hits:
            memory = entries_by_id[entry_id].memory_id
            if memory in memory_scores:
                memory_sources[memory].add("STRUCTURED")
        ordered = sorted(
            (
                (memory_id, score)
                for memory_id, score in memory_scores.items()
                if min_similarity is None
                or memory_similarities.get(memory_id, -1.0) >= min_similarity
            ),
            key=lambda item: (
                -(item[1] + (activation_scores or {}).get(item[0], 0.0) * self._activation_weight),
                item[0],
            ),
        )
        return tuple(
            ScoredMemory(
                memory_id=memory_id,
                title=current_titles.get(memory_id, ""),
                matched_by=tuple(sorted(memory_sources[memory_id])),
                rrf_score=score,
                lexical_rank=best_lexical.get(memory_id),
                vector_rank=best_vector.get(memory_id),
                vector_similarity=memory_similarities.get(memory_id),
                activation_score=(activation_scores or {}).get(memory_id, 0.0),
            )
            for memory_id, score in ordered[:top_k]
        )

    async def _record_recall(
        self,
        session: AsyncSession,
        scored: ScoredMemory,
        context: WriteContext | None,
    ) -> None:
        """为命中记忆写入 RECALLED 事件；仅行为路径传入 context。"""
        if context is None:
            return
        session.add(
            MemoryEventModel(
                event_id=_new_id(),
                memory_id=scored.memory_id,
                revision_id=None,
                event_type=MemoryEventType.RECALLED,
                actor_type=context.actor_type,
                actor_ref=context.actor_ref,
                stream_id=context.stream_id,
                occurred_at=datetime.now(UTC),
                payload_json={
                    "matched_by": list(scored.matched_by),
                    "title": scored.title,
                },
            )
        )

    @staticmethod
    async def _current_revision_kinds(
        session: AsyncSession,
        memory_ids: set[str],
    ) -> dict[str, object]:
        """读取当前 Revision 的记忆类型映射。"""
        if not memory_ids:
            return {}
        rows = tuple(
            (
                await session.scalars(
                    select(MemoryModel).where(MemoryModel.memory_id.in_(memory_ids))
                )
            ).all()
        )
        revisions = tuple(
            (
                await session.scalars(
                    select(MemoryRevisionModel).where(
                        MemoryRevisionModel.revision_id.in_(
                            tuple(memory.current_revision_id for memory in rows)
                        )
                    )
                )
            ).all()
        )
        return {
            memory.memory_id: revision.memory_kind
            for memory in rows
            for revision in revisions
            if revision.revision_id == memory.current_revision_id
        }

    @staticmethod
    def _fuse(
        lexical_ranks: tuple[str, ...],
        vector_ranks: tuple[str, ...],
        structured_ranks: tuple[str, ...],
        top_k: int | None,
        rrf_k: int = RRF_K,
    ) -> tuple[tuple[str, float], ...]:
        """以 RRF 融合词法、向量与结构化三路排位。"""
        scores: dict[str, float] = defaultdict(float)
        for rank, entry_id in enumerate(lexical_ranks):
            scores[entry_id] += 1.0 / (rrf_k + rank + 1)
        for rank, entry_id in enumerate(vector_ranks):
            scores[entry_id] += 1.0 / (rrf_k + rank + 1)
        for rank, entry_id in enumerate(structured_ranks):
            scores[entry_id] += 1.0 / (rrf_k + rank + 1)
        ordered = tuple(sorted(scores.items(), key=lambda item: (-item[1], item[0])))
        return ordered if top_k is None else ordered[:top_k]
