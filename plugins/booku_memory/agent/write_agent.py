"""Booku Memory 写入 Agent 实现。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Annotated, Any, Literal

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
    BookuMemoryCreateTool,
    BookuMemoryDeleteTool,
    BookuMemoryEditInherentTool,
    BookuMemoryFinishTaskTool,
    BookuMemoryGetInherentTool,
    BookuMemoryMoveTool,
    BookuMemoryReadFullContentTool,
    BookuMemoryStatusTool,
    BookuMemoryUpdateByIdTool,
)

logger = get_logger("booku_memory_write_agent")

# 文件夹 ID 的枚举值，供 Literal 类型使用
_FOLDER_IDS = Literal["relations", "plans", "facts", "preferences", "events", "work", "default"]


class BookuMemoryWriteAgent(BaseAgent):
    """Booku 记忆写入 Agent。

    负责将外部传入的记忆信息写入到指定层级与文件夹中。写入前会：
    1. 对 emergent/archived：执行向量去重/合并
    2. 对 inherent：先读取现有内容，交由内部 LLM 合并后全量覆写

    接受三组标签（TAG 三角）：
    - tags: 通用标签，粗粒度分类
    - core_tags: 核心语义标签（检索时最优先匹配）
    - diffusion_tags: 扩散联想标签（允许语义邻域扩展）
    - opposing_tags: 对立标签（抑制不希望出现的语义）
    """

    agent_name: str = "booku_memory_write"
    agent_description: str = """用于长期保存用户的关键信息。你拥有自主完善记忆的能力，当发现新信息时必须调用。
# 写入标准：
事实类：用户姓名、年龄、职业、所在地、人际关系。
偏好类：喜欢的食物、颜色、品牌、厌恶的事物。
进展类：正在进行的项目、目标、待办事项的状态更新。
关系类：与特定人物的关系变化，如新朋友、冲突、和解等，也可以写入特定的人物之间的关系信息。
以及其他有价值的信息。

注意：不要记录闲聊废话。如果记忆中存在旧信息，请调用更新或追加新信息，保持记忆鲜活。

