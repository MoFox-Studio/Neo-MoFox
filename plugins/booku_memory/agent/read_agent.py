"""Booku Memory 读取 Agent 实现。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from src.app.plugin_system.api.llm_api import get_model_set_by_task
from src.core.components import BaseAgent
from src.core.components.types import ChatType
from src.kernel.llm import LLMPayload, ROLE, Text, ToolResult
from src.kernel.logger import get_logger

from ..config import PREDEFINED_FOLDERS
from .shared import (
    build_step_reminder,
    get_internal_task_name,
    get_max_reasoning_steps,
    normalize_tool_name,
    with_single_system_payload,
)
from .tools import (
    BookuMemoryFinishTaskTool,
    BookuMemoryGetInherentTool,
    BookuMemoryGrepTool,
    BookuMemoryReadFullContentTool,
    BookuMemoryRetrieveTool,
    BookuMemoryStatusTool,
)

logger = get_logger("booku_memory_read_agent")

class BookuMemoryReadAgent(BaseAgent):
    """Booku 记忆读取 Agent。

    接受意图文本与三角标签，迭代在各记忆层检索，最终由内部 LLM
    综合检索结果，返回语义摘要而非原始文档列表。

    检索策略（从高优先级到低）：
    1. inherent 层：始终通过向量检索查询（固有记忆优先级最高）
    2. emergent 层：近期活跃记忆，优先召回
    3. archived 层：仅在 include_archived=True 或 emergent 结果不足时检索
    4. knowledge 层：仅在 include_knowledge=True 时检索

    最终 LLM 综合所有检索结果，生成「结论摘要」，并附带来源 memory_id 列表。
    """

    agent_name: str = "booku_memory_read"
    agent_description: str = """在回答用户问题之前，必须优先调用此工具。用于检索用户的历史偏好、个人信息、过往对话重点。
