"""Booku Memory Agent 私有工具集。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api

from ..service import BookuMemoryService

BucketLiteral = Literal["emergent", "archived"]
FolderLiteral = Literal["relations", "plans", "facts", "preferences", "events", "work", "default"]
GrepScopeLiteral = Literal["title", "summary", "tags", "content", "metadata"]

logger = log_api.get_logger("booku_memory.tools")

def _service(plugin: Any) -> BookuMemoryService:
    """构建并返回绑定到指定插件实例的记忆服务对象。

    Args:
        plugin: 当前工具所属的插件实例，会被传递给 BookuMemoryService 构造函数。
            类型使用 Any 是因为工具基类未对 plugin 字段强制类型，实际运行时
            始终为 BasePlugin 子类实例。

    Returns:
        BookuMemoryService: 与该插件绑定的记忆服务实例。
    """
    return BookuMemoryService(plugin=plugin)


class BookuMemoryCreateTool(BaseTool):
    """工具一：创建记忆。"""

    tool_name: str = "memory_create"
    tool_description: str = "创建记忆，写入指定层级与 folder。"

    async def execute(
        self,
        title: Annotated[str, "笔记标题"],
        content: Annotated[str, "记忆正文"],
        bucket: Annotated[BucketLiteral, "写入层级：emergent 或 archived"],
        folder_id: Annotated[FolderLiteral, "记忆文件夹 ID"],
        core_tags: Annotated[list[str], "核心标签（不得为空列表，必须提供至少一个有效标签）"],
        diffusion_tags: Annotated[list[str], "扩散标签（不得为空列表，必须提供至少一个有效标签）"],
        opposing_tags: Annotated[list[str], "对立标签（不得为空列表，必须提供至少一个有效标签）"],
    ) -> tuple[bool, str | dict]:
        """创建一条新记忆，写入前执行向量新颖度去重检测，重复内容将被自动合并。

        Args:
            title: 笔记标题，建议简短描述记忆主题（≤20 字）。
            content: 记忆正文，应包含可复用的事实、偏好或结论。
            bucket: 写入层级，``emergent``（隐现/近期）或 ``archived``（归档/长期）。
            folder_id: 记忆所属文件夹 ID，参见 ``FolderLiteral`` 枚举。
            core_tags: 核心标签列表，不得为空，至少提供一个有效字符串。检索时最优先匹配。
            diffusion_tags: 扩散标签列表，不得为空，描述记忆的相关联场景。
            opposing_tags: 对立标签列表，不得为空，用于抑制不希望召回的语义方向。

        Returns:
            ``(True, result_dict)`` 成功时，result_dict 包含 action/mode/total/items 字段；
            ``(False, error_message)`` 失败时，error_message 为错误原因字符串。
        """
        if not core_tags:
            return False, "core_tags 不得为空列表，必须提供至少一个有效核心标签"
        if not diffusion_tags:
            return False, "diffusion_tags 不得为空列表，必须提供至少一个有效扩散标签"
        if not opposing_tags:
            return False, "opposing_tags 不得为空列表，必须提供至少一个有效对立标签"
        service = _service(self.plugin)
        try:
            result = await service.create_memory(
                title=title,
                content=content,
                bucket=bucket,
                folder_id=folder_id,
                core_tags=core_tags,
                diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
            )
            return True, result
        except Exception as error:
            logger.error(f"创建记忆失败: {error}", exc_info=True)
            return False, f"创建记忆失败: {error}"


class BookuMemoryEditInherentTool(BaseTool):
    """工具二：编辑固有记忆。"""

    tool_name: str = "memory_edit_inherent"
    tool_description: str = "编辑全局固有记忆，content 为完整更新后文本。"

    async def execute(
        self,
        content: Annotated[str, "编辑后的完整固有记忆文本"],
    ) -> tuple[bool, str | dict]:
        """替换全局固有记忆的完整内容（全量覆写，非增量追加）。

        固有记忆（inherent）是全局唯一的基础背景层，不按 folder 隔离。
        每次调用均会用 content 完整替换原有内容，请调用前先通过
        ``BookuMemoryGetInherentTool`` 读取现有内容后再合并编写。

        Args:
            content: 编辑后的完整固有记忆文本，将全量覆盖原有内容。

        Returns:
            ``(True, result_dict)`` 成功时，result_dict 包含 action/mode/total/items 字段；
            ``(False, error_message)`` 失败时，error_message 为错误原因字符串。
        """
        service = _service(self.plugin)
        try:
            result = await service.edit_inherent_memory(content=content)
            return True, result
        except Exception as error:
            logger.error(f"更新固有记忆失败: {error}", exc_info=True)
            return False, f"更新固有记忆失败: {error}"


class BookuMemoryGetInherentTool(BaseTool):
    """工具三：获取固有记忆。"""

    tool_name: str = "memory_inherent_read"
    tool_description: str = "按查询文本获取全局固有记忆。"

    async def execute(
        self,
        query_text: Annotated[str, "检索文本"],
        topk: Annotated[int, "召回条数"] = 5,
    ) -> tuple[bool, str | dict]:
        """按查询文本语义检索全局固有记忆（inherent bucket），无 folder 约束。

        Args:
            query_text: 检索文本，不能为空字符串，用于向量语义匹配。
            topk: 最大返回条数，默认为 5。

        Returns:
            成功时返回 ``(True, {"action": ..., "query": ..., "total": ..., "items": [...]})``;\n            失败时返回 ``(False, error_message)``，error_message 说明具体原因。
        """
        service = _service(self.plugin)
        resolved_query = query_text.strip()
        if not resolved_query:
            return False, "query_text 不能为空"
        try:
            result = await service.get_inherent_memories(
                query_text=resolved_query,
                top_k=topk,
            )
            return True, {
                "action": "get_inherent_memories",
                "query": result.get("query", resolved_query),
                "total": result.get("total", 0),
                "items": result.get("results", []),
            }
        except Exception as error:
            logger.error(f"获取固有记忆失败: {error}", exc_info=True)
            return False, f"获取固有记忆失败: {error}"


class BookuMemoryRetrieveTool(BaseTool):
    """工具四：记忆检索。"""

    tool_name: str = "memory_retrieve"
    tool_description: str = "按标签进行语义检索，返回 id/title/content_snippet/metadata。"

    async def execute(
        self,
        query_text: Annotated[str, "检索文本"],
        core_tags: Annotated[list[str], "核心标签（1-2个，优先宽泛上位概念）"],
        diffusion_tags: Annotated[list[str], "扩散标签（2-3个，语义邻域联想，比 core_tags 更宽泛）"],
        opposing_tags: Annotated[list[str], "对立标签"],
        topk: Annotated[int | None, "召回条数，默认使用系统配置"] = None,
        include_archived: Annotated[bool | None, "是否检索归档层"] = None,
        include_knowledge: Annotated[bool | None, "是否检索知识库"] = None,
        folder_id: Annotated[FolderLiteral | None, "在指定 folder 中检索"] = None,
    ) -> tuple[bool, str | dict]:
        """执行标签三角驱动的语义检索，融合 EPA（扩散-对立-核心）向量动力学重塑查询向量。

        Args:
            query_text: 检索文本。
            core_tags: 核心标签，检索引擎对这些标签的向量给予最高权重。
            diffusion_tags: 扩散标签，允许语义邻域扩展，提高召回率。
            opposing_tags: 对立标签，用于抑制不希望召回的语义方向。
            topk: 最大返回条数，``None`` 时使用系统默认配置。
            include_archived: 是否同时检索归档层（``archived`` bucket），默认 False。
            include_knowledge: 是否同时检索知识库（``knowledge`` bucket），默认 False。
            folder_id: 限定在指定 folder 中检索，``None`` 时搜索所有 folder。

        Returns:
            成功时返回 ``(True, {"action": ..., "query": ..., "total": ..., "items": [...]})``;\n            失败时返回 ``(False, error_message)``，两者均为 query_text/tags 皆为空的错误说明。
        """
        service = _service(self.plugin)
        query_tokens = [*(core_tags or []), *(diffusion_tags or [])]
        resolved_query = (query_text or "").strip()
        if not resolved_query:
            if not query_tokens:
                return False, "query_text 为空时，core_tags 与 diffusion_tags 至少需要一个非空标签"
            resolved_query = " ".join(token.strip() for token in query_tokens if token and token.strip())
        
        try:
            result = await service.retrieve_memories(
                query_text=resolved_query,
                folder_id=folder_id,
                top_k=topk,
                include_archived=include_archived,
                include_knowledge=include_knowledge,
                core_tags=core_tags or [],
                diffusion_tags=diffusion_tags or [],
                opposing_tags=opposing_tags or [],
            )
            return True, {
                "action": "retrieve_memories",
                "query": resolved_query,
                "total": result.get("total", 0),
                "items": result.get("results", []),
            }
        except Exception as error:
            logger.error(f"检索记忆失败: {error}", exc_info=True)
            return False, f"检索记忆失败: {error}"


class BookuMemoryGrepTool(BaseTool):
    """工具五：记忆 grep。"""

    tool_name: str = "memory_grep"
    tool_description: str = "按关键词 grep 记忆，可多选范围：title/summary/tags/content/metadata。"

    async def execute(
        self,
        query: Annotated[str, "关键词检索文本或正则表达式（use_regex=true 时）"],
        scopes: Annotated[list[GrepScopeLiteral], "grep 范围多选：title/summary/tags/content/metadata"],
        folder_id: Annotated[FolderLiteral | None, "在指定 folder 中检索"] = None,
        include_archived: Annotated[bool, "是否检索归档层"] = True,
        topk: Annotated[int, "返回条数"] = 10,
        use_regex: Annotated[bool, "为 true 时将 query 视为 Python 正则表达式（re.search），默认 false（LIKE 子串匹配）"] = False,
    ) -> tuple[bool, str | dict]:
        """在记忆的指定字段中执行关键词匹配（grep），适合定位已知词汇或特定模式。

        支持两种匹配模式：
        - ``use_regex=False``（默认）：``LIKE '%keyword%'`` 子串匹配，速度快，适合普通关键词。
        - ``use_regex=True``：``re.search(pattern, field)`` 正则匹配，支持完整 Python 正则语法。
          正则匹配在 Python 层完成，对大型记忆库性能略低，建议配合明确的 folder 和 scopes 缩小范围。

        不同于语义检索（retrieve），grep 基于精确字符匹配，不做语义扩展。
        当语义检索结果不足时，可将 grep 作为补充检索手段。

        Args:
            query: 关键词检索文本（``use_regex=False``）或 Python 正则表达式（``use_regex=True``）。
            scopes: 搜索范围的多选列表，可为 ``title``/``summary``/``tags``/``content``/``metadata``。
            folder_id: 限定在指定 folder 中检索，``None`` 时搜索所有 folder。
            include_archived: 是否同时检索归档层，默认 True。
            topk: 最大返回条数，默认为 10。
            use_regex: 是否启用正则匹配，默认 False。

        Returns:
            成功时返回 ``(True, result_dict)``，result_dict 包含 action/query/total/items 字段；
            失败时返回 ``(False, error_message)``。scopes 为空或正则无效时直接返回失败。
        """
        service = _service(self.plugin)
        if not scopes:
            return False, "scopes 不能为空"
        try:
            result = await service.grep_memories(
                query=query,
                search_fields=[str(scope) for scope in scopes],
                folder_id=folder_id,
                include_archived=include_archived,
                top_k=topk,
                use_regex=use_regex,
            )
            return True, result
        except ValueError as error:
            # 正则语法错误等参数类错误，给 LLM 可读的提示
            logger.error(f"grep 参数错误: {error}", exc_info=True)
            return False, f"grep 参数错误: {error}"
        except Exception as error:
            logger.error(f"grep 记忆失败: {error}", exc_info=True)
            return False, f"grep 记忆失败: {error}"


class BookuMemoryStatusTool(BaseTool):
    """工具六：查询记忆状态。"""

    tool_name: str = "memory_status"
    tool_description: str = "查看记忆数量、最近新记忆、指定 folder 的记忆 id 列表。"

    async def execute(
        self,
        folder_id: Annotated[FolderLiteral | None, "指定 folder 查询"] = None,
        include_archived: Annotated[bool, "最近记录是否包含 archived"] = True,
        recent_limit: Annotated[int, "最近记忆条数"] = 8,
    ) -> tuple[bool, str | dict]:
        """查询指定 folder（或全局）的记忆统计信息与最近记录。

        返回向量库与元数据库中各 bucket 的记忆数量、最近新增/更新的记忆列表，
        以及该 folder 内所有可用的 memory_id 列表。可用于判断检索可行性或
        防止在空 folder 上进行无意义的重复检索。

        Args:
            folder_id: 指定查询的 folder，``None`` 时使用默认 folder。
            include_archived: 最近记录是否包含 archived 层，默认 True。
            recent_limit: 返回最近记忆的条数上限，默认 8。

        Returns:
            成功时返回 ``(True, status_dict)``，status_dict 包含：
            ``action``、``folder_id``、``vector_counts``、``metadata_counts``、
            ``recent``、``folder_memory_ids`` 等字段；
            失败时返回 ``(False, error_message)``。
        """
        service = _service(self.plugin)
        try:
            result = await service.query_memory_status(
                folder_id=folder_id,
                include_archived=include_archived,
                recent_limit=recent_limit,
            )
            return True, result
        except Exception as error:
            logger.error(f"查询记忆状态失败: {error}", exc_info=True)
            return False, f"查询记忆状态失败: {error}"


class BookuMemoryReadFullContentTool(BaseTool):
    """工具七：读取完整内容。"""

    tool_name: str = "memory_read_full_content"
    tool_description: str = "按 id 列表读取记忆完整正文。"

    async def execute(
        self,
        ids: Annotated[list[str], "记忆 id 列表"],
    ) -> tuple[bool, str | dict]:
        """按 memory_id 列表批量读取记忆完整正文（不截断）。

        retrieve/grep 工具返回的 ``content_snippet`` 可能被截断；
        当需要完整内容时优先针对高相关度 id 调用本工具，避免批量读取浪费 token。

        Args:
            ids: 需要读取完整内容的记忆 id 列表。

        Returns:
            成功时返回 ``(True, result_dict)``，result_dict 包含 action/requested/total/items 字段，
            items 中每项均含未截断的 ``content`` 字段；
            失败时返回 ``(False, error_message)``。
        """
        service = _service(self.plugin)
        try:
            result = await service.read_full_content(memory_ids=ids)
            return True, result
        except Exception as error:
            logger.error(f"读取完整内容失败: {error}", exc_info=True)
            return False, f"读取完整内容失败: {error}"


class BookuMemoryDeleteTool(BaseTool):
    """工具八：删除指定 id 的记忆。"""

    tool_name: str = "memory_delete"
    tool_description: str = "删除指定 id 的记忆，默认软删，hard=true 硬删。"

    async def execute(
        self,
        ids: Annotated[list[str], "待删除记忆 id 列表"],
        hard: Annotated[bool, "是否执行硬删除"] = False,
    ) -> tuple[bool, str | dict]:
        """删除指定 id 的记忆，默认软删（可恢复），``hard=True`` 时执行不可逆硬删。

        软删除仅标记 ``is_deleted=1``，向量库数据保留；
        硬删除会同时从向量库和元数据库中永久移除所有相关数据。

        Args:
            ids: 待删除的 memory_id 列表。
            hard: 是否执行硬删除，默认 False（软删）。仅在用户明确要求「永久删除」时设为 True。

        Returns:
            成功时返回 ``(True, result_dict)``，result_dict 包含 action/mode/deleted/requested 字段；
            失败时返回 ``(False, error_message)``。
        """
        service = _service(self.plugin)
        try:
            result = await service.delete_memories(memory_ids=ids, hard=hard)
            return True, result
        except Exception as error:
            logger.error(f"删除记忆失败: {error}", exc_info=True)
            return False, f"删除记忆失败: {error}"


class BookuMemoryUpdateByIdTool(BaseTool):
    """工具九：编辑指定 id 的记忆。"""

    tool_name: str = "memory_update_by_id"
    tool_description: str = "按 id 编辑普通记忆（不含固有记忆）。"

    async def execute(
        self,
        id: Annotated[str, "目标记忆 id"],
        content: Annotated[str, "编辑后的完整正文"],
        title: Annotated[str | None, "编辑后的标题"] = None,
        core_tags: Annotated[list[str] | None, "核心标签"] = None,
        diffusion_tags: Annotated[list[str] | None, "扩散标签"] = None,
        opposing_tags: Annotated[list[str] | None, "对立标签"] = None,
    ) -> tuple[bool, str | dict]:
        """按 memory_id 就地更新普通记忆的内容、标题及标签（不含固有记忆）。

        仅修改有值的字段（最小变更原则）。若需保留原文，
        请先调用 ``BookuMemoryReadFullContentTool`` 获取原文后再合并。
        固有记忆（inherent）不适用本工具，请使用 ``BookuMemoryEditInherentTool``。

        Args:
            id: 目标记忆的 memory_id。
            content: 编辑后的完整正文（必填，全量替换）。
            title: 新标题（可选，不传则保留原标题）。
            core_tags: 新的核心标签列表（可选，不传则保留原标签）。
            diffusion_tags: 新的扩散标签列表（可选，不传则保留原标签）。
            opposing_tags: 新的对立标签列表（可选，不传则保留原标签）。

        Returns:
            成功时返回 ``(True, result_dict)``，result_dict 包含 action/updated/items 字段；
            更新数量为 0（记录不存在）时返回 ``(False, result_dict)``；
            失败时返回 ``(False, error_message)``。
        """
        service = _service(self.plugin)
        try:
            result = await service.update_memory_by_id(
                memory_id=id,
                title=title,
                content=content,
                core_tags=core_tags,
                diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
            )
            if result.get("updated", 0) <= 0:
                return False, result
            return True, result
        except Exception as error:
            logger.error(f"更新记忆失败: {error}", exc_info=True)
            return False, f"更新记忆失败: {error}"


class BookuMemoryMoveTool(BaseTool):
    """工具十：移动指定 id 的记忆。"""

    tool_name: str = "memory_move"
    tool_description: str = "将指定 id 记忆移动到目标 folder，或转移到其他层级。"

    async def execute(
        self,
        ids: Annotated[list[str], "待移动记忆 id 列表"],
        to_folder_id: Annotated[FolderLiteral | None, "目标 folder_id"] = None,
        to_bucket: Annotated[BucketLiteral | None, "目标 bucket（emergent/archived）"] = None,
    ) -> tuple[bool, str | dict]:
        """将指定记忆批量移动到目标 folder 或 bucket（或二者同时调整）。

        ``to_folder_id`` 与 ``to_bucket`` 至少需要提供其一。
        移动至 inherent bucket 时，folder 将自动设置为全局（``global``），无需手动指定。

        Args:
            ids: 待移动的 memory_id 列表，支持批量操作。
            to_folder_id: 目标文件夹 ID（可选）。
            to_bucket: 目标存储桶（可选），``emergent`` 或 ``archived``。

        Returns:
            成功时返回 ``(True, result_dict)``，result_dict 包含 action/moved/items/to_bucket/to_folder_id 字段；
            参数均为空时返回 ``(False, error_message)``；
            其他异常返回 ``(False, error_message)``。
        """
        if to_folder_id is None and to_bucket is None:
            return False, "to_folder_id 与 to_bucket 不能同时为空"

        service = _service(self.plugin)
        try:
            result = await service.move_memories(
                memory_ids=ids,
                to_folder_id=to_folder_id,
                to_bucket=to_bucket,
            )
            return True, result
        except Exception as error:
            logger.error(f"移动记忆失败: {error}", exc_info=True)
            return False, f"移动记忆失败: {error}"


class BookuMemoryFinishTaskTool(BaseTool):
    """结束当前 Agent 任务并返回自然语言结果。"""

    tool_name: str = "memory_finish_task"
    tool_description: str = (
        "结束当前任务并返回最终结果给主模型。"
        "调用本工具即视为任务完成，参数仅允许 content。"
    )

    async def execute(
        self,
        content: Annotated[str, "返回给主模型的自然语言结果"],
    ) -> tuple[bool, str]:
        """结束当前 Agent 任务并透传最终结果给主模型。

        内部 LLM 完成所有工具调用后必须调用本工具，否则 Agent 执行循环
        不会终止并会返回规划失败错误。调用后立即退出推理循环，不再执行后续工具。

        Args:
            content: 返回给主模型的自然语言摘要，应为可读的中文总结，
                包含核心结论、关键依据及不确定性说明（格式见 Agent 系统提示）。

        Returns:
            ``(True, content)`` 始终成功返回，content 原样透传。
            异常情况（理论上不应发生）下返回 ``(False, error_message)``。
        """
        try:
            return True, content
        except Exception as error:
            logger.error(f"结束任务失败: {error}", exc_info=True)
            return False, f"结束任务失败: {error}"