"""将人物印象补建、正式变化与有限重试合并到有界后台队列。"""

from __future__ import annotations

import asyncio

from src.app.plugin_system.api import log_api

from .domain import MemoryChanged
from .framework_bridge import (
    ManagedTaskHandle,
    cancel_managed_task,
    create_managed_task,
)
from .persona_service import PersonaService
from .repository import MemoryRepository

logger = log_api.get_logger(
    "engram_memory.vnext.persona_updater",
    display="Engram 记忆",
    color=log_api.COLOR.CYAN,
)


class PersonaUpdater:
    """以核心人物 ID 合并任务，共享并发名额且禁止同人并行。"""

    def __init__(
        self,
        service: PersonaService,
        repository: MemoryRepository,
        *,
        max_concurrency: int,
    ) -> None:
        """绑定共享 Persona 服务和人物别名仓储。"""
        self._service = service
        self._repository = repository
        if max_concurrency < 1:
            raise ValueError("人物并发上限必须大于零")
        self._max_concurrency = max_concurrency
        self._pending: dict[str, list[MemoryChanged]] = {}
        self._task: ManagedTaskHandle | None = None
        self._bootstrap: ManagedTaskHandle | None = None
        self._running: dict[str, ManagedTaskHandle] = {}
        self._retries: dict[str, ManagedTaskHandle] = {}
        self._attempts: dict[str, int] = {}
        self._wake = asyncio.Event()
        self._closed = False
        self.last_errors: dict[str, Exception] = {}

    def start(self) -> None:
        """在托管后台扫描需要首次补建的人物，不等待模型完成。"""
        if self._closed:
            raise RuntimeError("PersonaUpdater 已关闭")
        if self._bootstrap is None:
            self._bootstrap = create_managed_task(
                self._scan_missing(),
                name="engram_memory_persona_bootstrap",
                daemon=True,
            )

    async def enqueue(self, change: MemoryChanged) -> None:
        """按变化前后关联人物合并一次记忆变化。"""
        if self._closed:
            raise RuntimeError("PersonaUpdater 已关闭")
        people = set()
        for source_id in change.affected_person_ids:
            people.add(await self._canonical_person(source_id))
        if self._closed:
            raise RuntimeError("PersonaUpdater 已关闭")
        for person_id in people:
            self._pending.setdefault(person_id, []).append(change)
            self._attempts.pop(person_id, None)
        self._schedule()

    async def _canonical_person(self, person_id: str) -> str:
        """在任务进入待处理集合之前归一到核心人物标识。"""
        aliases = await self._repository.resolve_person_aliases(person_id)
        return next((item for item in aliases if ":" not in item), person_id)

    def _schedule(self) -> None:
        """唤醒唯一调度任务，处理等待中且未占用名额的人物。"""
        if self._closed:
            return
        self._wake.set()
        if self._pending and (
            self._task is None or self._task.task is None or self._task.task.done()
        ):
            self._task = create_managed_task(
                self._run(),
                name="engram_memory_persona_updater",
                daemon=True,
            )

    async def close(self) -> None:
        """取消扫描、调度、处理与延迟重试，等待后再释放数据库。"""
        self._closed = True
        handles = [
            self._task,
            self._bootstrap,
            *self._running.values(),
            *self._retries.values(),
        ]
        self._task = None
        current_task = asyncio.current_task()
        tasks = []
        for handle in handles:
            if (
                handle is not None
                and handle.task is not None
                and handle.task is not current_task
            ):
                if not handle.task.done():
                    cancel_managed_task(handle.task_id)
                tasks.append(handle.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._bootstrap = None
        self._pending.clear()
        self._running.clear()
        self._retries.clear()

    async def _scan_missing(self) -> None:
        """对当前 ACTIVE 主次人物去重，仅排队未认证或空印象的人物。"""
        try:
            seen: set[str] = set()
            for source_id in await self._service.get_active_person_ids():
                if self._closed:
                    return
                person_id = source_id
                try:
                    person_id = await self._canonical_person(source_id)
                    if person_id in seen:
                        continue
                    seen.add(person_id)
                    if (
                        person_id in self._pending
                        or person_id in self._running
                        or person_id in self._retries
                    ):
                        continue
                    persona = await self._service.get_persona(person_id)
                    if persona is not None and (
                        not persona.impression_text
                        or await self._service.needs_refresh(person_id)
                    ):
                        self._pending.setdefault(person_id, [])
                        self._schedule()
                except Exception as error:  # noqa: BLE001
                    self.last_errors[person_id] = error
                    logger.error(f"Persona 补建检查失败 {person_id}: {error}")
        except Exception as error:  # noqa: BLE001
            self.last_errors["bootstrap"] = error
            logger.error(f"Persona 补建扫描失败: {error}")

    async def _run(self) -> None:
        """将不同人物分派到共用名额，等待变化或任务完成后重新调度。"""
        try:
            while not self._closed:
                self._wake.clear()
                for person_id in tuple(self._pending):
                    if len(self._running) >= self._max_concurrency:
                        break
                    if (
                        person_id in self._running
                        or person_id in self._retries
                        or self._attempts.get(person_id, 0) > 2
                    ):
                        continue
                    batch = tuple(dict.fromkeys(self._pending.pop(person_id)))
                    self._running[person_id] = create_managed_task(
                        self._refresh_person(person_id, batch),
                        name=f"engram_memory_persona_update_{person_id}",
                        daemon=True,
                    )
                if not self._running:
                    return
                await self._wake.wait()
        finally:
            self._task = None

    async def _refresh_person(
        self, person_id: str, batch: tuple[MemoryChanged, ...]
    ) -> None:
        """执行一个人物批次，失败或过期时合并回队列并安排有限重试。"""
        try:
            result = await self._service.refresh(person_id, batch)
            if result is None:
                raise RuntimeError("人物印象输入在生成期间变化，结果已丢弃")
            self.last_errors.pop(person_id, None)
            self._attempts.pop(person_id, None)
        except Exception as error:  # noqa: BLE001
            self.last_errors[person_id] = error
            attempt = self._attempts.get(person_id, 0) + 1
            self._attempts[person_id] = attempt
            self._pending.setdefault(person_id, []).extend(batch)
            logger.error(f"Persona 刷新失败 {person_id}，尝试 {attempt}/3: {error}")
            if attempt <= 2 and not self._closed:
                self._retries[person_id] = create_managed_task(
                    self._retry_after(person_id, attempt),
                    name=f"engram_memory_persona_retry_{person_id}",
                    daemon=True,
                )
        finally:
            self._running.pop(person_id, None)
            self._schedule()

    async def _retry_after(self, person_id: str, attempt: int) -> None:
        """在不占用处理名额的延迟后重新唤醒人物任务。"""
        try:
            await asyncio.sleep(2**attempt)
        finally:
            self._retries.pop(person_id, None)
            self._schedule()
