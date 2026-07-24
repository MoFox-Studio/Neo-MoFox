"""Neo-Chatter 工具调用控制流模块。

负责在主会话逻辑调用统一执行器前，先拦截 ``pass_and_wait`` 与
``stop_conversation`` 两个控制流动作（不写回 TOOL_RESULT，避免模型继续 follow-up），
其余普通调用按原始顺序批量交给 ``run_tool_call``。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger,Logger
from src.app.plugin_system.types import (
    LLMPayload,
    Message,
    ROLE,
    Text,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from src.kernel.concurrency import get_watchdog

if TYPE_CHECKING:
    from src.app.plugin_system.types import LLMResponse

logger: Logger = get_logger("neo_chatter.tool_flow")

@dataclass
class ToolCallOutcome:
    """一次 tool call 列表的控制流处理结果。

    Attributes:
        should_wait: 是否需要等待（pass_and_wait 被调用）。
        wait_seconds: 等待秒数；为 None 表示仅等待新消息。
        should_stop: 是否需要停止当前对话一段时间。
        stop_minutes: 停止对话的分钟数。
        has_pending_tool_results: 是否写入了需要下一轮 LLM 继续推理的非 action 结果。
        execution_results: 本轮各调用的执行结果摘要，供 step_data 上报。
    """

    should_wait: bool = False
    wait_seconds: float | None = None
    should_stop: bool = False
    stop_minutes: float = 0.0
    has_pending_tool_results: bool = False
    execution_results: list[dict[str, object]] = field(default_factory=list)


RunToolCall = Callable[
    [list[ToolCall], "LLMResponse", ToolRegistry, Message | None],
    Awaitable[list[tuple[bool, bool]]],
]
"""统一执行器回调类型签名。

回调负责真正调用工具并把 ``TOOL_RESULT`` 写回 ``response``，返回值是与
``calls`` 等长的二元组列表 ``(appended, success)``：

