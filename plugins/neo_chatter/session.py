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
from src.app.plugin_system.api.log_api import get_logger
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
    append_suspend_payload_if_tool_result_tail,
    process_tool_calls,
)

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

#: 模块级日志记录器。
#:
#: 本文件所有 ``ConversationSession`` 实例共享同一个 logger，避免在每次创建会话
#: 时重复构造；同时与同插件的 ``service.py`` / ``actions/send_text.py`` 风格保持一致
#: （都使用 ``get_logger("neo_chatter", ...)``）。
logger = get_logger("neo_chatter", display="Neo-Chatter")

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
    enable_cooldown: bool = True
    enable_action_suspend: bool = True
    enable_stop_wake: bool = False
    stop_wake_prob: float = 0.0
    placeholder: str = "[图片-{idx}]"
    actor_task_name: str = "actor"


def _is_timer_resume(event: WaitResumeEvent | None) -> bool:
    """判断恢复事件是否来自定时器（即之前由 ``pass_and_wait`` 设置的等待已到期）。

    定时器恢复代表「LLM 自己设定的等待时间到了，但用户没有发新消息」，
    此时应该让 LLM 主动决定是否继续，而不是把消息塞进对话历史。

    Args:
        event: 驱动方送来的恢复事件，可能为 ``None``。

    Returns:
        bool: ``event`` 非空且 ``event.source == "timer"`` 时为 ``True``。
    """
    return event is not None and event.source == "timer"


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


