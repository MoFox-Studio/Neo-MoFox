"""Neo-Default-Chatter 主会话逻辑。

``ConversationSession`` 是 NFC 的对话主流程载体：自包含、不持有宿主 chatter 引用，
所有上下文从 ``stream_id`` 经框架 API 取得。``NeoChatter``（注册的 Chatter）和
第三方插件都通过 :class:`NeoChatterService` 拿到同一个 session 并驱动它。

会话状态机沿用 default_chatter 的四阶段模型（WAIT_USER / MODEL_TURN / TOOL_EXEC /
FOLLOW_UP），但用**事件驱动预处理**（发布 ``neo_default_chatter:preprocess``）替代了
sub-agent / 兴趣值过滤，并去掉了子代理协作、语义训练等内置策略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, TYPE_CHECKING

from src.app.plugin_system.api import stream_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import Failure, Stop, Wait, WaitResumeEvent
from src.app.plugin_system.types import (
    ChatStream,
    LLMPayload,
    LLMResponse,
    Message,
    ROLE,
    Text,
    ToolRegistry,
)

from .components.config import NeoChatterConfig
from .utils.event_publisher import NdfcPublisher
from .utils.prompt_builder import NeoChatterPromptBuilder
from .utils.tool_flow import (
    ToolCallOutcome,
    append_suspend_payload_if_action_only,
    append_suspend_payload_if_tool_result_tail,
    process_tool_calls,
)

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

#: 模块级日志记录器。
#:
#: 本文件所有 ``ConversationSession`` 实例共享同一个 logger，避免在每次创建会话
#: 时重复构造；同时与同插件的 ``service.py`` / ``actions/send_text.py`` 风格保持一致
#: （都使用 ``get_logger("neo_default_chatter", ...)``）。
logger = get_logger("neo_default_chatter", display="Neo-Default-Chatter")

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

    response: LLMConversationState  # LLMRequest | LLMResponse（鸭子类型，两者都有 add_payload/payloads/send）
    phase: _Phase
    history_merged: bool
    unreads: list[Message]
    unread_msgs_to_flush: list[Message]
    used_tools_in_round: set[str] = field(default_factory=set)
    seen_signatures: set[str] = field(default_factory=set)

    def has_tool_result_tail(self) -> bool:
        """检查 response 末尾的 payload 是否为 TOOL_RESULT 角色。

        用于 ``pass_and_wait`` 场景：当末尾 payload 是工具结果时，LLM 在下一轮
        可能会把上一次的工具结果误认成自己刚刚的输出；为此需要在 ``Wait`` 之前
        追加一条 ``ASSISTANT`` 占位（``__SUSPEND__``）切断上下文。

        Returns:
            bool: 末尾 payload 是 ``ROLE.TOOL_RESULT`` 时为 ``True``，
                否则（包括没有 payload 的情况）为 ``False``。
        """
        payloads = getattr(self.response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)


@dataclass(slots=True)
class _ConfigView:
    """从 :class:`NeoChatterConfig` 读取的运行时选项视图；配置缺失时回退默认值。"""

    native_multimodal: bool = False
    default_stop_minutes: float = 5.0
    enable_action_suspend: bool = True
    enable_stop_wake: bool = False
    actor_task_name: str = "actor"


def _is_message_resume(event: WaitResumeEvent | None) -> bool:
    """判断恢复事件是否来自新用户消息（即等待期间收到了用户的新消息）。

    消息恢复表示用户主动打破了等待，此时 ``fetch_unreads`` 会拉到这条新消息，
    会话不应再把 resume 文本拼进 prompt，而是走正常未读消息处理路径。

    Args:
        event: 驱动方送来的恢复事件，可能为 ``None``。

    Returns:
        bool: ``event`` 非空且 ``event.source == "message"`` 时为 ``True``。
    """
    return event is not None and event.source == "message"


def _consume_step_data(state: _SessionState) -> dict[str, Any]:
    """汇总本回合步骤元数据，供框架在步进完成后发布 ``actor_round`` 通知事件。

    同时清空 ``used_tools_in_round``，使下一回合的统计从 0 开始。
    返回结构形如 ``{"step_scope": "actor_round", "used_tools": ["send_text", ...]}``。

    Args:
        state: 当前会话运行时状态。

    Returns:
        dict[str, Any]: 包含 ``step_scope`` 与 ``used_tools`` 字段的元数据。
    """
    used_tools = sorted(state.used_tools_in_round)
    state.used_tools_in_round.clear()
    return {"step_scope": _AFTER_CHATTER_STEP_SCOPE, "used_tools": used_tools}


def _format_tool_args(args: Any) -> str:
    """格式化工具调用参数用于日志展示；跳过 ``reason`` 键。"""
    if not isinstance(args, dict):
        return ""
    display_items: list[str] = []
    for key, value in args.items():
        if key == "reason":
            continue
        display_items.append(f"{key}: {value}")
    return ", ".join(display_items)


def _build_actor_decision_panel(chat_stream: ChatStream, response: LLMResponse) -> str:
    """构造 Actor 决策面板文本：展示思考、独白与工具调用列表。"""
    stream_name = (
        chat_stream.stream_name
        or chat_stream.stream_id
        or "未知聊天流"
    )
    thought = response.reasoning_content.strip() if response.reasoning_content else "（无）"
    monologue = response.message.strip() if response.message else "（无）"

    tool_lines: list[str] = []
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
    chat_stream: ChatStream, response: LLMResponse
) -> None:
    """在控制台渲染 Actor 决策面板；无工具调用时跳过。"""
    if not response.call_list:
        return
    logger.print_panel(
        _build_actor_decision_panel(chat_stream, response),
        title="Actor 决策",
        border_style="cyan",
    )


class ConversationSession:
    """Neo-Default-Chatter 主会话逻辑。

    通过 :class:`NeoChatterService` 创建。调用 ``execute()`` 得到一个异步生成器，
    产出 ``Wait / Success / Failure / Stop``，并接收 ``WaitResumeEvent`` 恢复事件。
    """

    def __init__(self, stream_id: str, plugin: "BasePlugin") -> None:
        """初始化会话。

        Args:
            stream_id: 目标聊天流 ID。
            plugin: NFC 插件实例（用于读取 :class:`NeoChatterConfig`）。
        """
        self.stream_id = stream_id
        self.plugin = plugin
        self._config: NeoChatterConfig | None = (
            plugin.config if isinstance(plugin.config, NeoChatterConfig) else None
        )

    def _cfg(self) -> _ConfigView:
        """读取配置字段，配置不可用时回退到默认值。

        会话持有的 :class:`NeoChatterConfig` 可能在以下情况下为 ``None``：

        - 插件被加载但配置尚未热加载
        - 第三方插件传入的 plugin 实例 ``config`` 不是 ``NeoChatterConfig``

        此时返回一个全默认值的 :class:`_ConfigView`，保证会话能继续运行。

        Returns:
            _ConfigView: 包含本会话所需运行时选项的快照视图。
        """
        cfg = self._config
        if cfg is None:
            return _ConfigView()
        return _ConfigView(
            native_multimodal=bool(cfg.plugin.native_multimodal),
            default_stop_minutes=float(cfg.plugin.default_stop_minutes),
            enable_action_suspend=bool(cfg.plugin.enable_action_suspend),
            enable_stop_wake=bool(cfg.plugin.enable_stop_direct_message_wake),
            actor_task_name=str(cfg.plugin.actor_task_name),
        )

    async def _apply_stop_wake(
        self, stop_result: Stop, chat_type: Any
    ) -> Stop:
        """根据配置给 :class:`Stop` 结果补上「直接消息唤醒」参数（异步）。

        发布 :compute_stop_wake 事件委托默认 handler 算概率；``chat_type`` 透传
        给 handler 用于「仅私聊启用」判定（依据 §3.2.6）。

        Args:
            stop_result: 已构造好的 ``Stop``，包含冷却时长与 step_data。
            chat_type: 当前聊天流类型（``ChatType`` 枚举或字符串）。

        Returns:
            补齐 ``direct_message_wake_*`` 字段后的新 ``Stop`` 实例。
        """
        cfg = self._cfg()
        # 默认: defaults/compute_stop_wake.py
        probability = await NdfcPublisher.compute_stop_wake(
            stream_id=self.stream_id,
            config=self._config,
            chat_type=chat_type,
        )
        return Stop(
            time=stop_result.time,
            direct_message_wake_enabled=cfg.enable_stop_wake,
            direct_message_wake_probability=probability,
            step_data=stop_result.step_data,
        )

    async def _append_user_payload(
        self,
        response: "LLMResponse",
        formatted_text: str,
        unread_msgs: list[Message] | None = None,
    ) -> None:
        """把 user 提示词追加为 ``ROLE.USER`` payload（异步）。

        发布 :inject_unread_payload 事件委托默认 handler 注入：原生多模态开启时
        从未读消息提取图片按占位符内联，否则把提示文本作为纯 ``Text`` 追加。

        Args:
            response: 待追加 payload 的 LLM 请求 / 响应对象。
            formatted_text: 已格式化的用户提示词文本。
            unread_msgs: 本轮未读消息；多模态内联时用于提取图片。
        """
        cfg = self._cfg()
        # 默认: defaults/inject_unread_payload.py
        await NdfcPublisher.inject_unread_payload(
            stream_id=self.stream_id,
            response=response,
            formatted_text=formatted_text,
            unread_msgs=unread_msgs,
            native_multimodal=cfg.native_multimodal,
        )

    async def _transition(
        self, *, state: _SessionState, to_phase: _Phase, reason: str
    ) -> None:
        """切换状态机阶段，并在阶段变化时打 DEBUG 日志 + 发布 :session_transition 事件。

        Args:
            state: 当前会话运行时状态。
            to_phase: 目标阶段。
            reason: 阶段切换的人类可读理由（写入日志）。
        """
        if state.phase == to_phase:
            return
        from_phase = state.phase.value
        logger.debug(f"[FSM] {from_phase} -> {to_phase.value}: {reason}")
        state.phase = to_phase
        # 默认: defaults/session_transition.py
        await NdfcPublisher.session_transition(
            stream_id=self.stream_id,
            from_phase=from_phase,
            to_phase=to_phase.value,
        )

    async def execute(self) -> AsyncGenerator[Wait | Stop | Failure, WaitResumeEvent | None]:
        """激活聊天流并执行会话控制流。

        本方法是异步生成器，由驱动方（``NeoChatter`` 或第三方插件）``async for`` 驱动：

        - 每次产出 ``Wait / Stop / Failure`` 后挂起，等待驱动方通过 ``asend`` 推入
          ``WaitResumeEvent`` 恢复事件；
        - 恢复事件会原样透传给内部的 :meth:`_execute_with_stream`；
        - 会话产出 :class:`Stop` 或 :class:`Failure` 时视为终态，方法返回。
        """
        # 1) 激活目标聊天流；失败时直接产出 Failure 结束会话
        chat_stream = await stream_api.activate_stream(self.stream_id)
        if chat_stream is None:
            logger.error(f"无法激活聊天流: {self.stream_id}")
            yield Failure("无法激活聊天流")
            return

        # 2) 构造真正的会话生成器；维护「yield 出去 → asend 进来」的恢复事件透传循环
        runner = self._execute_with_stream(chat_stream)
        resume_event: WaitResumeEvent | None = None
        while True:
            try:
                # 3) 把上一次的 resume 推进给会话；会话产出新结果或自然结束
                result = await runner.asend(resume_event)
            except StopAsyncIteration:
                # 3a) 会话主动结束（产出 Stop/Failure 之后），整个 execute 也随之结束
                return
            # 4) 把会话产出物交给驱动方，同时挂起等待下一次 resume
            resume_event = yield result

    async def _execute_with_stream(
        self,
        chat_stream: ChatStream,
    ) -> AsyncGenerator[Wait | Stop | Failure, WaitResumeEvent | None]:
        """以已激活的聊天流执行主会话逻辑。

        主流程是状态机循环：``WAIT_USER → MODEL_TURN → TOOL_EXEC → FOLLOW_UP``，
        各阶段的语义见 :class:`_Phase`。本方法只产出 ``Wait / Stop / Failure``，
        不产出 ``Success``（NFC 默认通过 ``Wait`` 持续待机，主动结束用 ``Stop``）。

        Args:
            chat_stream: 由 :func:`stream_api.activate_stream` 激活的聊天流。
        """
        # === 阶段 0：运行前初始化 ===
        cfg = self._cfg()

        # 0.1) 原生多模态：让媒体管理器跳过本流的图片识别，
        #      图片改为通过占位符 ``[图片-{idx}]`` 内联到用户提示词
        if cfg.native_multimodal:
            from src.core.managers.media_manager import get_media_manager

            get_media_manager().skip_recognition_for_stream(
                chat_stream.stream_id, ["image"]
            )
            logger.debug(
                f"已跳过流 {chat_stream.stream_id[:8]} 的图片识别（原生多模态）"
            )

        # 0.2) 创建 LLM 请求对象；模型配置缺失 / 任务名错误时直接失败退出
        try:
            # 默认: defaults/create_request.py
            request = await NdfcPublisher.create_request(
                stream_id=chat_stream.stream_id,
                task_name=cfg.actor_task_name,
                with_reminder="actor",
            )
        except (ValueError, KeyError) as error:
            logger.error(f"模型配置错误: {error}")
            yield Failure(f"模型配置错误: {error}")
            return

        # 0.3) 构造一次性的系统提示词与历史摘要，并注入本会话可用的工具集
        system_prompt_text = await NeoChatterPromptBuilder.build_system_prompt(
            self._config, chat_stream
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

        # 默认: defaults/build_history_text.py
        history_text = await NdfcPublisher.build_history_text(
            stream_id=chat_stream.stream_id, chat_stream=chat_stream
        )
        # 默认: defaults/inject_usables.py
        usable_map: ToolRegistry = await NdfcPublisher.inject_usables(
            stream_id=chat_stream.stream_id, request=request
        )

        # 0.4) 初始化会话运行时状态，从 WAIT_USER 起步
        state = _SessionState(
            response=request,
            phase=_Phase.WAIT_USER,
            history_merged=False,
            unreads=[],
            unread_msgs_to_flush=[],
        )

        # resume_event 累积驱动方送来的恢复事件；循环顶部会把它「领走」并清零
        resume_event: WaitResumeEvent | None = None

        # === 主状态机循环 ===
        while True:
            # 1) 本轮要消费的恢复事件；用完即清，避免下一轮误用
            current_resume = resume_event
            resume_event = None

            # 2) 拉取最新未读消息（无论处于哪个阶段都拉，便于 WAIT_USER 决策）
            # 默认: defaults/fetch_unreads.py
            unread_msgs = await NdfcPublisher.fetch_unreads(chat_stream.stream_id)

            # === 阶段 WAIT_USER：等待用户消息 / 处理恢复事件 ===
            if state.phase == _Phase.WAIT_USER and not unread_msgs and current_resume is None:
                # 2.1) 没有新消息、也没有恢复事件：纯挂起等用户说话
                resume_event = yield Wait()
                continue

            if state.phase == _Phase.WAIT_USER:
                # 2.2) 消息恢复代表用户主动打破等待，新消息会走未读消息路径，
                #      所以这里把 resume 「吃掉」，不再拼 resume 文本
                if _is_message_resume(current_resume):
                    current_resume = None

                # 2.3) 处理非消息类恢复（定时器 / 子代理 / 内部上下文）：
                #      把构造好的系统提示塞进对话历史，让 LLM 自行决定下一步
                if current_resume is not None:
                    # 默认: defaults/build_resume_prompt.py
                    resume_text = await NdfcPublisher.build_resume_prompt(
                        stream_id=chat_stream.stream_id,
                        resume_event=current_resume,
                        source=current_resume.source or "",
                    )
                    # 恢复视为新一轮，重置跨回合状态
                    state.seen_signatures.clear()
                    state.used_tools_in_round.clear()
                    state.unreads = []
                    await self._append_user_payload(state.response, resume_text)
                    await self._transition(
                        state=state,
                        to_phase=_Phase.MODEL_TURN,
                        reason="收到恢复事件",
                    )
                    continue

                # 2.4) 没有恢复事件，但也没有未读消息：继续等
                if not unread_msgs:
                    resume_event = yield Wait()
                    continue

                # 2.5) 收到未读消息：重置跨回合状态，准备走预处理 → 模型生成
                state.seen_signatures.clear()
                state.used_tools_in_round.clear()
                state.unreads = unread_msgs

                # 默认: defaults/format_unread_line.py
                unread_lines = "\n".join(
                    [
                        await NdfcPublisher.format_unread_line(
                            stream_id=chat_stream.stream_id, message=msg
                        )
                        for msg in unread_msgs
                    ]
                )

                # 2.6) 跑 neo_default_chatter:preprocess 事件，让订阅者决定是否继续 / 注入 extra
                # 默认: probability_bypass.py + sub_agent_decision.py
                decision = await NdfcPublisher.preprocess(
                    chat_stream=chat_stream,
                    unreads=unread_msgs,
                    history_text=history_text if not state.history_merged else "",
                    config=self._config,
                    logger=logger,
                )

                # 2.7) 预处理要求拦截：
                #      - force_stop_minutes 不为空 → 立即 Stop 退出
                #      - 否则继续 Wait，把消息留到下一轮（不 flush）
                if not decision.proceed:
                    if decision.force_stop_minutes is not None:
                        # 默认: defaults/flush_unreads.py
                        await NdfcPublisher.flush_unreads(
                            stream_id=chat_stream.stream_id, messages=unread_msgs
                        )
                        stop_result = Stop(
                            decision.force_stop_minutes * 60,
                            step_data=_consume_step_data(state),
                        )
                        yield await self._apply_stop_wake(
                            stop_result, chat_stream.chat_type
                        )
                        return
                    logger.info(
                        f"[预处理] 拦截但继续等待：{decision.reason or '未提供理由'}"
                    )
                    resume_event = yield Wait()
                    continue

                # 2.8) 预处理放行：flush 未读消息，构造本轮 USER 提示词并追加
                # 默认: defaults/flush_unreads.py
                await NdfcPublisher.flush_unreads(
                    stream_id=chat_stream.stream_id, messages=unread_msgs
                )
                # 默认: defaults/build_negative_extra.py
                neg_extra = await NdfcPublisher.build_negative_extra(
                    stream_id=chat_stream.stream_id, config=self._config
                )
                fragments = [neg_extra] if neg_extra else []
                if decision.extra:
                    fragments.append(decision.extra)
                extra = "\n".join(fragments)

                user_prompt = await NeoChatterPromptBuilder.build_user_prompt(
                    chat_stream,
                    history_text=history_text if not state.history_merged else "",
                    unread_lines=unread_lines,
                    extra=extra,
                )
                state.history_merged = True
                await self._append_user_payload(
                    state.response, user_prompt, unread_msgs=unread_msgs
                )
                state.unread_msgs_to_flush = []
                await self._transition(
                    state=state,
                    to_phase=_Phase.MODEL_TURN,
                    reason="收到未读消息",
                )
                continue

            # === 阶段 MODEL_TURN / FOLLOW_UP：发起 LLM 请求 ===
            if state.phase in (_Phase.MODEL_TURN, _Phase.FOLLOW_UP):
                try:
                    # 3.1) 非流式发送请求并 await 完成；FOLLOW_UP 时是带工具结果的二次请求
                    logger.debug(
                        f"[LLM 请求] phase={state.phase.value} "
                        f"task={cfg.actor_task_name} "
                        f"payloads={len(state.response.payloads)}"
                    )
                    state.response = await state.response.send(stream=False)
                    await state.response
                    _resp = state.response
                    logger.debug(
                        f"[LLM 响应] message={(_resp.message or '')[:80]!r} "
                        f"calls={len(_resp.call_list or [])} "
                        f"reasoning={(_resp.reasoning_content or '')[:80]!r} "
                        f"stop={_resp.stop_reason}"
                    )
                    # 3.2) MODEL_TURN 完成后处理积压的 flush（避免重复发消息）
                    if state.phase == _Phase.MODEL_TURN and state.unread_msgs_to_flush:
                        # 默认: defaults/flush_unreads.py
                        await NdfcPublisher.flush_unreads(
                            stream_id=chat_stream.stream_id,
                            messages=state.unread_msgs_to_flush,
                        )
                        state.unread_msgs_to_flush = []
                except Exception as error:  # noqa: BLE001
                    # 3.3) LLM 请求失败：产出 Failure，但保留会话，下一轮回到 WAIT_USER
                    logger.error(f"LLM 请求失败: {error}", exc_info=True)
                    yield Failure("LLM 请求失败", error)
                    await self._transition(
                        state=state,
                        to_phase=_Phase.WAIT_USER,
                        reason="请求失败",
                    )
                    continue

                # 3.4) 请求成功，进入工具执行阶段
                await self._transition(
                    state=state,
                    to_phase=_Phase.TOOL_EXEC,
                    reason="模型已响应",
                )
                continue

            # === 阶段 TOOL_EXEC：解析工具调用并执行 ===
            if state.phase == _Phase.TOOL_EXEC:
                response = state.response
                calls = response.call_list or []
                # 4.1) 统计本回合调用了哪些工具（供 actor_round 通知）
                state.used_tools_in_round.update(
                    str(getattr(c, "name", "") or "").strip() for c in calls
                )

                # 4.1.1) 渲染 Actor 决策面板（思考/独白/工具调用）
                _print_actor_decision_panel(chat_stream, response)

                # 4.2) 没有工具调用：
                #      - message == __SUSPEND__ → 上轮是 action_only 挂起，正常 Wait
                #      - 非空 message           → LLM 直接吐纯文本，告警并 Wait
                #      - 空 message             → 无内容也无调用，直接 Wait
                if not calls:
                    message = getattr(response, "message", None)
                    if isinstance(message, str) and message.strip() == _SUSPEND_TEXT:
                        resume_event = yield Wait(step_data=_consume_step_data(state))
                        await self._transition(
                            state=state,
                            to_phase=_Phase.WAIT_USER,
                            reason="SUSPEND 挂起",
                        )
                        continue
                    if message and message.strip():
                        logger.warning(
                            f"LLM 返回纯文本而非工具调用: {message[:100]}"
                        )
                    resume_event = yield Wait(step_data=_consume_step_data(state))
                    await self._transition(
                        state=state,
                        to_phase=_Phase.WAIT_USER,
                        reason="无工具调用",
                    )
                    continue

                # 4.3) 有工具调用：日志记录，再交给 tool_flow 统一执行
                logger.info(
                    f"当前回合的工具调用: {[c.name for c in calls]}"
                )
                for call in calls:
                    args = call.args if isinstance(call.args, dict) else {}
                    reason = args.get("reason", "未提供原因")
                    logger.info(
                        f"LLM 调用了 {call.name}; 原因={reason}; "
                        f"参数={_format_tool_args(call.args)}"
                    )

                # 默认: defaults/pick_trigger_message.py
                trigger_msg = await NdfcPublisher.pick_trigger_message(
                    stream_id=chat_stream.stream_id,
                    chat_stream=chat_stream,
                    unreads=state.unreads,
                )

                async def _run_tool_call_cb(
                    calls: list,
                    response: Any,
                    usable_map: ToolRegistry,
                    trigger_msg: Message | None,
                ) -> list[tuple[bool, bool]]:
                    # 默认: defaults/run_tool_call.py
                    return await NdfcPublisher.run_tool_call(
                        stream_id=chat_stream.stream_id,
                        calls=calls,
                        response=response,
                        usable_map=usable_map,
                        trigger_msg=trigger_msg,
                    )

                outcome: ToolCallOutcome = await process_tool_calls(
                    stream_id=chat_stream.stream_id,
                    calls=calls,
                    response=response,
                    run_tool_call=_run_tool_call_cb,
                    usable_map=usable_map,
                    trigger_msg=trigger_msg,
                    pass_call_name=_PASS_CALL_NAME,
                    stop_call_name=_STOP_CALL_NAME,
                    default_stop_minutes=cfg.default_stop_minutes,
                    logger=logger,
                    seen_signatures=state.seen_signatures,
                )

                # 4.4) 工具要求 Stop（pass_and_wait 给出 stop_minutes 或 stop_conversation）
                if outcome.should_stop:
                    # 默认: defaults/compute_cooldown.py
                    cooldown = await NdfcPublisher.compute_cooldown(
                        stream_id=chat_stream.stream_id,
                        minutes=outcome.stop_minutes,
                        config=self._config,
                    )
                    stop_result = Stop(cooldown, step_data=_consume_step_data(state))
                    yield await self._apply_stop_wake(
                        stop_result, chat_stream.chat_type
                    )
                    return

                # 4.5) 还有未消费的工具结果：进入 FOLLOW_UP，让 LLM 二次请求消化
                if outcome.has_pending_tool_results:
                    await self._transition(
                        state=state,
                        to_phase=_Phase.FOLLOW_UP,
                        reason="有待消化的工具结果",
                    )
                    continue

                # 4.6) 全部为 action-* 工具（如 send_text）：考虑是否挂起会话
                action_only = bool(calls) and all(
                    c.name.startswith("action-") for c in calls
                )
                # 给纯 action 回合补一条 ASSISTANT __SUSPEND__ 占位，避免下次
                # LLM 误把工具结果当成自己的发言
                append_suspend_payload_if_action_only(
                    calls=calls,
                    response=response,
                    suspend_text=_SUSPEND_TEXT,
                    enable_action_suspend=cfg.enable_action_suspend,
                )

                # 4.7) 纯 action 且 pass_and_wait 没要求等待时：按配置挂起或直接 FOLLOW_UP
                if action_only and not outcome.should_wait:
                    if cfg.enable_action_suspend:
                        resume_event = yield Wait(step_data=_consume_step_data(state))
                        await self._transition(
                            state=state,
                            to_phase=_Phase.WAIT_USER,
                            reason="action 挂起",
                        )
                        continue
                    await self._transition(
                        state=state,
                        to_phase=_Phase.FOLLOW_UP,
                        reason="action 不挂起",
                    )
                    continue

                # 4.8) pass_and_wait 要求等待：进入定时等待分支
                if outcome.should_wait:
                    # 4.8.1) 在 Wait 之前再拉一次未读：若期间已有新消息，
                    #        跳过 Wait，直接回 WAIT_USER 处理（避免消息被 Wait 吞掉）
                    # 默认: defaults/fetch_unreads.py
                    fresh_unreads = await NdfcPublisher.fetch_unreads(
                        chat_stream.stream_id
                    )
                    if fresh_unreads:
                        logger.debug(
                            f"pass_and_wait 前检测到 {len(fresh_unreads)} 条新消息，跳过等待"
                        )
                        _consume_step_data(state)
                    else:
                        # 4.8.2) 真正进入 Wait：若末尾是工具结果，补一条
                        #        ASSISTANT __SUSPEND__ 切断上下文
                        append_suspend_payload_if_tool_result_tail(
                            response=response,
                            suspend_text=_SUSPEND_TEXT,
                        )
                        resume_event = yield Wait(
                            time=outcome.wait_seconds,
                            step_data=_consume_step_data(state),
                        )
                else:
                    # 4.9) 既不 Stop 也不 Wait：消费 step_data 后回 WAIT_USER
                    _consume_step_data(state)

                # 5) 默认回到 WAIT_USER，等待下一轮用户消息或恢复事件
                await self._transition(
                    state=state,
                    to_phase=_Phase.WAIT_USER,
                    reason="默认等待",
                )
                continue
