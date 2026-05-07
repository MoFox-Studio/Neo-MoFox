"""聊天场景下的默认上下文压缩实现。"""

from __future__ import annotations

import re

from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
from src.kernel.llm.types import ModelEntry, ModelSet

CONTEXT_COMPRESSION_TIMEOUT_SECONDS = 120.0
CONTEXT_COMPRESSION_MAX_RETRY = 3

DEFAULT_CHAT_CONTEXT_COMPRESSION_PROMPT = """根据你的需求，我将原始面向代码开发场景的上下文压缩提示词，调整为面向**社交平台聊天机器人**的版本。修改要点：将技术细节（文件名、代码片段、函数签名、错误修复等）替换为对话交互元素（话题、情感、用户意图、回复风格、冲突/误解、用户纠正等），保留原有的分析流程和摘要结构框架，但内容描述完全适配聊天机器人场景。

以下是修改后的提示词：

---

# 社交平台聊天机器人上下文压缩提示词（中文版）

## 主要提示
你的任务是创建一个迄今为止对话的详细摘要，密切关注用户的表述、情感倾向、话题演变以及你作为机器人的历史回复。该摘要应全面捕获用户的核心诉求、讨论的关键话题、你已提供的答复以及任何未解决的疑问或冲突，以便在不丢失对话连续性和用户个性的情况下继续后续交流。

## 分析流程
在提供最终摘要之前，请将你的分析包装在 <analysis> 标签中，以组织你的思路并确保涵盖所有必要的要点。在分析过程中：
按时间顺序分析对话中的每条消息。对每条消息深入识别：
- 用户的明确请求、问题或陈述
- 用户消息中隐含的情感或态度（如：愤怒、困惑、兴奋、幽默、讽刺等）
- 你作为机器人给出的回复内容、风格和效果
- 用户对你回复的反馈（是否满意、要求澄清、改变话题、纠正你等）
- 关键话题、观点、事实或故事细节，例如：
  - 用户分享的个人经历、观点或感受
  - 你给出的建议、解释、安慰或创意内容
  - 用户特别强调或重复的诉求
  - 任何误解、重复问题、用户纠正你错误的情况

特别关注用户明确要求你改变回复方式、更正信息或表达不满的时刻。仔细检查对话的连贯性和情感一致性，确保不遗漏用户的重要背景信息。

## 摘要结构
你的摘要应包括以下部分：

### 1. 主要请求和意图

详细捕获用户的所有明确请求、问题以及隐含的交流目的（例如：寻求建议、倾诉情绪、获取信息、娱乐闲聊等）。

### 2. 关键话题/概念

列出对话中讨论的所有重要话题、观点、人物、事件或专业概念（如：心理学名词、网络梗、产品推荐等）。

### 3. 对话历史关键消息

逐条或分段总结对话中的重要消息。重点关注：
- 用户的核心陈述或提问（保留关键的原文引用）
- 你给出的重要回复（特别是那些被用户认可或纠正的）
- 任何导致话题转折的节点
- 用户重复强调或追问的内容
- 总结每条关键消息为何重要（例如：建立了用户偏好、暴露了矛盾、表明了情感状态）

### 4. 错误和纠正

列出你（机器人）在对话中出现的任何不恰当回复、事实错误、逻辑矛盾或风格不符，以及用户如何指出并要求纠正。特别关注用户明确告诉你“不对”、“重新回答”、“换个方式”等反馈。

### 5. 问题解决

记录已解决的用户疑问、已满足的请求，以及任何尚未解决或正在进行的讨论。

### 6. 所有用户消息

逐条列出用户发送的所有原始消息（非你生成的回复内容）。这有助于完整保留用户的表达轨迹和意图变化。

### 7. 待处理任务

概述用户明确要求你完成但尚未执行的任何任务，例如：“稍后提醒我”、“帮我找一下XX资料”、“继续讲那个故事”等。

### 8. 当前工作

详细描述在此摘要请求之前正在进行的对话内容。包括用户最近的一条或几条消息，以及你最近给出的回复（如果你在中途停止）。如有被中断的回复或未完成的回答，请明确指出。

### 9. 可选的下一步

列出与当前对话状态最相关的下一步行动建议。例如：继续回答用户未尽的提问、澄清之前可能产生的误解、跟进用户提出的某个话题、或者主动询问用户对某个回复的满意度。确保下一步与用户明确表达的需求以及你正在进行的任务直接一致。如果上一轮对话已自然结束，则只在与用户请求相符的情况下提出下一步，不得擅自开启无关话题。

如果有下一步，请包含最近对话中的直接引用（用户或你的原话），准确显示你正在处理的任务以及停止的位置。引用应为逐字逐句，以确保理解无偏差。

## 输出格式示例（XML 格式）
<analysis>
  [你的思考过程，确保全面准确地涵盖上述所有要点]
</analysis>

<summary>
  1. 主要请求和意图：
  [详细描述]

  2. 关键话题/概念：
  - [话题 1]
  - [话题 2]
  - [...]

  3. 对话历史关键消息：
  - 用户：[引用关键消息] → 意义：[为什么重要]
  - 助手：[引用你的回复] → 效果：[用户如何反应]
  - [...]

  4. 错误和纠正：
  - [错误描述]：你最初回复了X，用户指出Y，你随后纠正为Z。
  - [...]

  5. 问题解决：
  [已解决问题和仍待处理问题的描述]

  6. 关键用户消息：
  - “[用户消息 1]”
  - “[用户消息 2]”
  - [...]

  7. 待处理任务：
  - [任务 1]
  - [任务 2]
  - [...]

  8. 当前工作：
  [当前对话状态的精确描述，包含最近1-2条用户消息和你未完成的回复]

  9. 可选的下一步：
  [如果有，列出建议的行动，并附上相关原话引用]

</summary>

## 附加说明

请根据迄今为止的对话提供摘要，遵循此结构并确保回复的精确性和全面性。"""


