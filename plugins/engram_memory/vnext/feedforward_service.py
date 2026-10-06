"""前馈式记忆检索：每轮必然执行的 ACT-R × KDA × LayerSplit × Transformer 管线。

与概率触发的闪回不同，本服务在每次回复前都运行一次完整前馈：

1. **L0 感知**：取当前流最近输入构造线索（文本、人物、情绪、时间）；
2. **候选池**：混合检索（词法/向量/结构化）命中 ∪ L2 热记忆 ∪ L1 工作集，
   不依赖本轮检索命中，正在想着的事会自然延续；
3. **ACT-R 激活**：每条候选按全部访问历史计算基础激活，叠加 L1 工作记忆
   扩散、KDA 线索读出扩散与人物/类型部分匹配，通道决定衰减率 d；
4. **Transformer 前馈块**：语义/人物/主题/情绪/时间/激活六个头对候选做注意力，
   门控随线索类型调整，经 FFN 与残差归一化得到最终分数；
5. **Boltzmann 选择**：按检索概率选出至多 ``max_memories`` 条，低于阈值不注入；
6. **巩固写回**：选中记忆写入 L1 复述、L2 热缓存，并以 delta 规则把
   「线索 → 记忆」写入对应通道的 KDA 状态，下一轮同类线索会更快想起它。

本服务只读取正式记忆，不创建、修订或作废记忆；进程内状态不持久化。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from .backends.actr import ACTRActivationEngine, ActivationComponents
from .backends.kda_memory import ChannelKDAMemory, ChannelPolicy, hashed_features
from .backends.layersplit_store import LayerSplitStore, SensoryItem
from .backends.transformer import MemoryToken, MemoryTransformerBlock, temporal_encoding
from .domain import RetrievalQuery
from .enums import EvidenceSourceType, MemoryEventType, MemoryKind, MemoryStatus
from .models import (
    EvidenceMessageLinkModel,
    EvidenceMessageSnapshotModel,
    EvidenceModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRevisionModel,
    MemoryRevisionParticipantModel,
    MemoryRevisionSubjectModel,
    RevisionEvidenceModel,
)
from .retrieval_service import RetrievalService, _tokenize
from .schema import VNextSchema

#: 前馈块的注意力头，每个头对应一种特征视图
HEAD_NAMES: tuple[str, ...] = (
    "semantic",
    "person",
    "topic",
    "emotion",
    "temporal",
    "activation",
)

#: 情绪词到情绪类别，与 Episode 线索提取保持同一口径
_EMOTION_TERMS: dict[str, str] = {
    "累": "疲惫",
    "困": "疲惫",
    "失眠": "疲惫",
    "开心": "愉快",
    "高兴": "愉快",
    "喜欢": "愉快",
    "难过": "低落",
    "伤心": "低落",
    "生气": "愤怒",
    "烦": "愤怒",
    "害怕": "焦虑",
    "焦虑": "焦虑",
    "担心": "焦虑",
    "心慌": "不适",
    "疼": "不适",
}
_TIME_TERMS = ("什么时候", "多久", "上次", "以前", "之前", "那天", "昨天", "去年", "最近")

#: 记忆通道：按聊天类型与记忆类型选择性遗忘
CHANNEL_PRIVATE = "private"
CHANNEL_GROUP = "group"
CHANNEL_EMOTIONAL = "emotional"
CHANNEL_FACTUAL = "factual"
CHANNELS: tuple[str, ...] = (
    CHANNEL_PRIVATE,
    CHANNEL_GROUP,
    CHANNEL_EMOTIONAL,
    CHANNEL_FACTUAL,
)

_EMOTIONAL_KINDS = frozenset(
    {MemoryKind.PREFERENCE, MemoryKind.PERSONAL_STATE, MemoryKind.RELATIONSHIP}
)
_FACTUAL_KINDS = frozenset({MemoryKind.FACT, MemoryKind.COMMITMENT})


def emotions_in(text: str) -> tuple[str, ...]:
    """抽取文本中出现的情绪类别。"""
    return tuple(dict.fromkeys(label for term, label in _EMOTION_TERMS.items() if term in text))


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """单个通道的 ACT-R 衰减率与 KDA 保留策略。"""

    decay: float
    retention_per_day: float
    learning_rate: float = 0.5


@dataclass(frozen=True, slots=True)
class FeedForwardSettings:
    """前馈检索的运行参数。"""

    max_memories: int = 3
    candidate_limit: int = 24
    hot_candidates: int = 8
    feature_dim: int = 64
    retrieval_threshold: float = -2.5
    temperature: float = 0.4
    noise_scale: float = 0.15
    mismatch_penalty: float = 0.6
    working_weight: float = 0.8
    kda_weight: float = 0.6
    relevance_floor: float = 0.0
    latency_budget_ms: int = 1500
    channels: dict[str, ChannelSettings] = field(
        default_factory=lambda: {
            CHANNEL_PRIVATE: ChannelSettings(decay=0.35, retention_per_day=0.98),
            CHANNEL_GROUP: ChannelSettings(decay=0.6, retention_per_day=0.9),
            CHANNEL_EMOTIONAL: ChannelSettings(decay=0.3, retention_per_day=0.99),
            CHANNEL_FACTUAL: ChannelSettings(decay=0.25, retention_per_day=0.995),
        }
    )

    def validate(self) -> None:
        """校验参数范围。

        Raises:
            ValueError: 参数越界或缺少通道时抛出。
        """
        if self.max_memories < 0:
            raise ValueError("max_memories 不能为负")
        if self.candidate_limit <= 0 or self.hot_candidates < 0:
            raise ValueError("candidate_limit 必须大于 0，hot_candidates 不能为负")
        if self.feature_dim < 8:
            raise ValueError("feature_dim 至少为 8")
        if self.latency_budget_ms <= 0:
            raise ValueError("latency_budget_ms 必须大于 0")
        missing = set(CHANNELS) - set(self.channels)
        if missing:
            raise ValueError(f"缺少记忆通道配置：{sorted(missing)}")
        for name, channel in self.channels.items():
            if not 0 < channel.decay < 1:
                raise ValueError(f"通道 {name} 的 decay 必须在 (0, 1) 之间")


@dataclass(frozen=True, slots=True)
class FeedForwardCue:
    """一次前馈的线索。"""

    stream_id: str
    text: str
    person_ids: tuple[str, ...]
    chat_type: str
    now: datetime

    @property
    def channel(self) -> str:
        """线索所属的聊天通道。"""
        return CHANNEL_PRIVATE if self.chat_type == "private" else CHANNEL_GROUP


@dataclass(slots=True)
class _CandidateFacts:
    """从数据库读出的单条候选事实，进入 L2 热缓存。"""

    memory_id: str
    title: str
    content: str
    memory_kind: str
    person_ids: tuple[str, ...]
    chat_types: tuple[str, ...]
    source_types: tuple[str, ...]
    access_times: tuple[datetime, ...]
    experienced_at: datetime

    def to_payload(self) -> dict[str, object]:
        """序列化为热缓存载荷。"""
        return {
            "memory_id": self.memory_id,
            "title": self.title,
            "content": self.content,
            "memory_kind": self.memory_kind,
            "person_ids": list(self.person_ids),
            "chat_types": list(self.chat_types),
            "source_types": list(self.source_types),
            "access_times": [item.isoformat() for item in self.access_times],
            "experienced_at": self.experienced_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> _CandidateFacts:
        """从热缓存载荷恢复。"""
        return cls(
            memory_id=str(payload["memory_id"]),
            title=str(payload["title"]),
            content=str(payload["content"]),
            memory_kind=str(payload["memory_kind"]),
            person_ids=tuple(str(item) for item in payload["person_ids"]),  # type: ignore[union-attr]
            chat_types=tuple(str(item) for item in payload["chat_types"]),  # type: ignore[union-attr]
            source_types=tuple(str(item) for item in payload["source_types"]),  # type: ignore[union-attr]
            access_times=tuple(
                datetime.fromisoformat(str(item)) for item in payload["access_times"]  # type: ignore[union-attr]
            ),
            experienced_at=datetime.fromisoformat(str(payload["experienced_at"])),
        )

    def channels(self) -> tuple[str, ...]:
        """候选所属的全部记忆通道。"""
        result: list[str] = []
        if "private" in self.chat_types:
            result.append(CHANNEL_PRIVATE)
        if any(item in {"group", "discuss"} for item in self.chat_types):
            result.append(CHANNEL_GROUP)
        if self.memory_kind in {kind.value for kind in _EMOTIONAL_KINDS} or emotions_in(
            self.content
        ):
            result.append(CHANNEL_EMOTIONAL)
        if self.memory_kind in {kind.value for kind in _FACTUAL_KINDS}:
            result.append(CHANNEL_FACTUAL)
        if not result:
            result.append(CHANNEL_GROUP)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class FeedForwardMemory:
    """一条被前馈选中的记忆及其可解释得分。"""

    memory_id: str
    title: str
    content: str
    probability: float
    activation: ActivationComponents
    transformer_score: float
    channels: tuple[str, ...]
    head_attention: dict[str, float]


@dataclass(frozen=True, slots=True)
class FeedForwardResult:
    """一轮前馈结果。"""

    selected: tuple[FeedForwardMemory, ...]
    candidate_count: int
    gates: dict[str, float]


class FeedForwardRetrievalService:
    """每轮必然执行的前馈式记忆检索。"""

    def __init__(
        self,
        schema: VNextSchema,
        retrieval: RetrievalService,
        settings: FeedForwardSettings | None = None,
        *,
        layers: LayerSplitStore | None = None,
    ) -> None:
        """装配四个认知后端。

        Args:
            schema: 正式记忆数据库。
            retrieval: 混合检索服务，作为候选召回入口。
            settings: 前馈参数。
            layers: 可注入的分层存储，默认新建。

        Raises:
            ValueError: 参数非法时抛出。
        """
        self._settings = settings or FeedForwardSettings()
        self._settings.validate()
        self._schema = schema
        self._retrieval = retrieval
        self.layers = layers or LayerSplitStore()
        self.actr = ACTRActivationEngine(
            decay=0.5,
            mismatch_penalty=self._settings.mismatch_penalty,
            noise_scale=self._settings.noise_scale,
            retrieval_threshold=self._settings.retrieval_threshold,
            temperature=self._settings.temperature,
        )
        dim = self._settings.feature_dim
        self.kda = ChannelKDAMemory(
            key_dim=dim,
            value_dim=dim,
            policies={
                name: ChannelPolicy(
                    retention_per_day=channel.retention_per_day,
                    learning_rate=channel.learning_rate,
                )
                for name, channel in self._settings.channels.items()
            },
        )
        self.block = MemoryTransformerBlock(head_names=HEAD_NAMES)
        self._turns: defaultdict[str, int] = defaultdict(int)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def settings(self) -> FeedForwardSettings:
        """当前参数。"""
        return self._settings

    # ------------------------------------------------------------------ 入口

    def perceive(
        self,
        stream_id: str,
        text: str,
        *,
        person_id: str | None,
        observed_at: datetime,
    ) -> None:
        """把一条输入放入 L0 感知缓冲。"""
        self.layers.perceive(
            stream_id, SensoryItem(text=text, person_id=person_id, observed_at=observed_at)
        )

    def build_cue(
        self,
        stream_id: str,
        *,
        chat_type: str,
        now: datetime | None = None,
        fallback_turns: Sequence[str] = (),
    ) -> FeedForwardCue | None:
        """从 L0 构造线索；L0 为空时退回调用方提供的最近对话文本。"""
        current = now or datetime.now(UTC)
        sensory = self.layers.sensory(stream_id, current)
        if sensory:
            text = "\n".join(item.text for item in sensory)
            people = tuple(
                dict.fromkeys(
                    item.person_id
                    for item in sensory
                    if item.person_id and item.person_id != "bot"
                )
            )
        else:
            text = "\n".join(piece.strip() for piece in fallback_turns if piece.strip())
            people = ()
        if not text.strip():
            return None
        return FeedForwardCue(
            stream_id=stream_id,
            text=text,
            person_ids=people,
            chat_type=chat_type,
            now=current,
        )

    async def feed_forward(self, cue: FeedForwardCue) -> FeedForwardResult:
        """执行一轮完整前馈；超出延迟预算时返回空结果。"""
        if self._settings.max_memories == 0:
            return FeedForwardResult(selected=(), candidate_count=0, gates={})
        async with self._locks[cue.stream_id]:
            try:
                return await asyncio.wait_for(
                    self._run(cue), timeout=self._settings.latency_budget_ms / 1000.0
                )
            except TimeoutError:
                return FeedForwardResult(selected=(), candidate_count=0, gates={})

    def forget(self, memory_id: str) -> None:
        """正式记忆变化时清除进程内缓存，KDA 状态通过读出时的事实过滤自然失效。"""
        self.layers.forget_memory(memory_id)

    # ------------------------------------------------------------------ 管线

    async def _run(self, cue: FeedForwardCue) -> FeedForwardResult:
        """候选召回 → ACT-R → Transformer → Boltzmann 选择 → 巩固写回。"""
        turn = self._turns[cue.stream_id]
        self._turns[cue.stream_id] = turn + 1
        retrieved = await self._retrieval.search(
            RetrievalQuery(text=cue.text, top_k=self._settings.candidate_limit)
        )
        similarity = {
            item.memory_id: max(0.0, float(item.vector_similarity or 0.0))
            for item in retrieved
        }
        lexical = {
            item.memory_id: 1.0 / (1.0 + item.lexical_rank)
            for item in retrieved
            if item.lexical_rank is not None
        }
        working = self.layers.working_bonus(cue.stream_id, turn)
        pool = tuple(
            dict.fromkeys(
                (
                    *similarity,
                    *working,
                    *self.layers.hot_ids(cue.now, limit=self._settings.hot_candidates),
                )
            )
        )
        facts = await self._load_facts(pool, cue.now)
        if not facts:
            return FeedForwardResult(selected=(), candidate_count=0, gates={})

        dim = self._settings.feature_dim
        cue_tokens = _tokenize(cue.text)
        cue_key = hashed_features(cue_tokens, dim, salt="cue")
        values = {
            memory_id: self._value_vector(memory_id) for memory_id in facts
        }
        kda_scores = (
            self.kda.scores(cue_key, values, at=cue.now)
            if any(cue_key)
            else {memory_id: 0.0 for memory_id in facts}
        )

        components: dict[str, ActivationComponents] = {}
        for memory_id, fact in facts.items():
            channels = fact.channels()
            decay = min(self._settings.channels[name].decay for name in channels)
            associations = [
                (self._settings.working_weight, working.get(memory_id, 0.0)),
                (self._settings.kda_weight, kda_scores.get(memory_id, 0.0)),
                (1.0, similarity.get(memory_id, 0.0)),
                (0.5, lexical.get(memory_id, 0.0)),
            ]
            if cue.person_ids and set(cue.person_ids) & set(fact.person_ids):
                associations.append((0.6, 1.0))
            mismatches: list[float] = []
            if cue.person_ids and fact.person_ids:
                mismatches.append(
                    1.0 if set(cue.person_ids) & set(fact.person_ids) else 0.0
                )
            components[memory_id] = self.actr.activation(
                access_times=fact.access_times,
                now=cue.now,
                associations=associations,
                mismatches=mismatches,
                noise_unit=self._noise_unit(cue, memory_id, turn),
                decay=decay,
            )

        query_views, gates = self._query_views(cue, cue_tokens)
        tokens = [
            MemoryToken(
                memory_id=memory_id,
                views=self._memory_views(fact, components[memory_id], cue.now),
                prior=components[memory_id].total,
            )
            for memory_id, fact in facts.items()
        ]
        output = self.block.forward(query_views, tokens, gates=gates)

        # Transformer 分数与 ACT-R 激活共同决定最终竞争激活
        final = {
            memory_id: ActivationComponents(
                base_level=parts.base_level,
                spreading=parts.spreading,
                partial_matching=parts.partial_matching,
                noise=parts.noise,
                total=parts.total + output.scores.get(memory_id, 0.0),
            )
            for memory_id, parts in components.items()
        }
        relevant = {
            memory_id: parts
            for memory_id, parts in final.items()
            if self._is_relevant(
                memory_id, similarity, lexical, working, kda_scores, cue, facts
            )
        }
        chosen = (
            self.actr.select(relevant, limit=self._settings.max_memories)
            if relevant
            else ()
        )
        selected = tuple(
            FeedForwardMemory(
                memory_id=item.memory_id,
                title=facts[item.memory_id].title,
                content=facts[item.memory_id].content,
                probability=item.probability,
                activation=components[item.memory_id],
                transformer_score=output.scores.get(item.memory_id, 0.0),
                channels=facts[item.memory_id].channels(),
                head_attention={
                    head.name: head.attention.get(item.memory_id, 0.0)
                    for head in output.heads
                },
            )
            for item in chosen
        )
        self._consolidate(cue, cue_key, selected, turn)
        return FeedForwardResult(
            selected=selected, candidate_count=len(facts), gates=dict(gates)
        )

    def _is_relevant(
        self,
        memory_id: str,
        similarity: dict[str, float],
        lexical: dict[str, float],
        working: dict[str, float],
        kda_scores: dict[str, float],
        cue: FeedForwardCue,
        facts: dict[str, _CandidateFacts],
    ) -> bool:
        """候选必须与当前线索有至少一条真实关联通路，避免只因「新」而注入。"""
        evidence = max(
            similarity.get(memory_id, 0.0),
            lexical.get(memory_id, 0.0),
            working.get(memory_id, 0.0),
            kda_scores.get(memory_id, 0.0),
            1.0 if set(cue.person_ids) & set(facts[memory_id].person_ids) else 0.0,
        )
        return evidence > self._settings.relevance_floor

    def _consolidate(
        self,
        cue: FeedForwardCue,
        cue_key: tuple[float, ...],
        selected: Sequence[FeedForwardMemory],
        turn: int,
    ) -> None:
        """选中记忆进入 L1 复述，并以 delta 规则写入线索所在通道。"""
        if not any(cue_key):
            return
        for item in selected:
            self.layers.rehearse(
                cue.stream_id,
                item.memory_id,
                activation=item.probability,
                turn=turn,
                now=cue.now,
            )
            # 写入线索通道与记忆自身的情感/事实通道，衰减速率各不相同
            channels = {cue.channel, *(
                name for name in item.channels if name in {CHANNEL_EMOTIONAL, CHANNEL_FACTUAL}
            )}
            for channel in sorted(channels):
                self.kda.write(
                    channel,
                    cue_key,
                    self._value_vector(item.memory_id),
                    at=cue.now,
                    strength=max(0.05, min(1.0, item.probability)),
                )

    # ------------------------------------------------------------------ 特征

    def _value_vector(self, memory_id: str) -> tuple[float, ...]:
        """记忆在 KDA 值空间中的确定性身份向量。"""
        return hashed_features(
            (memory_id, f"{memory_id}#1", f"{memory_id}#2"),
            self._settings.feature_dim,
            salt="memory-value",
        )

    def _query_views(
        self, cue: FeedForwardCue, cue_tokens: tuple[str, ...]
    ) -> tuple[dict[str, tuple[float, ...]], dict[str, float]]:
        """构造线索视图与按线索类型调整的头门控。"""
        dim = self._settings.feature_dim
        emotions = emotions_in(cue.text)
        views: dict[str, tuple[float, ...]] = {
            "semantic": hashed_features(cue_tokens, dim, salt="semantic"),
            "topic": hashed_features(cue_tokens, dim, salt="topic"),
            "temporal": temporal_encoding(0.0),
            "activation": (1.0, 1.0),
        }
        if cue.person_ids:
            views["person"] = hashed_features(cue.person_ids, dim, salt="person")
        if emotions:
            views["emotion"] = hashed_features(emotions, dim, salt="emotion")
        gates = {
            "semantic": 1.0,
            "topic": 0.8,
            "person": 1.5 if cue.person_ids else 0.0,
            "emotion": 1.2 if emotions else 0.0,
            "temporal": 1.2 if any(term in cue.text for term in _TIME_TERMS) else 0.4,
            "activation": 0.6,
        }
        return views, gates

    def _memory_views(
        self,
        fact: _CandidateFacts,
        activation: ActivationComponents,
        now: datetime,
    ) -> dict[str, tuple[float, ...]]:
        """构造候选记忆的各视图特征。"""
        dim = self._settings.feature_dim
        tokens = _tokenize(f"{fact.title}\n{fact.content}")
        age_hours = max(0.0, (now - fact.experienced_at).total_seconds() / 3600.0)
        emotions = emotions_in(fact.content)
        # 激活视图：方向编码激活高低，零激活附近仍是非零向量
        squashed = math.tanh(activation.total)
        views: dict[str, tuple[float, ...]] = {
            "semantic": hashed_features(tokens, dim, salt="semantic"),
            "topic": hashed_features(tokens, dim, salt="topic"),
            "temporal": temporal_encoding(age_hours),
            "activation": (1.0 + squashed, 1.0 - squashed),
        }
        if fact.person_ids:
            views["person"] = hashed_features(fact.person_ids, dim, salt="person")
        if emotions:
            views["emotion"] = hashed_features(emotions, dim, salt="emotion")
        return views

    @staticmethod
    def _noise_unit(cue: FeedForwardCue, memory_id: str, turn: int) -> float:
        """同一流、同一轮、同一记忆总得到同一噪声分位，结果可复查。"""
        digest = hashlib.sha256(f"{cue.stream_id}\0{turn}\0{memory_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    # ------------------------------------------------------------------ 数据

    async def _load_facts(
        self, memory_ids: Sequence[str], now: datetime
    ) -> dict[str, _CandidateFacts]:
        """优先读 L2 热缓存，未命中的批量回源 L3 冷库。"""
        result: dict[str, _CandidateFacts] = {}
        missing: list[str] = []
        for memory_id in memory_ids:
            payload = self.layers.hot_get(memory_id, now)
            if payload is None:
                missing.append(memory_id)
            else:
                result[memory_id] = _CandidateFacts.from_payload(payload)
        if missing:
            loaded = await self._query_facts(tuple(missing))
            for memory_id, fact in loaded.items():
                self.layers.hot_put(memory_id, fact.to_payload(), now)
                result[memory_id] = fact
        return {memory_id: result[memory_id] for memory_id in memory_ids if memory_id in result}

    async def _query_facts(self, memory_ids: tuple[str, ...]) -> dict[str, _CandidateFacts]:
        """从冷库读取 ACTIVE 记忆的当前版本、人物、来源与访问历史。"""
        if not memory_ids:
            return {}
        async with self._schema.database.session() as session:
            rows = (
                await session.execute(
                    select(
                        MemoryModel.memory_id,
                        MemoryModel.created_at,
                        MemoryModel.last_experienced_at,
                        MemoryRevisionModel.revision_id,
                        MemoryRevisionModel.title,
                        MemoryRevisionModel.content,
                        MemoryRevisionModel.memory_kind,
                        MemoryRevisionModel.observed_at,
                    )
                    .join(
                        MemoryRevisionModel,
                        MemoryRevisionModel.revision_id == MemoryModel.current_revision_id,
                    )
                    .where(
                        MemoryModel.memory_id.in_(memory_ids),
                        MemoryModel.status == MemoryStatus.ACTIVE,
                    )
                )
            ).all()
            if not rows:
                return {}
            revision_ids = tuple(row.revision_id for row in rows)
            people: defaultdict[str, set[str]] = defaultdict(set)
            for revision_id, person_id in (
                await session.execute(
                    select(
                        MemoryRevisionSubjectModel.revision_id,
                        MemoryRevisionSubjectModel.person_id,
                    ).where(MemoryRevisionSubjectModel.revision_id.in_(revision_ids))
                )
            ).all():
                if person_id:
                    people[revision_id].add(person_id)
            for revision_id, person_id in (
                await session.execute(
                    select(
                        MemoryRevisionParticipantModel.revision_id,
                        MemoryRevisionParticipantModel.person_id,
                    ).where(MemoryRevisionParticipantModel.revision_id.in_(revision_ids))
                )
            ).all():
                if person_id:
                    people[revision_id].add(person_id)
            sources: defaultdict[str, set[str]] = defaultdict(set)
            chat_types: defaultdict[str, set[str]] = defaultdict(set)
            redacted: set[str] = set()
            for revision_id, source_type, payload, redacted_at in (
                await session.execute(
                    select(
                        RevisionEvidenceModel.revision_id,
                        EvidenceModel.source_type,
                        EvidenceMessageSnapshotModel.payload,
                        EvidenceMessageSnapshotModel.redacted_at,
                    )
                    .join(
                        EvidenceModel,
                        EvidenceModel.evidence_id == RevisionEvidenceModel.evidence_id,
                    )
                    .outerjoin(
                        EvidenceMessageLinkModel,
                        EvidenceMessageLinkModel.evidence_id == EvidenceModel.evidence_id,
                    )
                    .outerjoin(
                        EvidenceMessageSnapshotModel,
                        (EvidenceMessageSnapshotModel.stream_id
                         == EvidenceMessageLinkModel.stream_id)
                        & (EvidenceMessageSnapshotModel.message_id
                           == EvidenceMessageLinkModel.message_id),
                    )
                    .where(RevisionEvidenceModel.revision_id.in_(revision_ids))
                )
            ).all():
                if isinstance(source_type, EvidenceSourceType):
                    sources[revision_id].add(source_type.value)
                if redacted_at is not None:
                    redacted.add(revision_id)
                if isinstance(payload, dict):
                    chat_type = payload.get("chat_type")
                    if isinstance(chat_type, str) and chat_type:
                        chat_types[revision_id].add(chat_type)
            accesses: defaultdict[str, list[datetime]] = defaultdict(list)
            for memory_id, occurred_at in (
                await session.execute(
                    select(MemoryEventModel.memory_id, MemoryEventModel.occurred_at).where(
                        MemoryEventModel.memory_id.in_(memory_ids),
                        MemoryEventModel.event_type.in_(
                            (MemoryEventType.RECALLED, MemoryEventType.FLASHBACK_EXPOSED)
                        ),
                    )
                )
            ).all():
                accesses[memory_id].append(occurred_at)
        result: dict[str, _CandidateFacts] = {}
        for row in rows:
            if row.revision_id in redacted:
                continue
            experienced = row.last_experienced_at or row.observed_at or row.created_at
            result[row.memory_id] = _CandidateFacts(
                memory_id=row.memory_id,
                title=str(row.title or ""),
                content=str(row.content or ""),
                memory_kind=(
                    row.memory_kind.value
                    if isinstance(row.memory_kind, MemoryKind)
                    else str(row.memory_kind)
                ),
                person_ids=tuple(sorted(people.get(row.revision_id, ()))),
                chat_types=tuple(sorted(chat_types.get(row.revision_id, ()))),
                source_types=tuple(sorted(sources.get(row.revision_id, ()))),
                access_times=tuple(
                    sorted({row.created_at, experienced, *accesses.get(row.memory_id, ())})
                ),
                experienced_at=experienced,
            )
        return result


__all__ = [
    "CHANNELS",
    "ChannelSettings",
    "FeedForwardCue",
    "FeedForwardMemory",
    "FeedForwardResult",
    "FeedForwardRetrievalService",
    "FeedForwardSettings",
    "HEAD_NAMES",
    "emotions_in",
]
