"""Default Chatter 子代理协作运行时。"""

from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass, field
from typing import Any

from src.core.components.base.chatter import BaseChatter, WaitResumeEvent
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.core.prompt import (
    SystemReminderBucket,
    SystemReminderInsertType,
    get_system_reminder_store,
)
from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager
from src.kernel.llm import LLMPayload, ROLE, Text, ToolRegistry
from src.kernel.logger import get_logger

from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls

logger = get_logger("default_chatter.sub_agent_collaboration")

_ACTIVE_AGENT_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "default_chatter_active_agent_name",
    default=None,
)

_PASS_AND_WAIT = "action-pass_and_wait"
_STOP_CONVERSATION = "action-stop_conversation"
_SUSPEND_TEXT = "__SUSPEND__"
_ACTOR_REMINDER_NAME = "sub_agent_activity"
_BACKGROUND_START_PROMPT = (
    "系统事件：你已被创建并开始后台执行。"
    "请根据系统提示中描述的委托任务立即开始工作。"
    "如果能够推进，请直接使用已分配工具执行；"
    "如果任务已完成，请给出简洁结果；"
    "如果仍缺少关键信息，请明确说明缺口与下一步建议。"
)

FIXED_SUB_AGENT_SYSTEM_PROMPT = """你是由 default chatter 创建的子代理。

你的职责是帮助上级代理完成明确的局部任务，而不是脱离任务自行扩张目标。

行为规范：
1. 只围绕当前被委托的任务行动。
2. 只使用分配给你的工具；不要假装自己拥有未分配的能力。
3. 你的动作会直接作用于当前聊天流，因此发送消息等操作必须谨慎。
4. 当你完成任务时，给上级代理返回简洁、可执行的结果。
5. 如果你被授予 create_agent、get_agent、kill_agent，则你可以继续创建更窄的子代理；否则不要尝试多级委托。
"""


def _resolve_sub_agent_task_name(chatter: BaseChatter) -> str:
    """解析协作子代理使用的模型任务名。"""
    resolver = getattr(chatter, "_get_sub_agent_task_name", None)
    if callable(resolver):
        task_name = str(resolver() or "").strip()
        if task_name:
            return task_name
    return "actor"


def get_active_sub_agent_name() -> str | None:
    """获取当前执行中的子代理名称。"""
    return _ACTIVE_AGENT_NAME.get()


@dataclass(slots=True)
class SubAgentActivity:
    """子代理活动记录。"""

    activity_type: str
    content: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "type": self.activity_type,
            "content": self.content,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class SubAgentCompletionEvent:
    """等待主 actor 消费的一次性子代理结果。"""

    name: str
    status: str
    content: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "name": self.name,
            "status": self.status,
            "content": self.content,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class SubAgentSession:
    """单个子代理的持久状态。"""

    name: str
    stream_id: str
    parent_name: str | None
    allow_create_sub_agent: bool
    allowed_tool_names: list[str]
    allowed_mcp_names: list[str]
    registry: ToolRegistry
    state: Any
    activities: list[SubAgentActivity] = field(default_factory=list)
    children: set[str] = field(default_factory=set)
    last_response: str = ""
    latest_assistant_activity: str = ""
    status: str = "idle"
    error_message: str = ""
    pending_questions: list[tuple[str, bool]] = field(default_factory=list)
    current_task: asyncio.Task[Any] | None = None
    cross_round_seen_signatures: set[str] = field(default_factory=set)

    def append_activity(self, activity_type: str, content: str) -> None:
        """追加一条活动记录。"""
        self.activities.append(SubAgentActivity(activity_type=activity_type, content=content))
        if activity_type == "assistant":
            self.latest_assistant_activity = content


def _close_tool_result_tail(response: Any) -> None:
    """在挂起前闭合 TOOL_RESULT 尾部，避免下次继续时上下文非法。"""
    payloads = getattr(response, "payloads", None)
    if not payloads or payloads[-1].role != ROLE.TOOL_RESULT:
        return
    response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(_SUSPEND_TEXT)))


