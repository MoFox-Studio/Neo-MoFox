"""Shameimaru Memory 事件处理器。

回复前注入器：订阅 ``on_prompt_build``，根据当前聊天流 unread message 中
出现的人物，将涉及的相关新闻与人物背景写入流私有 system reminder
（insert_type=dynamic，consume=forever），由 LLMContextManager 在每次
请求发送前动态注入，而不是直接修改 prompt。

注入位置与去重语义：
- ``dynamic`` reminder 会被注入到最后一条 user 消息（对话尾部）；
- 每次请求前，旧 reminder 文本会先从所有 user 消息中剥离，再注入新文本，
  因此全上下文中同一 reminder 始终只出现一次，不会重复累积。
"""

from __future__ import annotations

import time
from typing import Any

from src.app.plugin_system.api import log_api, prompt_api, service_api, stream_api
from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.core.prompt import SystemReminderConsumeType, SystemReminderInsertType
from src.kernel.event import EventDecision

from .config import ShameimaruMemoryConfig
from .recall import RecallCandidate, select_candidates
from .store import ShameimaruMemoryStore, shared_store
from .utils import format_local_time, person_id_of

logger = log_api.get_logger("shameimaru_memory.injector")

_NEWS_REMINDER_NAME = "相关新闻"
_PERSONA_REMINDER_NAME = "相关人物背景"

_NEWS_GUIDE_HEADER = (
    "以下是与当前对话中出现的相关人物有关的近期新闻记忆，供你回复时参考："
)
_NEWS_GUIDE_FOOTER = (
    "请自然地参考这些信息，仅在相关时提及，不要一次性罗列；"
    "这是你记忆中的内容，不要向对方说明这些信息的来源。"
)

_PERSONA_GUIDE_HEADER = (
    "以下是你对当前对话中出现的相关人物长期了解到的背景信息："
)
_PERSONA_GUIDE_FOOTER = (
    "请依据这些背景自然地理解对方、保持言行一致；"
    "这是你长期记忆的一部分，不要向对方透露你掌握这些信息的来源。"
)


