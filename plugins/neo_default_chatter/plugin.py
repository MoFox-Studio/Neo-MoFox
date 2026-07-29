"""Neo-Default-Chatter 插件类与生命周期实现。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.app.plugin_system.api.log_api import get_logger
from src.core.config import get_core_config
from src.core.prompt import get_prompt_manager, min_len, optional, wrap

from .components.actions import (
    PassAndWaitAction,
    SendTextAction,
    StopConversationAction,
)
from .components.chatter import NeoChatter
from .components.config import NeoChatterConfig
from .components.event_handlers import (
    BuildHistoryTextDefaultHandler,
    BuildNegativeExtraDefaultHandler,
    BuildResumePromptDefaultHandler,
    ComputeCooldownDefaultHandler,
    ComputeStopWakeDefaultHandler,
    CreateRequestDefaultHandler,
    DedupeToolCallDefaultHandler,
    FetchUnreadsDefaultHandler,
    FlushUnreadsDefaultHandler,
    FormatToolResultDefaultHandler,
    FormatUnreadLineDefaultHandler,
    InjectUnreadPayloadDefaultHandler,
    InjectUsablesDefaultHandler,
    PickTriggerMessageDefaultHandler,
    PrivateChatBypassHandler,
    ProbabilityBypassHandler,
    RunToolCallDefaultHandler,
    SessionTransitionDefaultHandler,
    SubAgentDecisionHandler,
)
from .components.service import NeoChatterService
from .utils.prompts import (
    sub_agent_system_prompt,
    sub_agent_user_prompt,
    system_prompt,
    user_prompt,
)

_SYSTEM_TEMPLATE_NAME = "neo_default_chatter_system_prompt"
_USER_TEMPLATE_NAME = "neo_default_chatter_user_prompt"
_SUB_AGENT_SYSTEM_TEMPLATE_NAME = "neo_default_chatter_sub_agent_system_prompt"
_SUB_AGENT_USER_TEMPLATE_NAME = "neo_default_chatter_sub_agent_user_prompt"

logger = get_logger("Neo-Default-Chatter")

@register_plugin
class NeoChatterPlugin(BasePlugin):
    """Neo-Default-Chatter 插件。

    会话逻辑中台：事件驱动预处理 + 原生多模态。
    注册提示词模板并把 Chatter / Service / 三个默认 Action 暴露给框架。
    """

    plugin_name = "neo_default_chatter"
    plugin_version = "0.1.0"
    plugin_author = "MoFox Team"
    plugin_description = "可复用的会话逻辑中台，事件驱动预处理 + 原生多模态"
    configs = [NeoChatterConfig]

    async def on_plugin_loaded(self) -> None:
        """注册系统与用户提示词模板。"""
        personality = get_core_config().personality

        get_prompt_manager().get_or_create(
            name=_SYSTEM_TEMPLATE_NAME,
            template=system_prompt,
            policies={
                "nickname": optional(personality.nickname),
                "bot_nickname": optional("未设置"),
                "alias_names": optional("、".join(personality.alias_names)),
                "personality_core": optional(personality.personality_core),
                "personality_side": optional(personality.personality_side),
                "identity": optional(personality.identity),
                "background_story": optional(personality.background_story)
                .then(min_len(10))
                .then(
                    wrap(
                        "# 背景故事\n",
                        "\n- （以上为背景知识，请理解并作为行动依据，但不要在对话中直接复述。）",
                    )
                ),
                "reply_style": optional(personality.reply_style),
                "safety_guidelines": optional("\n".join(personality.safety_guidelines)),
                "negative_behaviors": optional("\n".join(personality.negative_behaviors)),
                "theme_guide": optional(""),
                "action_suspend_guidance": optional(""),
                "introduce": optional(""),
            },
        )

        get_prompt_manager().get_or_create(
            name=_USER_TEMPLATE_NAME,
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
                "stream_id": optional(""),
            },
        )

        get_prompt_manager().get_or_create(
            name=_SUB_AGENT_SYSTEM_TEMPLATE_NAME,
            template=sub_agent_system_prompt,
            policies={},
        )

        get_prompt_manager().get_or_create(
            name=_SUB_AGENT_USER_TEMPLATE_NAME,
            template=sub_agent_user_prompt,
            policies={
                "stream_name": optional("未知对话"),
                "chat_type": optional("未知类型"),
                "bot_nickname": optional("机器人"),
                "history": optional("（无）"),
                "unreads": optional("（无）"),
            },
        )

    def get_components(self) -> list[type]:
        """返回插件注册的组件类。"""
        if not self.config.plugin.enabled:
            logger.info("Neo-Default-Chatter 插件未启用")
            return [] 
        return [
            NeoChatter,
            NeoChatterService,
            SendTextAction,
            PassAndWaitAction,
            StopConversationAction,
            PrivateChatBypassHandler,
            ProbabilityBypassHandler,
            SubAgentDecisionHandler,
            # Tier II 默认 handler（16 个）：NDFC 自带的可替换 seam 兜底实现
            FetchUnreadsDefaultHandler,
            FormatUnreadLineDefaultHandler,
            FlushUnreadsDefaultHandler,
            CreateRequestDefaultHandler,
            InjectUsablesDefaultHandler,
            RunToolCallDefaultHandler,
            InjectUnreadPayloadDefaultHandler,
            BuildHistoryTextDefaultHandler,
            BuildNegativeExtraDefaultHandler,
            PickTriggerMessageDefaultHandler,
            BuildResumePromptDefaultHandler,
            DedupeToolCallDefaultHandler,
            FormatToolResultDefaultHandler,
            ComputeStopWakeDefaultHandler,
            ComputeCooldownDefaultHandler,
            SessionTransitionDefaultHandler,
        ]