def _clone_models_for_context_compression(model_set: ModelSet) -> ModelSet:
    """为上下文压缩请求生成固定超时和重试配置。"""

    return [
        {
            **model,
            "timeout": CONTEXT_COMPRESSION_TIMEOUT_SECONDS,
            "max_retry": CONTEXT_COMPRESSION_MAX_RETRY,
        }
        for model in model_set
    ]


def _extract_summary_content(raw_text: str) -> str:
    """从模型返回中提取 summary 节点内容。"""

    if not raw_text:
        return ""

    match = re.search(r"<summary>(.*?)</summary>", raw_text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return raw_text.strip()


async def default_chat_context_compression_handler(
    request: LLMRequest,
    dropped_groups: list[list[LLMPayload]],
    remaining_payloads: list[LLMPayload],
    model: ModelEntry,
) -> list[LLMPayload]:
    """将超出窗口的历史对话压缩为单条 user 摘要消息。"""

    del remaining_payloads, model

    history_payloads = [payload for group in dropped_groups for payload in group]
    if not history_payloads:
        return []

    compression_request = LLMRequest(
        model_set=_clone_models_for_context_compression(request.model_set),
        request_name=f"{request.request_name}:context_compression",
        clients=request.clients,
        enable_metrics=request.enable_metrics,
    )
    compression_request.payloads = list(history_payloads) + [
        LLMPayload(ROLE.USER, Text(DEFAULT_CHAT_CONTEXT_COMPRESSION_PROMPT))
    ]

    response = await compression_request.send(auto_append_response=False, stream=False)
    summary_content = _extract_summary_content(response.message or "")
    if not summary_content:
        return []

    compressed_context = (
        "以下是已经压缩过的历史对话上下文，请将其视为此前已经发生的交流，并在此基础上继续当前对话：\n\n"
        f"<summary>\n{summary_content}\n</summary>"
    )
    return [LLMPayload(ROLE.USER, Text(compressed_context))]