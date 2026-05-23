"""Default Chatter 执行器模块。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, TypeGuard

from src.core.components.base import Wait, WaitResumeEvent, Success, Failure, Stop
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.kernel.logger import Logger
from src.kernel.llm import LLMPayload, ROLE, Text

from .type_defs import DefaultChatterRuntime, LLMConversationState, LLMResponseLike
from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls

# LLM 返回纯文本（非 tool call）时的最大重试次数
_MAX_PLAIN_TEXT_RETRIES = 0

# 重试时注入的提醒文本
_PLAIN_TEXT_RETRY_REMINDER = (
    "（系统提示：你刚才返回了纯文本而非工具调用。"
    "请务必通过工具调用来完成任务，不要直接输出文字回复。）"
)
_AFTER_CHATTER_STEP_SCOPE = "actor_round"


class _ToolCallWorkflowPhase(str, Enum):
    """default_chatter 的 toolcall 工作流相位（简化 FSM）。

    约束目标：强制会话严格遵守
    USER → ASSISTANT(tool_calls) → TOOL_RESULT → ASSISTANT(follow-up) → USER

    - 仅在 WAIT_USER 阶段允许注入新的 USER payload
    - 仅在 MODEL_TURN/FOLLOW_UP 阶段允许向模型发起 send
    - TOOL_EXEC 阶段只执行工具并写回 TOOL_RESULT，不发起新的 USER
    """

    WAIT_USER = "wait_user"
    MODEL_TURN = "model_turn"
    TOOL_EXEC = "tool_exec"
    FOLLOW_UP = "follow_up"


@dataclass
class _EnhancedWorkflowRuntime:
    """enhanced 模式运行时状态。"""

    response: LLMConversationState
    phase: _ToolCallWorkflowPhase
    history_merged: bool
    unreads: list[Message]
    cross_round_seen_signatures: set[str]
    unread_msgs_to_flush: list[Message]
    plain_text_retry_count: int = 0
    used_tools_in_round: set[str] = field(default_factory=set)

    def has_tool_result_tail(self) -> bool:
        """当前上下文尾部是否为 TOOL_RESULT。"""
        payloads = getattr(self.response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)


def _is_response_like(response: LLMConversationState) -> TypeGuard[LLMResponseLike]:
    """判断当前会话状态是否已经进入响应阶段。"""
    return hasattr(response, "call_list") and hasattr(response, "message")


def _require_response(response: LLMConversationState) -> LLMResponseLike:
    """将会话状态收窄为已完成的 LLM 响应。"""
    if _is_response_like(response):
        return response
    raise TypeError("当前会话状态尚未进入响应阶段")


def _format_tool_args(args: Any) -> str:
    """格式化单个工具调用的参数展示。"""
    if not isinstance(args, dict):
        return ""

    display_items: list[str] = []
    for key, value in args.items():
        if key == "reason":
            continue
        display_items.append(f"{key}: {value}")
    return ", ".join(display_items)


def _collect_used_tool_names(calls: list[Any]) -> set[str]:
    """提取当前 actor 响应里实际调用过的工具名。"""

    return {
        normalized_name
        for call in calls
        if (normalized_name := str(getattr(call, "name", "") or "").strip())
    }


def _consume_actor_round_step_data(rt: _EnhancedWorkflowRuntime) -> dict[str, Any]:
    """在 actor 整轮结束时提取并重置轮次步骤数据。"""

    used_tools = sorted(rt.used_tools_in_round)
    rt.used_tools_in_round.clear()
    return {
        "step_scope": _AFTER_CHATTER_STEP_SCOPE,
        "used_tools": used_tools,
    }


def _is_suspend_message(message: str | None, suspend_text: str) -> bool:
    """判断模型返回是否为 SUSPEND 挂起文本。"""
    return isinstance(message, str) and message.strip() == suspend_text


def _is_timer_resume_event(event: WaitResumeEvent | None) -> bool:
    """判断本轮是否由定时 wait 主动恢复。"""
    return event is not None and event.source == "timer"


def _is_sub_agent_resume_event(event: WaitResumeEvent | None) -> bool:
    """判断本轮是否由子代理后台完成主动恢复。"""
    return event is not None and event.source == "sub_agent"


def _append_suspend_payload_if_tool_result_tail(
    *,
    response: LLMResponseLike,
    suspend_text: str,
    logger: Logger,
) -> None:
    """在进入等待前用占位 assistant 闭合尾部 TOOL_RESULT。"""
    payloads = getattr(response, "payloads", None)
    if not payloads or payloads[-1].role != ROLE.TOOL_RESULT:
        return

    response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
    logger.debug("已注入 SUSPEND 占位符（等待前闭合工具结果）")


def _extract_latest_user_text(response: LLMConversationState) -> str:
    """从当前请求上下文里提取最近一条 USER 文本。"""
    payloads = getattr(response, "payloads", None) or []
    for payload in reversed(payloads):
        if str(getattr(payload, "role", "")) != str(ROLE.USER):
            continue

        text_parts: list[str] = []
        for item in getattr(payload, "content", None) or []:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text)

        if text_parts:
            return "\n".join(text_parts)

    return ""


def _build_synthetic_trigger_message(chat_stream: ChatStream, prompt_text: str) -> Message:
    """为主动恢复轮次构造最小触发消息，避免工具调用因缺少 message 被跳过。"""
    return Message(
        message_id=f"actor-{int(time.time() * 1000)}",
        content=prompt_text,
        processed_plain_text=prompt_text,
        platform=str(getattr(chat_stream, "platform", "") or ""),
        chat_type=str(getattr(chat_stream, "chat_type", "") or "private"),
        stream_id=str(getattr(chat_stream, "stream_id", "") or ""),
        sender_name="actor",
    )


def _pick_actor_trigger_message(
    *,
    chat_stream: ChatStream,
    rt: _EnhancedWorkflowRuntime,
) -> Message:
    """为 actor 当前轮工具调用选择触发消息。"""
    if rt.unreads:
        return rt.unreads[-1]

    context = getattr(chat_stream, "context", None)
    if context is not None:
        current_message = getattr(context, "current_message", None)
        if current_message is not None:
            return current_message

        unread_messages = getattr(context, "unread_messages", None) or []
        if unread_messages:
            return unread_messages[-1]

        history_messages = getattr(context, "history_messages", None) or []
        if history_messages:
            return history_messages[-1]

    return _build_synthetic_trigger_message(
        chat_stream,
        _extract_latest_user_text(rt.response),
    )


def _build_wait_timeout_prompt(event: WaitResumeEvent) -> str:
    """构建 wait 定时到期后的主动恢复提示词。"""
    waited_text = (
        "你之前设置的等待时间已经结束。"
        if event.wait_time is None
        else f"你之前设置的等待 {event.wait_time} 秒已经结束。"
    )
    return (
        f"系统事件：{waited_text} 当前没有新的用户消息。"
        "请基于已有上下文主动决定下一步。"
        "如果现在不应继续，请再次调用 pass_and_wait；"
        "如果需要回复或执行动作，请直接使用相应工具。"
    )


def _build_sub_agent_resume_prompt(_: WaitResumeEvent) -> str:
    """构建子代理后台完成后的主动恢复提示词。"""
    return (
        "系统事件：有子代理已在后台完成一轮任务。"
        "请查看动态 system reminder 中的子代理最新 assistant 动态，"
        "并结合已有上下文决定下一步。"
        "如果现在无需继续处理，请调用 pass_and_wait；"
        "如果需要继续回复、委派或执行动作，请直接使用相应工具。"
    )


def _build_sub_agent_result_user_prompt(events: list[dict[str, Any]]) -> str:
    """把子代理完成结果拼成一次性的 USER 消息。"""
    lines = ["以下是子代理刚刚返回的结果，请基于这些结果继续处理："]
    for event in events:
        name = str(event.get("name", "unknown"))
        status = str(event.get("status", "completed"))
        content = str(event.get("content", "")).strip() or "(无文本结果)"
        lines.append(f"[{name}] {status}")
        lines.append(content)
    return "\n".join(lines)


def _build_actor_decision_panel(chat_stream: ChatStream, response: LLMResponseLike) -> str:
    """构建 actor 本次决策摘要面板内容。"""
    stream_name = (
        getattr(chat_stream, "stream_name", "")
        or getattr(chat_stream, "stream_id", "")
        or "未知聊天流"
    )
    thought = response.reasoning_content.strip() if response.reasoning_content else "（无）"
    monologue = response.message.strip() if response.message else "（无）"

    tool_lines = []
    for call in response.call_list or []:
        formatted_args = _format_tool_args(call.args)
        if formatted_args:
            tool_lines.append(f"    {call.name} ({formatted_args})")
        else:
            tool_lines.append(f"    {call.name}")

    tools_text = "\n".join(tool_lines) if tool_lines else "    （无）"
    return (
        f"聊天流名称：{stream_name}\n\n"
        f"思考：{thought}\n\n"
        f"独白：{monologue}\n\n"
        f"调用工具：\n{tools_text}"
    )


def _print_actor_decision_panel(
    chat_stream: ChatStream,
    response: LLMResponseLike,
    logger: Logger,
) -> None:
    """当 actor 给出 tool call 时打印本次决策摘要。"""
    if not response.call_list:
        return

    print_panel = getattr(logger, "print_panel", None)
    if callable(print_panel):
        print_panel(
            _build_actor_decision_panel(chat_stream, response),
            title="Actor 决策",
            border_style="cyan",
        )


def _transition(
    *,
    rt: _EnhancedWorkflowRuntime,
    to_phase: _ToolCallWorkflowPhase,
    logger: Logger,
    reason: str,
) -> None:
    """执行状态机相位切换，并记录调试日志。"""
    if rt.phase == to_phase:
        return
    debug_fn = getattr(logger, "debug", None)
    if callable(debug_fn):
        debug_fn(f"[FSM] {rt.phase.value} -> {to_phase.value}: {reason}")
    rt.phase = to_phase


async def run_enhanced(
    chatter: DefaultChatterRuntime,
    chat_stream: ChatStream,
    logger: Logger,
    pass_call_name: str,
    stop_call_name: str,
    suspend_text: str,
    enable_action_suspend: bool = True,
    enable_cooldown: bool = False,
    native_multimodal: bool = False,
) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]:
    """enhanced 模式执行流程。

    Args:
        native_multimodal: 启用后将 image 媒体以 base64 直接打包进 USER payload，
            同时为当前 stream 注册 "image" 类型的 VLM 跳过（表情包 emoji 仍由框架
            VLM 生成描述，受益于哈希缓存）。执行结束后会自动取消注册，避免残留副作用。
            历史消息不传图片，仅未读消息携带图片。
    """
    if native_multimodal:
        from src.core.managers.media_manager import get_media_manager
        get_media_manager().skip_vlm_for_stream(chat_stream.stream_id, ["image"])
        logger.debug(f"已为 stream {chat_stream.stream_id[:8]} 注册跳过 image 类型的 VLM 识别")

    try:
        request = chatter.create_request("actor", with_reminder="actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    system_prompt_text = await chatter._build_system_prompt(chat_stream)
    request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

    history_text = chatter._build_enhanced_history_text(chat_stream)
    usable_map = await chatter.inject_usables(request)

    rt = _EnhancedWorkflowRuntime(
        response=request,
        phase=_ToolCallWorkflowPhase.FOLLOW_UP if request.payloads and request.payloads[-1].role == ROLE.TOOL_RESULT else _ToolCallWorkflowPhase.WAIT_USER,
        history_merged=False,
        unreads=[],
        cross_round_seen_signatures=set(),
        unread_msgs_to_flush=[],
    )

    resume_event: WaitResumeEvent | None = None

    while True:
        current_resume_event = resume_event
        resume_event = None
        _, unread_msgs = await chatter.fetch_unreads()

        # 安全兜底：若上下文尾部为 TOOL_RESULT，必须进入 FOLLOW_UP
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER and rt.has_tool_result_tail():
            _transition(
                rt=rt,
                to_phase=_ToolCallWorkflowPhase.FOLLOW_UP,
                logger=logger,
                reason="context tail is TOOL_RESULT; must follow-up before new USER",
            )

        # FSM 驱动：每次循环只推进一个相位（或 yield）
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER:
            if _is_timer_resume_event(current_resume_event) or _is_sub_agent_resume_event(
                current_resume_event
            ):
                assert current_resume_event is not None
                rt.cross_round_seen_signatures.clear()
                rt.plain_text_retry_count = 0
                rt.used_tools_in_round.clear()
                rt.unreads = []
                rt.unread_msgs_to_flush = []
                reminder_text = (
                    _build_sub_agent_resume_prompt(current_resume_event)
                    if _is_sub_agent_resume_event(current_resume_event)
                    else _build_wait_timeout_prompt(current_resume_event)
                )
                if _is_sub_agent_resume_event(current_resume_event):
                    from .sub_agent_collaboration import (
                        get_sub_agent_collaboration_manager,
                    )

                    completed_events = get_sub_agent_collaboration_manager().drain_completed_events(
                        chatter.stream_id
                    )
                    if completed_events:
                        reminder_text = _build_sub_agent_result_user_prompt(completed_events)

                chatter._upsert_pending_unread_payload(
                    response=rt.response,
                    formatted_text=reminder_text,
                )
                _transition(
                    rt=rt,
                    to_phase=_ToolCallWorkflowPhase.MODEL_TURN,
                    logger=logger,
                    reason=(
                        "sub-agent completed"
                        if _is_sub_agent_resume_event(current_resume_event)
                        else "wait timer elapsed"
                    ),
                )
                continue

            if not unread_msgs:
                resume_event = yield Wait()
                continue

            # 仅在采纳新未读消息时清空跨轮去重状态；FOLLOW_UP 阶段不应清空。
            rt.cross_round_seen_signatures.clear()
            rt.plain_text_retry_count = 0
            rt.used_tools_in_round.clear()
            rt.unreads = unread_msgs

            unread_lines = "\n".join(
                chatter.format_message_line(msg) for msg in unread_msgs
            )
            unread_user_prompt = await chatter._build_user_prompt(
                chat_stream,
                history_text=history_text if not rt.history_merged else "",
                unread_lines=unread_lines,
                extra=chatter._build_negative_behaviors_extra(),
            )
            rt.history_merged = True

            decision = await chatter.sub_agent(
                unread_lines,
                unread_msgs,
                chat_stream,
            )
            logger.info(
                f"Sub-agent 决策: {decision['reason']} (响应: {decision['should_respond']})"
            )

            if not decision["should_respond"]:
                logger.info("Sub-agent 决定不响应，继续等待...")
                resume_event = yield Wait()
                continue

            chatter._upsert_pending_unread_payload(
                response=rt.response,
                formatted_text=unread_user_prompt,
                unread_msgs=unread_msgs,
                native_multimodal=native_multimodal,
                logger_override=logger,
            )
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.MODEL_TURN, logger=logger, reason="accepted unread batch")

            # MODEL_TURN 阶段发送后才 flush 本轮采纳的 unread
            rt.unread_msgs_to_flush = unread_msgs
            continue

        if rt.phase in (_ToolCallWorkflowPhase.MODEL_TURN, _ToolCallWorkflowPhase.FOLLOW_UP):
            # FOLLOW_UP 阶段严禁 flush 新未读；MODEL_TURN 才 flush 本轮采纳的 unread。
            try:
                rt.response = await rt.response.send(stream=False)
                await rt.response
                if rt.phase == _ToolCallWorkflowPhase.MODEL_TURN:
                    if rt.unread_msgs_to_flush:
                        await chatter.flush_unreads(rt.unread_msgs_to_flush)
                    rt.unread_msgs_to_flush = []
            except Exception as error:
                logger.error(f"LLM 请求失败: {error}", exc_info=True)
                yield Failure("LLM 请求失败", error)
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="request failed")
                continue

            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.TOOL_EXEC, logger=logger, reason="model responded")
            continue

        if rt.phase == _ToolCallWorkflowPhase.TOOL_EXEC:
            llm_response = _require_response(rt.response)
            current_calls = llm_response.call_list or []
            rt.used_tools_in_round.update(_collect_used_tool_names(current_calls))

            _print_actor_decision_panel(chat_stream, llm_response, logger)

            if not llm_response.call_list:
                if _is_suspend_message(llm_response.message, suspend_text):
                    resume_event = yield Wait(
                        step_data=_consume_actor_round_step_data(rt)
                    )
                    _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="model returned suspend")
                    continue
                if llm_response.message and llm_response.message.strip():
                    logger.warning(
                        f"LLM 返回了纯文本而非 tool call: "
                        f"{llm_response.message[:100]}"
                    )
                    yield Stop(0, step_data=_consume_actor_round_step_data(rt))
                    return
                resume_event = yield Wait(
                    step_data=_consume_actor_round_step_data(rt)
                )
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="no call_list")
                continue

            logger.info(f"本轮调用列表：{[call.name for call in current_calls]}")
            for call in current_calls:
                args = call.args if isinstance(call.args, dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(f"LLM 调用 {call.name}，原因: {reason}，参数: {args}")

            call_outcome = await process_tool_calls(
                stream_id=chat_stream.stream_id,
                calls=current_calls,
                response=llm_response,
                run_tool_call=chatter.run_tool_call,
                usable_map=usable_map,
                trigger_msg=_pick_actor_trigger_message(
                    chat_stream=chat_stream,
                    rt=rt,
                ),
                pass_call_name=pass_call_name,
                stop_call_name=stop_call_name,
                cross_round_seen_signatures=rt.cross_round_seen_signatures,
            )

            if call_outcome.should_stop:
                cooldown_seconds = call_outcome.stop_minutes * 60 if enable_cooldown else 0
                logger.info(f"对话已结束，冷却 {call_outcome.stop_minutes} 分钟（{'已启用' if enable_cooldown else '已禁用，实际不冷却'}）")
                yield Stop(
                    cooldown_seconds,
                    step_data=_consume_actor_round_step_data(rt),
                )
                return

            if call_outcome.has_pending_tool_results:
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.FOLLOW_UP, logger=logger, reason="pending tool results")
                continue

            action_only_round = bool(current_calls) and all(
                call.name.startswith("action-") for call in current_calls
            )
            append_suspend_payload_if_action_only(
                calls=current_calls,
                response=llm_response,
                suspend_text=suspend_text,
                enable_action_suspend=enable_action_suspend,
                logger=logger,
            )

            # 工具链已闭合，可以进入等待或接受新 user。
            if action_only_round and not call_outcome.should_wait:
                if enable_action_suspend:
                    resume_event = yield Wait(
                        step_data=_consume_actor_round_step_data(rt)
                    )
                    _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="action-only round suspended")
                    continue
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.FOLLOW_UP, logger=logger, reason="action-only round continues follow-up")
                continue

            if call_outcome.should_wait:
                _append_suspend_payload_if_tool_result_tail(
                    response=llm_response,
                    suspend_text=suspend_text,
                    logger=logger,
                )
                resume_event = yield Wait(
                    time=getattr(call_outcome, "wait_seconds", None),
                    step_data=_consume_actor_round_step_data(rt),
                )
            else:
                _consume_actor_round_step_data(rt)
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="tool exec done")
            continue
