"""Booku Memory 写入工作流工具。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from json_repair import repair_json

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.core.components import BaseTool
from src.kernel.concurrency import get_task_manager
from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.logger import get_logger

from ..config import PREDEFINED_FOLDERS, BookuMemoryConfig
from ..agent.shared import get_internal_task_name
from ..agent.tools import (
    BookuMemoryCreateTool,
    BookuMemoryReadFullContentTool,
    BookuMemoryRetrieveTool,
    BookuMemoryUpdateByIdTool,
)

logger = get_logger("booku_memory_write_tool")

_FOLDER_IDS = Literal[
    "relations", "plans", "facts", "preferences", "events", "work", "default"
]
_BUCKET_IDS = Literal["emergent", "archived"]


class BookuMemoryWriteTool(BaseTool):
    """固定工作流的记忆写入工具。

    该工具对外暴露统一入口 ``memory_write``，内部显式编排：
    - memory_retrieve（判重候选检索）
    - memory_read_full_content（读取候选全文）
    - memory_create（新增写入）
    - memory_update_by_id（按 id 更新）
    """

    tool_name: str = "memory_write"
    tool_description: str = (
        "用固定工作流写入或更新记忆，包含判重、标签生成与审计摘要输出。"
    )

    async def execute(
        self,
        title: Annotated[str, "记忆标题，简短描述内容主题"],
        content: Annotated[str, "记忆正文内容，应为可复用事实/偏好/约束/结论"],
        core_tags: Annotated[list[str], "核心语义标签"],
        diffusion_tags: Annotated[list[str], "扩散联想标签"],
        opposing_tags: Annotated[list[str], "对立标签"],
        folder: Annotated[
            _FOLDER_IDS,
            "目标记忆文件夹：relations/plans/facts/preferences/events/work/default",
        ] = "default",
        bucket_hint: Annotated[
            _BUCKET_IDS,
            "建议写入层级：emergent/archived",
        ] = "emergent",
    ) -> tuple[bool, str | dict[str, Any]]:
        """提交写入任务入口。

        该入口默认以后台任务执行写入，以减少主链路等待时间；
        真正的写入逻辑由 ``_execute_workflow`` 完成，并在共享锁内串行化。

        Args:
            title: 记忆标题，简短描述内容主题。
            content: 记忆正文内容，应为可复用事实/偏好/约束/结论。
            core_tags: 核心语义标签。
            diffusion_tags: 扩散联想标签。
            opposing_tags: 对立标签。
            folder: 目标文件夹；传 default 时流程可能自动选择更合适的文件夹。
            bucket_hint: 写入层级建议，仅用于 create 分支。

        Returns:
            成功提交后台任务时返回 ``(True, message)``；
            输入非法时返回 ``(False, error_message)``。
        """
        normalized_title = (title or "").strip()
        body_content = (content or "").strip()
        if not body_content and normalized_title:
            body_content = normalized_title
        if not normalized_title and not body_content:
            return False, "title 与 content 至少需要提供一个有效内容"

        task_name = (
            f"{getattr(self.plugin, 'plugin_name', 'unknown_plugin')}:memory_write"
        )
        get_task_manager().create_task(
            self._background_write(
                title=normalized_title,
                content=body_content,
                core_tags=core_tags,
                diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
                folder=folder,
                bucket_hint=bucket_hint,
                task_name=task_name,
            ),
            name=task_name,
            daemon=True,
        )

        display_name = normalized_title or body_content[:20]
        return True, f"已提交后台写入任务：{display_name}"

    async def _execute_workflow(
        self,
        title: Annotated[str, "记忆标题，简短描述内容主题"],
        content: Annotated[str, "记忆正文内容，应为可复用事实/偏好/约束/结论"],
        core_tags: Annotated[list[str], "核心语义标签"],
        diffusion_tags: Annotated[list[str], "扩散联想标签"],
        opposing_tags: Annotated[list[str], "对立标签"],
        folder: Annotated[
            _FOLDER_IDS,
            "目标记忆文件夹：relations/plans/facts/preferences/events/work/default",
        ] = "default",
        bucket_hint: Annotated[
            _BUCKET_IDS,
            "建议写入层级：emergent/archived",
        ] = "emergent",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行写记忆流程入口。

        Args:
            title: 记忆标题，简短描述内容主题。
            content: 记忆正文内容，应为可复用事实/偏好/约束/结论。
            core_tags: 核心标签。
            diffusion_tags: 扩散标签。
            opposing_tags: 对立标签。
            folder: 目标记忆文件夹。
            bucket_hint: 写入层级建议。

        Returns:
            成功时返回 ``(True, result_text)``；
            输入非法时返回 ``(False, error_message)``。
        """
        # 阶段1：输入规整，保证 title/content 至少一个有效内容。
        normalized_title = (title or "").strip()
        body_content = (content or "").strip()
        if not body_content and normalized_title:
            body_content = normalized_title
        if not normalized_title and not body_content:
            return False, "title 与 content 至少需要提供一个有效内容"

        # 阶段2：合并为对比文本，用于判重检索与语义比较。
        merged_for_compare = (
            f"# {normalized_title}\n{body_content}"
            if normalized_title and body_content
            else (normalized_title or body_content)
        )

        # 阶段3：基础配置与检索标签兜底（用于判重召回）。
        config = self._get_config()
        retrieve_tags = {
            "core_tags": core_tags,
            "diffusion_tags": diffusion_tags,
            "opposing_tags": opposing_tags,
        }

        # 阶段4：防重复检索，找强相关候选（分数超过阈值才进入下一步）。
        strong_candidate = await self._find_strong_candidate(
            merged_for_compare,
            retrieve_tags=retrieve_tags,
            config=config,
        )

        # 阶段5：冲突/重复判断，有强相关候选时才读取全文并快速对比。
        branch = "create"
        target_memory: dict[str, Any] | None = None
        relation = "none"
        if strong_candidate is not None:
            target_memory = await self._read_candidate_full_content(strong_candidate)
            if target_memory:
                relation = await self._quick_compare(
                    new_content=merged_for_compare,
                    existing_content=str(target_memory.get("content", "") or ""),
                    config=config,
                )
                if relation in {"conflict", "duplicate"}:
                    branch = "update"

        # 阶段6：标签与时间处理，仅在必要时调用轻量子模型，减少开销。
        tags_ready = self._normalize_tags(core_tags)
        diffusion_ready = self._normalize_tags(diffusion_tags)
        opposing_ready = self._normalize_tags(opposing_tags)
        needs_tags = not tags_ready or not diffusion_ready or not opposing_ready
        needs_time = self._needs_time_resolution(body_content)

        if needs_tags or needs_time:
            payload = await self._generate_tags_and_resolve_time(
                title=normalized_title,
                content=body_content,
                core_tags=tags_ready,
                diffusion_tags=diffusion_ready,
                opposing_tags=opposing_ready,
                now=datetime.now(),
                config=config,
            )
            body_content = payload.get("content", body_content)
            tags_ready = self._normalize_tags(payload.get("core_tags")) or tags_ready
            diffusion_ready = (
                self._normalize_tags(payload.get("diffusion_tags")) or diffusion_ready
            )
            opposing_ready = (
                self._normalize_tags(payload.get("opposing_tags")) or opposing_ready
            )

        # 阶段7：兜底标签，保证三角标签合规（每类至少一个非空字符串）。
        if not tags_ready:
            tags_ready = ["记忆"]
        if not diffusion_ready:
            diffusion_ready = ["对话"]
        if not opposing_ready:
            opposing_ready = ["无关"]

        # 阶段8：文件夹与摘要，仅在 folder=default 时才调用辅助选择。
        resolved_folder = folder
        summary_text = ""
        if resolved_folder == "default":
            folder_payload = await self._select_folder_and_summary(
                title=normalized_title,
                content=body_content,
                core_tags=tags_ready,
                diffusion_tags=diffusion_ready,
                opposing_tags=opposing_ready,
                config=config,
            )
            folder_candidate = str(folder_payload.get("folder_id", "")).strip()
            if folder_candidate in PREDEFINED_FOLDERS:
                resolved_folder = folder_candidate
            summary_text = str(folder_payload.get("summary", "") or "").strip()

        # 阶段9：分支执行写入：新增走 create，冲突/重复走 update。
        if branch == "create":
            success, result = await BookuMemoryCreateTool(self.plugin).execute(
                title=normalized_title or body_content[:20],
                content=body_content,
                bucket=bucket_hint,
                folder_id=resolved_folder,
                core_tags=tags_ready,
                diffusion_tags=diffusion_ready,
                opposing_tags=opposing_ready,
            )
        else:
            if not target_memory or not target_memory.get("id"):
                return False, "无法定位需要更新的记忆 id"
            updated_content = body_content
            if relation == "duplicate":
                # duplicate 分支：尽量合并新旧内容，避免信息丢失与重复堆叠。
                updated_content = await self._merge_content(
                    new_content=body_content,
                    existing_content=str(target_memory.get("content", "") or ""),
                    config=config,
                )
            success, result = await BookuMemoryUpdateByIdTool(self.plugin).execute(
                id=str(target_memory.get("id")),
                content=updated_content,
                title=normalized_title or None,
                core_tags=tags_ready,
                diffusion_tags=diffusion_ready,
                opposing_tags=opposing_ready,
            )

        # 阶段10：汇总审计摘要并返回（便于日志追踪与调用方回显）。
        summary = self._build_audit_summary(
            success=bool(success),
            branch=branch,
            title=normalized_title,
            summary=summary_text,
            tags=(tags_ready, diffusion_ready, opposing_ready),
        )

        if success:
            logger.info(
                "记忆写入成功",
                branch=branch,
                title=normalized_title,
                tags=(tags_ready, diffusion_ready, opposing_ready),
            )
            return True, summary
        logger.error(
            "记忆写入失败",
            branch=branch,
            title=normalized_title,
            tags=(tags_ready, diffusion_ready, opposing_ready),
            error=result,
        )
        return False, {"error": result, "summary": summary}

    async def _background_write(
        self,
        *,
        title: str,
        content: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
        folder: _FOLDER_IDS,
        bucket_hint: _BUCKET_IDS,
        task_name: str,
    ) -> None:
        """后台写入任务主体。

        在共享锁内串行化写入，避免与读流程或其他写入任务互相干扰。
        """
        lock = self._get_write_lock()
        async with lock:
            try:
                success, result = await self._execute_workflow(
                    title=title,
                    content=content,
                    core_tags=core_tags,
                    diffusion_tags=diffusion_tags,
                    opposing_tags=opposing_tags,
                    folder=folder,
                    bucket_hint=bucket_hint,
                )
                if not success:
                    logger.error(
                        "记忆写入后台任务失败",
                        task_name=task_name,
                        title=title,
                        error=result,
                    )
            except Exception:
                logger.error(
                    "记忆写入后台任务异常",
                    task_name=task_name,
                    title=title,
                    exc_info=True,
                )

    def _get_write_lock(self) -> asyncio.Lock:
        """获取读写共享锁。"""
        lock = getattr(self.plugin, "_booku_memory_write_lock", None)
        if isinstance(lock, asyncio.Lock):
            return lock
        lock = asyncio.Lock()
        setattr(self.plugin, "_booku_memory_write_lock", lock)
        return lock

    def _get_config(self) -> BookuMemoryConfig:
        """读取插件配置，缺失时返回默认配置对象。"""
        if isinstance(self.plugin.config, BookuMemoryConfig):
            return self.plugin.config
        return BookuMemoryConfig()

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        """清理标签列表，仅保留非空字符串。"""
        if not tags:
            return []
        return [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]

    def _ensure_retrieve_tags(
        self,
        *,
        core_tags: list[str] | None,
        diffusion_tags: list[str] | None,
        opposing_tags: list[str] | None,
    ) -> dict[str, list[str]]:
        """为判重检索构造可用的三角标签集合。"""
        core = self._normalize_tags(core_tags) or ["记忆"]
        diffusion = self._normalize_tags(diffusion_tags) or ["对话"]
        opposing = self._normalize_tags(opposing_tags) or ["无关"]
        return {
            "core_tags": core,
            "diffusion_tags": diffusion,
            "opposing_tags": opposing,
        }

    async def _find_strong_candidate(
        self,
        query_text: str,
        *,
        retrieve_tags: dict[str, list[str]],
        config: BookuMemoryConfig,
    ) -> dict[str, Any] | None:
        """检索强相关候选项，用于快速判重/冲突分支决策。"""
        success, result = await BookuMemoryRetrieveTool(self.plugin).execute(
            query_text=query_text,
            core_tags=retrieve_tags["core_tags"],
            diffusion_tags=retrieve_tags["diffusion_tags"],
            opposing_tags=retrieve_tags["opposing_tags"],
            topk=config.write_conflict.top_n,
            include_archived=True,
            include_knowledge=False,
            folder_id=None,
        )
        if not success or not isinstance(result, dict):
            return None
        items = result.get("items") or result.get("results") or []
        if not isinstance(items, list):
            return None
        threshold = float(config.retrieval.deduplication_threshold)
        best_item: dict[str, Any] | None = None
        best_score = -1.0
        for item in items:
            if not isinstance(item, dict):
                continue
            score = float(item.get("score", 0.0) or 0.0)
            if score >= threshold and score > best_score:
                best_score = score
                best_item = item
        return best_item

    async def _read_candidate_full_content(
        self, candidate: dict[str, Any]
    ) -> dict[str, Any] | None:
        """读取候选记忆的完整内容。"""
        memory_id = str(candidate.get("id") or candidate.get("memory_id") or "")
        if not memory_id:
            return None
        success, result = await BookuMemoryReadFullContentTool(self.plugin).execute(
            ids=[memory_id]
        )
        if not success or not isinstance(result, dict):
            return None
        items = result.get("items") or []
        if not isinstance(items, list) or not items:
            return None
        item = items[0]
        if not isinstance(item, dict):
            return None
        return {
            "id": memory_id,
            "content": item.get("content", ""),
        }

    async def _quick_compare(
        self,
        *,
        new_content: str,
        existing_content: str,
        config: BookuMemoryConfig,
    ) -> str:
        """快速判断新旧记忆关系：conflict/duplicate/none。"""
        prompt = (
            "你是记忆对比器，只判断两段记忆是否冲突或重复。"
            "只返回 JSON，不要额外文本。"
            "字段：relation=conflict|duplicate|none。"
        )
        payload = {
            "new_content": new_content,
            "existing_content": existing_content,
        }
        data = await self._call_llm_json(prompt, payload, config=config)
        relation = str(data.get("relation", "")).strip().lower()
        if relation in {"conflict", "duplicate", "none"}:
            return relation
        return "none"

    def _needs_time_resolution(self, text: str) -> bool:
        """判断文本是否包含相对时间表达。"""
        if not text:
            return False
        patterns = [
            "今天",
            "明天",
            "后天",
            "昨天",
            "前天",
            "本周",
            "下周",
            "上周",
            "本月",
            "下个月",
            "上个月",
            "今年",
            "明年",
            "最近",
            "今晚",
            "今早",
            "明早",
            "明晚",
            "后天",
            "下周末",
            "本周末",
        ]
        return any(token in text for token in patterns)

    async def _generate_tags_and_resolve_time(
        self,
        *,
        title: str,
        content: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
        now: datetime,
        config: BookuMemoryConfig,
    ) -> dict[str, Any]:
        """调用轻量子模型补全标签并规整时间表达。"""
        prompt = (
            "你是记忆标签与时间归一化助手。"
            "只返回 JSON，不要额外文本。"
            "字段：core_tags,diffusion_tags,opposing_tags,content,changed_time。"
            "如果已有标签非空就保留或轻量补充；不要输出空列表。"
            "如内容含相对时间，必须改写为绝对日期（YYYY-MM-DD）。"
        )
        payload = {
            "title": title,
            "content": content,
            "core_tags": core_tags,
            "diffusion_tags": diffusion_tags,
            "opposing_tags": opposing_tags,
            "current_time": now.strftime("%Y-%m-%d %H:%M"),
            "valid_folders": list(PREDEFINED_FOLDERS.keys()),
        }
        data = await self._call_llm_json(prompt, payload, config=config)
        if not isinstance(data, dict):
            return {}
        return data

    async def _select_folder_and_summary(
        self,
        *,
        title: str,
        content: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
        config: BookuMemoryConfig,
    ) -> dict[str, Any]:
        """在 folder=default 时辅助选择 folder 并生成简短摘要。"""
        prompt = (
            "你是文件夹选择器与摘要助手。"
            "只返回 JSON，不要额外文本。"
            "字段：folder_id,summary。"
            "folder_id 必须在 relations/plans/facts/preferences/events/work/default 之中。"
            "summary 不超过30字。"
        )
        payload = {
            "title": title,
            "content": content,
            "core_tags": core_tags,
            "diffusion_tags": diffusion_tags,
            "opposing_tags": opposing_tags,
        }
        data = await self._call_llm_json(prompt, payload, config=config)
        if not isinstance(data, dict):
            return {}
        return data

    async def _merge_content(
        self,
        *,
        new_content: str,
        existing_content: str,
        config: BookuMemoryConfig,
    ) -> str:
        """在 duplicate 分支合并新旧内容，减少重复并保留要点。"""
        prompt = (
            "你是记忆合并助手。"
            "只返回 JSON，不要额外文本。"
            "字段：content。"
            "要求：保留关键细节，不重复，语句通顺。"
        )
        payload = {
            "new_content": new_content,
            "existing_content": existing_content,
        }
        data = await self._call_llm_json(prompt, payload, config=config)
        merged = str(data.get("content", "")).strip()
        if merged:
            return merged
        return new_content

    async def _call_llm_json(
        self, system_prompt: str, payload: dict[str, Any], *, config: BookuMemoryConfig
    ) -> dict[str, Any]:
        """调用内部模型并解析 JSON 响应。

        解析策略：
        1. 直接 json.loads；
        2. 失败后使用 json_repair 修复并重试；
        3. 仍失败则返回空字典。
        """
        model_set = get_model_set_by_task(get_internal_task_name(config))
        request = create_llm_request(
            model_set=model_set,
            request_name="booku_memory_write_workflow",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(
            LLMPayload(ROLE.USER, Text(json.dumps(payload, ensure_ascii=False)))
        )
        response = await request.send(stream=False)
        await response
        message = (response.message or "").strip()
        if not message:
            return {}
        try:
            return json.loads(message)
        except Exception:
            try:
                repaired = repair_json(message)
                return json.loads(repaired)
            except Exception:
                logger.warning("无法解析子任务 JSON 输出")
                return {}

    @staticmethod
    def _build_audit_summary(
        *,
        success: bool,
        branch: str,
        title: str,
        summary: str,
        tags: tuple[list[str], list[str], list[str]],
    ) -> str:
        """生成审计摘要，便于调用方回显与日志定位。"""
        operation = "新建" if branch == "create" else "更新"
        result_text = "成功" if success else "失败"
        memory_text = summary or title or "未命名记忆"
        core_tags, diffusion_tags, opposing_tags = tags
        tag_text = (
            f"core:{' '.join(core_tags)} | "
            f"diffusion:{' '.join(diffusion_tags)} | "
            f"opposing:{' '.join(opposing_tags)}"
        )
        return (
            f"【操作类型】{operation}\n"
            f"【执行结果】{result_text}\n"
            f"【涉及记忆】{memory_text}\n"
            f"【标签摘要】{tag_text}"
        )