class ShameimaruPromptInjector(BaseEventHandler):
    """回复前记忆注入器。

    订阅 ``on_prompt_build``，在 ``neo_default_chatter_user_prompt`` 构建时：
    1. 读取当前聊天流的 unread message 与最近历史消息，收集出现的人物 ID；
    2. 过滤新闻层中涉及相关人物的新闻，写入流私有 system reminder；
    3. 过滤人物层中涉及相关人物的人物背景信息，写入流私有 system reminder。

    无相关内容时删除对应 reminder，避免过期内容被继续注入。
    """

    name: str = "shameimaru_prompt_injector"
    description: str = "回复前根据当前对话中出现的人物，以 system reminder 注入相关新闻与人物背景"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_PROMPT_BUILD]
    dependencies: list[str] = []

    def _get_config(self) -> ShameimaruMemoryConfig:
        if isinstance(self.plugin.config, ShameimaruMemoryConfig):
            return self.plugin.config
        return ShameimaruMemoryConfig()

    def _build_store(self) -> ShameimaruMemoryStore:
        return shared_store(self.plugin, self._get_config)

    @staticmethod
    def _collect_unread_person_ids(
        stream: Any,
        history_limit: int = 20,
    ) -> set[str]:
        """收集当前对话中出现的人物 ID。

        优先读取 unread_messages；兼容已把 unread flush 进 history 的
        chatter 时序（neo_default_chatter 构建 prompt 前先 flush），
        再扫描最近 ``history_limit`` 条历史消息。排除 bot 自身消息。

        Args:
            stream: 聊天流实例。
            history_limit: 扫描最近历史消息的条数上限。
        """
        if stream is None:
            return set()
        context = getattr(stream, "context", None)
        if context is None:
            return set()
        person_ids: set[str] = set()

        def _collect(messages: Any) -> None:
            for message in messages or []:
                if str(getattr(message, "sender_role", "") or "").lower() == "bot":
                    continue
                person_id = person_id_of(message)
                if person_id:
                    person_ids.add(person_id)

        _collect(getattr(context, "unread_messages", None) or [])
        history = getattr(context, "history_messages", None) or []
        _collect(history[-max(0, int(history_limit)):])
        return person_ids

    @staticmethod
    def _sync_reminder(
        stream_id: str,
        bucket: str,
        name: str,
        content: str,
    ) -> None:
        """同步一条流私有 system reminder。

        有内容时写入（dynamic 注入到对话尾部）；无内容时删除，
        防止过期内容残留在后续请求中被重复注入。

        Args:
            stream_id: 聊天流 ID。
            bucket: reminder bucket（与 chatter 的 with_reminder 一致）。
            name: reminder 名称（同一 bucket 内唯一）。
            content: reminder 内容，空字符串表示删除。
        """
        if content:
            prompt_api.add_stream_reminder(
                stream_id,
                bucket,
                name,
                content,
                insert_type=SystemReminderInsertType.DYNAMIC,
                consume=SystemReminderConsumeType.FOREVER,
            )
        else:
            prompt_api.delete_stream_reminder(stream_id, bucket, name)

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件，按需同步相关记忆 reminder。"""
        config = self._get_config()
        if not bool(getattr(config.plugin, "enabled", True)):
            return EventDecision.SUCCESS, params
        injection = config.injection

        if params.get("name") != injection.prompt_name:
            return EventDecision.SUCCESS, params
        if not injection.inject_news and not injection.inject_personas:
            return EventDecision.SUCCESS, params

        values = params.get("values")
        if not isinstance(values, dict):
            return EventDecision.SUCCESS, params

        stream_id = str(values.get("stream_id") or "").strip()
        if not stream_id:
            return EventDecision.SUCCESS, params
        bucket = str(injection.bucket or "actor").strip()

        if injection.delegate_to_engram and service_api.get_service(
            "engram_memory:service:memory_service"
        ) is not None:
            self._sync_reminder(stream_id, bucket, _NEWS_REMINDER_NAME, "")
            self._sync_reminder(stream_id, bucket, _PERSONA_REMINDER_NAME, "")
            return EventDecision.SUCCESS, params

        stream = await stream_api.get_stream(stream_id)
        context_text = self._build_context_text(
            stream,
            int(getattr(injection, "person_scan_history_limit", 20) or 20),
        )
        person_ids = self._collect_unread_person_ids(
            stream,
            int(getattr(injection, "person_scan_history_limit", 20) or 20),
        )
        store = self._build_store()

        # 群聊流首次对话时注册到摘要文件，使摘要任务只对活跃群聊发起 LLM 调用
        if stream is not None and getattr(stream, "chat_type", "") == "group":
            await store.ensure_group(
                stream_id,
                platform=str(getattr(stream, "platform", "") or ""),
                group_name=str(getattr(stream, "stream_name", "") or ""),
            )

        news_matched = 0
        personas_matched = 0
        if injection.inject_news:
            if injection.recall_enabled:
                block, news_matched = await self._build_news_block(
                    store,
                    stream_id,
                    context_text,
                    person_ids,
                    int(injection.news_max_inject),
                    float(injection.recall_noise),
                    int(injection.recall_inhibition_seconds),
                )
            else:
                block, news_matched = await self._build_legacy_news_block(
                    store, person_ids, int(injection.news_max_inject)
                )
            self._sync_reminder(stream_id, bucket, _NEWS_REMINDER_NAME, block)

        if injection.inject_personas:
            block, personas_matched = await self._build_persona_block(
                store, person_ids, int(injection.persona_max_inject)
            )
            self._sync_reminder(stream_id, bucket, _PERSONA_REMINDER_NAME, block)

        logger.debug(
            f"已同步流私有 system reminder stream={stream_id[:8]} "
            f"bucket={bucket} persons={len(person_ids)} "
            f"news_matched={news_matched} personas_matched={personas_matched} "
            f"person_ids={sorted(person_ids)}"
        )
        return EventDecision.SUCCESS, params

    async def _build_legacy_news_block(
        self, store: ShameimaruMemoryStore, person_ids: set[str], max_inject: int
    ) -> tuple[str, int]:
        """前馈召回关闭时保留旧的按时间注入行为。"""
        news = await store.get_news()
        matched = [
            entry
            for entry in news
            if any(ref.person_id in person_ids for ref in entry.participants)
        ]
        matched.sort(key=lambda entry: entry.timestamp, reverse=True)
        matched = matched[:max_inject]
        if not matched:
            return "", 0
        lines = [
            f"- [{format_local_time(entry.timestamp)}] {entry.title}：{entry.content}"
            for entry in matched
        ]
        return (
            f"{_NEWS_GUIDE_HEADER}\n{chr(10).join(lines)}\n\n{_NEWS_GUIDE_FOOTER}",
            len(matched),
        )

    @staticmethod
    def _build_context_text(stream: Any, history_limit: int) -> str:
        """从当前上下文生成一次前馈召回的线索文本。"""
        if stream is None or getattr(stream, "context", None) is None:
            return ""
        context = stream.context
        messages = list(getattr(context, "unread_messages", None) or [])
        messages.extend((getattr(context, "history_messages", None) or [])[-history_limit:])
        return "\n".join(
            str(getattr(message, "processed_plain_text", "") or "").strip()
            for message in messages
            if str(getattr(message, "sender_role", "") or "").lower() != "bot"
            and str(getattr(message, "processed_plain_text", "") or "").strip()
        )

    async def _build_news_block(
        self,
        store: ShameimaruMemoryStore,
        stream_id: str,
        context_text: str,
        person_ids: set[str],
        max_inject: int,
        noise: float,
        inhibition_seconds: int,
    ) -> tuple[str, int]:
        """构建涉及当前人物的新闻注入块。

        Returns:
            tuple[str, int]: (注入块文本，匹配到的新闻条数；无匹配时块为空)。
        """
        news = await store.get_news()
        news = [entry for entry in news if not entry.stream_id or entry.stream_id == stream_id]
        candidates = [
            RecallCandidate(
                memory_id=entry.id,
                text=f"{entry.title} {entry.content}",
                timestamp=entry.timestamp,
                person_ids=frozenset(ref.person_id for ref in entry.participants),
            )
            for entry in news
        ]
        state = await store.get_recall_state(stream_id)
        selected = select_candidates(
            candidates,
            query=context_text,
            person_ids=person_ids,
            state=state,
            stream_id=stream_id,
            limit=max_inject,
            now=time.time(),
            noise=noise,
            inhibition_seconds=inhibition_seconds,
        )
        selected_ids = {candidate.memory_id for candidate in selected}
        matched = [entry for entry in news if entry.id in selected_ids]
        by_id = {entry.id: entry for entry in matched}
        matched = [by_id[candidate.memory_id] for candidate in selected]
        await store.record_recall(
            stream_id,
            [candidate.memory_id for candidate in selected],
            recalled_at=time.time(),
        )
        if not matched:
            return "", 0

        lines: list[str] = []
        for entry in matched:
            clock = format_local_time(entry.timestamp)
            lines.append(f"- [{clock}] {entry.title}：{entry.content}")
        body = "\n".join(lines)
        return f"{_NEWS_GUIDE_HEADER}\n{body}\n\n{_NEWS_GUIDE_FOOTER}", len(matched)

    async def _build_persona_block(
        self, store: ShameimaruMemoryStore, person_ids: set[str], max_inject: int
    ) -> tuple[str, int]:
        """构建涉及当前人物的人物背景信息注入块。

        Returns:
            tuple[str, int]: (注入块文本，匹配到的人物条数；无匹配时块为空)。
        """
        all_personas = await store.get_all_personas()
        matched = [
            (person_id, text)
            for person_id, text in all_personas.items()
            if person_id in person_ids and text
        ]
        matched.sort(key=lambda item: len(item[1]), reverse=True)
        matched = matched[:max_inject]
        if not matched:
            return "", 0

        lines: list[str] = []
        for person_id, text in matched:
            name = self._person_display_name(person_id)
            lines.append(f"{name}（{person_id}）：\n{text}")
        body = "\n\n".join(lines)
        return (
            f"{_PERSONA_GUIDE_HEADER}\n\n{body}\n\n{_PERSONA_GUIDE_FOOTER}",
            len(matched),
        )

    def _person_display_name(self, person_id: str) -> str:
        """从人物 ID 推断展示名称（无昵称表时退回 ID 末段）。"""
        if ":" in person_id:
            return person_id.rsplit(":", 1)[-1]
        return person_id
