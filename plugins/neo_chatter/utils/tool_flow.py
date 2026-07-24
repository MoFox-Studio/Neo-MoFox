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

from src.app.plugin_system.api.log_api import Logger
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
    seen: set[str] = set()
    pending_calls: list[ToolCall] = []

    async def flush_pending_calls() -> None:
        """批量执行暂存的普通调用，并更新本轮控制流状态。"""
        if not pending_calls:
            return

        current_pending = list(pending_calls)
        pending_calls.clear()
        results = await run_tool_call(current_pending, response, usable_map, trigger_msg)

        for pending_call, (appended, success) in zip(current_pending, results, strict=False):
            outcome.execution_results.append(
                {"name": pending_call.name, "success": bool(success)}
            )
            if appended and not pending_call.name.startswith("action-"):
                outcome.has_pending_tool_results = True

    for call in calls:
        get_watchdog().feed_dog(stream_id)

        args = call.args if isinstance(call.args, dict) else {}
        dedupe_args = (
            {k: v for k, v in args.items() if k != "reason"}
            if isinstance(args, dict)
            else args
        )
        dedupe_key = _build_call_dedupe_key(call.name, dedupe_args)
        if dedupe_key in seen or (seen_signatures is not None and dedupe_key in seen_signatures):
            await flush_pending_calls()
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
        seen.add(dedupe_key)
        if seen_signatures is not None:
            seen_signatures.add(dedupe_key)

        if call.name == pass_call_name:
            await flush_pending_calls()
            wait_seconds = args.get("seconds")
            outcome.wait_seconds = None if wait_seconds is None else float(wait_seconds)
            wait_text = (
                "已登记等待，本轮动作完成后等待用户新消息"
                if outcome.wait_seconds is None
                else f"已登记等待，本轮动作完成后等待 {outcome.wait_seconds} 秒后继续对话"
            )
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

        if call.name == stop_call_name:
            await flush_pending_calls()
            raw_minutes = args.get("minutes")
            try:
                outcome.stop_minutes = (
                    float(raw_minutes) if raw_minutes is not None else float(default_stop_minutes)
                )
            except (TypeError, ValueError):
                outcome.stop_minutes = float(default_stop_minutes)
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

        pending_calls.append(call)

    await flush_pending_calls()
    return outcome


def append_suspend_payload_if_action_only(
    *,
    calls: list[ToolCall],
    response: "LLMResponse",
    suspend_text: str,
    enable_action_suspend: bool,
    logger: Logger,
) -> None:
    """当本轮全是 action 调用时，补充 SUSPEND 占位 assistant 消息。"""
    if enable_action_suspend and calls and all(call.name.startswith("action-") for call in calls):
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
        logger.debug("已注入 SUSPEND 占位符（本轮全部为 action 调用）")


def _build_call_dedupe_key(call_name: str, args: object) -> str:
    """构建 tool call 去重键。"""
    try:
        serialized_args = json.dumps(
            args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except TypeError:
        serialized_args = str(args)
    return f"{call_name}:{serialized_args}"