def _build_synthetic_trigger_message(
    chat_stream: ChatStream,
    question: str,
) -> Message:
    """在流里没有现成消息时，构造最小可执行的触发消息。"""
    return Message(
        message_id=f"sub-agent-{int(time.time() * 1000)}",
        content=question,
        processed_plain_text=question,
        platform=chat_stream.platform,
        chat_type=chat_stream.chat_type,
        stream_id=chat_stream.stream_id,
        sender_name="sub_agent",
    )


def _pick_trigger_message(chat_stream: ChatStream, question: str) -> Message | None:
    """为子代理工具调用选择一个可复用的触发消息。"""
    context = chat_stream.context
    if context.current_message is not None:
        return context.current_message
    if context.unread_messages:
        return context.unread_messages[-1]
    if context.history_messages:
        return context.history_messages[-1]
    return _build_synthetic_trigger_message(chat_stream, question)


def _format_tool_args(args: Any) -> str:
    """格式化工具参数，忽略 reason 字段。"""
    if not isinstance(args, dict):
        return ""

    display_items: list[str] = []
    for key, value in args.items():
        if key == "reason":
            continue
        display_items.append(f"{key}: {value}")
    return ", ".join(display_items)


def _build_sub_agent_decision_panel(session: SubAgentSession, response: Any) -> str:
    """构建子代理本次决策摘要面板内容。"""
    thought = (
        response.reasoning_content.strip()
        if getattr(response, "reasoning_content", None)
        else "（无）"
    )
    monologue = (
        response.message.strip() if getattr(response, "message", None) else "（无）"
    )

    tool_lines = []
    for call in getattr(response, "call_list", None) or []:
        formatted_args = _format_tool_args(call.args)
        if formatted_args:
            tool_lines.append(f"    {call.name} ({formatted_args})")
        else:
            tool_lines.append(f"    {call.name}")

    tools_text = "\n".join(tool_lines) if tool_lines else "    （无）"
    return (
        f"子代理名称：{session.name}\n"
        f"聊天流：{session.stream_id}\n\n"
        f"思考：{thought}\n\n"
        f"独白：{monologue}\n\n"
        f"调用工具：\n{tools_text}"
    )


def _print_sub_agent_decision_panel(session: SubAgentSession, response: Any) -> None:
    """当子代理给出 tool call 时打印本次决策摘要。"""
    if not getattr(response, "call_list", None):
        return

    print_panel = getattr(logger, "print_panel", None)
    if callable(print_panel):
        print_panel(
            _build_sub_agent_decision_panel(session, response),
            title=f"子代理决策: {session.name}",
            border_style="green",
        )