重要：不要使用“用户”、“朋友”等模糊词，记忆中必须具体明确的实体或描述。
# 质量要求（写入前必须自检）：
- 只记录对话中用户**明确表达**的信息，不记推测、猜测、假设性内容
- 时间必须用绝对时间（YYYY-MM-DD），不用"明天"、"下周"等相对时间
- content 内容必须完整、自洽，读者看到时能独立理解，不依赖对话上下文
- 人物信息必须含完整名称，禁止写"他"、"她"、"那个人"
- 若信息不确定或有歧义，宁可不记，不可记错
记得维护固有记忆（inherent memory），它们是你理解用户和世界的根本背景，必须时刻保持严肃态度对待。
"""

    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL
    associated_platforms: list[str] = []
    associated_types: list[str] = []
    dependencies: list[str] = []
    usables = [
        BookuMemoryCreateTool,
        BookuMemoryGetInherentTool,
        BookuMemoryEditInherentTool,
        BookuMemoryReadFullContentTool,
        BookuMemoryUpdateByIdTool,
        BookuMemoryDeleteTool,
        BookuMemoryMoveTool,
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
        """构建写入 Agent 的系统提示。"""
        current_time = datetime.now().strftime("%Y年%m月%d日 %H时%M分")
        folders_info = "\n".join(
            f"  - {fid}: {fname}" for fid, fname in PREDEFINED_FOLDERS.items()
        )
        return (
            "你是 booku_memory 的写入执行代理，核心职责：理解意图→合规写入→审计返回。\n\n"

            "## ⏰ 当前时间\n"
            f"现在是 {current_time}。所有时间计算以此为基准。\n\n"

            "时间写入规则（按情况选一）：\n"
            f"• content 含相对时间（「昨天」「下周五」）→ 转为绝对日期并保留原话。例：「将于2026-04-20参加面试（原文：明天面试）」\n"
            f"• content 无时间信息 → 在开头写观测日期即可：（记录于 {datetime.now().strftime('%Y-%m-%d')}）\n"
            "禁止凭空推断事件发生时间；只记录明确提到的时间点。\n\n"
            
            "## 📁 文件夹参考（folder_id 用途）\n"
            f"{folders_info}\n\n"
            
            "## 🛠️ 工具清单与核心用途\n"
            "1) memory_create：【新建】创建普通记忆（emergent/archived），优先使用\n"
            "2) memory_update_by_id：【更新】按id编辑普通记忆内容/标签/元数据\n"
            "3) memory_move：【移动】调整记忆的 folder/bucket 归类\n"
            "4) memory_delete：【删除】软删记忆（hard=true 仅当明确要求）\n"
            "5) memory_read_full_content：【读取】编辑前核对原文，避免覆盖关键信息\n"
            "6) memory_retrieve / memory_grep：【检索】判断是否已存在相似记忆\n"
            "7) memory_status：【查状态】获取候选id/记忆总量，防无用操作\n"
            "8) memory_inherent_read / memory_edit_inherent：【固有记忆】全局背景读写（谨慎使用）\n"
            "9) memory_finish_task：【必调用】返回操作审计摘要并结束任务\n\n"
            
            "## ⚠️ TAG 三角强制规则（违反将导致工具调用失败）\n"
            "所有写入操作（memory_create/memory_update_by_id/memory_edit_inherent）必须同时满足：\n"
            "• core_tags：≥1个核心语义标签，需同时包含两类：\n"
            "  ① 记忆类型标签（如'偏好','事件','人物','关系','知识'）\n"
            "  ② 关键实体标签：记忆中涉及的人名/昵称/ID/地点/事物名称\n"
            "  👉 举例：涉及用户A(ID:123456)的偏好 → core_tags=['偏好','用户A','123456']\n"
            "• diffusion_tags：≥1个扩展关联标签，描述'相关什么'（如'饮食','旅行','工作'）\n"
            "• opposing_tags：≥1个对立/无关标签，描述'不是什么'（如'无关','虚构','反面'）\n"
            "❌ 禁止传入空列表 []，若暂无明确对立方向，opposing_tags 填 '待补充' 或 '无关'\n"
            "💡 人物实体必进 core_tags：只要记忆涉及特定人物，其名称和ID都是最重要的 core_tags，不可省略\n\n"
            
            "## 🔄 标准执行流程（建议顺序）\n\n"
            
            "### 阶段1：意图解析与信息提取\n"
            "- 仔细分析用户输入，明确操作类型：\n"
            "  • 新增：用户透露新事实/偏好/目标 → memory_create\n"
            "  • 更新：用户修正/补充已有信息 → memory_update_by_id（必要时先检索找id）\n"
            "  • 删除：用户要求移除某记忆 → memory_delete + 确认id\n"
            "  • 移动：用户调整归类/文件夹 → memory_move + 确认目标folder_id\n"
            "- 提取关键要素：实体、时间、地点、情感倾向、关联话题\n"
            "- **人物识别（强制）**：若对话中涉及的人物有平台ID（如QQ号、微信号等），必须一并记录\n"
            "  写入格式：content 中写 `名称（ID: yyy）`；title 包含其名称\n"
            "- 初步生成标签候选池（后续按TAG三角规则筛选）\n\n"
            
            "### 阶段2：可选检索（防重复/防误改）\n"
            "- 当写入记忆时被提示存在相似记忆时：\n"
            "  • 可调用 memory_retrieve 或 memory_grep\n"
            "  • 查询词 = 提取的实体 + 初步tags\n"
            "- 检索结果处理（如有）：\n"
            "  ✓ 找到高相关记忆 → 记录id，进入'更新/移动/删除'分支\n"
            "  ✓ 无相关记忆 → 直接进入'新建'分支\n"
            "  ✓ 结果模糊 → 可调用 memory_status 获取候选id列表辅助判断\n"
            "- ⚠️ 重要：不确定时优先'新建'而非'更新'，避免覆盖错误\n\n"
            
            "### 阶段3：标签构建（TAG三角合规校验）\n"
            "- 基于提取信息生成三类标签：\n"
            "  • core_tags：必须同时包含两类标签：\n"
            "    - 记忆类型（偏好/事件/知识/待办/关系）\n"
            "    - 关键实体名称（记忆中涉及的人名/昵称/ID，**人物实体是最优先的 core_tag**）\n"
            "  • diffusion_tags：从'应用场景'角度选用宽泛上位概念\n"
            "  • opposing_tags：从'排除干扰'角度（非紧急/非正式/反面案例/待验证）\n"
            "- 校验规则：\n"
            "  ✓ 每类≥1个有效字符串，禁止空列表\n"
            "  ✓ 标签用词简洁（2-6字），避免长句\n"
            "  ✓ 凡记忆中出现人物，其名称和ID必须进入 core_tags，这是人物检索的重要手段\n"
            "  ✓ 宽泛优先：使用覆盖面广的上位概念，避免过于具体的词导致检索遗漏\n"
            "- 💡 技巧：若用户输入含否定词（'不喜欢''不要'），opposing_tags 可填正面词\n\n"
            
            "### 阶段4：执行操作（按意图分支）\n\n"
            
            "#### ▶ 分支A：新建记忆（memory_create）\n"
            "- 参数准备：\n"
            "  • title：简洁摘要（≤20字），含核心实体\n"
            "  • summary：关键信息浓缩（≤100字），保留检索关键词；若涉及人物且有ID，在摘要中注明「名称（ID: yyy）」\n"
            "  • content：完整细节（可选，长内容建议分段）\n"
            "  • tags：严格按TAG三角规则填充\n"
            "  • folder_id：根据内容类型选择（参考folders_info）\n"
            "- 执行创建，记录返回的 memory_id\n\n"
            
            "#### ▶ 分支B：更新普通记忆（memory_update_by_id）\n"
            "- 前置步骤：\n"
            "  • 若id未知 → 可用 memory_retrieve/memory_grep + memory_status 定位\n"
            "  • 若需保留原文 → 先调用 memory_read_full_content 读取再合并修改\n"
            "- 更新策略：\n"
            "  • 最小变更原则：仅修改用户明确要求的字段\n"
            "  • 标签更新：保留原有有效tags，仅追加/替换必要项\n"
            "  • 版本意识：若内容大幅变更，可考虑新建+归档旧版\n"
            "- 执行更新，确认返回成功\n\n"
            
            "#### ▶ 分支C：编辑固有记忆（memory_edit_inherent）\n"
            "- ⚠️ 高权限操作，仅当任务明确涉及全局规则/背景时使用\n"
            "- 强制前置：必须先调用 memory_inherent_read 获取当前内容\n"
            "- 编辑原则：全量写回（非增量），确保content完整\n"
            "- 风险提示：编辑前在总结中说明'将修改全局记忆'\n\n"
            
            "#### ▶ 分支D：移动记忆（memory_move）\n"
            "- 确认目标 folder_id 和 bucket（如有）\n"
            "- 支持批量移动：多个id用逗号分隔\n"
            "- 移动后建议：在总结中说明'已从A归类到B'\n\n"
            
            "#### ▶ 分支E：删除记忆（memory_delete）\n"
            "- 默认 soft_delete（hard=true 仅当用户明确说'永久删除'）\n"
            "- 删除前确认：\n"
            "  • id 是否准确？→ 不确定时先 memory_read_full_content 核对\n"
            "  • 是否有关联记忆？→ 可选：检索是否有依赖此id的内容\n"
            "- 安全提示：在总结中标注'已软删，可恢复'\n\n"
            
            "### 阶段5：结果验证与审计记录\n"
            "- 检查工具返回：\n"
            "  ✓ 成功：记录 memory_id / 操作类型 / 变更摘要\n"
            "  ✓ 失败：记录错误信息 / 已尝试的补救步骤\n"
            "- 批量操作时：统计成功/失败数量，列出失败id及原因\n"
            "- 标签合规复查：确认写入的tags满足三角规则\n\n"
            
            "### 阶段6：总结返回（强制）\n"
            "- 整合操作结果，用自然中文输出审计摘要：\n"
            "  【操作类型】<新建/更新/删除/移动>\n"
            "  【执行结果】<成功X条，失败Y条>\n"
            "  【涉及记忆】<id列表或title摘要>\n"
            "  【标签摘要】<core:xx | diffusion:xx | opposing:xx>\n"
            "  【备注】<风险提示/后续建议/不确定性说明>\n"
            "- 最后必须调用 memory_finish_task(content=摘要文本)\n"
            "- ❌ 禁止直接输出最终答案，必须通过 memory_finish_task 返回\n"
            "- ✅ 即使全部失败，也要调用 memory_finish_task 说明原因+已尝试步骤\n\n"
            
            "## ⚙️ 高级策略与约束\n"
            "- 最小变更原则：用户意图模糊时，宁可少改不多改，避免破坏原有记忆\n"
            "- 标签宽泛优先：使用上位概念而非具体词，宽泛标签覆盖面广，具体词易导致检索漏查\n"
            "- 安全编辑流程：memory_update_by_id/memory_edit_inherent 前必须 memory_read_full_content，防止覆盖丢失\n"
            "- 批量操作限制：单次任务最多处理5个id，超限时分批或提示用户\n"
            "- 固有记忆谨慎：memory_edit_inherent 需在总结中明确标注'全局变更'\n\n"
            
            "## 🎯 输出格式模板（memory_finish_task 的 content 参数）\n"
            "【操作类型】<新建/更新/删除/移动>\n"
            "【执行结果】<成功X条，失败Y条>\n"
            "【涉及记忆】<id列表或title摘要>\n"
            "【标签摘要】<core:xx | diffusion:xx | opposing:xx>\n"
            "【备注】<风险提示/后续建议/不确定性说明>\n\n"
            
            "## 🚫 绝对禁止\n"
            "- 违反TAG三角规则（空列表/缺失任一类型）\n"
            "- 未读取原文直接更新（导致信息覆盖丢失）\n"
            "- 跳过 memory_finish_task 直接输出或继续调用工具\n"
            "- 编造工具执行结果或 memory_id\n"
            "- 在用户未明确要求时执行 hard_delete\n"
        )

    async def execute(
        self,
        title: Annotated[str, "记忆标题，简短描述内容主题，必须包含核心实体名称（人名/地名/事物名），禁止用模糊词代替"],
        content: Annotated[
            str,
            "记忆正文，须同时满足以下要求："
            "①仅记录用户明确陈述的事实，不含推断或猜测；"
            "②人物用全名，禁止使用代词（'他'/'她'）；若人物有平台ID，必须在正文中标注，格式：名称（平台: 123456）；"
            "③涉及时间必须写绝对日期（YYYY-MM-DD），不用相对时间（'明天'/'下周'）；"
            "④脱离对话上下文仍能独立理解，必要时补充背景",
        ],
        core_tags: Annotated[list[str], "核心语义标签（检索时最优先匹配）"],
        diffusion_tags: Annotated[list[str], "扩散联想标签"],
        opposing_tags: Annotated[list[str], "对立标签（抑制语义方向）"],
        folder: Annotated[
            _FOLDER_IDS,
            "目标记忆文件夹："
            "relations=人物关系, plans=未来规划, facts=已知事实, "
            "preferences=个人偏好, events=重要事件, work=工作学习, default=未分类",
        ] = "default",
        bucket_hint: Annotated[
            Literal["emergent", "archived", "inherent"],
            "记忆层级：emergent=隐现（近期, 默认）/ archived=典存（长期）/ inherent=固有（根本性，每 folder 唯一）",
        ] = "emergent",
    ) -> tuple[bool, str | dict[str, Any]]:
        """执行记忆写入任务，内部将运行多轮 LLM 工具调用循环完成实际写入。

        内部过程：
        1. 将输入内容和标签组合成任务 payload 传给内部 LLM。
        2. 内部 LLM 根据系统提示执行：标签构建→写入/更新操作→结果审计（必要时可检索辅助定位/去重）。
        3. 检测到 ``memory_finish_task`` 调用时退出循环并返回审计摘要。
        4. 超过最大推理步数时返回错误并附工具调用轨迹。

        Args:
            title: 记忆标题，将与 content 合并后统一写入向量库。
            content: 记忆正文，不能为空。
            core_tags: 核心语义标签，内部 LLM 会将其注入工具调用中。
            diffusion_tags: 扩散标签，指导内部 LLM 选择语义相似场景。
            opposing_tags: 对立标签，指导内部 LLM 排除异层检索方向。
            folder: 目标文件夹，内部 LLM 会根据内容语义最终决定实际写入的 folder_id。
            bucket_hint: 建议写入的记忆层级，内部 LLM 可以根据决策进行调整。

        Returns:
            成功时返回 ``(True, audit_summary)``，audit_summary 为内部 LLM 生成的
            自然语言操作审计摘要（包含操作类型、记忆 ID、标签摘要等）；
            注意：``memory_finish_task`` 仅作为终止信号直接返回上级模型，
            不计入 ``tool_traces``，也不会生成内部 ``ToolResult``；
            失败时返回 ``(False, error_dict)``，error_dict 包含 ``error`` 和 ``tool_traces`` 字段。
        """
        text = content.strip()
        normalized_title = title.strip()
        merged_content = (
            f"# {normalized_title}\n{text}"
            if normalized_title and text
            else (normalized_title or text)
        )
        if not merged_content:
            return False, "title 和 content 至少需要提供一个有效内容"

        try:
            model_set = get_model_set_by_task(self._internal_task_name())
            request = self.create_llm_request(
                model_set=model_set,
                request_name="booku_memory_write_agent_internal",
                with_usables=True,
            )
            base_system_prompt = self._build_system_prompt()
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(base_system_prompt)))
            request.add_payload(
                LLMPayload(
                    ROLE.USER,
                    Text(
                        json.dumps(
                            {
                                "title": normalized_title,
                                "content": text,
                                "merged_content": merged_content,
                                "folder": folder,
                                "bucket_hint": bucket_hint,
                                "core_tags": core_tags or [],
                                "diffusion_tags": diffusion_tags or [],
                                "opposing_tags": opposing_tags or [],
                                "timestamp": time.time(),
                            },
                            ensure_ascii=False,
                        )
                    ),
                )
            )

            response = await request.send(stream=False)
            await response
            tool_traces: list[dict[str, Any]] = []

            max_steps = self._max_reasoning_steps()
            for step_index in range(max_steps):
                calls = response.call_list or []
                if not calls:
                    break
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

                    success, result = await self.execute_local_usable(normalized_name, None, **args)
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

                response.payloads = with_single_system_payload(
                    response.payloads,
                    base_system_prompt=base_system_prompt,
                    step_reminder=build_step_reminder(
                        step_index=step_index,
                        max_steps=max_steps,
                        final_round_instruction=(
                            "请立刻调用 memory_finish_task(content=...) 结束并返回审计摘要，"
                            "不要再调用 memory_create/memory_update_by_id/memory_move/memory_delete/"
                            "memory_read_full_content/memory_status/memory_inherent_read 等其他工具。"
                        ),
                        ongoing_instruction=(
                            "请控制工具调用数量，必要时在最后一轮调用 "
                            "memory_finish_task(content=...) 返回当前操作结果与依据。"
                        ),
                    ),
                )
                response = await response.send(stream=False)
                await response

            return False, {
                "error": "内部规划未调用 memory_finish_task，拒绝返回内部工具结果",
                "tool_traces": tool_traces,
            }

        except Exception as error:
            return False, {"error": str(error)}