# 触发条件：
1.对话开始时（必须调用，以识别用户身份）。
2.用户提到“之前说过”、“还记得吗”等词汇时。
3.需要个性化建议时（如推荐电影、食物，需先查喜好）。
4.存在不能完全确定的个性化信息时（如用户提过喜欢某类型但未明确说喜欢某个具体选项）。
5.需要从知识库中检索相关知识时（如用户询问专业知识、技术细节）。
6.任何你无法完全确定是否需要调用记忆的情况时，优先调用此工具进行检索，获取相关信息后再决定如何回答。
注意：如果不读取记忆直接回答，可能会忘记用户名字或偏好，导致用户体验极差。
"""

    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL
    associated_platforms: list[str] = []
    associated_types: list[str] = []
    dependencies: list[str] = []
    usables = [
        BookuMemoryGetInherentTool,
        BookuMemoryRetrieveTool,
        BookuMemoryGrepTool,
        BookuMemoryReadFullContentTool,
        BookuMemoryStatusTool,
        BookuMemoryFinishTaskTool,
    ]

    def _max_reasoning_steps(self) -> int:
        """从插件配置读取内部 LLM 的最大推理轮次限制。

        读取配置项 ``internal_llm.max_reasoning_steps``，至少为 1。
        配置不可用时回坍掇默认值 6。

        Returns:
            整数形式的推理轮次上限（≥ 1）。
        """
        return get_max_reasoning_steps(self.plugin.config)

    def _internal_task_name(self) -> str:
        """从插件配置读取内部 LLM 决策使用的模型任务名（task_name）。

        读取配置项 ``internal_llm.task_name``，为空时回坍掇默认值 ``"tool_use"``。
        task_name 用于通过 ``get_model_set_by_task`` 匹配内部专用模型配置。

        Returns:
            模型任务名字符串，用于 ``get_model_set_by_task()`` 查找对应模型。
        """
        return get_internal_task_name(self.plugin.config)

    @staticmethod
    def _build_system_prompt() -> str:
        """构建读取 Agent 的系统提示。"""
        current_time = datetime.now().strftime("%Y年%m月%d日 %H时%M分")
        folders_info = "\n".join(
            f"  - {fid}: {fname}" for fid, fname in PREDEFINED_FOLDERS.items()
        )
        return (
            "你是 booku_memory 的读取执行代理，核心职责：理解任务→智能检索→精准总结→规范返回。\n\n"

            "## ⏰ 当前时间基准\n"
            f"当前时间：{current_time}\n"
            "涉及日期/时间时，必须以此时间为计算基准。\n"
            "禁止使用相对时间表述（如：明天、后天、下周、过两天、最近）。"
            "必须将其转换为绝对时间，至少精确到年月日（YYYY-MM-DD）。\n\n"

            "## 📁 文件夹参考（folder_id 用途）\n"
            f"{folders_info}\n\n"

            "## 🛠️ 工具清单与核心用途\n"
            "1) memory_retrieve：【主检索】语义+标签组合检索，优先使用\n"
            "2) memory_grep：【补检索】关键词精确定位，当语义检索不足时启用\n"
            "3) memory_read_full_content：【读全文】按id获取完整正文，仅在片段信息不足时调用\n"
            "4) memory_status：【查状态】查看记忆总量/最近记录/id列表，用于判断检索可行性\n"
            "5) memory_inherent_read：【查背景】读取固有记忆（可选，回复器通常已可见）\n"
            "6) memory_finish_task：【必调用】返回最终总结并结束任务\n\n"

            "## 🔄 标准执行流程（严格按顺序）\n\n"

            "### 阶段1：任务解析与目标设定\n"
            "- 仔细理解传入的 task/query 参数，明确用户真正需要什么\n"
            "- 自动补全检索标签：从query中提取/推断可能的 tags（如偏好、事件、人物等）\n"
            "  **标签生成准则（严格遵守）**：\n"
            "  - 数量：core_tags 1-2 个，diffusion_tags 2-3 个\n"
            "  - 粒度：优先使用上位概念，宽泛覆盖语义领域而非锁定具体细节\n"
            "  - 宽泛优先：宽泛标签覆盖面广，窄标签易漏查；首次检索必须使用宽泛标签，禁止使用具体词\n"
            "  - **人物查询特例**：若任务涉及特定人物，core_tags 中**必须**包含该人物的名称/昵称/ID（实体名称本身就是最重要的 core_tag）\n"
            "- 确定检索目标：是找事实？找偏好？还是找历史对话进展？\n"
            "- **人物查询**：若任务涉及特定人物且已知其平台ID，同时用名称和ID两个角度检索\n"
            "  → 先 memory_retrieve 语义检索，无果时用 memory_grep 分别尝试名称和ID\n\n"

            "### 阶段2：主检索（必须首先执行）\n"
            "- 调用 memory_retrieve，使用三种角度覆盖语义空间：\n"
            "  • 实体维度：以人名/物名/事件名为核心，宽泛 tags 覆盖关联领域\n"
            "  • 语义维度：query 的同义/上位概念展开，不局限于原词\n"
            "  • 场景维度：从查询意图反推对应的记忆类型（偏好/经历/计划等）\n"
            "- 至少发起 **2 次** memory_retrieve 调用，每次从不同维度出发，尽量覆盖更多方向\n"
            "- 若问题与专业知识相关，建议设置 include_knowledge=true；若无需访问知识库或已经访问过但无结果则设为 false\n\n"

            "### 阶段3：结果评估与全文读取\n"
            "- 阅读 memory_retrieve 返回的片段结果，判断：\n"
            "  ✓ 信息是否完整？→ 是：进入阶段5总结\n"
            "  ✓ 片段被截断/缺少细节？→ 否：对关键id调用 memory_read_full_content\n"
            "- 注意：memory_read_full_content 仅针对高相关度的id调用，避免批量读取\n\n"

            "### 阶段4：补检索（仅当阶段2-3信息不足时）\n"
            "- 调用 memory_grep 进行关键词检索：\n"
            "  • 优先检索 title/summary 字段快速定位\n"
            "  • 无果时扩展至 content/tags 字段\n"
            "  • 可尝试更换关键词角度（同义词/上下位词/场景词）\n"
            "  • 人物类查询：若有ID，也用ID作为关键词尝试；scopes 必须包含 tags，否则会遗漏按人名打标的记忆；topk 设为 50，include_archived=true\n"
            "- 对 memory_grep 命中的高价值id，按需调用 memory_read_full_content 读取全文\n"
            "- 💡 技巧：memory_grep 返回的 metadata 可帮助判断是否值得读全文\n\n"

            "### 阶段5：状态检查与降级策略（检索无果时）\n"
            "- 若上述步骤仍无有效内容，先调用 memory_status 检查：\n"
            "  • 记忆总量是否很少？→ 是：直接 memory_finish_task 说明'记忆库内容不足'\n"
            "  • 目标 folder 是否为空？→ 是：尝试切换 folder_id 或说明范围限制\n"
            "  • 最近是否有新记忆？→ 否：提示用户可能尚未记录相关信息\n"
            "- memory_status 不包括知识库中的记忆\n"
            "- ⚠️ 严禁编造：无依据时明确说明'未找到相关记忆'，可基于常识给出建议但需标注\n\n"

            "### 阶段6：迭代尝试的防无用功机制\n"
            "- 若想更换参数重新检索（如换tags/关键词/folder），必须先调用 memory_status 确认：\n"
            "  • 该方向是否有潜在记忆？\n"
            "  • 避免在空folder或无相关tags上重复尝试\n"
            "- 每次迭代检索后，重新评估结果质量，最多尝试2-3个方向\n\n"

            "### 阶段7：总结与返回（强制）\n"
            "- 整合所有获取的信息，按输出格式要求生成内容：\n"
            "  ① 【结论】：综合归纳，按维度/话题组织，不逐条转述原文；含时间范围（若跨度 >30 天）\n"
            "  ② 【来源】：简短溯源列表，每条含日期和 id，作为证据附录\n"
            "  ③ 【不确定】：（可选）有明显信息缺口或记录过旧时才写\n"
            "- 最后必须调用 memory_finish_task(content=总结文本)\n"
            "- ❌ 禁止直接输出最终答案，必须通过 memory_finish_task 返回\n"
            "- ✅ 即使失败/无结果，也要调用 memory_finish_task 说明原因+已尝试步骤\n\n"

            "## ⚙️ 高级策略与约束\n"
            "- memory_inherent_read 使用建议：仅当任务明显依赖全局背景/规则时调用，否则跳过\n"
            "- 标签补全技巧：从query提取实体→映射上位概念tags→组合检索，标签应覆盖语义领域而非具体细节\n"
            "- 截断处理：memory_retrieve/memory_grep 返回的 summary 若含‘...’或长度接近limit，优先 memory_read_full_content\n"
            "- 多id处理：若多个id相关，按相关性排序，优先读取top-3，避免token浪费\n"
            "- 不确定性表达：使用'根据现有记忆...''未找到明确记录，但...等措辞\n"
            "- **时间判断准则**：metadata 中的 `created_at_readable` 和 `updated_at_readable` 是人类可读的创建/更新时间（格式 YYYY-MM-DD HH:MM）。\n"
            "  处理涉及时间的查询时，以这两个字段判断记忆新旧，优先引用较新的记忆作为结论依据。\n\n"

            "## 🎯 输出格式要求\n"
            "memory_finish_task 的 content 按以下结构组织：\n\n"
            "【结论】\n"
            "综合各条记忆后的核心知识，按话题/维度归纳，而非逐条转述原文。\n"
            "若记忆时间跨度超过 30 天，在此处标注：（信息时间范围：YYYY-MM-DD ～ YYYY-MM-DD）\n\n"
            "【来源】（溯源用，不是主体，简短即可）\n"
            "• [YYYY-MM-DD] <片段摘要> (id:xxx)\n"
            "• [YYYY-MM-DD] <片段摘要> (id:yyy)\n\n"
            "【不确定】（可选，仅当有明显信息缺口时才写）\n"
            "未找到的信息 / 记录时间较旧可能已过期 / 建议后续补充\n\n"
            "逻辑要求：结论是综合分析，来源是证据列表，不要把来源内容再原文贴到结论里。\n\n"

            "## 🚫 绝对禁止\n"
            "- 编造不存在的记忆内容或id\n"
            "- 跳过 memory_finish_task 直接输出\n"
            "- 在无状态检查的情况下盲目重复检索\n"
            "- 输出工具调用的中间调试信息\n"
        )

    async def execute(
        self,
        intent_text: Annotated[str, "意图描述，描述想要了解的内容，应清晰描述「想知道什么」，而非泛泛一问"],
        core_tags: Annotated[list[str], "核心语义标签，最优先匹配，是目标记忆最相关的关键词；查询特定人物时若已知其平台ID，必须将ID也加入此列表"],
        diffusion_tags: Annotated[list[str], "扩散联想标签，表示目标记忆可能的相关标签"],
        opposing_tags: Annotated[list[str], "对立标签，抑制不希望召回的方向"],
        context: Annotated[str, "当前对话上下文（可选），辅助语义精确化，推荐填写以帮助agent理解检索场景"] = "",
        include_archived: Annotated[bool, "是否检索归档层（默认 False）"] = False,
        include_knowledge: Annotated[bool, "是否检索知识库（默认 False）"] = False,
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行记忆检索与综合任务，内部将运行多轮 LLM 工具调用循环。

        内部过程：
        1. 构建内部系统提示及检索任务 payload。
        2. 太迭代调用 LLM，LLM 通过工具分析策略并查询记忆库。
        3. 检测到 ``memory_finish_task`` 调用时经本 agent 退出循环并返回结果。
        4. 超过最大推理步数时返回错误。

        Args:
            intent_text: 意图描述，不能为空字符串，不到位将直接返回失败。
            core_tags: 桀心语义标签，内部 LLM 会优先匹配此列表中的记忆。
            diffusion_tags: 扩散标签，援宿语义相似设定。
            opposing_tags: 对立标签，为内部 LLM 提供检索抽象限制。
            context: 对话上下文文本，会被拼接到 intent_text 后一起加入检索。
            include_archived: 传递给内部 LLM 的归档层检索开关，默认 False。
            include_knowledge: 传递给内部 LLM 的知识库检索开关，默认 False。

        Returns:
            成功时返回 ``(True, summary_text)``，summary_text 为内部 LLM 生成的
            自然语言检索摘要（包含结论、依据来源及不确定性说明）；
            失败时返回 ``(False, error_dict)``，error_dict 包含 ``error`` 字段。
        """
        if not intent_text.strip():
            return False, "intent_text 不能为空"

        query_text = intent_text.strip()
        if context.strip():
            query_text = f"{query_text} {context.strip()}"

        try:
            model_set = get_model_set_by_task(self._internal_task_name())
            request = self.create_llm_request(
                model_set=model_set,
                request_name="booku_memory_read_agent_internal",
                with_usables=True,
            )
            base_system_prompt = self._build_system_prompt()
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(base_system_prompt)))
            request.add_payload(
                LLMPayload(
                    ROLE.USER,
                    Text(
                        "\n".join(
                            ("以下参数必须严格遵守，不能忽略或改变：\n",
                            json.dumps(
                                {
                                    "intent_text": intent_text.strip(),
                                    "context": context.strip(),
                                    "query_text": query_text,
                                    "core_tags": core_tags or [],
                                    "diffusion_tags": diffusion_tags or [],
                                    "opposing_tags": opposing_tags or [],
                                    "include_archived": include_archived,
                                    "include_knowledge": include_knowledge,
                                },
                                ensure_ascii=False,
                            ))
                        )
                    ),
                )
            )

            max_steps = self._max_reasoning_steps()

            response = await request.send(stream=False)
            await response
            tool_traces: list[dict[str, Any]] = []
            _retrieve_hint: str = ""

            for step_index in range(max_steps):
                calls = response.call_list or []
                if not calls:
                    logger.warning("LLM 未返回任何工具调用，可能是模型配置问题，建议更换模型。")
                    return False, {"error": "LLM 未返回任何工具调用，可能是模型配置问题，建议更换模型。"}

                step_retrieve_result_count: int | None = None

                for call in calls:
                    logger.info(f"调用工具：{call.name}")
                    logger.debug(f"工具调用请求：{call.name}，参数：{call.args}")

                    normalized_name = normalize_tool_name(call.name)
                    args = call.args if isinstance(call.args, dict) else {}
                    if normalized_name == "memory_finish_task":
                        finish_content = str(args.get("content", "")).strip()
                        if not finish_content:
                            return False, "memory_finish_task 的 content 不能为空"
                        return True, finish_content

                    success, result = await self.execute_local_usable(
                        normalized_name, None, **args
                    )
                    trace = {"tool": call.name, "success": success, "result": result}
                    tool_traces.append(trace)
                    response.add_payload(
                        LLMPayload(
                            ROLE.TOOL_RESULT,
                            ToolResult(  # type: ignore[arg-type]
                                value=trace,
                                call_id=call.id,
                                name=call.name,
                            ),
                        )
                    )

                    # 追踪本步骤 memory_retrieve 的结果数量
                    if normalized_name == "memory_retrieve" and success and isinstance(result, dict):
                        step_retrieve_result_count = len(result.get("items", []))

                # 根据本步骤 retrieve 结果计算动态提示
                if step_retrieve_result_count is not None:
                    if step_retrieve_result_count == 0:
                        _retrieve_hint = (
                            "⚠️ 向量检索未找到结果，标签可能过窄或与记忆内容不匹配。"
                            "请尝试：① 使用 `memory_grep` 提取 query 中的关键词（人名、地名、事件名）进行精确匹配；"
                            "② 或将 core_tags 更换为更宽泛的上位词（如'旅行'代替'某城市旅行'）再次检索。"
                        )
                    elif step_retrieve_result_count < 2:
                        _retrieve_hint = (
                            "⚠️ 检索结果少于 2 条，可能标签过窄。"
                            "请将 core_tags 减少到 1 个并使用更宽泛的上位词后再次检索。"
                        )
                    else:
                        _retrieve_hint = ""

                ongoing_instruction = (
                    f"{_retrieve_hint}\n" if _retrieve_hint else ""
                ) + (
                    "请控制工具调用数量，必要时在最后一轮调用 "
                    "memory_finish_task(content=...) 返回当前结论与依据。"
                )

                response.payloads = with_single_system_payload(
                    response.payloads,
                    base_system_prompt=base_system_prompt,
                    step_reminder=build_step_reminder(
                        step_index=step_index,
                        max_steps=max_steps,
                        final_round_instruction=(
                            "请立刻调用 memory_finish_task(content=...) 结束并返回总结，"
                            "不要再调用 memory_retrieve/memory_grep/memory_read_full_content/"
                            "memory_status/memory_inherent_read 等其他工具。"
                        ),
                        ongoing_instruction=ongoing_instruction,
                    ),
                )
                response = await response.send(stream=False)
                await response
            return False, {
                "error": "记忆检索未能在规定的推理步数内完成"
            }

        except Exception as error:
            return False, {"error": str(error)}
