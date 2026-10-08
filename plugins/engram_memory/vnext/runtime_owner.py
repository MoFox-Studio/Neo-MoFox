"""Engram Memory 运行时资源所有者。

将记忆数据库、领域服务、派生向量索引和后台任务绑定到一个插件实例，
供框架组件共享。
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.app.plugin_system.api import event_api, log_api, stream_api

from ..diary.runtime import DiaryRuntime
from .doctor_service import DoctorService
from .claim_service import ClaimHypothesisService
from .decay_service import MemoryDecayService
from .backends.neo4j import Neo4jEpisodeGraph
from .domain import MemoryChanged
from .episode_service import EpisodeService
from .backends.layersplit_store import LayerSplitStore
from .enums import VectorIndexStatus
from .feedforward_service import (
    CHANNEL_EMOTIONAL,
    CHANNEL_FACTUAL,
    CHANNEL_GROUP,
    CHANNEL_PRIVATE,
    ChannelSettings,
    FeedForwardCue,
    FeedForwardRetrievalService,
    FeedForwardSettings,
)
from .flashback_service import FlashbackCandidate, FlashbackService
from .framework_bridge import (
    TaskNotFoundError,
    cancel_managed_task,
    create_managed_task,
    get_managed_task,
)
from .models import VectorIndexManifestModel
from .persona_service import PersonaService
from .persona_updater import PersonaUpdater
from .proposal_service import ProposalService
from .repository import MemoryRepository
from .retrieval_service import RetrievalService, VectorSearchBackend
from .runtime import (
    DEFAULT_EMBEDDING_MODEL_TASK,
    ChromaVectorSink,
    VectorOutboxWorker,
    message_to_snapshot,
)
from .schema import VNextSchema
from .tool_service import VNextToolService
from .vector_service import (
    UNAVAILABLE_EMBEDDING_MODEL_ID,
    VectorIndexService,
)

logger = log_api.get_logger(
    "engram_memory.vnext.runtime_owner", display="Engram 记忆", color=log_api.COLOR.CYAN
)

_VECTOR_WORKER_BATCH_SIZE = 30
_UNAVAILABLE_EMBEDDING_MODEL_ID = UNAVAILABLE_EMBEDDING_MODEL_ID


class ChromaVectorSearchBackend(VectorSearchBackend):
    """通过 vNext 派生 Chroma 集合提供向量召回。"""

    def __init__(
        self,
        sink: ChromaVectorSink,
        schema: VNextSchema | None = None,
    ) -> None:
        """绑定只读向量查询端。"""
        self._sink = sink
        self._schema = schema

    async def query(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[str, ...]:
        """以第一项查询文本访问派生索引。"""
        if not texts:
            return ()
        if self._schema is None:
            return ()
        async with self._schema.database.session() as session:
            manifest = (
                await session.scalars(
                    select(VectorIndexManifestModel)
                    .where(VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE)
                    .order_by(VectorIndexManifestModel.created_at.desc())
                )
            ).first()
        if manifest is None:
            return ()
        allowed_ids = {entry_id for entry_id, _ in texts[1:]}
        sink = self._sink.for_index(
            manifest.index_id,
            manifest.embedding_model_id,
            manifest.embedding_dimension,
        )
        result = await sink.query_entries(texts[0][1], top_k)
        return tuple(entry_id for entry_id in result if entry_id in allowed_ids)

    async def query_scored(
        self,
        texts: tuple[tuple[str, str], ...],
        top_k: int,
    ) -> tuple[tuple[str, float | None], ...]:
        """读取 ACTIVE 索引的排位与真实语义相似度。"""
        if not texts or self._schema is None:
            return ()
        async with self._schema.database.session() as session:
            manifest = (
                await session.scalars(
                    select(VectorIndexManifestModel)
                    .where(VectorIndexManifestModel.status == VectorIndexStatus.ACTIVE)
                    .order_by(VectorIndexManifestModel.created_at.desc())
                )
            ).first()
        if manifest is None:
            return ()
        allowed_ids = {entry_id for entry_id, _ in texts[1:]}
        sink = self._sink.for_index(
            manifest.index_id,
            manifest.embedding_model_id,
            manifest.embedding_dimension,
        )
        results = await sink.query_scored_entries(texts[0][1], top_k)
        return tuple(
            (entry_id, score) for entry_id, score in results if entry_id in allowed_ids
        )


class VNextRuntimeOwner:
    """持有插件范围内唯一的 vNext 运行时资源。"""

    def __init__(self, plugin: Any) -> None:
        """根据插件配置装配 vNext 资源，但不执行外部 I/O。"""
        from ..config import EngramMemoryConfig

        self.plugin = plugin
        if not isinstance(plugin.config, EngramMemoryConfig):
            raise TypeError("Engram Runtime 必须使用已加载的插件配置")
        self.config: EngramMemoryConfig = plugin.config
        self.diary = DiaryRuntime(self.config.diary)
        config = self.config
        vnext = config.vnext
        self.schema = VNextSchema(config.storage.vnext_db_path)
        self.repository = MemoryRepository(self.schema)
        self.vector_sink = ChromaVectorSink(
            db_path=config.storage.vector_db_path,
            collection_name="engram_vnext_retrieval",
            embedding_task=DEFAULT_EMBEDDING_MODEL_TASK,
        )
        try:
            self._embedding_model_identity = self.vector_sink.embedding_model_identity()
        except Exception as error:  # noqa: BLE001
            self._embedding_model_identity = _UNAVAILABLE_EMBEDDING_MODEL_ID
            logger.warning(
                "Embedding 模型身份不可用，先以无向量模式装配 Engram: "
                f"{type(error).__name__}: {error}"
            )
        self.vector_backend = ChromaVectorSearchBackend(self.vector_sink, self.schema)
        self.retrieval = RetrievalService(
            self.schema,
            self.vector_backend,
            rrf_k=vnext.retrieval.rrf_k,
            activation_half_life_days=vnext.retrieval.activation_half_life_days,
            activation_weight=vnext.retrieval.activation_weight,
            activation_noise=vnext.retrieval.activation_noise,
        )
        self.decay = MemoryDecayService(
            self.schema,
            half_life_days=vnext.retrieval.memory_half_life_days,
            recall_half_life_days=vnext.retrieval.recall_half_life_days,
            forget_threshold=vnext.retrieval.forget_threshold,
            min_age_days=vnext.retrieval.forget_min_age_days,
        )
        self.tools = VNextToolService(
            self.schema,
            self.vector_backend,
            embedding_model_id=self._embedding_model_identity,
            recent_memory_limit=vnext.persona.recent_memory_limit,
            default_search_limit=vnext.retrieval.default_limit,
            max_search_limit=vnext.retrieval.max_limit,
            rrf_k=vnext.retrieval.rrf_k,
            activation_half_life_days=vnext.retrieval.activation_half_life_days,
            activation_weight=vnext.retrieval.activation_weight,
            activation_noise=vnext.retrieval.activation_noise,
            on_memory_changed=self._on_memory_changed,
        )
        self.persona_service = PersonaService(self.schema, persona_config=vnext.persona)
        self.persona_updater = PersonaUpdater(
            self.persona_service,
            self.repository,
            max_concurrency=vnext.persona.max_concurrency,
        )
        self.vector_index = VectorIndexService(
            self.schema,
            self.vector_sink,
            max_attempts=vnext.vector.worker_retry_limit,
        )
        self.vector_worker = VectorOutboxWorker(
            self.vector_index,
            batch_size=_VECTOR_WORKER_BATCH_SIZE,
            poll_interval_seconds=10.0,
            retry_failed=False,
        )
        self.doctor: DoctorService | None = None
        self.flashback = FlashbackService(
            self.schema,
            self.retrieval,
            context_turns=vnext.flashback.context_turns,
            latency_budget_ms=vnext.flashback.latency_budget_ms,
            max_memories=vnext.flashback.max_memories,
            cooldown_turns=vnext.flashback.cooldown_turns,
        )
        ff = vnext.feedforward
        self.feedforward = FeedForwardRetrievalService(
            self.schema,
            self.retrieval,
            FeedForwardSettings(
                max_memories=ff.max_memories,
                candidate_limit=ff.candidate_limit,
                hot_candidates=ff.hot_candidates,
                feature_dim=ff.feature_dim,
                retrieval_threshold=ff.retrieval_threshold,
                temperature=ff.temperature,
                noise_scale=ff.noise_scale,
                mismatch_penalty=ff.mismatch_penalty,
                working_weight=ff.working_weight,
                kda_weight=ff.kda_weight,
                recency_weight=ff.recency_weight,
                inhibition_weight=ff.inhibition_weight,
                latency_budget_ms=ff.latency_budget_ms,
                channels={
                    CHANNEL_PRIVATE: ChannelSettings(
                        ff.private_decay, ff.private_retention_per_day, ff.kda_learning_rate
                    ),
                    CHANNEL_GROUP: ChannelSettings(
                        ff.group_decay, ff.group_retention_per_day, ff.kda_learning_rate
                    ),
                    CHANNEL_EMOTIONAL: ChannelSettings(
                        ff.emotional_decay, ff.emotional_retention_per_day, ff.kda_learning_rate
                    ),
                    CHANNEL_FACTUAL: ChannelSettings(
                        ff.factual_decay, ff.factual_retention_per_day, ff.kda_learning_rate
                    ),
                },
            ),
            layers=LayerSplitStore(
                sensory_capacity=ff.sensory_capacity,
                sensory_ttl_seconds=ff.sensory_ttl_seconds,
                working_capacity=ff.working_capacity,
                working_half_life_turns=ff.working_half_life_turns,
                hot_capacity=ff.hot_capacity,
                hot_ttl_seconds=ff.hot_ttl_seconds,
            ),
        )
        self.episode_service = EpisodeService(
            self.schema,
            max_working_memory=vnext.flashback.max_working_memory,
            relation_hops=vnext.flashback.relation_hops,
            reconstruction_noise=vnext.flashback.reconstruction_noise,
            working_memory_ttl_seconds=vnext.flashback.working_memory_ttl_seconds,
            embedding_function=(
                self.vector_sink.embed_texts
                if vnext.flashback.semantic_recall_enabled
                else None
            ),
            semantic_candidate_limit=vnext.flashback.semantic_candidate_limit,
            semantic_min_similarity=vnext.flashback.semantic_min_similarity,
        )
        self.proposal_service = ProposalService(self.schema)
        self.claim_service = ClaimHypothesisService(
            self.schema,
            self.proposal_service,
            enabled=vnext.claim_review.enabled,
            model_task=vnext.claim_review.model_task,
            max_per_run=vnext.claim_review.max_per_run,
        )
        self.neo4j_graph: Neo4jEpisodeGraph | None = None
        if vnext.neo4j.enabled:
            uri = os.environ.get("ENGRAM_NEO4J_URI") or vnext.neo4j.uri
            password = os.environ.get("ENGRAM_NEO4J_PASSWORD") or vnext.neo4j.password
            if password:
                self.neo4j_graph = Neo4jEpisodeGraph(
                    uri,
                    vnext.neo4j.user,
                    password,
                    database=vnext.neo4j.database,
                )
            else:
                logger.warning("Neo4j 已启用但未配置密码，跳过 Episode 图镜像")
        self._initialized = False
        self._prompt_turns: dict[str, int] = {}
        self._flashback_trigger_turns: dict[str, tuple[int, bool]] = {}
        self._task_ids: set[str] = set()
        self._schema_initialized = False
        self._worker_started = False
        self._recent_messages: dict[str, dict[str, object]] = {}
        self._flashback_task_ids: dict[str, str] = {}
        self._flashback_results: dict[str, tuple[tuple[object, ...], int, int]] = {}
        self._flashback_generations: dict[str, int] = {}
        self._task_ids_by_task: dict[asyncio.Task[Any], str] = {}
        self._flashback_locks: dict[str, asyncio.Lock] = {}
        self._sqlite_write_lock = asyncio.Lock()  # 插件级单写者锁
        self._working_memory_results: dict[str, dict[str, object]] = {}
        self._consolidation_task_id: str | None = None
        self._vector_available = False
        self._vector_error: str | None = None

    @property
    def vector_available(self) -> bool:
        """返回派生向量索引当前是否可用。"""
        return self._vector_available

    @property
    def vector_error(self) -> str | None:
        """返回最近一次向量初始化失败的可定位原因。"""
        return self._vector_error

    async def initialize(self) -> None:
        """初始化记忆数据库并启动派生向量后台任务。"""
        if self._initialized:
            return
        try:
            self._schema_initialized = True
            await self.schema.initialize()
            if self.neo4j_graph is not None:
                try:
                    await self.neo4j_graph.connect()
                except Exception as error:  # noqa: BLE001
                    # 镜像是旁路能力，不能阻断正式记忆加载
                    logger.warning(f"Neo4j 连接失败，禁用 Episode 图镜像: {type(error).__name__}: {error}")
                    await self.neo4j_graph.close()
                    self.neo4j_graph = None
            cleared_personas = await self.persona_service.clear_legacy_impressions()
            logger.info(f"人物印象启动核对完成：归档并清理 {cleared_personas} 份旧稿")
            try:
                (
                    embedding_identity,
                    embedding_dimension,
                ) = await self.vector_sink.inspect_embedding_settings()
                recovering_embedding = (
                    self._embedding_model_identity == _UNAVAILABLE_EMBEDDING_MODEL_ID
                )
                if (
                    embedding_identity != self._embedding_model_identity
                    and not recovering_embedding
                ):
                    raise RuntimeError("Embedding task identity 在初始化期间发生变化")
                if recovering_embedding:
                    await self.vector_index.rebind_unavailable_outbox(embedding_identity)
                    self._embedding_model_identity = embedding_identity
                    self.tools.rebind_embedding_model(embedding_identity)
                self.doctor = DoctorService(
                    self.schema,
                    vector_service=self.vector_index,
                    embedding_model_id=embedding_identity,
                    embedding_dimension=embedding_dimension,
                    retrieval_schema_version="engram-vnext-2",
                )
                await self.vector_index.ensure_active_manifest(
                    embedding_identity,
                    embedding_dimension,
                    "engram-vnext-2",
                )
            except Exception as error:  # noqa: BLE001
                self.doctor = None
                self._vector_available = False
                self._vector_error = f"{type(error).__name__}: {error}"
                logger.warning(
                    "Embedding/Vector 派生索引不可用，继续使用词法和结构化记忆能力: "
                    f"{self._vector_error}"
                )
            else:
                self._vector_available = True
                self._vector_error = None
                self._worker_started = True
                self.vector_worker.start()
            self._initialized = True
            await self.diary.initialize()
            self.persona_updater.start()
            if self.config.vnext.claim_review.background_enabled:
                self._start_consolidation_loop()
        except BaseException:
            await self._shutdown_resources()
            raise

    async def close(self) -> None:
        """停止后台任务、取消当前实例的托管任务并关闭记忆数据库。"""
        await self._shutdown_resources()

    async def _on_memory_changed(self, change: MemoryChanged) -> None:
        """发布已提交的正式记忆变化，向量投递由独立后台任务处理。"""
        self.feedforward.forget(change.memory_id)
        await event_api.publish_event(
            "engram_memory:memory_changed",
            {"change": change},
        )

    async def _shutdown_resources(self) -> None:
        """幂等释放当前实例已部分或完整初始化的运行时资源。"""
        self._initialized = False
        task_ids = tuple(self._task_ids | set(self._flashback_task_ids.values()))
        task_infos = []
        for task_id in task_ids:
            try:
                task_info = get_managed_task(task_id)
            except TaskNotFoundError:
                continue
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    f"读取待关闭的 vNext 托管任务失败: {type(error).__name__}: {error}"
                )
                continue
            task_infos.append(task_info)
            cancel_managed_task(task_id)
        current_task = asyncio.current_task()
        pending_tasks = tuple(
            info.task
            for info in task_infos
            if info.task is not None and info.task is not current_task
        )
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        self._task_ids.clear()
        self._flashback_task_ids.clear()
        self._flashback_results.clear()
        self._flashback_generations.clear()
        self._flashback_trigger_turns.clear()
        self._task_ids_by_task.clear()
        self._prompt_turns.clear()
        self._flashback_locks.clear()
        self._sqlite_write_lock = asyncio.Lock()
        self._working_memory_results.clear()
        self._consolidation_task_id = None
        self._recent_messages.clear()
        self._vector_available = False
        self._vector_error = None
        self.feedforward.layers.clear()
        if self.neo4j_graph is not None:
            try:
                await self.neo4j_graph.close()
            except Exception as error:  # noqa: BLE001
                logger.warning(f"Neo4j Episode 图关闭失败: {type(error).__name__}: {error}")
        try:
            try:
                await self.diary.close()
            finally:
                await self.persona_updater.close()
        finally:
            if self._worker_started:
                try:
                    await self.vector_worker.stop()
                except Exception as error:  # noqa: BLE001
                    logger.warning(f"vNext Vector Worker 停止失败: {error}")
                finally:
                    self._worker_started = False
            if self._schema_initialized:
                try:
                    await self.schema.close()
                except Exception as error:  # noqa: BLE001
                    logger.warning(f"vNext Schema 关闭失败: {error}")
                finally:
                    self._schema_initialized = False

    def observe_message(self, message: object) -> None:
        """记录新消息并安排该流的闪回预取。"""
        stream_id = str(self._message_value(message, "stream_id") or "").strip()
        if not stream_id:
            return
        message_id = str(
            self._message_value(message, "message_id")
            or self._message_value(message, "id")
            or ""
        ).strip()
        if message_id:
            recent = self._recent_messages.setdefault(stream_id, {})
            recent[message_id] = message
            while len(recent) > self.config.vnext.flashback.context_turns:
                recent.pop(next(iter(recent)))
        if self.config.vnext.feedforward.enabled:
            try:
                snapshot = message_to_snapshot(message)  # type: ignore[arg-type]
            except ValueError:
                snapshot = None
            if snapshot is not None:
                self.feedforward.perceive(
                    stream_id,
                    snapshot.text,
                    person_id=snapshot.snapshot.get("person_id"),  # type: ignore[arg-type]
                    observed_at=snapshot.time,
                )
        if self._initialized:
            self._schedule_flashback_prefetch(stream_id)

    def _start_consolidation_loop(self) -> None:
        """启动低频候选巩固扫描，任务由框架统一托管。"""
        if self._consolidation_task_id is not None:
            return
        handle = create_managed_task(
            self._run_consolidation_loop(),
            name="engram_vnext_consolidation",
            daemon=True,
        )
        self._consolidation_task_id = handle.task_id
        self._task_ids.add(handle.task_id)
        if handle.task is not None:
            self._task_ids_by_task[handle.task] = handle.task_id

    async def _run_consolidation_loop(self) -> None:
        """周期性收集并审核候选，不直接确认正式 Memory。"""
        interval = self.config.vnext.claim_review.background_interval_seconds
        limit = self.config.vnext.claim_review.background_stream_limit
        try:
            while self._initialized:
                await asyncio.sleep(interval)
                stream_ids = tuple(sorted(self._recent_messages))[:limit]
                for stream_id in stream_ids:
                    try:
                        async with self._sqlite_write_lock:
                            await self.proposal_service.collect_consolidation_candidates(
                                stream_id
                            )
                            await self.claim_service.sync_pending_proposals(stream_id)
                            await self.claim_service.review_pending(stream_id)
                    except Exception as error:  # noqa: BLE001
                        logger.warning(
                            f"后台巩固扫描失败 stream={stream_id}: "
                            f"{type(error).__name__}: {error}"
                        )
        except asyncio.CancelledError:
            raise
        finally:
            current_task = asyncio.current_task()
            if current_task is not None:
                task_id = self._task_ids_by_task.pop(current_task, None)
                if task_id is not None:
                    self._task_ids.discard(task_id)
                    if self._consolidation_task_id == task_id:
                        self._consolidation_task_id = None

    @staticmethod
    def _message_value(message: object, field: str) -> object:
        """读取公开消息对象或消息映射的动态字段。"""
        if isinstance(message, Mapping):
            return message.get(field)
        return getattr(message, field, None)

    def _schedule_flashback_prefetch(self, stream_id: str) -> None:
        """取消当前流的过期闪回预取并安排新任务。"""
        generation = self._flashback_generations.get(stream_id, 0) + 1
        self._flashback_generations[stream_id] = generation
        self._flashback_results.pop(stream_id, None)
        previous_id = self._flashback_task_ids.pop(stream_id, None)
        if previous_id is not None:
            cancel_managed_task(previous_id)
        task_info = create_managed_task(
            self._prefetch_flashback_task(stream_id, generation),
            name=f"engram_vnext_flashback_{stream_id[:16]}",
            daemon=True,
        )
        self._flashback_task_ids[stream_id] = task_info.task_id
        self._task_ids.add(task_info.task_id)
        if task_info.task is not None:
            self._task_ids_by_task[task_info.task] = task_info.task_id

    async def _prefetch_flashback_task(
        self,
        stream_id: str,
        generation: int,
    ) -> tuple[object, ...]:
        """执行当前流的回复前闪回检索并缓存当前请求的结果。"""
        try:
            recent_message = self._latest_recent_message(stream_id)
            if recent_message is not None:
                async with self._sqlite_write_lock:
                    episode = await self.episode_service.record_episode(recent_message)
                    await self.mirror_episode_to_graph(episode.episode_id)
                    working_memory = await self.episode_service.recall_working_memory(episode)
                if self._flashback_generations.get(stream_id) == generation:
                    self._working_memory_results[stream_id] = working_memory
            turn_index = self._prompt_turns.get(stream_id)
            if turn_index is None:
                turn_index = await self.flashback.next_turn_index(stream_id)
            self._prompt_turns.setdefault(stream_id, turn_index)
            candidates = await self._flashback_for_stream(
                stream_id,
                turn_index=turn_index,
                record_exposure=False,
            )
            current_task = asyncio.current_task()
            if current_task is not None:
                task_id = self._task_ids_by_task.get(current_task)
                if task_id is not None:
                    if (
                        self._flashback_task_ids.get(stream_id) == task_id
                        and self._flashback_generations.get(stream_id) == generation
                    ):
                        self._flashback_results[stream_id] = (
                            candidates,
                            turn_index,
                            generation,
                        )
            return candidates
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning(
                f"闪回预取失败 stream={stream_id}: {type(error).__name__}: {error}"
            )
            return ()
        finally:
            self._forget_current_task()

    def _forget_current_task(self) -> None:
        """移除已完成任务的句柄，不影响当前流的新预取任务。"""
        current_task = asyncio.current_task()
        if current_task is None:
            return
        task_id = self._task_ids_by_task.pop(current_task, None)
        if task_id is None:
            return
        self._task_ids.discard(task_id)
        for stream_id, prefetch_task_id in tuple(self._flashback_task_ids.items()):
            if prefetch_task_id == task_id:
                self._flashback_task_ids.pop(stream_id, None)

    def _latest_recent_message(self, stream_id: str) -> object | None:
        """返回当前流最近收到的消息，供 Episode 低成本记录。"""
        messages = self._recent_messages.get(stream_id)
        if not messages:
            return None
        return next(reversed(messages.values()))

    def observe_output_message(self, message: object) -> None:
        """回复发送后只登记 OUTPUT Episode，不触发当前轮回忆或模型调用。"""
        if not self._initialized:
            return
        stream_id = str(self._message_value(message, "stream_id") or "").strip()
        if not stream_id:
            return
        task_info = create_managed_task(
            self._record_output_episode(message),
            name=f"engram_episode_output_{stream_id[:16]}",
            daemon=True,
        )
        self._task_ids.add(task_info.task_id)
        if task_info.task is not None:
            self._task_ids_by_task[task_info.task] = task_info.task_id

        review_task = create_managed_task(
            self._review_output_candidates(message),
            name=f"engram_claim_review_{stream_id[:16]}",
            daemon=True,
        )
        self._task_ids.add(review_task.task_id)
        if review_task.task is not None:
            self._task_ids_by_task[review_task.task] = review_task.task_id

    async def _review_output_candidates(self, message: object) -> None:
        """输出后异步收集并审核当前流候选，不影响回复发送。"""
        try:
            stream_id = str(self._message_value(message, "stream_id") or "").strip()
            if not stream_id:
                return
            async with self._sqlite_write_lock:
                await self.proposal_service.collect_consolidation_candidates(stream_id)
                await self.claim_service.sync_pending_proposals(stream_id)
                await self.claim_service.review_pending(stream_id)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Claim/Hypothesis 后台审核失败: {type(error).__name__}: {error}")
        finally:
            self._forget_current_task()

    async def _record_output_episode(self, message: object) -> None:
        """后台写入输出经历，失败只记录日志。"""
        try:
            async with self._sqlite_write_lock:
                episode = await self.episode_service.record_output_observation(message)
                await self.mirror_episode_to_graph(episode.episode_id)
                stream_id = str(self._message_value(message, "stream_id") or "").strip()
                if stream_id:
                    await self.proposal_service.collect_consolidation_candidates(stream_id)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"输出 Episode 记录失败: {type(error).__name__}: {error}")
        finally:
            self._forget_current_task()

    async def mirror_episode_to_graph(self, episode_id: str) -> None:
        """异步镜像 Episode 与关系边到可选 Neo4j，主库不依赖镜像成功。"""
        if self.neo4j_graph is None:
            return
        try:
            async with self.schema.database.session() as session:
                from sqlalchemy import select

                from .models import EpisodeModel, EpisodeRelationModel

                episode = await session.get(EpisodeModel, episode_id)
                if episode is None:
                    return
                relations = (await session.scalars(select(EpisodeRelationModel).where(
                    EpisodeRelationModel.source_episode_id == episode_id,
                ))).all()
            await self.neo4j_graph.upsert_episode(
                episode.episode_id,
                {
                    "stream_id": episode.stream_id,
                    "observed_at": episode.observed_at.isoformat(),
                    "episode_kind": episode.episode_kind,
                    "salience": float(episode.salience),
                },
                tuple((item.target_episode_id, item.relation_type, float(item.weight)) for item in relations),
            )
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Neo4j Episode 镜像失败: {type(error).__name__}: {error}")

    def consume_working_memory(self, stream_id: str) -> dict[str, object] | None:
        """消费当前流最新工作记忆，防止旧工作记忆跨轮重复注入。"""
        return self._working_memory_results.pop(stream_id, None)

    async def consume_flashback_prefetch(self, stream_id: str) -> tuple[object, ...]:
        """在延迟预算内等待并消费当前流的最新闪回预取结果。"""
        if not self._initialized or not stream_id.strip():
            return ()
        lock = self._flashback_locks.setdefault(stream_id, asyncio.Lock())
        async with lock:
            stored = self._flashback_results.pop(stream_id, None)
            if stored is not None:
                result, turn_index, generation = stored
                if generation != self._flashback_generations.get(stream_id):
                    return ()
                return await self._commit_flashback_result(
                    stream_id,
                    result,
                    turn_index,
                )
            task_id = self._flashback_task_ids.get(stream_id)
            if task_id is None:
                return ()
            try:
                task_info = get_managed_task(task_id)
            except TaskNotFoundError:
                if self._flashback_task_ids.get(stream_id) == task_id:
                    self._flashback_task_ids.pop(stream_id, None)
                return ()
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    f"读取 vNext Flashback 预取任务失败: {type(error).__name__}: {error}"
                )
                if self._flashback_task_ids.get(stream_id) == task_id:
                    self._flashback_task_ids.pop(stream_id, None)
                return ()
            task = task_info.task
            if task is None:
                return ()
            budget = self.config.vnext.flashback.latency_budget_ms / 1000
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=budget)
            except TimeoutError:
                cancel_managed_task(task_id)
                await asyncio.gather(task, return_exceptions=True)
                return ()
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                return ()
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    f"vNext Flashback 预取失败: {type(error).__name__}: {error}"
                )
                return ()
            finally:
                if self._flashback_task_ids.get(stream_id) == task_id:
                    self._flashback_task_ids.pop(stream_id, None)
            if not isinstance(result, tuple):
                return ()
            stored = self._flashback_results.pop(stream_id, None)
            if stored is None:
                return ()
            stored_result, turn_index, generation = stored
            if generation != self._flashback_generations.get(stream_id):
                return ()
            return await self._commit_flashback_result(
                stream_id,
                stored_result,
                turn_index,
            )

    async def _commit_flashback_result(
        self,
        stream_id: str,
        candidates: tuple[object, ...],
        turn_index: int,
    ) -> tuple[object, ...]:
        """消费有效闪回结果并推进回复轮次；注入事件由 prompt handler 确认。"""
        self._prompt_turns[stream_id] = max(
            self._prompt_turns.get(stream_id, turn_index), turn_index + 1
        )
        if not candidates:
            return ()
        logger.info(f"vNext 闪回 | 为当前回复准备了 {len(candidates)} 条相关记忆")
        return candidates

    async def record_flashback_injection(
        self,
        stream_id: str,
        injected_candidates: tuple[object, ...],
        turn_index: int,
    ) -> None:
        """仅为成功安装到当前 prompt 的闪回记忆记录曝光。"""
        if not injected_candidates:
            return
        injected_memory_ids = tuple(
            str(getattr(candidate, "memory_id"))
            for candidate in injected_candidates
        )
        try:
            await self.flashback.record_exposure(
                injected_memory_ids,
                stream_id,
                turn_index,
                details={
                    str(getattr(candidate, "memory_id")): {
                        "stage": "prompt_injected",
                        "content": str(getattr(candidate, "current_brief", "")),
                        "strength": float(getattr(candidate, "strength", 1.0)),
                        "detail_level": str(getattr(candidate, "detail_level", "detailed")),
                        "blurred": bool(getattr(candidate, "blurred", False)),
                    }
                    for candidate in injected_candidates
                },
            )
        except Exception as error:  # noqa: BLE001
            logger.warning(
                f"vNext Flashback exposure 记录失败 stream={stream_id}: {error}"
            )

    async def recent_turns_for_flashback(
        self,
        stream_id: str,
    ) -> tuple[str, ...]:
        """从公共 API 合并持久消息与未落库消息并读取最近多轮文本。"""
        limit = self.config.vnext.flashback.context_turns
        messages = await stream_api.get_stream_messages(stream_id, limit=limit)
        merged: dict[str, object] = {}
        for index, message in enumerate(messages):
            message_id = str(
                self._message_value(message, "message_id")
                or self._message_value(message, "id")
                or f"__stream_{index}"
            ).strip()
            merged[message_id] = message
        for message_id, message in self._recent_messages.get(stream_id, {}).items():
            merged[message_id] = message

        def sort_key(message: object) -> tuple[datetime, str]:
            """按公开消息时间与 ID 生成稳定的 UTC 排序键。"""
            value = self._message_value(message, "time")
            if isinstance(value, datetime):
                timestamp = (
                    value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
                )
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                timestamp = datetime.fromtimestamp(float(value), tz=UTC)
            elif isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    parsed = datetime.min.replace(tzinfo=UTC)
                timestamp = (
                    parsed.astimezone(UTC)
                    if parsed.tzinfo
                    else parsed.replace(tzinfo=UTC)
                )
            else:
                timestamp = datetime.min.replace(tzinfo=UTC)
            return timestamp, str(
                self._message_value(message, "message_id")
                or self._message_value(message, "id")
                or ""
            )

        turns: list[str] = []
        for message in sorted(merged.values(), key=sort_key)[-limit:]:
            text = str(
                self._message_value(message, "processed_plain_text")
                or self._message_value(message, "content")
                or ""
            ).strip()
            if not text:
                continue
            speaker = str(
                self._message_value(message, "sender_name")
                or self._message_value(message, "sender_id")
                or "未知"
            ).strip()
            turns.append(f"{speaker}: {text}")
        return tuple(turns)

    async def flashback_for_stream(self, stream_id: str) -> tuple[object, ...]:
        """在当前回复前运行统一 vNext Flashback 检索。"""
        if not self._initialized or not stream_id.strip():
            return ()
        turn_index = self._prompt_turns.get(stream_id)
        if turn_index is None:
            turn_index = await self.flashback.next_turn_index(stream_id)
        self._prompt_turns[stream_id] = turn_index + 1
        async with self._sqlite_write_lock:
            return await self._flashback_for_stream(
                stream_id,
                turn_index=turn_index,
                record_exposure=True,
            )

    async def _flashback_for_stream(
        self,
        stream_id: str,
        *,
        turn_index: int,
        record_exposure: bool,
    ) -> tuple[object, ...]:
        """按当前轮次的触发决定检索闪回，并显式控制曝光记录。"""
        if self.config.vnext.feedforward.enabled:
            return await self._feedforward_for_stream(stream_id)
        settings = self.config.vnext.flashback
        if not settings.enabled or settings.max_memories == 0:
            return ()
        decision = self._flashback_trigger_turns.get(stream_id)
        if decision is None or decision[0] != turn_index:
            probability = settings.trigger_probability
            decision = (turn_index, probability >= 1.0 or random.random() < probability)
            self._flashback_trigger_turns[stream_id] = decision
        if not decision[1]:
            return ()
        turns = await self.recent_turns_for_flashback(stream_id)
        return await self.flashback.flashback(
            turns,
            enabled=self.config.vnext.flashback.enabled,
            stream_key=stream_id,
            turn_index=turn_index,
            record_exposure=record_exposure,
        )

    async def _feedforward_for_stream(self, stream_id: str) -> tuple[object, ...]:
        """每轮必然执行前馈检索，并转换为闪回候选以复用注入链路。"""
        recent = self._recent_messages.get(stream_id, {})
        chat_type = ""
        person_ids: list[str] = []
        for message in reversed(tuple(recent.values())):
            observed_chat_type = str(
                self._message_value(message, "chat_type") or ""
            ).casefold()
            if not chat_type and observed_chat_type in {"private", "group", "discuss"}:
                chat_type = observed_chat_type
            if observed_chat_type == "private":
                try:
                    snapshot = message_to_snapshot(message)  # type: ignore[arg-type]
                except ValueError:
                    snapshot = None
                person_id = str(snapshot.snapshot.get("person_id") or "") if snapshot else ""
                if person_id and person_id not in {"bot", "system"}:
                    person_ids.append(person_id)
        if not chat_type:
            info = await stream_api.get_stream_info(stream_id)
            chat_type = str(info.get("chat_type") or "") if info else ""
        if chat_type not in {"private", "group", "discuss"}:
            return ()
        fallback = (
            await self.recent_turns_for_flashback(stream_id)
            if not self.feedforward.layers.sensory(stream_id, datetime.now(UTC))
            else ()
        )
        cue = self.feedforward.build_cue(
            stream_id, chat_type=chat_type, fallback_turns=fallback
        )
        if cue is None:
            return ()
        cue = FeedForwardCue(
            stream_id=cue.stream_id,
            text=cue.text,
            person_ids=tuple(dict.fromkeys(person_ids)) or cue.person_ids,
            chat_type=chat_type,
            now=cue.now,
            latest_text=cue.latest_text,
        )
        result = await self.feedforward.feed_forward(cue)
        return tuple(
            FlashbackCandidate(
                memory_id=item.memory_id,
                title=item.title,
                current_brief=f"{item.title}: {item.content}" if item.content else item.title,
                matched_cue=item.title or "相关经历",
                strength=item.probability,
            )
            for item in result.selected
        )
