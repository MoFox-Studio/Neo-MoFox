"""Default Chatter 执行器模块。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator, TypeGuard

from src.core.components.base import Wait, WaitResumeEvent, Success, Failure, Stop
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.kernel.logger import Logger
from src.kernel.llm import LLMPayload, ROLE, Text, Content, LLMUsable

from .type_defs import DefaultChatterRuntime, LLMConversationState, LLMResponseLike
from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls
from .multimodal import ImageBudget, build_multimodal_content, extract_images_from_messages

# LLM 返回纯文本（非 tool call）时的最大重试次数
_MAX_PLAIN_TEXT_RETRIES = 0

# 重试时注入的提醒文本
_PLAIN_TEXT_RETRY_REMINDER = (
    "（系统提示：你刚才返回了纯文本而非工具调用。"
    "请务必通过工具调用来完成任务，不要直接输出文字回复。）"
)


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


def _is_suspend_message(message: str | None, suspend_text: str) -> bool:
    """判断模型返回是否为 SUSPEND 挂起文本。"""
    return isinstance(message, str) and message.strip() == suspend_text


def _is_timer_resume_event(event: WaitResumeEvent | None) -> bool:
    """判断本轮是否由定时 wait 主动恢复。"""
    return event is not None and event.source == "timer"


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


def _append_unread_user_payload(
    response: LLMConversationState,
    chatter: DefaultChatterRuntime,
    formatted_text: str,
    unread_msgs: list[Message],
    native_multimodal: bool,
    image_budget: ImageBudget,
    logger: Logger,
) -> None:
    """将未读消息 payload 追加到 response。"""
    content_list: list[Content | LLMUsable]
    if native_multimodal and not image_budget.is_exhausted():
        images = extract_images_from_messages(unread_msgs, image_budget.remaining)
        content_list = build_multimodal_content(formatted_text, images)
        image_budget.consume(len(images))
        if images:
            logger.debug(f"已提取 {len(images)} 张图片, 剩余图片配额 {image_budget.remaining}")
    else:
        content_list = [Text(formatted_text)]

    # 尝试合并到最后一个 USER payload
    if response.payloads and response.payloads[-1].role == ROLE.USER:
        last_payload = response.payloads[-1]
        if last_payload.content and isinstance(last_payload.content[-1], Text) and isinstance(content_list[0], Text):
            # 两个文本可以合并
            existing_text = last_payload.content[-1].text
            last_payload.content[-1] = Text(f"{existing_text}\n{content_list[0].text}")
            last_payload.content.extend(content_list[1:])
        else:
            # 直接追加
            last_payload.content.extend(content_list)
    else:
        # 创建新 USER payload
        response.add_payload(LLMPayload(ROLE.USER, content_list))


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
    max_images_per_payload: int = 4,
) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]:
    """enhanced 模式执行流程。

    Args:
        native_multimodal: 启用后将 image 媒体以 base64 直接打包进 USER payload，
            同时为当前 stream 持久注册 "image" 类型的 VLM 跳过（表情包 emoji 仍由框架
            VLM 生成描述，受益于哈希缓存）。注册后**不会在执行结束后取消**，
            防止该 stream 下一条图片在执行间隙被 VLM 处理。
        max_images_per_payload: 单次 payload 的总图片配额。
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

    image_budget = ImageBudget(total_max=max_images_per_payload)

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
            if _is_timer_resume_event(current_resume_event):
                assert current_resume_event is not None
                rt.cross_round_seen_signatures.clear()
                rt.plain_text_retry_count = 0
                rt.unreads = []
                rt.unread_msgs_to_flush = []
                chatter._upsert_pending_unread_payload(
                    response=rt.response,
                    formatted_text=_build_wait_timeout_prompt(current_resume_event),
                )
                _transition(
                    rt=rt,
                    to_phase=_ToolCallWorkflowPhase.MODEL_TURN,
                    logger=logger,
                    reason="wait timer elapsed",
                )
                continue

            if not unread_msgs:
                resume_event = yield Wait()
                continue

            # 仅在采纳新未读消息时清空跨轮去重状态；FOLLOW_UP 阶段不应清空。
            rt.cross_round_seen_signatures.clear()
            rt.plain_text_retry_count = 0
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

            _append_unread_user_payload(
                response=rt.response,
                chatter=chatter,
                formatted_text=unread_user_prompt,
                unread_msgs=unread_msgs,
                native_multimodal=native_multimodal,
                image_budget=image_budget,
                logger=logger,
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

            _print_actor_decision_panel(chat_stream, llm_response, logger)

            if not llm_response.call_list:
                if _is_suspend_message(llm_response.message, suspend_text):
                    resume_event = yield Wait()
                    _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="model returned suspend")
                    continue
                if llm_response.message and llm_response.message.strip():
                    logger.warning(
                        f"LLM 返回了纯文本而非 tool call: "
                        f"{llm_response.message[:100]}"
                    )
                    yield Stop(0)
                    return
                resume_event = yield Wait()
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
                trigger_msg=rt.unreads[-1] if rt.unreads else None,
                pass_call_name=pass_call_name,
                stop_call_name=stop_call_name,
                cross_round_seen_signatures=rt.cross_round_seen_signatures,
            )

            if call_outcome.should_stop:
                cooldown_seconds = call_outcome.stop_minutes * 60 if enable_cooldown else 0
                logger.info(f"对话已结束，冷却 {call_outcome.stop_minutes} 分钟（{'已启用' if enable_cooldown else '已禁用，实际不冷却'}）")
                yield Stop(cooldown_seconds)
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
                    resume_event = yield Wait()
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
                    time=getattr(call_outcome, "wait_seconds", None)
                )
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="tool exec done")
            continue