class SubAgentCollaborationManager:
    """管理 default chatter 下的子代理生命周期与执行。"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, SubAgentSession]] = {}
        self._completed_events: dict[str, list[SubAgentCompletionEvent]] = {}

    def _get_stream_sessions(self, stream_id: str) -> dict[str, SubAgentSession]:
        return self._sessions.setdefault(stream_id, {})

    def _push_completed_event(
        self,
        *,
        stream_id: str,
        name: str,
        status: str,
        content: str,
    ) -> None:
        """登记一条等待 actor 消费的一次性子代理结果。"""
        self._completed_events.setdefault(stream_id, []).append(
            SubAgentCompletionEvent(
                name=name,
                status=status,
                content=content,
            )
        )

    def drain_completed_events(self, stream_id: str) -> list[dict[str, Any]]:
        """取出并清空当前流等待 actor 消费的子代理结果。"""
        events = self._completed_events.pop(stream_id, [])
        return [event.to_dict() for event in events]

    def _set_actor_dynamic_reminder(self, stream_id: str) -> None:
        """刷新 actor 侧可见的子代理动态 reminder。"""
        store = get_system_reminder_store()
        sessions = self._sessions.get(stream_id, {})
        active_sessions = [
            session
            for session in sessions.values()
            if session.status not in {"completed", "failed", "killed"}
        ]
        if not active_sessions:
            store.delete(SystemReminderBucket.ACTOR, _ACTOR_REMINDER_NAME)
            return

        lines = ["以下是当前子代理的最新 assistant 动态："]
        for session in sorted(active_sessions, key=lambda item: item.name):
            latest_message = session.latest_assistant_activity.strip()
            if not latest_message:
                latest_message = "(暂无 assistant 动态)"
            lines.append(f"[{session.name}] {session.status}")
            lines.append(latest_message)

        store.set(
            bucket=SystemReminderBucket.ACTOR,
            name=_ACTOR_REMINDER_NAME,
            content="\n".join(lines),
            insert_type=SystemReminderInsertType.DYNAMIC,
        )

    @staticmethod
    def _queue_question(
        session: SubAgentSession,
        question: str,
        *,
        visible: bool,
    ) -> bool:
        """为子代理追加一条待处理问题。"""
        normalized_question = question.strip()
        if not normalized_question:
            return False
        session.pending_questions.append((normalized_question, visible))
        return True

    def _ensure_background_runner(
        self,
        *,
        chatter: BaseChatter,
        session: SubAgentSession,
        enable_action_suspend: bool,
    ) -> None:
        """确保子代理后台执行任务已启动。"""
        current_task = session.current_task
        if current_task is not None and not current_task.done():
            return

        from src.kernel.concurrency import get_task_manager

        runner = self._run_session_in_background(
            chatter=chatter,
            session=session,
            enable_action_suspend=enable_action_suspend,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            runner.close()
            session.status = "idle"
            self._set_actor_dynamic_reminder(session.stream_id)
            return

        session.status = "running"
        self._set_actor_dynamic_reminder(session.stream_id)
        task_info = get_task_manager().create_task(
            runner,
            name=f"sub_agent_{session.stream_id[:16]}_{session.name}",
            daemon=True,
        )
        session.current_task = task_info.task

    async def _run_session_in_background(
        self,
        *,
        chatter: BaseChatter,
        session: SubAgentSession,
        enable_action_suspend: bool,
    ) -> None:
        """后台串行执行子代理待处理队列。"""
        try:
            while session.pending_questions:
                question, visible = session.pending_questions.pop(0)
                session.status = "running"
                self._set_actor_dynamic_reminder(session.stream_id)
                should_resume_parent = await self._drive_agent_once(
                    chatter=chatter,
                    session=session,
                    question=question,
                    visible=visible,
                    enable_action_suspend=enable_action_suspend,
                )
                if should_resume_parent:
                    await self._resume_actor(stream_id=session.stream_id)

            if session.status == "running":
                session.status = "idle"
                self._set_actor_dynamic_reminder(session.stream_id)
        except asyncio.CancelledError:
            session.status = "killed"
            self._set_actor_dynamic_reminder(session.stream_id)
            raise
        except Exception as error:
            session.status = "failed"
            session.error_message = str(error)
            session.append_activity("system", f"后台执行失败: {error}")
            self._push_completed_event(
                stream_id=session.stream_id,
                name=session.name,
                status="failed",
                content=f"子代理执行失败：{error}",
            )
            self._set_actor_dynamic_reminder(session.stream_id)
            await self._resume_actor(stream_id=session.stream_id)
        finally:
            session.current_task = None

    async def _resume_actor(self, *, stream_id: str) -> None:
        """向主 actor 注入一次恢复信号。"""
        await get_stream_loop_manager().trigger_external_resume(
            stream_id,
            event=WaitResumeEvent(source="sub_agent"),
        )

    async def _drive_agent_once(
        self,
        *,
        chatter: BaseChatter,
        session: SubAgentSession,
        question: str,
        visible: bool,
        enable_action_suspend: bool,
    ) -> bool:
        """执行子代理的一次指令推进；返回是否应唤醒 actor。"""
        normalized_question = question.strip()
        if not normalized_question:
            return False

        if visible:
            session.append_activity("user", normalized_question)
        session.state.add_payload(LLMPayload(ROLE.USER, Text(normalized_question)))

        from src.core.managers.stream_manager import get_stream_manager

        chat_stream = await get_stream_manager().get_or_create_stream(
            stream_id=chatter.stream_id
        )
        trigger_msg = _pick_trigger_message(chat_stream, normalized_question)

        current_state = session.state
        should_resume_parent = False

        while True:
            response = await current_state.send(stream=False)
            await response
            session.state = response

            _print_sub_agent_decision_panel(session, response)

            message_text = getattr(response, "message", None)
            if isinstance(message_text, str) and message_text.strip():
                session.last_response = message_text.strip()
                session.append_activity("assistant", session.last_response)
                self._set_actor_dynamic_reminder(session.stream_id)

            current_calls = list(getattr(response, "call_list", None) or [])
            if not current_calls:
                completion_text = session.last_response or "子代理已完成，但没有返回文本结果。"
                session.status = "completed"
                self._push_completed_event(
                    stream_id=session.stream_id,
                    name=session.name,
                    status="completed",
                    content=completion_text,
                )
                self._set_actor_dynamic_reminder(session.stream_id)
                return True

            session.append_activity(
                "tool_calls",
                ", ".join(call.name for call in current_calls),
            )

            token = _ACTIVE_AGENT_NAME.set(session.name)
            try:
                outcome = await process_tool_calls(
                    stream_id=chat_stream.stream_id,
                    calls=current_calls,
                    response=response,
                    run_tool_call=chatter.run_tool_call,
                    usable_map=session.registry,
                    trigger_msg=trigger_msg,
                    pass_call_name=_PASS_AND_WAIT,
                    stop_call_name=_STOP_CONVERSATION,
                    cross_round_seen_signatures=session.cross_round_seen_signatures,
                )
            finally:
                _ACTIVE_AGENT_NAME.reset(token)

            if outcome.should_stop:
                _close_tool_result_tail(response)
                session.append_activity(
                    "control",
                    f"stop_conversation({outcome.stop_minutes})",
                )
                completion_text = session.last_response or "子代理已结束当前任务，请主代理接手。"
                session.status = "completed"
                self._push_completed_event(
                    stream_id=session.stream_id,
                    name=session.name,
                    status="completed",
                    content=completion_text,
                )
                self._set_actor_dynamic_reminder(session.stream_id)
                return True

            if outcome.has_pending_tool_results:
                current_state = response
                continue

            action_only_round = bool(current_calls) and all(
                call.name.startswith("action-") for call in current_calls
            )
            append_suspend_payload_if_action_only(
                calls=current_calls,
                response=response,
                suspend_text=_SUSPEND_TEXT,
                enable_action_suspend=enable_action_suspend,
                logger=logger,
            )

            if action_only_round:
                if enable_action_suspend:
                    session.status = "waiting"
                    self._set_actor_dynamic_reminder(session.stream_id)
                    return False
                current_state = response
                continue

            if outcome.should_wait:
                _close_tool_result_tail(response)
                session.append_activity("control", "pass_and_wait")
                session.status = "waiting"
                self._set_actor_dynamic_reminder(session.stream_id)
                return False

            should_resume_parent = True
            break

        session.status = "completed" if should_resume_parent else "idle"
        self._set_actor_dynamic_reminder(session.stream_id)
        return should_resume_parent

    def create_agent(
        self,
        *,
        chatter: BaseChatter,
        name: str,
        system_prompt: str,
        usable_classes: list[type[Any]],
        allowed_tool_names: list[str],
        allowed_mcp_names: list[str],
        allow_create_sub_agent: bool,
        enable_action_suspend: bool,
        parent_name: str | None = None,
    ) -> dict[str, Any]:
        """创建并登记一个新的子代理。"""
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("name 不能为空")

        sessions = self._get_stream_sessions(chatter.stream_id)
        if normalized_name in sessions:
            raise ValueError(f"子代理已存在: {normalized_name}")

        request = chatter.create_request(
            _resolve_sub_agent_task_name(chatter),
            request_name=f"sub_agent:{normalized_name}",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))

        registry = ToolRegistry()
        for usable_cls in usable_classes:
            registry.register(usable_cls)
        if registry.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, registry.get_all()))  # type: ignore[arg-type]

        session = SubAgentSession(
            name=normalized_name,
            stream_id=chatter.stream_id,
            parent_name=parent_name,
            allow_create_sub_agent=allow_create_sub_agent,
            allowed_tool_names=list(allowed_tool_names),
            allowed_mcp_names=list(allowed_mcp_names),
            registry=registry,
            state=request,
            status="created",
        )
        session.append_activity("system", "子代理已创建")
        sessions[normalized_name] = session

        if parent_name and parent_name in sessions:
            sessions[parent_name].children.add(normalized_name)

        self._queue_question(
            session,
            _BACKGROUND_START_PROMPT,
            visible=False,
        )
        self._ensure_background_runner(
            chatter=chatter,
            session=session,
            enable_action_suspend=enable_action_suspend,
        )
        self._set_actor_dynamic_reminder(chatter.stream_id)

        return self._snapshot(session, message_limit=10)

    async def get_agent(
        self,
        *,
        chatter: BaseChatter,
        name: str,
        question: str,
        message_limit: int,
        enable_action_suspend: bool,
    ) -> dict[str, Any]:
        """向指定子代理发送一条指令，并返回活动快照。"""
        sessions = self._get_stream_sessions(chatter.stream_id)
        session = sessions.get(name)
        if session is None:
            raise ValueError(f"子代理不存在: {name}")

        if question.strip():
            self._queue_question(session, question, visible=True)
            self._ensure_background_runner(
                chatter=chatter,
                session=session,
                enable_action_suspend=enable_action_suspend,
            )

        return self._snapshot(session, message_limit=message_limit)

    def kill_agent(self, *, stream_id: str, name: str) -> dict[str, Any]:
        """销毁指定子代理，并级联销毁其后代。"""
        sessions = self._get_stream_sessions(stream_id)
        session = sessions.get(name)
        if session is None:
            raise ValueError(f"子代理不存在: {name}")

        killed_names = self._kill_agent_recursive(sessions, name)
        if not sessions:
            self._sessions.pop(stream_id, None)
            self._completed_events.pop(stream_id, None)

        self._set_actor_dynamic_reminder(stream_id)

        return {"killed": killed_names, "name": name}

    def _kill_agent_recursive(
        self,
        sessions: dict[str, SubAgentSession],
        name: str,
    ) -> list[str]:
        session = sessions.get(name)
        if session is None:
            return []

        if session.current_task is not None and not session.current_task.done():
            session.current_task.cancel()

        killed_names: list[str] = []
        for child_name in list(session.children):
            killed_names.extend(self._kill_agent_recursive(sessions, child_name))

        if session.parent_name and session.parent_name in sessions:
            sessions[session.parent_name].children.discard(name)

        sessions.pop(name, None)
        killed_names.append(name)
        return killed_names

    def _snapshot(self, session: SubAgentSession, message_limit: int) -> dict[str, Any]:
        """生成子代理当前状态快照。"""
        selected_activities = session.activities
        if message_limit > 0:
            selected_activities = selected_activities[-message_limit:]

        return {
            "name": session.name,
            "parent": session.parent_name,
            "children": sorted(session.children),
            "allow_create_sub_agent": session.allow_create_sub_agent,
            "status": session.status,
            "tools": list(session.allowed_tool_names),
            "mcp": list(session.allowed_mcp_names),
            "last_response": session.last_response,
            "pending_questions": len(session.pending_questions),
            "activities": [activity.to_dict() for activity in selected_activities],
        }


_global_sub_agent_collaboration_manager: SubAgentCollaborationManager | None = None


def get_sub_agent_collaboration_manager() -> SubAgentCollaborationManager:
    """获取全局子代理协作管理器。"""
    global _global_sub_agent_collaboration_manager
    if _global_sub_agent_collaboration_manager is None:
        _global_sub_agent_collaboration_manager = SubAgentCollaborationManager()
    return _global_sub_agent_collaboration_manager
