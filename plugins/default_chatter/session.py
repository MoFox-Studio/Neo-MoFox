"""Reusable Default Chatter session core."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, TypeGuard

from src.core.components.base import Failure, Stop, Wait, WaitResumeEvent
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.kernel.llm import LLMPayload, ROLE, Text

from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls
from .type_defs import (
    DefaultChatterResumeEvent,
    DefaultChatterResult,
    DefaultChatterSessionAdapters,
    DefaultChatterSessionOptions,
    LLMConversationState,
    LLMResponseLike,
)

_AFTER_CHATTER_STEP_SCOPE = "actor_round"


class DefaultChatterSessionPhase(str, Enum):
    """Default Chatter 会话的不同阶段，定义了会话核心的状态机。"""

    WAIT_USER = "wait_user"
    MODEL_TURN = "model_turn"
    TOOL_EXEC = "tool_exec"
    FOLLOW_UP = "follow_up"


@dataclass(slots=True)
class DefaultChatterSessionState:
    """单个聊天会话拥有的可变运行时状态。"""

    response: LLMConversationState
    phase: DefaultChatterSessionPhase
    history_merged: bool
    unreads: list[Message]
    cross_round_seen_signatures: set[str]
    unread_msgs_to_flush: list[Message]
    plain_text_retry_count: int = 0
    used_tools_in_round: set[str] = field(default_factory=set)

    def has_tool_result_tail(self) -> bool:
        payloads = getattr(self.response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)


def _is_response_like(response: LLMConversationState) -> TypeGuard[LLMResponseLike]:
    return hasattr(response, "call_list") and hasattr(response, "message")


def _require_response(response: LLMConversationState) -> LLMResponseLike:
    if _is_response_like(response):
        return response
    raise TypeError("当前会话状态不是一个 LLM 响应")


def _format_tool_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""

    display_items: list[str] = []
    for key, value in args.items():
        if key == "reason":
            continue
        display_items.append(f"{key}: {value}")
    return ", ".join(display_items)


def collect_used_tool_names(calls: list[Any]) -> set[str]:
    """收集当前模型回合中使用的规范化工具名称。"""

    return {
        normalized_name
        for call in calls
        if (normalized_name := str(getattr(call, "name", "") or "").strip())
    }


def _consume_actor_round_step_data(state: DefaultChatterSessionState) -> dict[str, Any]:
    used_tools = sorted(state.used_tools_in_round)
    state.used_tools_in_round.clear()
    return {
        "step_scope": _AFTER_CHATTER_STEP_SCOPE,
        "used_tools": used_tools,
    }


def _is_suspend_message(message: str | None, suspend_text: str) -> bool:
    return isinstance(message, str) and message.strip() == suspend_text


def _is_timer_resume_event(event: WaitResumeEvent | None) -> bool:
    return event is not None and event.source == "timer"


def _is_sub_agent_resume_event(event: WaitResumeEvent | None) -> bool:
    return event is not None and event.source == "sub_agent"


def _append_suspend_payload_if_tool_result_tail(
    *,
    response: LLMConversationState,
    suspend_text: str,
    session: "DefaultChatterSession",
) -> None:
    payloads = getattr(response, "payloads", None)
    if not payloads or payloads[-1].role != ROLE.TOOL_RESULT:
        return

    response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
    session.logger.debug(
        "注入 SUSPEND 占位符以在等待之前关闭工具结果尾部"
    )


def _extract_latest_user_text(response: LLMConversationState) -> str:
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
    return Message(
        message_id=f"actor-{int(time.time() * 1000)}",
        content=prompt_text,
        processed_plain_text=prompt_text,
        platform=chat_stream.platform,
        chat_type=chat_stream.chat_type,
        stream_id=chat_stream.stream_id,
        sender_name="actor",
    )


def _pick_actor_trigger_message(
    *,
    chat_stream: ChatStream,
    state: DefaultChatterSessionState,
) -> Message:
    if state.unreads:
        return state.unreads[-1]

    context = chat_stream.context
    if context.current_message is not None:
        return context.current_message

    if context.unread_messages:
        return context.unread_messages[-1]

    if context.history_messages:
        return context.history_messages[-1]

    return _build_synthetic_trigger_message(chat_stream, _extract_latest_user_text(state.response))


def _build_wait_timeout_prompt(event: WaitResumeEvent) -> str:
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
    return (
        "系统事件：有子代理已在后台完成一轮任务。"
        "请查看动态 system reminder 中的子代理最新状态，"
        "并结合已有上下文决定下一步。"
        "如果现在无需继续处理，请调用 pass_and_wait；"
        "如果需要继续回复、委派或执行动作，请直接使用相应工具。"
    )


def _build_sub_agent_result_user_prompt(events: list[dict[str, Any]]) -> str:
    lines = ["以下是子代理刚刚返回的结果，请基于这些结果继续处理："]
    for event in events:
        name = str(event.get("name", "unknown"))
        status = str(event.get("status", "completed"))
        content = str(event.get("content", "")).strip() or "(no text result)"
        lines.append(f"[{name}] {status}")
        lines.append(content)
    return "\n".join(lines)


def _build_actor_decision_panel(chat_stream: ChatStream, response: LLMResponseLike) -> str:
    stream_name = (
        chat_stream.stream_name
        or chat_stream.stream_id
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
    session: "DefaultChatterSession",
) -> None:
    if not response.call_list:
        return

    print_panel = getattr(session.logger, "print_panel", None)
    if callable(print_panel):
        print_panel(
            _build_actor_decision_panel(chat_stream, response),
            title="Actor 决策",
            border_style="cyan",
        )


def _transition(
    *,
    state: DefaultChatterSessionState,
    to_phase: DefaultChatterSessionPhase,
    session: "DefaultChatterSession",
    reason: str,
) -> None:
    if state.phase == to_phase:
        return
    session.logger.debug(f"[FSM] {state.phase.value} -> {to_phase.value}: {reason}")
    state.phase = to_phase


@dataclass(slots=True)
class DefaultChatterSession:
    """Default Chatter 会话核心，封装了一个聊天会话的完整运行时状态和执行逻辑。通过组合不同的适配器和配置选项，可以支持多种聊天场景和定制化需求。"""

    stream_id: str
    options: DefaultChatterSessionOptions
    adapters: DefaultChatterSessionAdapters
    pass_call_name: str = "action-pass_and_wait"
    stop_call_name: str = "action-stop_conversation"
    suspend_text: str = "__SUSPEND__"

    @property
    def logger(self):  # type: ignore[no-untyped-def]
        return self.adapters.logger_adapter

    def _apply_stop_wake_config(self, result: Stop) -> Stop:
        probability = max(
            0.0,
            min(1.0, float(self.options.stop_direct_message_wake_probability)),
        )
        return Stop(
            time=result.time,
            direct_message_wake_enabled=self.options.enable_stop_direct_message_wake,
            direct_message_wake_probability=probability,
            step_data=result.step_data,
        )

    async def execute(self) -> AsyncGenerator[DefaultChatterResult, DefaultChatterResumeEvent]:
        from src.core.managers.stream_manager import get_stream_manager

        chat_stream = await get_stream_manager().activate_stream(self.stream_id)
        if chat_stream is None:
            self.logger.error(f"无法激活聊天流: {self.stream_id}")
            yield Failure("无法激活聊天流")
            return

        runner = self.execute_with_stream(
            chat_stream,
            apply_stop_wake_config=True,
        )
        resume_event: WaitResumeEvent | None = None

        while True:
            try:
                result = await runner.asend(resume_event)
            except StopAsyncIteration:
                return
            resume_event = yield result

    async def execute_with_stream(
        self,
        chat_stream: ChatStream,
        *,
        apply_stop_wake_config: bool,
    ) -> AsyncGenerator[DefaultChatterResult, DefaultChatterResumeEvent]:
        if self.options.native_multimodal:
            from src.core.managers.media_manager import get_media_manager

            get_media_manager().skip_vlm_for_stream(chat_stream.stream_id, ["image"])
            self.logger.debug(
                f"Skipped VLM image recognition for stream {chat_stream.stream_id[:8]}"
            )

        try:
            request = self.adapters.request_adapter.create_request(
                self.options.actor_task_name,
                with_reminder="actor",
            )
        except (ValueError, KeyError) as error:
            self.logger.error(f"模型配置错误: {error}")
            yield Failure(f"模型配置错误: {error}")
            return

        system_prompt_text = await self.adapters.prompt_adapter._build_system_prompt(chat_stream)
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

        history_text = self.adapters.prompt_adapter._build_enhanced_history_text(chat_stream)
        usable_map = await self.adapters.usable_adapter.inject_usables(request)

        state = DefaultChatterSessionState(
            response=request,
            phase=(
                DefaultChatterSessionPhase.FOLLOW_UP
                if request.payloads and request.payloads[-1].role == ROLE.TOOL_RESULT
                else DefaultChatterSessionPhase.WAIT_USER
            ),
            history_merged=False,
            unreads=[],
            cross_round_seen_signatures=set(),
            unread_msgs_to_flush=[],
        )

        resume_event: WaitResumeEvent | None = None

        while True:
            current_resume_event = resume_event
            resume_event = None
            _, unread_msgs = await self.adapters.unread_adapter.fetch_unreads()

            if (
                state.phase == DefaultChatterSessionPhase.WAIT_USER
                and state.has_tool_result_tail()
                and not unread_msgs
            ):
                _transition(
                    state=state,
                    to_phase=DefaultChatterSessionPhase.FOLLOW_UP,
                    session=self,
                    reason="context tail is TOOL_RESULT and no unread is pending",
                )

            if state.phase == DefaultChatterSessionPhase.FOLLOW_UP and unread_msgs:
                unread_lines = "\n".join(
                    self.adapters.unread_adapter.format_message_line(msg) for msg in unread_msgs
                )
                decision = await self.adapters.sub_agent_adapter.sub_agent(
                    unread_lines,
                    unread_msgs,
                    chat_stream,
                )
                self.logger.info(
                    f"子代理决策: {decision['reason']} (respond={decision['should_respond']})"
                )

                if decision["should_respond"]:
                    unread_user_prompt = await self.adapters.prompt_adapter._build_user_prompt(
                        chat_stream,
                        history_text=history_text if not state.history_merged else "",
                        unread_lines=unread_lines,
                        extra=self.adapters.prompt_adapter._build_negative_behaviors_extra(),
                    )
                    state.history_merged = True
                    state.unreads = unread_msgs
                    self.adapters.unread_adapter._upsert_pending_unread_payload(
                        response=state.response,
                        formatted_text=unread_user_prompt,
                        unread_msgs=unread_msgs,
                        native_multimodal=self.options.native_multimodal,
                        logger_override=self.logger,
                    )
                    state.unread_msgs_to_flush = unread_msgs
                    _transition(
                        state=state,
                        to_phase=DefaultChatterSessionPhase.MODEL_TURN,
                        session=self,
                        reason="在工具跟进期间接受未读批次",
                    )
                    continue

            if state.phase == DefaultChatterSessionPhase.WAIT_USER:
                if _is_timer_resume_event(current_resume_event) or _is_sub_agent_resume_event(
                    current_resume_event
                ):
                    assert current_resume_event is not None
                    state.cross_round_seen_signatures.clear()
                    state.plain_text_retry_count = 0
                    state.used_tools_in_round.clear()
                    state.unreads = []
                    state.unread_msgs_to_flush = []
                    reminder_text = (
                        _build_sub_agent_resume_prompt(current_resume_event)
                        if _is_sub_agent_resume_event(current_resume_event)
                        else _build_wait_timeout_prompt(current_resume_event)
                    )
                    if _is_sub_agent_resume_event(current_resume_event):
                        from .sub_agent_collaboration import get_sub_agent_collaboration_manager

                        completed_events = get_sub_agent_collaboration_manager().drain_completed_events(
                            self.stream_id
                        )
                        if completed_events:
                            reminder_text = _build_sub_agent_result_user_prompt(completed_events)

                    self.adapters.unread_adapter._upsert_pending_unread_payload(
                        response=state.response,
                        formatted_text=reminder_text,
                    )
                    _transition(
                        state=state,
                        to_phase=DefaultChatterSessionPhase.MODEL_TURN,
                        session=self,
                        reason=(
                            "子代理完成"
                            if _is_sub_agent_resume_event(current_resume_event)
                            else "等待计时器到期"
                        ),
                    )
                    continue

                if not unread_msgs:
                    resume_event = yield Wait()
                    continue

                state.cross_round_seen_signatures.clear()
                state.plain_text_retry_count = 0
                state.used_tools_in_round.clear()
                state.unreads = unread_msgs

                unread_lines = "\n".join(
                    self.adapters.unread_adapter.format_message_line(msg) for msg in unread_msgs
                )
                unread_user_prompt = await self.adapters.prompt_adapter._build_user_prompt(
                    chat_stream,
                    history_text=history_text if not state.history_merged else "",
                    unread_lines=unread_lines,
                    extra=self.adapters.prompt_adapter._build_negative_behaviors_extra(),
                )
                state.history_merged = True

                decision = await self.adapters.sub_agent_adapter.sub_agent(
                    unread_lines,
                    unread_msgs,
                    chat_stream,
                )
                self.logger.info(
                    f"子代理决策: {decision['reason']} (respond={decision['should_respond']})"
                )

                if not decision["should_respond"]:
                    self.logger.info("子代理决定暂不响应; 继续等待")
                    resume_event = yield Wait()
                    continue

                self.adapters.unread_adapter._upsert_pending_unread_payload(
                    response=state.response,
                    formatted_text=unread_user_prompt,
                    unread_msgs=unread_msgs,
                    native_multimodal=self.options.native_multimodal,
                    logger_override=self.logger,
                )
                _transition(
                    state=state,
                    to_phase=DefaultChatterSessionPhase.MODEL_TURN,
                    session=self,
                    reason="接受未读批次",
                )
                state.unread_msgs_to_flush = unread_msgs
                continue

            if state.phase in (
                DefaultChatterSessionPhase.MODEL_TURN,
                DefaultChatterSessionPhase.FOLLOW_UP,
            ):
                try:
                    state.response = await state.response.send(stream=False)
                    await state.response
                    if state.phase == DefaultChatterSessionPhase.MODEL_TURN:
                        if state.unread_msgs_to_flush:
                            await self.adapters.unread_adapter.flush_unreads(state.unread_msgs_to_flush)
                        state.unread_msgs_to_flush = []
                except Exception as error:  # noqa: BLE001
                    self.logger.error(f"LLM request failed: {error}", exc_info=True)
                    yield Failure("LLM 请求失败", error)
                    _transition(
                        state=state,
                        to_phase=DefaultChatterSessionPhase.WAIT_USER,
                        session=self,
                        reason="请求失败",
                    )
                    continue

                _transition(
                    state=state,
                    to_phase=DefaultChatterSessionPhase.TOOL_EXEC,
                    session=self,
                    reason="模型已响应",
                )
                continue

            if state.phase == DefaultChatterSessionPhase.TOOL_EXEC:
                llm_response = _require_response(state.response)
                current_calls = llm_response.call_list or []
                state.used_tools_in_round.update(collect_used_tool_names(current_calls))

                _print_actor_decision_panel(chat_stream, llm_response, self)

                if not llm_response.call_list:
                    if _is_suspend_message(llm_response.message, self.suspend_text):
                        resume_event = yield Wait(
                            step_data=_consume_actor_round_step_data(state)
                        )
                        _transition(
                            state=state,
                            to_phase=DefaultChatterSessionPhase.WAIT_USER,
                            session=self,
                            reason="模型返回挂起",
                        )
                        continue
                    if llm_response.message and llm_response.message.strip():
                        self.logger.warning(
                            f"LLM 返回纯文本而不是工具调用: {llm_response.message[:100]}"
                        )
                        plain_text_adapter = self.adapters.plain_text_adapter
                        if plain_text_adapter is not None:
                            handling = plain_text_adapter.handle_plain_text_response(
                                message=llm_response.message,
                                retry_count=state.plain_text_retry_count,
                                response=llm_response,
                            )
                            action = handling["action"]
                            reminder_text = handling.get("reminder_text", "")
                            if action == "retry" and reminder_text.strip():
                                state.plain_text_retry_count += 1
                                llm_response.add_payload(
                                    LLMPayload(ROLE.USER, Text(reminder_text))
                                )
                                _transition(
                                    state=state,
                                    to_phase=DefaultChatterSessionPhase.MODEL_TURN,
                                    session=self,
                                    reason="plain text retry",
                                )
                                continue
                            if action == "wait":
                                resume_event = yield Wait(
                                    step_data=_consume_actor_round_step_data(state)
                                )
                                _transition(
                                    state=state,
                                    to_phase=DefaultChatterSessionPhase.WAIT_USER,
                                    session=self,
                                    reason="plain text fallback wait",
                                )
                                continue
                        stop_result = Stop(
                            0,
                            step_data=_consume_actor_round_step_data(state),
                        )
                        yield (
                            self._apply_stop_wake_config(stop_result)
                            if apply_stop_wake_config
                            else stop_result
                        )
                        return
                    resume_event = yield Wait(
                        step_data=_consume_actor_round_step_data(state)
                    )
                    _transition(
                        state=state,
                        to_phase=DefaultChatterSessionPhase.WAIT_USER,
                        session=self,
                        reason="没有调用列表",
                    )
                    continue

                self.logger.info(f"当前回合的工具调用: {[call.name for call in current_calls]}")
                for call in current_calls:
                    args = dict(call.args) if isinstance(call.args, dict) else {}
                    reason = args.pop("reason", "未提供原因")
                    self.logger.info(f"LLM 调用了 {call.name}; 原因={reason}; 参数={args}")

                call_outcome = await process_tool_calls(
                    stream_id=chat_stream.stream_id,
                    calls=current_calls,
                    response=llm_response,
                    run_tool_call=self.adapters.tool_execution_adapter.run_tool_call,
                    usable_map=usable_map,
                    trigger_msg=_pick_actor_trigger_message(
                        chat_stream=chat_stream,
                        state=state,
                    ),
                    pass_call_name=self.pass_call_name,
                    stop_call_name=self.stop_call_name,
                    cross_round_seen_signatures=state.cross_round_seen_signatures,
                )

                if call_outcome.should_stop:
                    cooldown_seconds = call_outcome.stop_minutes * 60 if self.options.enable_cooldown else 0
                    stop_result = Stop(
                        cooldown_seconds,
                        step_data=_consume_actor_round_step_data(state),
                    )
                    yield self._apply_stop_wake_config(stop_result) if apply_stop_wake_config else stop_result
                    return

                if call_outcome.has_pending_tool_results:
                    _transition(
                        state=state,
                        to_phase=DefaultChatterSessionPhase.FOLLOW_UP,
                        session=self,
                        reason="待处理的工具结果",
                    )
                    continue

                action_only_round = bool(current_calls) and all(
                    call.name.startswith("action-") for call in current_calls
                )
                append_suspend_payload_if_action_only(
                    calls=current_calls,
                    response=llm_response,
                    suspend_text=self.suspend_text,
                    enable_action_suspend=self.options.enable_action_suspend,
                    logger=self.logger,
                )

                if action_only_round and not call_outcome.should_wait:
                    if self.options.enable_action_suspend:
                        resume_event = yield Wait(
                            step_data=_consume_actor_round_step_data(state)
                        )
                        _transition(
                            state=state,
                            to_phase=DefaultChatterSessionPhase.WAIT_USER,
                            session=self,
                            reason="仅动作回合挂起",
                        )
                        continue
                    _transition(
                        state=state,
                        to_phase=DefaultChatterSessionPhase.FOLLOW_UP,
                        session=self,
                        reason="仅动作回合继续跟进",
                    )
                    continue

                if call_outcome.should_wait:
                    _append_suspend_payload_if_tool_result_tail(
                        response=llm_response,
                        suspend_text=self.suspend_text,
                        session=self,
                    )
                    resume_event = yield Wait(
                        time=getattr(call_outcome, "wait_seconds", None),
                        step_data=_consume_actor_round_step_data(state),
                    )
                else:
                    _consume_actor_round_step_data(state)
                _transition(
                    state=state,
                    to_phase=DefaultChatterSessionPhase.WAIT_USER,
                    session=self,
                    reason="工具执行完成",
                )
                continue
