"""Booku Memory 读取工作流工具。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from json_repair import repair_json

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.core.components import BaseTool
from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.logger import get_logger

from ..config import PREDEFINED_FOLDERS, BookuMemoryConfig
from ..agent.shared import get_internal_task_name
from ..agent.tools import BookuMemoryGrepTool, BookuMemoryRetrieveTool, BookuMemoryStatusTool

logger = get_logger("booku_memory_read_tool")

_FOLDER_IDS = Literal[
    "relations", "plans", "facts", "preferences", "events", "work", "default"
]
_LAYER_IDS = Literal["inherent", "emergent", "archived", "knowledge"]


class BookuMemoryReadTool(BaseTool):
    """固定工作流的记忆读取工具。

    该工具对外暴露统一入口 ``memory_read``，内部显式编排：
    - memory_retrieve
    - memory_status
    - memory_grep
    """

    tool_name: str = "memory_read"
    tool_description: str = """在回答用户问题之前，必须优先调用此工具。用于检索用户的历史偏好、个人信息、过往对话重点。
# 触发条件：
1.对话开始时（必须调用，以识别用户身份）。
2.用户提到“之前说过”、“还记得吗”等词汇时。
3.需要个性化建议时（如推荐电影、食物，需先查喜好）。
4.存在不能完全确定的个性化信息时（如用户提过喜欢某类型但未明确说喜欢某个具体选项）。
5.需要从知识库中检索相关知识时（如用户询问专业知识、技术细节）。
6.任何你无法完全确定是否需要调用记忆的情况时，优先调用此工具进行检索，获取相关信息后再决定如何回答。
注意：如果不读取记忆直接回答，可能会忘记用户名字或偏好，导致用户体验极差。
"""
    
    async def execute(
        self,
        intent_text: Annotated[str, "检索意图文本，描述当前想了解的问题"],
        core_tags: Annotated[list[str], "核心语义标签"],
        diffusion_tags: Annotated[list[str], "扩散联想标签"],
        opposing_tags: Annotated[list[str], "对立标签"],
        context: Annotated[str, "补充上下文（可选）"] = "",
        include_knowledge: Annotated[bool, "是否仅检索知识库层，默认 false"] = False,
        topk: Annotated[int, "每次检索返回条数"] = 3,
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行读记忆流程入口。

        Args:
            intent_text: 用户意图文本，必须为非空字符串。
            core_tags: 核心标签。
            diffusion_tags: 扩散标签。
            opposing_tags: 对立标签。
            context: 额外上下文，将与 intent_text 拼接形成 query。
            include_knowledge: 为 True 时仅检索知识库层。
            topk: 每次检索的最大返回条数，最小值为 1。

        Returns:
            成功时返回 ``(True, result_text)``；
            输入非法时返回 ``(False, error_message)``。
        """
        query = self._build_query_text(intent_text=intent_text, context=context)
        if not query:
            return False, "intent_text 不能为空"
        task_name = (
            f"{getattr(self.plugin, 'plugin_name', 'unknown_plugin')}:memory_read"
        )

        # 与写工作流共用同一把锁，避免写入事务中读取到不一致状态。
        lock = self._get_db_lock()
        async with lock:
            try:
                success, result = await self._execute_workflow(
                    query_text=query,
                    core_tags=core_tags,
                    diffusion_tags=diffusion_tags,
                    opposing_tags=opposing_tags,
                    include_knowledge=include_knowledge,
                    topk=max(1, int(topk)),
                )
                if not success:
                    logger.error(
                        "记忆读取任务失败",
                        task_name=task_name,
                        error=result,
                    )
                    return False, result
                return True, result
            except Exception:
                logger.error(
                    "记忆读取任务异常",
                    task_name=task_name,
                    exc_info=True,
                )
                return False, "记忆读取任务异常"

    async def _execute_workflow(
        self,
        *,
        query_text: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
        include_knowledge: bool,
        topk: int,
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行固定工作流主逻辑。"""
        # 阶段1：规整标签与时间文本。
        normalized_core = self._normalize_tags(core_tags)
        normalized_diffusion = self._normalize_tags(diffusion_tags)
        normalized_opposing = self._normalize_tags(opposing_tags)
        needs_tag_completion = (
            len(normalized_core) == 0
            or len(normalized_diffusion) == 0
            or len(normalized_opposing) == 0
        )
        needs_time_resolution = self._needs_time_resolution(query_text)

        if needs_tag_completion or needs_time_resolution:
            revised = await self._fast_prepare_query(
                query_text=query_text,
                core_tags=normalized_core,
                diffusion_tags=normalized_diffusion,
                opposing_tags=normalized_opposing,
            )
            query_text = (
                str(revised.get("query_text", query_text) or query_text).strip()
                or query_text
            )
            normalized_core = (
                self._normalize_tags(revised.get("core_tags")) or normalized_core
            )
            normalized_diffusion = (
                self._normalize_tags(revised.get("diffusion_tags"))
                or normalized_diffusion
            )
            normalized_opposing = (
                self._normalize_tags(revised.get("opposing_tags"))
                or normalized_opposing
            )

        if not normalized_core:
            normalized_core = ["记忆"]
        if not normalized_diffusion:
            normalized_diffusion = ["对话"]
        if not normalized_opposing:
            normalized_opposing = ["无关"]

        # 阶段2：选择优先检索 folder。
        target_folder = await self._select_target_folder(
            query_text=query_text,
            core_tags=normalized_core,
            diffusion_tags=normalized_diffusion,
        )

        layers: list[_LAYER_IDS]
        if include_knowledge:
            layers = ["knowledge"]
        else:
            layers = ["inherent", "emergent", "archived"]

        # 阶段3：按层级逐层检索；命中即返回。
        for layer in layers:
            if layer == "knowledge":
                found = await self._retrieve_in_folder(
                    layer=layer,
                    folder_id="default",
                    query_text=query_text,
                    core_tags=normalized_core,
                    diffusion_tags=normalized_diffusion,
                    opposing_tags=normalized_opposing,
                    topk=topk,
                )
                if found:
                    return True, self._format_found_result(
                        query_text=query_text,
                        layer=layer,
                        folder_id="default",
                        items=found,
                    )
                return True, "未检索到相关记忆。"

            found = await self._retrieve_in_folder(
                layer=layer,
                folder_id=target_folder,
                query_text=query_text,
                core_tags=normalized_core,
                diffusion_tags=normalized_diffusion,
                opposing_tags=normalized_opposing,
                topk=topk,
            )
            if found:
                return True, self._format_found_result(
                    query_text=query_text,
                    layer=layer,
                    folder_id=target_folder,
                    items=found,
                )

            # 当前 folder 无结果时，扩展到同层其他非空 folder。
            fallback_folders = await self._find_non_empty_folders(
                layer=layer, skip_folder=target_folder
            )
            for folder_id in fallback_folders:
                # 先用 grep 低成本探测可用性，再执行 retrieve。
                grep_hit = await self._grep_probe(
                    layer=layer,
                    folder_id=cast(_FOLDER_IDS, folder_id),
                    query_text=query_text,
                    topk=topk,
                )
                if not grep_hit:
                    continue
                found_in_other = await self._retrieve_in_folder(
                    layer=layer,
                    folder_id=cast(_FOLDER_IDS, folder_id),
                    query_text=query_text,
                    core_tags=normalized_core,
                    diffusion_tags=normalized_diffusion,
                    opposing_tags=normalized_opposing,
                    topk=topk,
                )
                if found_in_other:
                    return True, self._format_found_result(
                        query_text=query_text,
                        layer=layer,
                        folder_id=cast(_FOLDER_IDS, folder_id),
                        items=found_in_other,
                    )

        return True, "未检索到相关记忆。"

    async def _retrieve_in_folder(
        self,
        *,
        layer: _LAYER_IDS,
        folder_id: _FOLDER_IDS,
        query_text: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
        topk: int,
    ) -> list[dict[str, Any]]:
        """在指定层级和 folder 内执行一次向量检索并做层级过滤。"""
        include_archived = layer == "archived"
        include_knowledge = layer == "knowledge"
        success, result = await BookuMemoryRetrieveTool(self.plugin).execute(
            query_text=query_text,
            core_tags=core_tags,
            diffusion_tags=diffusion_tags,
            opposing_tags=opposing_tags,
            topk=topk,
            include_archived=include_archived,
            include_knowledge=include_knowledge,
            folder_id=folder_id,
        )
        if not success or not isinstance(result, dict):
            return []
        items = result.get("items", [])
        if not isinstance(items, list):
            return []
        filtered: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata", {})
            bucket = (
                str(metadata.get("bucket", "") if isinstance(metadata, dict) else "")
                .strip()
                .lower()
            )
            if bucket == layer:
                filtered.append(item)
        return filtered

    async def _grep_probe(
        self,
        *,
        layer: _LAYER_IDS,
        folder_id: _FOLDER_IDS,
        query_text: str,
        topk: int,
    ) -> bool:
        """对候选 folder 做关键词预探测，减少无效 retrieve 调用。"""
        include_archived = layer == "archived"
        success, result = await BookuMemoryGrepTool(self.plugin).execute(
            query=query_text,
            scopes=["title", "summary", "tags", "content"],
            folder_id=folder_id,
            include_archived=include_archived,
            topk=topk,
            use_regex=False,
        )
        if not success or not isinstance(result, dict):
            return False
        total = int(result.get("total", 0) or 0)
        return total > 0

    async def _find_non_empty_folders(
        self,
        *,
        layer: _LAYER_IDS,
        skip_folder: _FOLDER_IDS,
    ) -> list[str]:
        """查询同层可用 folder 列表。

        通过 memory_status 判断每个 folder 在目标层是否非空，
        仅返回有内容的 folder，降低后续检索噪音。
        """
        non_empty: list[str] = []
        for folder_id in PREDEFINED_FOLDERS.keys():
            if folder_id == skip_folder:
                continue
            success, result = await BookuMemoryStatusTool(self.plugin).execute(
                folder_id=cast(_FOLDER_IDS, folder_id),
                include_archived=True,
                recent_limit=5,
            )
            if not success or not isinstance(result, dict):
                continue
            metadata_counts = result.get("metadata_counts", {})
            if not isinstance(metadata_counts, dict):
                continue
            count = int(metadata_counts.get(layer, 0) or 0)
            if count > 0:
                non_empty.append(folder_id)
        return non_empty

    async def _fast_prepare_query(
        self,
        *,
        query_text: str,
        core_tags: list[str],
        diffusion_tags: list[str],
        opposing_tags: list[str],
    ) -> dict[str, Any]:
        """调用轻量子模型补全标签并规整时间表达。"""
        config = self._get_config()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        prompt = (
            "你是记忆检索输入规整器。"
            "仅输出 JSON，不要输出任何额外文本。"
            "字段必须包含：query_text,core_tags,diffusion_tags,opposing_tags,changed_time。"
            "要求：每类标签至少一个非空字符串；若文本含相对时间，改写为绝对日期。"
        )
        payload = {
            "query_text": query_text,
            "core_tags": core_tags,
            "diffusion_tags": diffusion_tags,
            "opposing_tags": opposing_tags,
            "current_time": now,
        }
        return await self._call_llm_json(prompt, payload, config=config)

    async def _call_llm_json(
        self,
        system_prompt: str,
        payload: dict[str, Any],
        *,
        config: BookuMemoryConfig,
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
            request_name="booku_memory_read_workflow",
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
            data = json.loads(message)
        except Exception:
            try:
                repaired = repair_json(message)
                data = json.loads(repaired)
            except Exception:
                return {}
        return data if isinstance(data, dict) else {}

    async def _select_target_folder(
        self,
        *,
        query_text: str,
        core_tags: list[str],
        diffusion_tags: list[str],
    ) -> _FOLDER_IDS:
        """基于 query 与标签做 folder 选择。

        优先使用规则化匹配；若无法命中，则使用轻量子模型做一次兜底选择。
        """
        text = f"{query_text} {' '.join(core_tags)} {' '.join(diffusion_tags)}".lower()
        rules: list[tuple[_FOLDER_IDS, tuple[str, ...]]] = [
            ("relations", ("关系", "朋友", "家人", "同学", "同事", "恋人", "父母")),
            ("plans", ("计划", "安排", "待办", "目标", "打算", "日程", "todo")),
            ("facts", ("事实", "设定", "背景", "档案", "信息", "身份", "年龄")),
            ("preferences", ("喜欢", "偏好", "口味", "习惯", "爱好", "讨厌")),
            ("events", ("事件", "发生", "昨天", "今天", "明天", "会议", "旅行")),
            ("work", ("工作", "项目", "学习", "代码", "任务", "考试", "课程")),
        ]
        folder_id_list = []
        for folder_id, keywords in rules:
            if any(keyword in text for keyword in keywords):
                folder_id_list.append(folder_id)
        if len(folder_id_list) == 1:
            return folder_id_list[0]
        return await self._fallback_select_folder_by_llm(
            query_text=query_text,
            core_tags=core_tags,
            diffusion_tags=diffusion_tags,
        )

    async def _fallback_select_folder_by_llm(
        self,
        *,
        query_text: str,
        core_tags: list[str],
        diffusion_tags: list[str],
    ) -> _FOLDER_IDS:
        """当规则化匹配失败时，使用轻量子模型选择目标 folder。"""
        config = self._get_config()
        prompt = (
            "你是记忆检索的 folder 选择器。"
            "仅输出 JSON，不要输出任何额外文本。"
            "字段必须包含：folder_id。"
            "folder_id 必须是 relations/plans/facts/preferences/events/work/default 之一。"
            "要求：尽量选择与 query 和标签语义最匹配的 folder。"
        )
        payload = {
            "query_text": query_text,
            "core_tags": core_tags,
            "diffusion_tags": diffusion_tags,
            "valid_folders": list(PREDEFINED_FOLDERS.keys()),
        }
        data = await self._call_llm_json(prompt, payload, config=config)
        folder = str(data.get("folder_id", "") or "").strip()
        if folder in PREDEFINED_FOLDERS:
            return cast(_FOLDER_IDS, folder)
        return "default"

    @staticmethod
    def _build_query_text(*, intent_text: str, context: str) -> str:
        """拼接检索 query 文本。"""
        intent = (intent_text or "").strip()
        context_text = (context or "").strip()
        if not intent:
            return ""
        if context_text:
            return f"{intent} {context_text}"
        return intent

    @staticmethod
    def _normalize_tags(tags: Any) -> list[str]:
        """清理标签列表，仅保留非空字符串。"""
        if not isinstance(tags, list):
            return []
        return [
            str(tag).strip()
            for tag in tags
            if isinstance(tag, str) and str(tag).strip()
        ]

    @staticmethod
    def _needs_time_resolution(text: str) -> bool:
        """判断文本是否包含相对时间表达。"""
        candidates = (
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
            "下周末",
            "本周末",
        )
        return any(token in text for token in candidates)

    def _get_config(self) -> BookuMemoryConfig:
        """读取插件配置，缺失时返回默认配置对象。"""
        if isinstance(self.plugin.config, BookuMemoryConfig):
            return self.plugin.config
        return BookuMemoryConfig()

    def _get_db_lock(self) -> asyncio.Lock:
        """获取读写共享锁。"""
        lock = getattr(self.plugin, "_booku_memory_write_lock", None)
        if isinstance(lock, asyncio.Lock):
            return lock
        lock = asyncio.Lock()
        setattr(self.plugin, "_booku_memory_write_lock", lock)
        return lock

    @staticmethod
    def _format_found_result(
        *,
        query_text: str,
        layer: _LAYER_IDS,
        folder_id: _FOLDER_IDS,
        items: list[dict[str, Any]],
    ) -> str:
        """将命中结果格式化为可读文本。"""
        if not items:
            return "未检索到相关记忆。"
        blocks: list[str] = []
        for item in items:
            memory_id = str(item.get("id", "") or "")
            title = str(item.get("title", "") or "")
            snippet = str(item.get("content_snippet", "") or "")
            score = float(item.get("score", 0.0) or 0.0)
            line = f"- id={memory_id} | 标题={title or '未命名'} | score={score:.4f} | 片段={snippet}"
            blocks.append(line)
        return (
            f"检索完成。\n"
            f"query: {query_text}\n"
            f"layer: {layer}\n"
            f"folder: {folder_id}\n"
            f"total: {len(items)}\n" + "\n".join(blocks)
        )
