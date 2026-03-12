"""Default Chatter 工具调用控制流模块。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Awaitable, Callable

from src.core.models.message import Message
from src.kernel.logger import Logger
from src.kernel.llm import LLMPayload, ROLE, Text, ToolResult
from src.kernel.llm import ToolCall, ToolRegistry
from src.kernel.concurrency import get_watchdog

from .type_defs import LLMResponseLike

@dataclass
class ToolCallOutcome:
    """一次 call_list 处理结果。"""

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
        [ToolCall, LLMResponseLike, ToolRegistry, Message | None],
        Awaitable[tuple[bool, bool]],
    ],
    usable_map: ToolRegistry,
    trigger_msg: Message | None,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str | None,
    break_on_send_text: bool,
    cross_round_seen_signatures: set[str] | None = None,
) -> ToolCallOutcome:
    """处理单轮 LLM 的 tool calls 并返回控制流结果。"""
    outcome = ToolCallOutcome()
    seen_call_signatures: set[str] = set()
    sent_text_successfully = False

    for idx, call in enumerate(calls):
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

        appended, success = await run_tool_call(call, response, usable_map, trigger_msg)

        if send_text_call_name and success and call.name == send_text_call_name:
            sent_text_successfully = True

        if appended and not call.name.startswith("action-"):
            # 仅 tool/agent 等“有信息返回、通常需要后续推理”的调用，
            # 才标记为需要继续发起下一轮 LLM 请求。
            # action 调用（如 send_text）执行后通常应等待新消息，不应立即二次请求。
            outcome.has_pending_tool_results = True

        # 注意：不在 send_text 本身处立即 break。
        # 这样可以支持 LLM 在同一轮内多次 action-send_text 分段回复；
        # 但一旦后续出现非 action 调用，会在循环开头被统一跳过并写回 TOOL_RESULT。

    return outcome


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


def append_suspend_payload_if_action_only(
    *,
    calls: list[ToolCall],
    response: LLMResponseLike,
    suspend_text: str,
    logger: Logger,
) -> None:
    """当本轮全是 action 调用时，补充 SUSPEND 占位 assistant 消息。"""
    if calls and all(call.name.startswith("action-") for call in calls):
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
        logger.debug("已注入 SUSPEND 占位符（本轮全部为 action 调用）")
