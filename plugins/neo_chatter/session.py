"""Neo-Chatter 主会话逻辑。

``ConversationSession`` 是 NFC 的对话主流程载体：自包含、不持有宿主 chatter 引用，
所有上下文从 ``stream_id`` 经框架 API 取得。``NeoChatter``（注册的 Chatter）和
第三方插件都通过 :class:`NeoChatterService` 拿到同一个 session 并驱动它。

会话状态机沿用 default_chatter 的四阶段模型（WAIT_USER / MODEL_TURN / TOOL_EXEC /
FOLLOW_UP），但用**事件驱动预处理**（发布 ``neo_chatter:preprocess``）替代了
sub-agent / 兴趣值过滤，并去掉了子代理协作、语义训练等内置策略。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, TYPE_CHECKING

from src.app.plugin_system.api import stream_api
from src.app.plugin_system.api.log_api import Logger, get_logger
from src.app.plugin_system.base import Failure, Stop, Wait, WaitResumeEvent
from src.app.plugin_system.types import (
    ChatStream,
    Content,
    LLMPayload,
    LLMResponse,
    LLMUsable,
    Message,
    ROLE,
    Text,
    ToolRegistry,
)

from .components.config import NeoChatterConfig
from .utils.multimodal import extract_images_from_messages, inline_images_into_text
from .utils.preprocess import run_preprocess
from .utils.prompt_builder import NeoChatterPromptBuilder
from .utils.tool_flow import (
    ToolCallOutcome,
    append_suspend_payload_if_action_only,
    process_tool_calls,
)

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

#: 控制流工具调用名（``to_schema`` 会给 action 加 ``action-`` 前缀）
_PASS_CALL_NAME = "action-pass_and_wait"
_STOP_CALL_NAME = "action-stop_conversation"
_SUSPEND_TEXT = "__SUSPEND__"
_AFTER_CHATTER_STEP_SCOPE = "actor_round"

#: LLM 请求 / 响应的统一状态类型
LLMConversationState = Any  # LLMRequest | LLMResponse


class _Phase(str, Enum):
    """会话状态机阶段。"""

    WAIT_USER = "wait_user"
    MODEL_TURN = "model_turn"
    TOOL_EXEC = "tool_exec"
    FOLLOW_UP = "follow_up"


@dataclass(slots=True)
class _SessionState:
    """单次会话的可变运行时状态。"""

    response: "LLMResponse"
    phase: _Phase
    history_merged: bool
    unreads: list[Message]
    unread_msgs_to_flush: list[Message]
    used_tools_in_round: set[str] = field(default_factory=set)
    seen_signatures: set[str] = field(default_factory=set)

    def has_tool_result_tail(self) -> bool:
        payloads = getattr(self.response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)


@dataclass(slots=True)
class _ConfigView:
    """从 :class:`NeoChatterConfig` 读取的运行时选项视图；配置缺失时回退默认值。"""

    native_multimodal: bool = False
    default_stop_minutes: float = 5.0
    enable_cooldown: bool = True
    enable_action_suspend: bool = True
    enable_stop_wake: bool = False
    stop_wake_prob: float = 0.0
    placeholder: str = "[图片-{idx}]"
    actor_task_name: str = "actor"


def _is_timer_resume(event: WaitResumeEvent | None) -> bool:
    return event is not None and event.source == "timer"


def _is_message_resume(event: WaitResumeEvent | None) -> bool:
    return event is not None and event.source == "message"


def _build_timer_resume_prompt(event: WaitResumeEvent) -> str:
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


def _build_generic_resume_prompt(event: WaitResumeEvent) -> str:
    """未知 / 子代理 / 内部上下文等 source 的通用恢复提示。"""
    custom = event.extra.get("resume_prompt")
    if isinstance(custom, str) and custom.strip():
        return custom
    source_label = event.source or "未知来源"
    return (
        f"系统事件：收到来自 '{source_label}' 的恢复请求。"
        "请基于已有上下文主动决定下一步。"
        "如果现在无需继续处理，请调用 pass_and_wait；"
        "如果需要继续回复或执行动作，请直接使用相应工具。"
    )


def _pick_trigger_message(chat_stream: ChatStream, unreads: list[Message]) -> Message:
    """选取本轮工具执行所用的触发消息。"""
    if unreads:
        return unreads[-1]

    context = chat_stream.context
    if context.current_message is not None:
        return context.current_message
    if context.unread_messages:
        return context.unread_messages[-1]
    if context.history_messages:
        return context.history_messages[-1]

    return Message(
        message_id=f"neo-chatter-{int(time.time() * 1000)}",
        content="",
        processed_plain_text="",
        platform=chat_stream.platform,
        chat_type=chat_stream.chat_type,
        stream_id=chat_stream.stream_id,
        sender_name="neo_chatter",
    )


def _consume_step_data(state: _SessionState) -> dict[str, Any]:
    """汇总本回合步骤元数据，供框架在步进完成后发布通知事件。"""
    used_tools = sorted(state.used_tools_in_round)
    state.used_tools_in_round.clear()
    return {"step_scope": _AFTER_CHATTER_STEP_SCOPE, "used_tools": used_tools}


class ConversationSession:
    """Neo-Chatter 主会话逻辑。

    通过 :class:`NeoChatterService` 创建。调用 ``execute()`` 得到一个异步生成器，
    产出 ``Wait / Success / Failure / Stop``，并接收 ``WaitResumeEvent`` 恢复事件。
    """

    def __init__(self, stream_id: str, plugin: "BasePlugin") -> None:
        """初始化会话。

        Args:
            stream_id: 目标聊天流 ID。
            plugin: NFC 插件实例（用于读取 :class:`NeoChatterConfig` 与构造私有 chatter）。
        """
        self.stream_id = stream_id
        self.plugin = plugin
        self._config: NeoChatterConfig | None = (
            plugin.config if isinstance(plugin.config, NeoChatterConfig) else None
        )
        self._logger: Logger = get_logger("neo_chatter", display="Neo-Chatter")
        self._chatter: Any = None  # 私有 NeoChatter 实例，延迟创建

    @property
    def logger(self) -> Logger:
        """会话日志记录器。"""
        return self._logger

    @property
    def _runtime(self) -> Any:
        """延迟创建私有 NeoChatter 实例，复用 BaseChatter 的会话辅助方法。

        该实例仅用于调用 ``fetch_unreads`` / ``flush_unreads`` / ``create_request``
        / ``inject_usables`` / ``run_tool_call`` 等辅助方法，**不会**调用其 ``execute()``，
        因此不会与驱动方产生循环。
        """
        if self._chatter is None:
            from .components.chatter import NeoChatter

            self._chatter = NeoChatter(self.stream_id, self.plugin)
        return self._chatter

    def _cfg(self) -> _ConfigView:
        """读取配置字段，配置不可用时回退到默认值。"""
        cfg = self._config
        if cfg is None:
            return _ConfigView()
        return _ConfigView(
            native_multimodal=bool(cfg.plugin.native_multimodal),
            default_stop_minutes=float(cfg.plugin.default_stop_minutes),
            enable_cooldown=bool(cfg.plugin.enable_cooldown),
            enable_action_suspend=bool(cfg.plugin.enable_action_suspend),
            enable_stop_wake=bool(cfg.plugin.enable_stop_direct_message_wake),
            stop_wake_prob=float(cfg.plugin.stop_direct_message_wake_probability),
            placeholder=str(cfg.plugin.image_placeholder_template),
            actor_task_name=str(cfg.plugin.actor_task_name),
        )

    def _apply_stop_wake(self, stop_result: Stop) -> Stop:
        """根据配置给 Stop 结果补上直接唤醒参数。"""
        cfg = self._cfg()
        probability = max(0.0, min(1.0, cfg.stop_wake_prob))
        return Stop(
            time=stop_result.time,
            direct_message_wake_enabled=cfg.enable_stop_wake,
            direct_message_wake_probability=probability,
            step_data=stop_result.step_data,
        )

    def _append_user_payload(
        self,
        response: "LLMResponse",
        formatted_text: str,
        unread_msgs: list[Message] | None = None,
    ) -> None:
        """把 user 提示词追加为 USER payload，按配置决定是否内联图片。"""
        cfg = self._cfg()
        content_list: list[Content | LLMUsable]
        if cfg.native_multimodal and unread_msgs:
            images = extract_images_from_messages(unread_msgs)
            content_list = inline_images_into_text(formatted_text, images, cfg.placeholder)
            if images:
                self._logger.debug(f"已内联 {len(images)} 张图片到占位符位置")
        else:
            content_list = [Text(formatted_text)]
        response.add_payload(LLMPayload(ROLE.USER, content_list))

    async def execute(self) -> AsyncGenerator[Wait | Stop | Failure, WaitResumeEvent | None]:
        """激活聊天流并执行会话控制流。"""
        chat_stream = await stream_api.activate_stream(self.stream_id)
        if chat_stream is None:
            self._logger.error(f"无法激活聊天流: {self.stream_id}")
            yield Failure("无法激活聊天流")
            return

        runner = self._execute_with_stream(chat_stream)
        resume_event: WaitResumeEvent | None = None
        while True:
            try:
                result = await runner.asend(resume_event)
            except StopAsyncIteration:
                return
            resume_event = yield result

    async def _execute_with_stream(
        self,
        chat_stream: ChatStream,
    ) -> AsyncGenerator[Wait | Stop | Failure, WaitResumeEvent | None]:
        """以已激活的聊天流执行主会话逻辑。"""
        cfg = self._cfg()

        if cfg.native_multimodal:
            from src.core.managers.media_manager import get_media_manager

            get_media_manager().skip_recognition_for_stream(
                chat_stream.stream_id, ["image"]
            )
            self._logger.debug(
                f"已跳过流 {chat_stream.stream_id[:8]} 的图片识别（原生多模态）"
            )

        try:
            request = self._runtime.create_request(
                cfg.actor_task_name, with_reminder="actor"
            )
        except (ValueError, KeyError) as error:
            self._logger.error(f"模型配置错误: {error}")
            yield Failure(f"模型配置错误: {error}")
            return

        system_prompt_text = await NeoChatterPromptBuilder.build_system_prompt(
            self._config, chat_stream
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

        history_text = NeoChatterPromptBuilder.build_history_text(
            chat_stream, self._runtime.format_message_line
        )
        usable_map: ToolRegistry = await self._runtime.inject_usables(request)

        state = _SessionState(
            response=request,
            phase=_Phase.WAIT_USER,
            history_merged=False,
            unreads=[],
            unread_msgs_to_flush=[],
        )

        resume_event: WaitResumeEvent | None = None

        while True:
            current_resume = resume_event
            resume_event = None

            _, unread_msgs = await self._runtime.fetch_unreads()

            if state.phase == _Phase.WAIT_USER and not unread_msgs and current_resume is None:
                resume_event = yield Wait()
                continue

            if state.phase == _Phase.WAIT_USER:
                if _is_message_resume(current_resume):
                    current_resume = None

                if current_resume is not None:
                    if _is_timer_resume(current_resume):
                        resume_text = _build_timer_resume_prompt(current_resume)
                    else:
                        resume_text = _build_generic_resume_prompt(current_resume)
                    state.seen_signatures.clear()
                    state.used_tools_in_round.clear()
                    state.unreads = []
                    self._append_user_payload(state.response, resume_text)
                    state.phase = _Phase.MODEL_TURN
                    continue

                if not unread_msgs:
                    resume_event = yield Wait()
                    continue

                state.seen_signatures.clear()
                state.used_tools_in_round.clear()
                state.unreads = unread_msgs

                unread_lines = "\n".join(
                    self._runtime.format_message_line(msg) for msg in unread_msgs
                )

                decision = await run_preprocess(
                    chat_stream=chat_stream,
                    unreads=unread_msgs,
                    history_text=history_text if not state.history_merged else "",
                    config=self._config,
                    logger=self._logger,
                )

                if not decision.proceed:
                    if decision.force_stop_minutes is not None:
                        await self._runtime.flush_unreads(unread_msgs)
                        stop_result = Stop(
                            decision.force_stop_minutes * 60,
                            step_data=_consume_step_data(state),
                        )
                        yield self._apply_stop_wake(stop_result)
                        return
                    self._logger.info(
                        f"[预处理] 拦截但继续等待：{decision.reason or '未提供理由'}"
                    )
                    resume_event = yield Wait()
                    continue

                await self._runtime.flush_unreads(unread_msgs)
                extra = NeoChatterPromptBuilder.build_negative_behaviors_extra(self._config)
                if decision.extra:
                    extra = f"{extra}\n{decision.extra}" if extra else decision.extra

                user_prompt = await NeoChatterPromptBuilder.build_user_prompt(
                    chat_stream,
                    history_text=history_text if not state.history_merged else "",
                    unread_lines=unread_lines,
                    extra=extra,
                )
                state.history_merged = True
                self._append_user_payload(
                    state.response, user_prompt, unread_msgs=unread_msgs
                )
                state.unread_msgs_to_flush = []
                state.phase = _Phase.MODEL_TURN
                continue

            if state.phase in (_Phase.MODEL_TURN, _Phase.FOLLOW_UP):
                try:
                    state.response = await state.response.send(stream=False)
                    await state.response
                    if state.phase == _Phase.MODEL_TURN and state.unread_msgs_to_flush:
                        await self._runtime.flush_unreads(state.unread_msgs_to_flush)
                        state.unread_msgs_to_flush = []
                except Exception as error:  # noqa: BLE001
                    self._logger.error(f"LLM 请求失败: {error}", exc_info=True)
                    yield Failure("LLM 请求失败", error)
                    state.phase = _Phase.WAIT_USER
                    continue

                state.phase = _Phase.TOOL_EXEC
                continue

            if state.phase == _Phase.TOOL_EXEC:
                response = state.response
                calls = getattr(response, "call_list", None) or []
                state.used_tools_in_round.update(
                    str(getattr(c, "name", "") or "").strip() for c in calls
                )

                if not calls:
                    message = getattr(response, "message", None)
                    if isinstance(message, str) and message.strip() == _SUSPEND_TEXT:
                        resume_event = yield Wait(step_data=_consume_step_data(state))
                        state.phase = _Phase.WAIT_USER
                        continue
                    if message and message.strip():
                        self._logger.warning(
                            f"LLM 返回纯文本而非工具调用: {message[:100]}"
                        )
                    resume_event = yield Wait(step_data=_consume_step_data(state))
                    state.phase = _Phase.WAIT_USER
                    continue

                self._logger.info(
                    f"当前回合的工具调用: {[c.name for c in calls]}"
                )

                outcome: ToolCallOutcome = await process_tool_calls(
                    stream_id=chat_stream.stream_id,
                    calls=calls,
                    response=response,
                    run_tool_call=self._runtime.run_tool_call,
                    usable_map=usable_map,
                    trigger_msg=_pick_trigger_message(chat_stream, state.unreads),
                    pass_call_name=_PASS_CALL_NAME,
                    stop_call_name=_STOP_CALL_NAME,
                    default_stop_minutes=cfg.default_stop_minutes,
                    logger=self._logger,
                    seen_signatures=state.seen_signatures,
                )

                if outcome.should_stop:
                    cooldown = (
                        outcome.stop_minutes * 60 if cfg.enable_cooldown else 0
                    )
                    stop_result = Stop(cooldown, step_data=_consume_step_data(state))
                    yield self._apply_stop_wake(stop_result)
                    return

                if outcome.has_pending_tool_results:
                    state.phase = _Phase.FOLLOW_UP
                    continue

                action_only = bool(calls) and all(
                    c.name.startswith("action-") for c in calls
                )
                append_suspend_payload_if_action_only(
                    calls=calls,
                    response=response,
                    suspend_text=_SUSPEND_TEXT,
                    enable_action_suspend=cfg.enable_action_suspend,
                    logger=self._logger,
                )

                if action_only and not outcome.should_wait:
                    if cfg.enable_action_suspend:
                        resume_event = yield Wait(step_data=_consume_step_data(state))
                        state.phase = _Phase.WAIT_USER
                        continue
                    state.phase = _Phase.FOLLOW_UP
                    continue

                if outcome.should_wait:
                    _, fresh_unreads = await self._runtime.fetch_unreads()
                    if fresh_unreads:
                        self._logger.debug(
                            f"pass_and_wait 前检测到 {len(fresh_unreads)} 条新消息，跳过等待"
                        )
                        _consume_step_data(state)
                    else:
                        if state.has_tool_result_tail():
                            response.add_payload(
                                LLMPayload(ROLE.ASSISTANT, Text(_SUSPEND_TEXT))
                            )
                        resume_event = yield Wait(
                            time=outcome.wait_seconds,
                            step_data=_consume_step_data(state),
                        )
                else:
                    _consume_step_data(state)

                state.phase = _Phase.WAIT_USER
                continue