- ``appended``: 是否成功向 ``response`` 追加了结果消息。
- ``success``: 工具本身执行是否成功（用于上报 step_data）。
"""


async def process_tool_calls(
    *,
    stream_id: str,
    calls: list[ToolCall],
    response: "LLMResponse",
    run_tool_call: RunToolCall,
    usable_map: ToolRegistry,
    trigger_msg: Message | None,
    pass_call_name: str,
    stop_call_name: str,
    default_stop_minutes: float,
    logger: Logger,
    seen_signatures: set[str] | None = None,
) -> ToolCallOutcome:
    """处理单轮 LLM 的 tool calls 并返回控制流结果。

    先处理 pass/stop/去重等控制流调用；普通可执行调用暂存起来，
    在遇到控制流边界或循环结束时批量交给统一执行器。批量执行结果仍按原始
    call 顺序写回 response。

    Args:
        stream_id: 当前对话流 ID，用于喂 watchdog。
        calls: 本轮 LLM 响应中的 tool call 列表。
        response: 当前 LLM 响应对象；控制流结果和 TOOL_RESULT 会写回其中。
        run_tool_call: 批量执行普通 tool calls 的回调。
        usable_map: 可调用组件注册表。
        trigger_msg: 触发本轮对话的消息；为 None 时普通调用会被执行器跳过。
        pass_call_name: "等待新消息"控制流调用名（含 ``action-`` 前缀）。
        stop_call_name: "结束对话"控制流调用名（含 ``action-`` 前缀）。
        default_stop_minutes: stop_conversation 未传 minutes 时的默认冷却分钟数。
        logger: 用于记录调试信息的 logger。
        seen_signatures: 跨轮去重集合；为 None 时只做本轮去重。

    Returns:
        ToolCallOutcome: 本轮控制流与普通调用执行后的汇总结果。
    """
    outcome = ToolCallOutcome()
    # 本轮内已见过的调用签名，避免同一轮 LLM 输出里重复执行同款调用
    seen: set[str] = set()
    # 控制流边界前累积的普通调用，待批量交给执行器
    pending_calls: list[ToolCall] = []

    async def flush_pending_calls() -> None:
        """批量执行暂存的普通调用，并更新本轮控制流状态。

        之所以拆出内部函数：``pass_and_wait`` / ``stop_conversation`` /
        去重跳过 三个分支都需要在各自处理前把已暂存的普通调用一次性 flush
        出去，保证普通调用与控制流调用之间的相对顺序符合模型输出顺序。
        """
        if not pending_calls:
            return

        # 复制一份并立即清空原列表，避免执行过程中再次累积造成死循环
        current_pending = list(pending_calls)
        pending_calls.clear()
        # 批量交给统一执行器；results 与 current_pending 顺序一一对应
        results = await run_tool_call(current_pending, response, usable_map, trigger_msg)

        # 把执行结果摘要回填到 outcome，并判断是否产生了下一轮需要 LLM 继续推理的 TOOL_RESULT
        for pending_call, (appended, success) in zip(current_pending, results, strict=False):
            outcome.execution_results.append(
                {"name": pending_call.name, "success": bool(success)}
            )
            # action-* 调用不向模型回写结果，因此不会触发 pending_tool_results
            if appended and not pending_call.name.startswith("action-"):
                outcome.has_pending_tool_results = True

    # ===== 主循环：按模型输出顺序逐条处理 tool calls =====
    for call in calls:
        # 每处理一条都喂 watchdog，避免长轮 tool calls 被判定为卡死
        get_watchdog().feed_dog(stream_id)

        # 规范化入参：非 dict 视作空 dict，方便后续按 key 取值
        args = call.args if isinstance(call.args, dict) else {}
        # 去重时忽略 reason 字段：相同动作不同理由仍视为同一调用
        dedupe_args = (
            {k: v for k, v in args.items() if k != "reason"}
            if isinstance(args, dict)
            else args
        )
        # 生成形如 "call_name:{json}" 的稳定签名
        dedupe_key = _build_call_dedupe_key(call.name, dedupe_args)

        # 去重判定：本轮已见过 或 跨轮去重集合中已存在 都跳过
        if dedupe_key in seen or (seen_signatures is not None and dedupe_key in seen_signatures):
            # 跳过前先把已暂存的普通调用 flush 掉，保证顺序
            await flush_pending_calls()
            # 写回一条 TOOL_RESULT 告知模型本次调用被跳过，避免模型认为调用成功而后续走错流程
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value="检测到重复工具调用，已自动跳过",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            continue

        # 登记到本轮与跨轮去重集合，确保后续相同调用同样被跳过
        seen.add(dedupe_key)
        if seen_signatures is not None:
            seen_signatures.add(dedupe_key)

        # ----- 控制流分支 1：pass_and_wait（等待新消息）-----
        if call.name == pass_call_name:
            # 进入等待前先把已累积的普通调用批量执行，确保等待动作生效时它们已完成
            await flush_pending_calls()
            # seconds 缺省表示无限期等待新消息；否则按用户指定秒数定时唤醒
            wait_seconds = args.get("seconds")
            outcome.wait_seconds = None if wait_seconds is None else float(wait_seconds)
            wait_text = (
                "已登记等待，本轮动作完成后等待用户新消息"
                if outcome.wait_seconds is None
                else f"已登记等待，本轮动作完成后等待 {outcome.wait_seconds} 秒后继续对话"
            )
            # 写回提示文本，但本函数不再触发后续 LLM follow-up（由 should_wait 标志位控制上层逻辑）
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value=wait_text,
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            outcome.should_wait = True
            continue

        # ----- 控制流分支 2：stop_conversation（结束对话）-----
        if call.name == stop_call_name:
            # 停止前同样先 flush 普通调用，避免被丢弃导致本轮应完成的副作用未执行
            await flush_pending_calls()
            # 解析 minutes；非法或缺省时退回默认冷却分钟数
            raw_minutes = args.get("minutes")
            try:
                outcome.stop_minutes = (
                    float(raw_minutes) if raw_minutes is not None else float(default_stop_minutes)
                )
            except (TypeError, ValueError):
                outcome.stop_minutes = float(default_stop_minutes)
            # 写回停止提示文本，由上层根据 should_stop 切换对话状态
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value=f"对话已结束，将在 {outcome.stop_minutes} 分钟后允许新对话",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            outcome.should_stop = True
            continue

        # ----- 默认分支：普通可执行调用，先暂存等待批量执行 -----
        pending_calls.append(call)

    # 循环结束前若还有未 flush 的普通调用，补一次批量执行
    await flush_pending_calls()
    return outcome


def append_suspend_payload_if_action_only(
    *,
    calls: list[ToolCall],
    response: "LLMResponse",
    suspend_text: str,
    enable_action_suspend: bool,
) -> None:
    """当本轮全是 action 调用时，补充 SUSPEND 占位 assistant 消息。

    若本轮 LLM 输出的 tool calls 全部以 ``action-`` 开头，意味着模型
    只产出动作、没有可让模型继续推理的 ``TOOL_RESULT``。这种情况下若上层
    不注入占位 assistant 消息，下一轮上下文会以 tool_result 结尾，部分
    模型会因此报错。本函数负责在该场景下补一段占位文本。

    Args:
        calls: 本轮 LLM 响应中的 tool call 列表。
        response: 当前 LLM 响应对象；占位 assistant 消息会写回其中。
        suspend_text: SUSPEND 占位符的文本内容。
        enable_action_suspend: 总开关；为 False 时本函数直接 no-op。
        logger: 用于记录调试信息的 logger。
    """
    # 仅在开关开启且 calls 非空且全部为 action-* 时注入，避免误污染普通对话轮
    if enable_action_suspend and calls and all(call.name.startswith("action-") for call in calls):
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
        logger.debug("已注入 SUSPEND 占位符（本轮全部为 action 调用）")


def append_suspend_payload_if_tool_result_tail(
    *,
    response: "LLMResponse",
    suspend_text: str,
) -> None:
    """若 response 末尾 payload 是 TOOL_RESULT，追加一条 ASSISTANT SUSPEND 占位。

    用于 ``pass_and_wait`` 进入 ``Wait`` 之前：闭合工具结果尾巴，避免下一轮
    LLM 把上一条 ``TOOL_RESULT`` 当成自己未收尾的发言而续写、复述或把
    紧随其后的用户消息与工具回执串成一段连续输入.

    与 :func:`append_suspend_payload_if_action_only` 互补：
    - 前者面向「纯 action 回合」的尾态闭合，受 ``enable_action_suspend`` 开关约束；
    - 后者面向「pass_and_wait 要求定时等待」的尾态闭合，无论开关与否都生效，
      因为定时等待结束后模型会基于该上下文继续推理，裸 TOOL_RESULT 尾巴必须
      显式闭合。

    Args:
        response: 当前 LLM 响应对象；占位 assistant 消息会写回其中。
        suspend_text: SUSPEND 占位符的文本内容。
        logger: 用于记录调试信息的 logger。
    """
    payloads = getattr(response, "payloads", None)
    if not payloads or payloads[-1].role != ROLE.TOOL_RESULT:
        return
    response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
    logger.debug("注入 SUSPEND 占位符以在等待之前关闭工具结果尾部")


def _build_call_dedupe_key(call_name: str, args: object) -> str:
    """构建 tool call 的去重键。

    将调用名与入参序列化为稳定字符串 ``"<call_name>:<json>"``，便于放入
    ``set`` 做本轮或跨轮去重判定。使用 ``sort_keys=True`` 与紧凑分隔符确保
    相同语义的不同 dict 字面量（如键顺序不同）能映射到同一键。

    Args:
        call_name: tool call 的名称（含可能的 ``action-`` 前缀）。
        args: 已经预处理过（如剥离 reason 字段）的入参对象，通常为 dict。

    Returns:
        形如 ``"call_name:{json}"`` 的稳定字符串键。

    Note:
        若 ``args`` 包含不可 JSON 序列化的对象，会退回 ``str(args)`` 作为兜底，
        保证函数永不抛 ``TypeError``。
    """
    try:
        # sort_keys=True 保证 dict 键顺序不影响序列化结果；separators 去掉多余空白
        serialized_args = json.dumps(
            args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except TypeError:
        # 极端情况下 json.dumps 仍可能失败（例如自定义 __str__ 抛错），退回 str()
        serialized_args = str(args)
    return f"{call_name}:{serialized_args}"