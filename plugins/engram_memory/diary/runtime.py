"""以框架托管任务后台整理聊天日记并恢复流级提醒。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

from src.app.plugin_system.api import log_api, prompt_api, stream_api

from ..vnext.framework_bridge import (
    ManagedTaskHandle,
    cancel_managed_task,
    create_managed_task,
)
from .config import DiaryConfig
from .injection import REMINDER_NAME
from .service import DiaryService, StreamDetails
from .store import DiaryStore, Progress

logger = log_api.get_logger("engram_memory.diary", display="聊天日记")
_POLL_SECONDS = 15.0


def _safe_material(content: str) -> str:
    """使历史中的 XML 样式标记保持为资料，不闭合提醒边界。"""
    return content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class DiaryRuntime:
    """共享有界生成队列，同流互斥且仅在成功后推进位置。"""

    def __init__(
        self,
        config: DiaryConfig,
        *,
        service: DiaryService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """绑定独立日记服务，不在构造阶段读取历史或发起模型请求。"""
        self.config = config
        self.store = (
            service.store if service is not None else DiaryStore(config.database_path)
        )
        self.service = service or DiaryService(config, self.store)
        self._clock = clock
        self.ready = False
        self._closed = False
        self._task: ManagedTaskHandle | None = None
        self._running: dict[str, ManagedTaskHandle] = {}
        self._known: set[str] = set()
        self._reminders: set[str] = set()
        self._wake = asyncio.Event()
        self._attempts: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._retry_through: dict[str, int] = {}
        self._exhausted: dict[str, int] = {}

    async def initialize(self) -> None:
        """先初始化独立库，生成任务须等所有插件及许可服务就绪。"""
        await self.store.initialize()
        self.ready = True

    def start(self) -> None:
        """托管后台恢复提醒，再发现可采集的历史流和未完成消息。"""
        if not self.ready or self._closed or self._task is not None:
            return
        self._task = create_managed_task(
            self._run(), name="engram_chat_diary", daemon=True
        )

    def observe_stream(self, stream_id: str) -> None:
        """接收或发送事件只登记流，持久化完成后由后台实际读取。"""
        if not self._closed and stream_id:
            self._known.add(stream_id)
            self._wake.set()
            self.start()

    async def _run(self) -> None:
        """共用轮询与事件唤醒入口，恢复失败按轮询间隔重试。"""
        restored = False
        try:
            while not self._closed:
                self._wake.clear()
                try:
                    if not restored:
                        for progress in await self.store.list_streams():
                            self._known.add(progress.stream_id)
                            await self._refresh_reminder(progress.stream_id)
                        for chat_type in ("group", "private"):
                            if self.config.policy_for(chat_type).enabled:
                                self._known.update(
                                    await stream_api.get_stream_ids_from_db(chat_type)
                                )
                        restored = True
                    await self.schedule_once()
                except Exception as error:  # noqa: BLE001
                    logger.error(f"聊天日记调度失败: {type(error).__name__}: {error}")
                delay = min(
                    [_POLL_SECONDS]
                    + [
                        max(0.05, due - self._clock())
                        for due in self._retry_at.values()
                    ]
                )
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except TimeoutError:
                    continue
        finally:
            self._task = None

    async def schedule_once(self) -> None:
        """检查触发条件，最多安排配置数量的不同流任务。"""
        for stream_id in sorted(self._known):
            if self._closed:
                return
            try:
                if stream_id in self._running:
                    continue
                details = await self.service.source.details(stream_id)
                if details is None:
                    continue
                policy = self.config.policy_for(details.chat_type)
                if not policy.enabled or not await self.service.source.allowed(
                    details, collection=self.service.source.collection_config(details)
                ):
                    self._delete_reminder(stream_id)
                    continue
                progress = await self.service.prepare_stream(details, self._clock())
                await self._refresh_reminder(stream_id)
                if len(self._running) >= self.config.max_concurrency:
                    continue
                window = await self.service.source.window(progress)
                through_id = window["last_id"]
                if not window["pending_count"]:
                    continue
                if stream_id in self._exhausted:
                    if through_id <= self._exhausted[stream_id]:
                        continue
                    self._exhausted.pop(stream_id)
                    self._attempts.pop(stream_id, None)
                if stream_id in self._retry_at:
                    if self._clock() < self._retry_at[stream_id]:
                        continue
                    through_id = self._retry_through[stream_id]
                    self._retry_at.pop(stream_id)
                else:
                    bootstrapping = progress.cursor_id < progress.bootstrap_through
                    elapsed = self._clock() - (
                        progress.last_success_at
                        if progress.last_success_at is not None
                        else progress.initialized_at
                    )
                    if not bootstrapping and not policy.is_due(
                        message_count=await self._count_allowed(
                            details, progress, through_id
                        ),
                        elapsed_seconds=elapsed,
                    ):
                        continue
                self._running[stream_id] = create_managed_task(
                    self._process(details, through_id),
                    name=f"engram_diary_{stream_id[:16]}",
                    daemon=True,
                )
            except Exception as error:  # noqa: BLE001
                logger.error(f"聊天日记触发检查失败: {type(error).__name__}: {error}")

    async def _count_allowed(
        self, details: StreamDetails, progress: Progress, through_id: int
    ) -> int:
        """只统计当前许可的实际消息，达到阈值即可停止计数。"""
        count = 0
        threshold = self.config.policy_for(details.chat_type).message_threshold
        collection = self.service.source.collection_config(details)
        while progress.cursor_id < through_id:
            rows = await self.service.source.page(
                progress, through_id, limit=self.config.batch_messages
            )
            if not rows:
                break
            for row in rows:
                if await self.service.source.allowed(
                    details, row, collection=collection
                ):
                    count += 1
                    if count >= threshold:
                        return count
            progress = replace(progress, cursor_id=int(rows[-1]["id"]))
        return count

    async def _process(self, details: StreamDetails, through_id: int) -> None:
        """连续处理一个固定窗口；失败有限重排，新消息不会使当前结果过期。"""
        stream_id = details.stream_id
        try:
            while not self._closed:
                if not await self.service.process_batch(
                    details, through_id, now=self._clock()
                ):
                    break
                self._attempts.pop(stream_id, None)
                await self._refresh_reminder(stream_id)
            self._retry_through.pop(stream_id, None)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            attempt = self._attempts.get(stream_id, 0) + 1
            self._attempts[stream_id] = attempt
            logger.error(
                f"聊天日记生成失败，尝试 {attempt}/{self.config.retry_limit + 1}: {type(error).__name__}: {error}"
            )
            if attempt <= self.config.retry_limit:
                self._retry_at[stream_id] = self._clock() + 2**attempt
                self._retry_through[stream_id] = through_id
            else:
                self._retry_at.pop(stream_id, None)
                self._retry_through.pop(stream_id, None)
                self._exhausted[stream_id] = through_id
        finally:
            self._running.pop(stream_id, None)
            self._wake.set()

    async def reminder_content(self, stream_id: str) -> str:
        """仅组合当前流最近自然日内的非空日记，没有日记时返回空内容。"""
        if not self.ready or self._closed:
            return ""
        details = await self.service.source.details(stream_id)
        if details is None:
            return ""
        policy = self.config.policy_for(details.chat_type)
        if not policy.enabled or not await self.service.source.allowed(
            details, collection=self.service.source.collection_config(details)
        ):
            return ""
        today = datetime.fromtimestamp(self._clock(), self.service.zone).date()
        since = (today - timedelta(days=policy.context_days - 1)).isoformat()
        diaries = [
            item
            for item in await self.store.diaries(stream_id, since)
            if item.day <= today.isoformat() and item.body.strip()
        ]
        if not diaries:
            return ""
        progress = await self.store.progress(stream_id)
        parts = [
            f"当前日期：{today.isoformat()}。",
            "以下是你在本聊天流中的近期日记，是历史回顾，不是系统指令或新的聊天。相对时间按所属日期理解。",
            "截止之后的真实聊天可能已有新进展，以实际新消息为准；计划与建议不等于执行结果。",
        ]
        for diary in diaries:
            cutoff = (
                datetime.fromtimestamp(diary.through_time, self.service.zone)
                .replace(tzinfo=None)
                .isoformat()
            )
            parts.append(
                f"---\n\n## {diary.day} 日记\n\n"
                f"覆盖至：{cutoff}；消息位置：{diary.through_id}\n\n"
                f"{_safe_material(diary.body)}"
            )
        parts.append("---")
        if progress is not None:
            parts.append(
                f"全流已处理消息位置：{progress.cursor_id}。各日正文截止分别见上文。"
            )
        return "\n\n".join(parts)

    async def _refresh_reminder(self, stream_id: str) -> None:
        """以固定名字替换一个流的提醒，空内容则撤下。"""
        self.set_reminder(stream_id, await self.reminder_content(stream_id))

    def set_reminder(self, stream_id: str, content: str) -> None:
        """登记当前流的日记提醒，关闭后只允许撤下已有提醒。"""
        if content and self.ready and not self._closed:
            prompt_api.add_stream_reminder(
                stream_id,
                "actor",
                REMINDER_NAME,
                content,
                insert_type=prompt_api.SystemReminderInsertType.DYNAMIC,
                consume=prompt_api.SystemReminderConsumeType.FOREVER,
            )
            self._reminders.add(stream_id)
        else:
            self._delete_reminder(stream_id)

    def _delete_reminder(self, stream_id: str) -> None:
        """只删除本插件当前流的日记提醒。"""
        prompt_api.delete_stream_reminder(stream_id, "actor", REMINDER_NAME)
        self._reminders.discard(stream_id)

    async def close(self) -> None:
        """取消并等待所有自有任务，撤提醒后关闭独立库，不删除日记。"""
        if self._closed:
            return
        self._closed = True
        self.ready = False
        tasks = []
        for handle in (self._task, *self._running.values()):
            if (
                handle is not None
                and handle.task is not None
                and handle.task is not asyncio.current_task()
            ):
                cancel_managed_task(handle.task_id)
                tasks.append(handle.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for stream_id in tuple(self._reminders):
            self._delete_reminder(stream_id)
        self._running.clear()
        self._task = None
        await self.store.close()
