"""Engram Memory 运行时资源所有者。

将记忆数据库、领域服务、派生向量索引和后台任务绑定到一个插件实例，
供框架组件共享。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.app.plugin_system.api import event_api, log_api, stream_api

from ..diary.runtime import DiaryRuntime
from .doctor_service import DoctorService
from .domain import MemoryChanged
from .enums import VectorIndexStatus
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
from .repository import MemoryRepository
from .retrieval_service import RetrievalService, VectorSearchBackend
from .runtime import (
    DEFAULT_EMBEDDING_MODEL_TASK,
    ChromaVectorSink,
    VectorOutboxWorker,
)
from .schema import VNextSchema
from .tool_service import VNextToolService
from .vector_service import VectorIndexService

logger = log_api.get_logger(
    "engram_memory.vnext.runtime_owner", display="Engram 记忆", color=log_api.COLOR.CYAN
)

_VECTOR_WORKER_BATCH_SIZE = 30


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
        self._embedding_model_identity = self.vector_sink.embedding_model_identity()
        self.vector_backend = ChromaVectorSearchBackend(self.vector_sink, self.schema)
        self.retrieval = RetrievalService(
            self.schema,
            self.vector_backend,
            rrf_k=vnext.retrieval.rrf_k,
        )
        self.tools = VNextToolService(
            self.schema,
            self.vector_backend,
            embedding_model_id=self._embedding_model_identity,
            recent_memory_limit=vnext.persona.recent_memory_limit,
            default_search_limit=vnext.retrieval.default_limit,
            max_search_limit=vnext.retrieval.max_limit,
            rrf_k=vnext.retrieval.rrf_k,
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
        self._initialized = False
        self.vector_error: Exception | None = None
        self._prompt_turns: dict[str, int] = {}
        self._flashback_trigger_turns: dict[str, tuple[int, bool]] = {}
        self._task_ids: set[str] = set()
        self._schema_initialized = False
        self._worker_started = False
        self._recent_messages: dict[str, dict[str, object]] = {}
        self._flashback_task_ids: dict[str, str] = {}
        self._flashback_results: dict[str, tuple[tuple[FlashbackCandidate, ...], int, int]] = {}
        self._flashback_generations: dict[str, int] = {}
        self._task_ids_by_task: dict[asyncio.Task[Any], str] = {}
        self._flashback_locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """初始化记忆数据库并启动派生向量后台任务。"""
        if self._initialized:
            return
        try:
            self._schema_initialized = True
            await self.schema.initialize()
            cleared_personas = await self.persona_service.clear_legacy_impressions()
            logger.info(f"人物印象启动核对完成：归档并清理 {cleared_personas} 份旧稿")
            await self.diary.initialize()
            self.persona_updater.start()
        except BaseException:
            await self._shutdown_resources()
            raise
        try:
            (
                embedding_identity,
                embedding_dimension,
            ) = await self.vector_sink.inspect_embedding_settings()
            if embedding_identity != self._embedding_model_identity:
                raise RuntimeError("Embedding task identity 在初始化期间发生变化")
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
            self._worker_started = True
            self.vector_worker.start()
            self.vector_error = None
        except Exception as error:  # noqa: BLE001
            self.vector_error = error
            logger.error(f"向量功能初始化失败，独立日记继续运行，重启后可重新初始化: {error}")
        except BaseException:
            await self._shutdown_resources()
            raise
        self._initialized = True

    async def close(self) -> None:
        """停止后台任务、取消当前实例的托管任务并关闭记忆数据库。"""
        await self._shutdown_resources()

    async def _on_memory_changed(self, change: MemoryChanged) -> None:
        """直接登记人物更新后发布通知，避免事件总线吞掉内部入队失败。"""
        await self.persona_updater.enqueue(change)
        await event_api.publish_event(
            "engram_memory:memory_changed",
            {"change": change, "already_enqueued": True},
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
        self._recent_messages.clear()
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
        if self._initialized:
            self._schedule_flashback_prefetch(stream_id)

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
    ) -> tuple[FlashbackCandidate, ...]:
        """执行当前流的回复前闪回检索并缓存当前请求的结果。"""
        try:
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
                if (
                    task_id is not None
                    and self._flashback_task_ids.get(stream_id) == task_id
                    and self._flashback_generations.get(stream_id) == generation
                ):
                    self._flashback_results[stream_id] = (
                        candidates,
                        turn_index,
                        generation,
                    )
            return candidates
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

    async def consume_flashback_prefetch(self, stream_id: str) -> tuple[FlashbackCandidate, ...]:
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
        candidates: tuple[FlashbackCandidate, ...],
        turn_index: int,
    ) -> tuple[FlashbackCandidate, ...]:
        """仅为已接受注入的闪回结果记录曝光并推进回复轮次。"""
        self._prompt_turns[stream_id] = max(
            self._prompt_turns.get(stream_id, turn_index), turn_index + 1
        )
        if not candidates:
            return ()
        try:
            await self.flashback.record_exposure(
                tuple(str(candidate.memory_id) for candidate in candidates),
                stream_id,
                turn_index,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning(
                f"vNext Flashback exposure 记录失败 stream={stream_id}: {error}"
            )
            return ()
        logger.info(f"vNext 闪回 | 为当前回复准备了 {len(candidates)} 条相关记忆")
        return candidates

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
                    parsed = datetime.fromisoformat(value)
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

    async def flashback_for_stream(self, stream_id: str) -> tuple[FlashbackCandidate, ...]:
        """在当前回复前运行统一 vNext Flashback 检索。"""
        if not self._initialized or not stream_id.strip():
            return ()
        turn_index = self._prompt_turns.get(stream_id)
        if turn_index is None:
            turn_index = await self.flashback.next_turn_index(stream_id)
        self._prompt_turns[stream_id] = turn_index + 1
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
    ) -> tuple[FlashbackCandidate, ...]:
        """按当前轮次的触发决定检索闪回，并显式控制曝光记录。"""
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
