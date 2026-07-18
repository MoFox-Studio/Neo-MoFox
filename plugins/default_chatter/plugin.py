"""DefaultChatter 插件。

提供默认的聊天对话逻辑，包含三个核心 Action：
- send_text: 发送文本消息给用户
- pass_and_wait: 跳过本次动作，等待新消息
- stop_conversation: 结束当前对话轮次，设置冷却时间

使用 personality 配置动态构建系统提示词。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from src.core.components.types import ChatType
from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import (
    BaseChatter,
    BasePlugin,
    Wait,
    WaitResumeEvent,
    Success,
    Failure,
    Stop,
)
from src.core.models.stream import ChatStream
from src.core.components.loader import register_plugin
from src.core.config import get_core_config
from src.core.prompt import get_prompt_manager
from src.core.models.message import Message
from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.payload.content import Content
from src.kernel.llm.payload.tooling import LLMUsable
from src.kernel.logger import Logger

from .actions import (
    PassAndWaitAction,
    SendTextAction,
    StopConversationAction,
)
from .config import DefaultChatterConfig
from .interest_gate import InterestGate
from .multimodal import (
    extract_images_from_messages,
    inline_images_into_text,
)
from .prompt_builder import DefaultChatterPromptBuilder
from .prompts import system_prompt, user_prompt, sub_agent_system_prompt
from .service import DefaultChatterService
from .type_defs import (
    DefaultChatterSessionOptions,
    LLMConversationState,
    LLMResponseLike,
    SubAgentDecision,
)
from .usable_resolver import (
    build_sub_agent_collaboration_system_extra,
    create_managed_sub_agent as _create_managed_sub_agent,
    inject_collaboration_usables,
    kill_managed_sub_agent as _kill_managed_sub_agent,
    query_managed_sub_agent as _query_managed_sub_agent,
)

logger = get_logger("default_chatter")

# 控制流标记名称，与 BaseAction.to_schema() 生成的 name 保持一致（含 action- 前缀）
_PASS_AND_WAIT = "action-pass_and_wait"
_STOP_CONVERSATION = "action-stop_conversation"

# SUSPEND 占位符：当 LLM 本轮全部调用的都是 action 时，注入此占位防止上下文缺少 assistant 轮次
_SUSPEND_TEXT = "__SUSPEND__"


class DefaultChatter(BaseChatter):
    """默认聊天组件。

    实现完整的对话循环：
    1. 构建 LLM 上下文（系统提示 + 历史消息 + 当前未读消息）
    2. 注册所有可用的 LLMUsable 工具
    3. 循环调用 LLM 并执行其返回的 tool calls
    4. 根据 pass_and_wait / stop_conversation 控制对话流程

    概率门和兴趣值决策分别委托给 probability_gate 和 interest_gate 模块。
    """

    chatter_name: str = "default_chatter"
    chatter_description: str = "默认聊天组件，提供基础的消息处理和回复功能"

    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL

    dependencies: list[str] = []

    def __init__(self, stream_id: str, plugin: Any) -> None:
        """初始化 DefaultChatter。

        Args:
            stream_id: 聊天流 ID
            plugin: 插件实例
        """
        super().__init__(stream_id, plugin)
        self._interest_gate = InterestGate(plugin)
        self._interest_gate._chatter = self

    # ── 会话选项与配置读取 ────────────────────────────────────

    def _build_session_options(self) -> DefaultChatterSessionOptions:
        """构造DefaultChatterSessionOptions，供会话使用。"""
        return DefaultChatterService._build_default_options(self.plugin)

    def _is_action_suspend_enabled(self) -> bool:
        """读取纯 Action 回合的 SUSPEND 开关。"""
        plugin_config = getattr(self.plugin, "config", None)
        return not isinstance(plugin_config, DefaultChatterConfig) or bool(
            plugin_config.plugin.enable_action_suspend
        )

    def _build_negative_behaviors_extra(self) -> str:
        """若配置启用，构建用于 user extra 板块的负面行为再次强调文本。

        Returns:
            str: 强调文本；未启用或无负面行为条目时返回空字符串
        """
        plugin_config = getattr(self.plugin, "config", None)
        return DefaultChatterPromptBuilder.build_negative_behaviors_extra(plugin_config)

    def _is_sub_agent_collaboration_enabled(self) -> bool:
        """读取子代理协作模式开关。"""
        plugin_config = getattr(self.plugin, "config", None)
        plugin_section = getattr(plugin_config, "plugin", None)
        return bool(
            getattr(plugin_section, "enable_sub_agent_collaboration", False)
        )

    def _get_sub_agent_task_name(self) -> str:
        """读取协作子代理使用的模型任务名。"""
        plugin_config = getattr(self.plugin, "config", None)
        plugin_section = getattr(plugin_config, "plugin", None)
        task_name = str(getattr(plugin_section, "sub_agent_task_name", "actor") or "").strip()
        return task_name or "actor"

    # ── 提示词构建 ────────────────────────────────────────────

    async def _build_system_prompt(self, chat_stream: ChatStream) -> str:
        """构建系统提示词。"""
        plugin_config = self.plugin.config
        return await DefaultChatterPromptBuilder.build_system_prompt(
            plugin_config if isinstance(plugin_config, DefaultChatterConfig) else None,
            chat_stream,
            extra=self._build_sub_agent_collaboration_system_extra(),
        )

    def _build_enhanced_history_text(self, chat_stream: ChatStream) -> str:
        """构建历史消息文本。"""
        return DefaultChatterPromptBuilder.build_enhanced_history_text(
            chat_stream,
            self.format_message_line,
        )

    async def _build_user_prompt(
        self,
        chat_stream: ChatStream,
        history_text: str,
        unread_lines: str,
        extra: str = "",
    ) -> str:
        """通过 user prompt 模板构建用户提示词。

        Args:
            chat_stream: 当前聊天流
            history_text: 格式化后的历史消息文本（各行已用统一格式）
            unread_lines: 格式化后的未读消息文本
            extra: 额外信息文本

        Returns:
            str: 渲染后的 user 提示词
        """
        return await DefaultChatterPromptBuilder.build_user_prompt(
            chat_stream,
            history_text,
            unread_lines,
            extra,
        )

    @staticmethod
    def _upsert_pending_unread_payload(
        response: LLMConversationState,
        formatted_text: str,
        unread_msgs: list[Message] | None = None,
        native_multimodal: bool = False,
        logger_override: Logger | None = None,
    ) -> None:
        """在未发送前合并未读消息到最后一个 USER payload。

        当 ``native_multimodal`` 启用时，图片以 ``Image`` 对象内联到
        ``formatted_text`` 中 ``[图片]`` 占位符的位置，使 LLM 能精确
        将每张图片与其所属消息上下文对应。
        """
        content_list: list[Content | LLMUsable]
        if native_multimodal and unread_msgs:
            images = extract_images_from_messages(unread_msgs)
            content_list = inline_images_into_text(formatted_text, images)
            if images:
                (logger_override or logger).debug(f"已内联 {len(images)} 张图片到占位符位置")
        else:
            content_list = [Text(formatted_text)]
        response.add_payload(LLMPayload(ROLE.USER, content_list))

    # ── sub-agent 决策（委托 InterestGate）──────────────────

    async def sub_agent(
        self,
        unreads_text: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
        history_text: str = "",
        decision_history: list[SubAgentDecision] | None = None,
    ) -> SubAgentDecision:
        """子代理决策：判断是否需要响应未读消息。

        委托给 InterestGate 处理三种过滤模式（sub_only / interest_only / interest_then_sub），
        内含概率门快速过滤 + 兴趣值判定 + LLM sub-agent 决策。

        Args:
            unreads_text: 格式化后的未读消息文本
            unread_msgs: 未读消息对象列表
            chat_stream: 当前会话流，用于读取历史消息
            history_text: 历史消息摘要文本（供 sub-agent 上下文感知）
            decision_history: 之前的决策记录列表

        Returns:
            dict: 包含 should_respond (bool) 和 reason (str)
        """
        return await self._interest_gate.decide(
            unreads_text=unreads_text,
            unread_msgs=unread_msgs,
            chat_stream=chat_stream,
            history_text=history_text,
            decision_history=decision_history,
        )

    # ── 执行入口 ──────────────────────────────────────────────

    async def execute(
        self,
    ) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]:
        """执行聊天器的对话循环。

        一轮对话包含完整的上下文消息（系统提示 + 历史 + 未读 + LLM call history）。
        新的 LLM 交互记录会不断追加到上下文中。当 stop_conversation 被调用后，
        本轮对话结束，下次触发将使用全新的上下文。

        Yields:
            Wait | Success | Failure | Stop: 执行结果
        """
        service = DefaultChatterService(self.plugin)
        session = service.create_default_session(
            stream_id=self.stream_id,
            plugin=self.plugin,
            chatter=self,
            options=self._build_session_options(),
        )
        runner = session.execute()
        resume_event: WaitResumeEvent | None = None

        while True:
            try:
                result = await runner.asend(resume_event)
            except StopAsyncIteration:
                return
            resume_event = yield result

    async def run_tool_call(
        self,
        calls,
        response: LLMResponseLike,
        usable_map,
        trigger_msg: Message | None,
    ) -> list[tuple[bool, bool]]:
        """执行一次响应中的一批普通工具调用并写回响应上下文。

        Args:
            calls: 待执行的 tool call 列表，按 LLM 输出顺序排列。
            response: 当前响应对象；执行结果会按 ``calls`` 顺序写回。
            usable_map: 可调用组件注册表。
            trigger_msg: 触发本轮对话的消息。

        Returns:
            list[tuple[bool, bool]]: 与 ``calls`` 顺序一致的
            ``(是否已写回 TOOL_RESULT, execute 是否成功)`` 列表。
        """
        return await super().run_tool_call(calls, response, usable_map, trigger_msg)

    # ── 子代理协作模式（委托 usable_resolver）──────────────────

    def _build_sub_agent_collaboration_system_extra(self) -> str:
        """构建子代理协作模式下追加到系统提示词的额外说明。"""
        return build_sub_agent_collaboration_system_extra(self)

    async def inject_usables(self, request) -> Any:
        """按模式注入可用工具；子代理协作开启时隐藏 defer_loading 的 MCP 工具。"""
        if not self._is_sub_agent_collaboration_enabled():
            return await super().inject_usables(request)
        return await inject_collaboration_usables(self, request)

    async def create_managed_sub_agent(
        self,
        *,
        name: str,
        system_prompt: str,
        tools: list[str],
        mcp: list[str],
        allow_create_sub_agent: bool,
    ) -> tuple[bool, dict[str, Any]]:
        """创建一个受管子代理。"""
        return await _create_managed_sub_agent(
            self,
            name=name,
            system_prompt=system_prompt,
            tools=tools,
            mcp=mcp,
            allow_create_sub_agent=allow_create_sub_agent,
        )

    async def query_managed_sub_agent(
        self,
        *,
        name: str,
        message_limit: int,
        question: str,
    ) -> tuple[bool, dict[str, Any]]:
        """查询或驱动一个受管子代理。"""
        return await _query_managed_sub_agent(
            self,
            name=name,
            message_limit=message_limit,
            question=question,
        )

    async def kill_managed_sub_agent(self, *, name: str) -> tuple[bool, dict[str, Any]]:
        """销毁一个受管子代理。"""
        return _kill_managed_sub_agent(self, name=name)


# ─── Plugin ─────────────────────────────────────────────────


@register_plugin
class DefaultChatterPlugin(BasePlugin):
    """默认聊天插件。"""

    plugin_name = "default_chatter"
    plugin_version = "1.2.0-alpha"
    plugin_author = "MoFox Team"
    plugin_description = "默认聊天组件，提供基础的消息处理和回复功能"
    configs = [DefaultChatterConfig]

    async def on_plugin_loaded(self) -> None:
        """插件加载时注册提示词模板并启动语义训练。"""
        from src.core.prompt import optional, wrap, min_len

        config = get_core_config()
        personality = config.personality

        get_prompt_manager().get_or_create(
            name="default_chatter_system_prompt",
            template=system_prompt,
            policies={
                "nickname": optional(personality.nickname),
                "alias_names": optional("、".join(personality.alias_names)),
                "personality_core": optional(personality.personality_core),
                "personality_side": optional(personality.personality_side),
                "identity": optional(personality.identity),
                "background_story": optional(personality.background_story)
                .then(min_len(10))
                .then(
                    wrap(
                        "# 背景故事\n",
                        "\n- （以上为背景知识，请理解并作为行动依据，但不要在对话中直接复述。）"
                    )
                ),
                "reply_style": optional(personality.reply_style),
                "safety_guidelines": optional("\n".join(personality.safety_guidelines)),
                "negative_behaviors": optional("\n".join(personality.negative_behaviors)),
                "sub_agent_collaboration_extra": optional(""),
            },
        )

        get_prompt_manager().get_or_create(
            name="default_chatter_sub_agent_prompt",
            template=sub_agent_system_prompt,
            policies={
                "nickname": optional(personality.nickname),
                "bot_id": optional(""),
                "bot_id_section": optional(""),
                "personality_core_section": optional(personality.personality_core)
                .then(wrap("它的核心人格是：", "\n")),
                "personality_side_section": optional(personality.personality_side)
                .then(wrap("它的人格侧面是：", "\n")),
            },
        )

        get_prompt_manager().get_or_create(
            name="default_chatter_user_prompt",
            template=user_prompt,
            policies={
                "stream_name": optional("未知对话"),
                "current_time": optional("未知时间"),
                "platform": optional("未知平台"),
                "chat_type": optional("未知类型"),
                "platform_name": optional("未知"),
                "platform_id": optional("未知ID"),
                "extra_info": optional(""),
                "history": optional("")
                .then(min_len(2))
                .then(
                    wrap(
                        "# 历史消息\n",
                        "\n- （以上为历史消息摘要，供你参考了解之前的对话历史但不必复述）",
                    )
                ),
                "unreads": optional("")
                .then(min_len(2))
                .then(
                    wrap(
                        "# 新收到的消息\n",
                        "\n- （以上为新收到的消息，请基于这些消息生成回复）",
                    )
                ),
                "extra": optional("")
                .then(min_len(2))
                .then(wrap("# 额外信息\n", "\n- （以上为额外信息，你可以适当参考）")),
            },
        )

        # 启动语义模型自动训练后台任务（仅兴趣值相关模式）
        await self._maybe_start_semantic_training()

    async def _maybe_start_semantic_training(self) -> None:
        """根据过滤模式启动语义模型自动训练后台任务。

        仅当 filter_mode 为 interest_only 或 interest_then_sub 时触发，
        sub_only 模式下跳过以避免不必要的 LLM 调用。
        """
        plugin_config = getattr(self, "config", None)
        if isinstance(plugin_config, DefaultChatterConfig):
            mode_str = str(
                getattr(plugin_config.plugin, "filter_mode", "sub_only") or "sub_only"
            )
            if mode_str == "sub_only":
                logger.debug("[语义训练] 当前为 sub_only 模式，跳过自动训练")
                return

        try:
            from src.kernel.concurrency import get_task_manager

            personality = get_core_config().personality
            persona_info: dict[str, Any] = {
                "name": personality.nickname,
                "personality_core": personality.personality_core,
                "personality_side": personality.personality_side,
                "identity": personality.identity,
            }

            task_manager = get_task_manager()
            task_manager.create_task(
                self._background_auto_train(persona_info),
                name="semantic_interest_auto_train",
            )
            logger.info("[语义训练] 已启动后台自动训练任务")
        except Exception as e:
            logger.warning(f"[语义训练] 启动后台训练失败: {e}")

    async def _background_auto_train(self, persona_info: dict[str, Any]) -> None:
        """后台自动训练语义兴趣度模型。

        训练完成后模型文件写入 data/semantic_interest/models/，
        各 DefaultChatter 实例会在下次 _get_interest_calculator() 调用时
        通过缓存失效逻辑自动加载新模型。
        """
        try:
            from .semantic_interest.auto_trainer import get_auto_trainer

            plugin_config = getattr(self, "config", None)
            train_cfg = None
            if isinstance(plugin_config, DefaultChatterConfig):
                train_cfg = getattr(plugin_config.plugin, "semantic_training", None)

            days = getattr(train_cfg, "training_days", 7) if train_cfg else 7
            max_samples = getattr(train_cfg, "training_max_samples", 1000) if train_cfg else 1000
            model_name = getattr(train_cfg, "training_model_name", None) if train_cfg else None
            batch_size = getattr(train_cfg, "training_batch_size", 50) if train_cfg else 50
            keyword_iters = getattr(train_cfg, "keyword_iterations", 3) if train_cfg else 3
            min_interval = getattr(train_cfg, "min_train_interval_hours", 720) if train_cfg else 720

            trainer = get_auto_trainer(min_train_interval_hours=min_interval)
            trained, model_path = await trainer.auto_train_if_needed(
                persona_info=persona_info,
                days=days,
                max_samples=max_samples,
                llm_model_name=model_name,
                max_samples_per_batch=batch_size,
                keyword_iterations=keyword_iters,
            )

            if trained and model_path:
                logger.info(f"[语义训练] 训练完成，模型: {model_path.name}")
            else:
                logger.debug("[语义训练] 无需训练或训练未完成")
        except Exception as e:
            logger.error(f"[语义训练] 后台训练失败: {e}")

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        Returns:
            list[type]: 插件内所有组件类的列表
        """
        return [
            DefaultChatter,
            DefaultChatterService,
            SendTextAction,
            PassAndWaitAction,
            StopConversationAction,
        ]
