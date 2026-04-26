"""Default Chatter 工具调用控制流模块。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.core.models.message import Message
from src.kernel.logger import Logger
from src.kernel.llm import LLMPayload, ROLE, Text, ToolResult
from src.kernel.llm import ToolCall, ToolRegistry
from src.kernel.concurrency import get_watchdog

from .type_defs import LLMResponseLike


@dataclass
class ToolCallOutcome:
    """一次 tool call 列表的控制流处理结果。

    Attributes:
        should_wait: 是否需要等待用户新消息。
        should_stop: 是否需要停止当前对话一段时间。
        stop_minutes: 停止对话的分钟数。
        sent_once: 本轮是否已经成功执行过 send_text。
        has_pending_tool_results: 是否写入了需要下一轮 LLM 继续推理的非 action 结果。
    """

    should_wait: bool = False
    should_stop: bool = False
    stop_minutes: float = 0.0
    sent_once: bool = False
    has_pending_tool_results: bool = False


async def process_tool_calls(
    *,
    stream_id: str,
    calls: list[ToolCall],
    response: LLMResponseLike,
    run_tool_call: Callable[
        [list[ToolCall], LLMResponseLike, ToolRegistry, Message | None],
        Awaitable[list[tuple[bool, bool]]],
    ],
    usable_map: ToolRegistry,
    trigger_msg: Message | None,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str | None,
    break_on_send_text: bool,
    cross_round_seen_signatures: set[str] | None = None,
) -> ToolCallOutcome:
    """处理单轮 LLM 的 tool calls 并返回控制流结果。

    该函数会先处理 pass/stop/去重等控制流调用；普通可执行调用会暂存起来，
    在遇到控制流边界或循环结束时批量交给统一执行器。批量执行结果仍按原始
    call 顺序写回 response。

    Args:
        stream_id: 当前对话流 ID，用于喂 watchdog。
        calls: 本轮 LLM 响应中的 tool call 列表。
        response: 当前 LLM 响应对象；控制流结果和 TOOL_RESULT 会写回其中。
        run_tool_call: 批量执行普通 tool calls 的回调。
        usable_map: 可调用组件注册表。
        trigger_msg: 触发本轮对话的消息；为 None 时普通调用会被执行器跳过。
        pass_call_name: “等待新消息”控制流调用名。
        stop_call_name: “结束对话”控制流调用名。
        send_text_call_name: 发送文本 Action 的调用名；为 None 时不启用特殊处理。
        break_on_send_text: 成功发送文本后，是否跳过后续第一个非 action 及其之后的调用。
        cross_round_seen_signatures: 跨轮去重集合；为 None 时只做本轮去重。

    Returns:
        ToolCallOutcome: 本轮控制流与普通调用执行后的汇总结果。
    """
    outcome = ToolCallOutcome()
    seen_call_signatures: set[str] = set()
    sent_text_successfully = False
    pending_calls: list[ToolCall] = []

    async def flush_pending_calls() -> None:
        """批量执行暂存的普通调用，并更新本轮控制流状态。"""
        nonlocal sent_text_successfully

        if not pending_calls:
            return

        current_pending = list(pending_calls)
        pending_calls.clear()
        results = await run_tool_call(current_pending, response, usable_map, trigger_msg)

        for pending_call, (appended, success) in zip(current_pending, results, strict=False):
            if send_text_call_name and success and pending_call.name == send_text_call_name:
                sent_text_successfully = True

            if appended and not pending_call.name.startswith("action-"):
                outcome.has_pending_tool_results = True

    for idx, call in enumerate(calls):
        if (
            break_on_send_text
            and pending_calls
            and send_text_call_name
            and any(pending.name == send_text_call_name for pending in pending_calls)
            and not call.name.startswith("action-")
        ):
            await flush_pending_calls()

        get_watchdog().feed_dog(stream_id)  # 喂狗，防止工具调用过久导致 Watchdog 误判超时

        # classical 模式可配置为“发出一次 send_text 后不再继续推理型工具调用”。
        # 但若后续仍是 action（例如再次 send_text 分段回复），则应允许继续执行。
        if break_on_send_text and sent_text_successfully and not call.name.startswith("action-"):
            for skipped in calls[idx:]:
                response.add_payload(
                    LLMPayload(
                        ROLE.TOOL_RESULT,
                        ToolResult(  # type: ignore[arg-type]
                            value="已成功发送消息，本轮后续非 action 调用已自动跳过",
                            call_id=getattr(skipped, "id", None),
                            name=getattr(skipped, "name", ""),
                        ),
                    )
                )
            outcome.sent_once = True
            break

        args = call.args if isinstance(call.args, dict) else {}
        dedupe_args = (
            {key: value for key, value in args.items() if key != "reason"}
            if isinstance(args, dict)
            else args
        )
        dedupe_key = _build_call_dedupe_key(call.name, dedupe_args)
        if dedupe_key in seen_call_signatures:
            await flush_pending_calls()
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value="检测到同一轮重复工具调用，已自动跳过",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            continue

        if cross_round_seen_signatures is not None and dedupe_key in cross_round_seen_signatures:
            await flush_pending_calls()
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value="检测到跨轮重复工具调用，已自动跳过",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            continue
        seen_call_signatures.add(dedupe_key)
        if cross_round_seen_signatures is not None:
            cross_round_seen_signatures.add(dedupe_key)

        if call.name == pass_call_name:
            await flush_pending_calls()
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value="已跳过，等待用户新消息",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            outcome.should_wait = True
            continue

        if call.name == stop_call_name:
            await flush_pending_calls()
            outcome.stop_minutes = float(args.get("minutes", 5.0))
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

        # 注意：不在 send_text 本身处立即 break。
        # 这样可以支持 LLM 在同一轮内多次 action-send_text 分段回复；
        # 但一旦后续出现非 action 调用，会在循环开头被统一跳过并写回 TOOL_RESULT。

    await flush_pending_calls()
    return outcome


def _build_call_dedupe_key(call_name: str, args: object) -> str:
    """构建 tool call 去重键。

    Args:
        call_name: tool call 名称。
        args: tool call 参数；会尽量稳定序列化以避免同参重复调用。

    Returns:
        str: 用于本轮和跨轮去重的稳定键。
    """
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


def append_suspend_payload_if_action_only(
    *,
    calls: list[ToolCall],
    response: LLMResponseLike,
    suspend_text: str,
    logger: Logger,
) -> None:
    """当本轮全是 action 调用时，补充 SUSPEND 占位 assistant 消息。

    Args:
        calls: 本轮 LLM 响应中的 tool call 列表。
        response: 当前 LLM 响应对象；需要时会写入 assistant 占位消息。
        suspend_text: 占位消息文本。
        logger: 用于记录调试信息的 logger。
    """
    if calls and all(call.name.startswith("action-") for call in calls):
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
        logger.debug("已注入 SUSPEND 占位符（本轮全部为 action 调用）")
