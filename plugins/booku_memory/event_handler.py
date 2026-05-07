"""Booku Memory 事件处理器。"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision

from .config import BookuMemoryConfig
from .service.booku_knowledge_service import BookuKnowledgeService
from .service import sync_booku_knowledge_actor_reminder

logger = get_logger("booku_memory_event_handler")

if TYPE_CHECKING:
    from .service.metadata_repository import BookuMemoryMetadataRepository

# 目标模板：仅对 default_chatter user prompt 闪回注入
_FLASHBACK_TARGET_PROMPT = "default_chatter_user_prompt"

_SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".docx"}


def _service(plugin: Any) -> BookuKnowledgeService:
    """构建并返回绑定到指定插件实例的记忆服务对象。

    Args:
        plugin: 当前工具所属的插件实例，会被传递给 BookuKnowledgeService 构造函数。
            类型使用 Any 是因为工具基类未对 plugin 字段强制类型，实际运行时
            始终为 BasePlugin 子类实例。

    Returns:
        BookuKnowledgeService: 与该插件绑定的专业知识服务实例。
    """
    return BookuKnowledgeService(plugin=plugin)


class BookuMemoryStartupIngestHandler(BaseEventHandler):
    """程序启动后自动导入本地知识库文档。并注入system_reminder"""

    handler_name: str = "booku_memory_startup_ingest"
    handler_description: str = "程序启动时按配置路径自动导入文档到本地知识库"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_START]
    dependencies: list[str] = []

    def _get_config(self) -> BookuMemoryConfig:
        if isinstance(self.plugin.config, BookuMemoryConfig):
            return self.plugin.config
        return BookuMemoryConfig()

    def _collect_files(
        self, configured_paths: list[str], recursive: bool
    ) -> list[Path]:
        collected: list[Path] = []
        seen: set[str] = set()
        for raw_path in configured_paths:
            path_value = raw_path.strip()
            if not path_value:
                continue
            target = Path(path_value).expanduser().resolve()
            if target.is_file():
                suffix = target.suffix.lower()
                if suffix in _SUPPORTED_SUFFIXES:
                    key = str(target).lower()
                    if key not in seen:
                        collected.append(target)
                        seen.add(key)
                continue
            if target.is_dir():
                iterator = target.rglob("*") if recursive else target.glob("*")
                for file in iterator:
                    if not file.is_file():
                        continue
                    if file.suffix.lower() not in _SUPPORTED_SUFFIXES:
                        continue
                    resolved = file.resolve()
                    key = str(resolved).lower()
                    if key in seen:
                        continue
                    collected.append(resolved)
                    seen.add(key)
        return collected

    def _resolve_ingest_roots(self, configured_paths: list[str]) -> list[Path]:
        roots: list[Path] = []
        for raw_path in configured_paths:
            path_value = raw_path.strip()
            if not path_value:
                continue
            target = Path(path_value).expanduser().resolve()
            if target.is_dir():
                roots.append(target)
        roots.sort(key=lambda item: len(item.parts), reverse=True)
        return roots

    def _build_ingest_title(
        self, file_path: Path, roots: list[Path], recursive: bool
    ) -> str:
        stem = file_path.stem.strip().lower()
        if not recursive:
            return stem
        matched_root: Path | None = None
        for root in roots:
            try:
                file_path.relative_to(root)
                matched_root = root
                break
            except ValueError:
                continue
        if matched_root is None:
            return stem
        relative_parent = file_path.parent.relative_to(matched_root)
        if not relative_parent.parts:
            return stem
        parent_parts = [part.strip().lower() for part in relative_parent.parts if part.strip()]
        if not parent_parts:
            return stem
        return ":".join([*parent_parts, stem])

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        config = self._get_config()
        startup = config.startup_ingest

        if not startup.enabled:
            await sync_booku_knowledge_actor_reminder(self.plugin)
            return EventDecision.SUCCESS, params

        targets = [item for item in startup.paths if isinstance(item, str)]
        files = self._collect_files(targets, recursive=bool(startup.recursive))
        roots = self._resolve_ingest_roots(targets)
        service = _service(plugin=self.plugin)

        existing_titles = (
            set(await service.export_document_titles())
            if startup.skip_existing_title
            else set()
        )

        ingested = 0
        skipped = 0
        failed = 0
        total = len(files)
        if total > 0:
            logger.info(f"启动导入开始: total={total}")
        for raw_path in targets:
            path_value = raw_path.strip()
            if not path_value:
                continue
            target = Path(path_value).expanduser().resolve()
            if target.exists():
                continue
            if startup.skip_missing_paths:
                logger.warning(f"启动导入路径不存在，已跳过: {target}")
                skipped += 1
                continue
            logger.error(f"启动导入路径不存在: {target}")
            failed += 1

        for index, file_path in enumerate(files, start=1):
            title = self._build_ingest_title(
                file_path=file_path,
                roots=roots,
                recursive=bool(startup.recursive),
            )
            wrapped_title = f"《{title}》"
            if startup.skip_existing_title and wrapped_title in existing_titles:
                skipped += 1
                continue
            try:
                result = await service.ingest_document(
                    title=title,
                    file_path=str(file_path),
                    source="startup_event",
                )
                resolved_title = str(result.get("title", wrapped_title))
                existing_titles.add(resolved_title)
                ingested += 1
                logger.info(f"已导入: {resolved_title} chunks={int(result.get('chunk_count', 0))}, index={index}/{total}")
            except Exception as exc:
                logger.error(f"启动导入失败: {index}/{total} {file_path} ({exc})")
                failed += 1

        logger.info(
            f"启动自动导入完成: ingested={ingested}, skipped={skipped}, failed={failed}"
        )
        await sync_booku_knowledge_actor_reminder(self.plugin)
        return EventDecision.SUCCESS, params


class MemoryFlashbackInjector(BaseEventHandler):
    """记忆闪回注入器。

    订阅 ``on_prompt_build`` 事件，当 ``default_chatter_user_prompt``
    模板即将构建时，按配置概率触发“记忆闪回”，并在 ``values.extra``
    中追加一个 markdown 小节。

    闪回抽取规则：
    - 触发概率由 ``flashback.trigger_probability`` 决定；
    - 归档层/隐现层选择由 ``flashback.archived_probability`` 决定；
    - 在目标层中按 activation_count 反向加权抽取（激活次数低更易被抽到）。
    """

    handler_name: str = "memory_flashback_injector"
    handler_description: str = "在 default_chatter user prompt extra 板块注入记忆闪回"
    weight: int = 10
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_prompt_build"]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._repo = None
        self._repo_initialized = False
        self._recent_flashbacks: dict[str, float] = {}

    def _prune_recent_flashbacks(self, now: float, cooldown_seconds: int) -> None:
        """清理过期的近期闪回记录。"""

        if cooldown_seconds <= 0:
            self._recent_flashbacks.clear()
            return

        expired: list[str] = []
        for memory_id, ts in self._recent_flashbacks.items():
            if now - ts >= cooldown_seconds:
                expired.append(memory_id)

        for memory_id in expired:
            self._recent_flashbacks.pop(memory_id, None)

    async def _get_repo(self) -> "BookuMemoryMetadataRepository":
        from .service.metadata_repository import BookuMemoryMetadataRepository

        config = (
            self.plugin.config
            if isinstance(self.plugin.config, BookuMemoryConfig)
            else BookuMemoryConfig()
        )
        if self._repo is None:
            self._repo = BookuMemoryMetadataRepository(
                db_path=config.storage.metadata_db_path
            )
        if not self._repo_initialized:
            await self._repo.initialize()
            self._repo_initialized = True
        return self._repo

    @staticmethod
    def _format_flashback_block(memory_text: str) -> str:
        """将闪回内容格式化为注入块。"""

        text = (memory_text or "").strip()
        return (
            "## 记忆闪回\n"
            "就在刚才，你突然回忆起了一些事情：\n"
            f"{text}\n"
            "- 这是你无征兆的回忆起的东西，你可以按实际情况处理，可以选择忽视，也可以选择其他做法。\n"
            "- 注：这是你记忆中已经存在的内容，不需要重新写入。"
        )

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理 on_prompt_build 事件，按需向 extra 注入闪回内容。"""

        if params.get("name") != _FLASHBACK_TARGET_PROMPT:
            return EventDecision.SUCCESS, params

        from .flashback import (
            activation_weight,
            pick_layer,
            should_trigger,
            weighted_choice,
        )

        config_obj = (
            self.plugin.config
            if isinstance(self.plugin.config, BookuMemoryConfig)
            else BookuMemoryConfig()
        )
        fb = config_obj.flashback
        if not fb.enabled:
            return EventDecision.SUCCESS, params

        if not should_trigger(
            trigger_probability=float(fb.trigger_probability), u=random.random()
        ):
            return EventDecision.SUCCESS, params

        bucket = pick_layer(
            archived_probability=float(fb.archived_probability), u=random.random()
        )
        repo = await self._get_repo()

        records = await repo.list_records_by_bucket(
            bucket=bucket,
            limit=int(fb.candidate_limit),
            include_deleted=False,
        )

        cooldown_seconds = int(getattr(fb, "cooldown_seconds", 0) or 0)
        now = time.time()
        self._prune_recent_flashbacks(now=now, cooldown_seconds=cooldown_seconds)
        if cooldown_seconds > 0 and records:
            before_count = len(records)
            records = [
                r
                for r in records
                if str(getattr(r, "memory_id", "") or "") not in self._recent_flashbacks
            ]
            if not records:
                logger.info(
                    "flashback 已触发但候选均处于冷却期（"
                    f"bucket={bucket}, cooldown_seconds={cooldown_seconds}, candidates={before_count}）"
                )
                return EventDecision.SUCCESS, params

        if not records:
            logger.info(
                f"flashback 已触发但无候选记忆（bucket={bucket}, limit={int(fb.candidate_limit)}）"
            )
            return EventDecision.SUCCESS, params

        weights = [
            activation_weight(
                activation_count=int(getattr(r, "activation_count", 0)),
                exponent=float(fb.activation_weight_exponent),
            )
            for r in records
        ]
        picked = weighted_choice(records, weights, u=random.random())
        if picked is None:
            return EventDecision.SUCCESS, params

        picked_id = str(getattr(picked, "memory_id", "") or "")
        if cooldown_seconds > 0 and picked_id:
            self._recent_flashbacks[picked_id] = now

        values: dict[str, Any] = params.get("values", {})
        existing_extra: str = values.get("extra", "") or ""
        block = self._format_flashback_block(getattr(picked, "content", ""))
        separator = "\n\n" if existing_extra else ""
        values["extra"] = existing_extra + separator + block

        # 显式写回，确保上层读取到变更
        params["values"] = values

        logger.info(f"已注入记忆闪回（bucket={bucket}, memory_id={picked_id}）")
        return EventDecision.SUCCESS, params