def _build_timer_resume_prompt(event: WaitResumeEvent) -> str:
    """构造定时器恢复时塞进对话历史的 USER 提示文本。

    通知 LLM「上一次设置的等待已经结束、当前没有新用户消息」，并要求它基于
    现有上下文主动决策：要么再次调用 ``pass_and_wait`` 推迟回复，
    要么直接产出回复 / 调用工具。

    Args:
        event: ``WaitResumeEvent``，``event.source`` 为 ``"timer"``。

    Returns:
        str: 直接作为 USER payload 追加到 response 的提示文本。
    """
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
    """未知 / 子代理 / 内部上下文等 source 的通用恢复提示。

    当 ``WaitResumeEvent.source`` 既不是 ``"timer"`` 也不是 ``"message"`` 时调用，
    例如子代理 / 内部上下文 / 自定义事件源。若上游通过 ``event.extra["resume_prompt"]``
    显式提供了提示文本则直接使用；否则按 source 标签生成一段默认提示。

    Args:
        event: ``WaitResumeEvent``，``event.source`` 不是 ``"timer"`` / ``"message"``。

    Returns:
        str: 直接作为 USER payload 追加到 response 的提示文本。
    """
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
    """选取本轮工具执行所用的触发消息。

    工具调用需要绑定一条「触发消息」用于权限校验、回复路由、@ 解析等。
    选取顺序为：

    1. 本轮未读消息的最后一条（最常见，对应「用户发问→LLM 工具调用」）
    2. context.current_message（当前激活消息）
    3. context.unread_messages 的最后一条
    4. context.history_messages 的最后一条
    5. 全部为空时构造一个伪空消息，保证工具调用流程可以继续

    Args:
        chat_stream: 已激活的聊天流，用于回退取上下文消息。
        unreads: 本轮 ``fetch_unreads`` 返回的未读消息列表。

    Returns:
        Message: 选中的触发消息。
    """
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
        # 日志记录器使用模块级全局变量 ``logger``，无需在每个会话实例上重复构造
        self._chatter: Any = None  # 私有 NeoChatter 实例，延迟创建

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
            enable_cooldown=bool(cfg.plugin.enable_cooldown),
            enable_action_suspend=bool(cfg.plugin.enable_action_suspend),
            enable_stop_wake=bool(cfg.plugin.enable_stop_direct_message_wake),
            stop_wake_prob=float(cfg.plugin.stop_direct_message_wake_probability),
            placeholder=str(cfg.plugin.image_placeholder_template),
            actor_task_name=str(cfg.plugin.actor_task_name),
        )

    def _apply_stop_wake(self, stop_result: Stop) -> Stop:
        """根据配置给 :class:`Stop` 结果补上「直接消息唤醒」参数。

        框架的 ``Stop`` 表示「主动结束会话并冷却 N 秒」。当 ``enable_stop_wake``
        开启时，冷却期间收到用户私信仍可按概率提前唤醒；本方法把配置里的
        开关与概率合并到 ``stop_result`` 上返回新的 :class:`Stop`。

        Args:
            stop_result: 已构造好的 ``Stop``，包含冷却时长与 step_data。

        Returns:
            Stop: 补齐 ``direct_message_wake_*`` 字段后的新 ``Stop`` 实例。
        """
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
        """把 user 提示词追加为 ``ROLE.USER`` payload，按配置决定是否内联图片。

        多模态开关 ``native_multimodal`` 开启时，会从未读消息中提取所有图片，
        按占位符模板 ``[图片-{idx}]`` 内联到提示文本里，构造多模态 Content 列表；
        否则把提示文本作为纯 ``Text`` 追加，图片交给媒体管理器走识别路径。

        Args:
            response: 待追加 payload 的 LLM 请求 / 响应对象。
            formatted_text: 已格式化的用户提示词文本。
            unread_msgs: 本轮未读消息；多模态内联时用于提取图片。
        """
        cfg = self._cfg()
        content_list: list[Content | LLMUsable]
        if cfg.native_multimodal and unread_msgs:
            images = extract_images_from_messages(unread_msgs)
            content_list = inline_images_into_text(formatted_text, images, cfg.placeholder)
            if images:
                logger.debug(f"已内联 {len(images)} 张图片到占位符位置")
        else:
            content_list = [Text(formatted_text)]
        response.add_payload(LLMPayload(ROLE.USER, content_list))

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
            request = self._runtime.create_request(
                cfg.actor_task_name, with_reminder="actor"
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

        history_text = NeoChatterPromptBuilder.build_history_text(
            chat_stream, self._runtime.format_message_line
        )
        usable_map: ToolRegistry = await self._runtime.inject_usables(request)

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
            _, unread_msgs = await self._runtime.fetch_unreads()


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
                    if _is_timer_resume(current_resume):
                        resume_text = _build_timer_resume_prompt(current_resume)
                    else:
                        resume_text = _build_generic_resume_prompt(current_resume)
                    # 恢复视为新一轮，重置跨回合状态
                    state.seen_signatures.clear()
                    state.used_tools_in_round.clear()
                    state.unreads = []
                    self._append_user_payload(state.response, resume_text)
                    state.phase = _Phase.MODEL_TURN
                    continue

                # 2.4) 没有恢复事件，但也没有未读消息：继续等
                if not unread_msgs:
                    resume_event = yield Wait()
                    continue

                # 2.5) 收到未读消息：重置跨回合状态，准备走预处理 → 模型生成
                state.seen_signatures.clear()
                state.used_tools_in_round.clear()
                state.unreads = unread_msgs

                unread_lines = "\n".join(
                    self._runtime.format_message_line(msg) for msg in unread_msgs
                )

                # 2.6) 跑 neo_chatter:preprocess 事件，让订阅者决定是否继续 / 注入 extra
                decision = await run_preprocess(
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
                        await self._runtime.flush_unreads(unread_msgs)
                        stop_result = Stop(
                            decision.force_stop_minutes * 60,
                            step_data=_consume_step_data(state),
                        )
                        yield self._apply_stop_wake(stop_result)
                        return
                    logger.info(
                        f"[预处理] 拦截但继续等待：{decision.reason or '未提供理由'}"
                    )
                    resume_event = yield Wait()
                    continue

                # 2.8) 预处理放行：flush 未读消息，构造本轮 USER 提示词并追加
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

            # === 阶段 MODEL_TURN / FOLLOW_UP：发起 LLM 请求 ===
            if state.phase in (_Phase.MODEL_TURN, _Phase.FOLLOW_UP):
                try:
                    # 3.1) 非流式发送请求并 await 完成；FOLLOW_UP 时是带工具结果的二次请求
                    state.response = await state.response.send(stream=False)
                    await state.response
                    # 3.2) MODEL_TURN 完成后处理积压的 flush（避免重复发消息）
                    if state.phase == _Phase.MODEL_TURN and state.unread_msgs_to_flush:
                        await self._runtime.flush_unreads(state.unread_msgs_to_flush)
                        state.unread_msgs_to_flush = []
                except Exception as error:  # noqa: BLE001
                    # 3.3) LLM 请求失败：产出 Failure，但保留会话，下一轮回到 WAIT_USER
                    logger.error(f"LLM 请求失败: {error}", exc_info=True)
                    yield Failure("LLM 请求失败", error)
                    state.phase = _Phase.WAIT_USER
                    continue

                # 3.4) 请求成功，进入工具执行阶段
                state.phase = _Phase.TOOL_EXEC
                continue

            # === 阶段 TOOL_EXEC：解析工具调用并执行 ===
            if state.phase == _Phase.TOOL_EXEC:
                response = state.response
                calls = response.call_list or []
                # 4.1) 统计本回合调用了哪些工具（供 actor_round 通知）
                state.used_tools_in_round.update(
                    str(getattr(c, "name", "") or "").strip() for c in calls
                )

                # 4.2) 没有工具调用：
                #      - message == __SUSPEND__ → 上轮是 action_only 挂起，正常 Wait
                #      - 非空 message           → LLM 直接吐纯文本，告警并 Wait
                #      - 空 message             → 无内容也无调用，直接 Wait
                if not calls:
                    message = getattr(response, "message", None)
                    if isinstance(message, str) and message.strip() == _SUSPEND_TEXT:
                        resume_event = yield Wait(step_data=_consume_step_data(state))
                        state.phase = _Phase.WAIT_USER
                        continue
                    if message and message.strip():
                        logger.warning(
                            f"LLM 返回纯文本而非工具调用: {message[:100]}"
                        )
                    resume_event = yield Wait(step_data=_consume_step_data(state))
                    state.phase = _Phase.WAIT_USER
                    continue

                # 4.3) 有工具调用：日志记录，再交给 tool_flow 统一执行
                logger.info(
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
                    logger=logger,
                    seen_signatures=state.seen_signatures,
                )

                # 4.4) 工具要求 Stop（pass_and_wait 给出 stop_minutes 或 stop_conversation）
                if outcome.should_stop:
                    cooldown = (
                        outcome.stop_minutes * 60 if cfg.enable_cooldown else 0
                    )
                    stop_result = Stop(cooldown, step_data=_consume_step_data(state))
                    yield self._apply_stop_wake(stop_result)
                    return

                # 4.5) 还有未消费的工具结果：进入 FOLLOW_UP，让 LLM 二次请求消化
                if outcome.has_pending_tool_results:
                    state.phase = _Phase.FOLLOW_UP
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
                        state.phase = _Phase.WAIT_USER
                        continue
                    state.phase = _Phase.FOLLOW_UP
                    continue

                # 4.8) pass_and_wait 要求等待：进入定时等待分支
                if outcome.should_wait:
                    # 4.8.1) 在 Wait 之前再拉一次未读：若期间已有新消息，
                    #        跳过 Wait，直接回 WAIT_USER 处理（避免消息被 Wait 吞掉）
                    _, fresh_unreads = await self._runtime.fetch_unreads()
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
                state.phase = _Phase.WAIT_USER
                continue
