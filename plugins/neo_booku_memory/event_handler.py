"""Neo Booku Memory 事件处理器。"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision

from .config import NeoBookuMemoryConfig
from .service import NeoMemoryService

logger = get_logger("neo_booku_memory_event_handler")

_FLASHBACK_TARGET_PROMPT = "default_chatter_user_prompt"
_MEMORY_TOOL_NAME = "memory_command"
_MEMORY_TOOL_WARNING = f"- *警告：检测到你连续多次没有使用记忆工具({_MEMORY_TOOL_NAME})，请保持对记忆的高频使用，无论是查询、新建还是整理记忆！*"

_SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".json", ".csv", ".log"}


# ======================================================================
# 工具使用跟踪
# ======================================================================

def _get_usage_warning_state(plugin: Any) -> dict[str, dict[str, Any]]:
    state = getattr(plugin, "_neo_booku_usage_warning_state", None)
    if isinstance(state, dict):
        return state
    state = {}
    setattr(plugin, "_neo_booku_usage_warning_state", state)
    return state


def _get_warning_threshold(plugin: Any) -> int:
    config = getattr(plugin, "config", None)
    if isinstance(config, NeoBookuMemoryConfig):
        return max(1, int(config.plugin.memory_tool_miss_warning_threshold))
    return 6


def _mark_memory_tool_usage(plugin: Any, *, stream_id: str, used_memory_tool: bool) -> None:
    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_stream_id:
        return
    state = _get_usage_warning_state(plugin)
    stream_state = state.setdefault(normalized_stream_id, {"miss_count": 0, "warning_pending": False})
    if used_memory_tool:
        stream_state["miss_count"] = 0
        stream_state["warning_pending"] = False
        return
    miss_count = int(stream_state.get("miss_count", 0)) + 1
    stream_state["miss_count"] = miss_count
    if miss_count >= _get_warning_threshold(plugin):
        stream_state["warning_pending"] = True


def _pop_memory_tool_warning(plugin: Any, *, stream_id: str) -> str:
    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_stream_id:
        return ""
    state = _get_usage_warning_state(plugin)
    stream_state = state.get(normalized_stream_id)
    if not isinstance(stream_state, dict) or not bool(stream_state.get("warning_pending")):
        return ""
    stream_state["warning_pending"] = False
    return _MEMORY_TOOL_WARNING


# ======================================================================
# 启动自动导入处理器
# ======================================================================

class NeoBookuMemoryStartupIngestHandler(BaseEventHandler):
    """程序启动后自动导入本地知识库文档并同步 system_reminder。"""

    name: str = "neo_booku_memory_startup_ingest"
    description: str = "程序启动时按配置路径自动导入文档到本地知识库"
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_START]
    dependencies: list[str] = []

    def _get_config(self) -> NeoBookuMemoryConfig:
        if isinstance(self.plugin.config, NeoBookuMemoryConfig):
            return self.plugin.config
        return NeoBookuMemoryConfig()

    def _collect_files(self, configured_paths: list[str], recursive: bool) -> list[Path]:
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
                    if not file.is_file() or file.suffix.lower() not in _SUPPORTED_SUFFIXES:
                        continue
                    resolved = file.resolve()
                    key = str(resolved).lower()
                    if key in seen:
                        continue
                    collected.append(resolved)
                    seen.add(key)
        return collected

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        config = self._get_config()
        startup = config.startup_ingest

        if not startup.enabled:
            service = NeoMemoryService(plugin=self.plugin)
            await service.sync_actor_reminder()
            return EventDecision.SUCCESS, params

        from src.kernel.concurrency import get_task_manager
        tm = get_task_manager()
        tm.create_task(self._run_startup_ingest(startup), name="neo_booku_startup_ingest_bg")
        return EventDecision.SUCCESS, params

    async def _run_startup_ingest(self, startup) -> None:
        targets = [item for item in startup.paths if isinstance(item, str)]
        files = self._collect_files(targets, recursive=bool(startup.recursive))
        service = NeoMemoryService(plugin=self.plugin)

        ingested = 0
        total = len(files)
        if total > 0:
            logger.info(f"Neo 启动导入开始: total={total}")

        for index, file_path in enumerate(files, start=1):
            stem = file_path.stem.strip().lower()
            title = f"《{stem}》"
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                await service.create_memory(
                    title=stem, content=text[:3000], bucket="knowledge",
                    core_tags=["知识库", "文档"], diffusion_tags=["自动导入"],
                    opposing_tags=["无关"],
                )
                ingested += 1
                logger.info(f"已导入: {title} index={index}/{total}")
            except Exception as exc:
                logger.error(f"Neo 启动导入失败: {index}/{total} {file_path} ({exc})")

        logger.info(f"Neo 启动自动导入完成: ingested={ingested}, total={total}")
        await service.sync_actor_reminder()


# ======================================================================
# 记忆闪回注入器
# ======================================================================

class NeoMemoryFlashbackInjector(BaseEventHandler):
    """记忆闪回注入器。"""

    name: str = "neo_memory_flashback_injector"
    description: str = "在 default_chatter user prompt extra 板块注入记忆闪回"
    weight: int = 10
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_PROMPT_BUILD]

    def __init__(self, plugin: Any) -> None:
        super().__init__(plugin)
        self._recent_flashbacks: dict[str, float] = {}

    def _prune_recent_flashbacks(self, now: float, cooldown_seconds: int) -> None:
        if cooldown_seconds <= 0:
            self._recent_flashbacks.clear()
            return
        expired = [mid for mid, ts in self._recent_flashbacks.items() if now - ts >= cooldown_seconds]
        for mid in expired:
            self._recent_flashbacks.pop(mid, None)

    @staticmethod
    def _format_flashback_block(memory_text: str) -> str:
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
        if params.get("name") != _FLASHBACK_TARGET_PROMPT:
            return EventDecision.SUCCESS, params

        from .flashback import activation_weight, pick_layer, should_trigger, weighted_choice

        config_obj = (
            self.plugin.config
            if isinstance(self.plugin.config, NeoBookuMemoryConfig)
            else NeoBookuMemoryConfig()
        )
        fb = config_obj.flashback
        if not fb.enabled:
            return EventDecision.SUCCESS, params

        if not should_trigger(trigger_probability=float(fb.trigger_probability), u=random.random()):
            return EventDecision.SUCCESS, params

        bucket = pick_layer(archived_probability=float(fb.archived_probability), u=random.random())
        service = NeoMemoryService(plugin=self.plugin)
        store = service._get_store()
        repo = await store._get_repo()

        records = await repo.list_records_by_bucket(
            bucket=bucket, limit=int(fb.candidate_limit), include_deleted=False,
        )

        cooldown_seconds = int(getattr(fb, "cooldown_seconds", 0) or 0)
        now = time.time()
        self._prune_recent_flashbacks(now=now, cooldown_seconds=cooldown_seconds)
        if cooldown_seconds > 0 and records:
            records = [
                r for r in records
                if str(getattr(r, "memory_id", "") or "") not in self._recent_flashbacks
            ]
            if not records:
                logger.info(
                    f"flashback 已触发但候选均处于冷却期（bucket={bucket}, cooldown_seconds={cooldown_seconds}）"
                )
                return EventDecision.SUCCESS, params

        if not records:
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
        params["values"] = values

        logger.info(f"已注入记忆闪回（bucket={bucket}, memory_id={picked_id}）")
        return EventDecision.SUCCESS, params


# ======================================================================
# 工具使用告警处理器
# ======================================================================

class NeoMemoryToolUsageWarningHandler(BaseEventHandler):
    """跟踪 actor 记忆工具使用情况，并按流注入一次性告警。"""

    name: str = "neo_memory_tool_usage_warning"
    description: str = "跟踪 actor 连续未使用 memory_command 的轮次，并向对应 prompt 注入告警"
    weight: int = 12
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_PROMPT_BUILD, EventType.AFTER_CHATTER_STEP]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        if event_name == EventType.AFTER_CHATTER_STEP:
            stream_id = str(params.get("stream_id") or "").strip()
            step_scope = str(params.get("step_scope") or "").strip()
            if step_scope != "actor_round":
                return EventDecision.SUCCESS, params
            used_tools = {
                str(item).strip()
                for item in params.get("used_tools", []) or []
                if str(item).strip()
            }
            _mark_memory_tool_usage(
                self.plugin, stream_id=stream_id,
                used_memory_tool=_MEMORY_TOOL_NAME in used_tools,
            )
            return EventDecision.SUCCESS, params

        if params.get("name") != _FLASHBACK_TARGET_PROMPT:
            return EventDecision.SUCCESS, params

        values = params.get("values")
        if not isinstance(values, dict):
            return EventDecision.SUCCESS, params

        stream_id = str(values.get("stream_id") or "").strip()
        warning_text = _pop_memory_tool_warning(self.plugin, stream_id=stream_id)
        if not warning_text:
            return EventDecision.SUCCESS, params

        existing_extra = str(values.get("extra") or "").strip()
        values["extra"] = f"{existing_extra}\n\n{warning_text}" if existing_extra else warning_text
        params["values"] = values
        logger.info(f"已向 stream={stream_id[:8]} 注入记忆工具使用告警")
        return EventDecision.SUCCESS, params
