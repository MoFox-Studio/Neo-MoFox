"""Neo-Chatter 插件类与生命周期实现。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
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
    ProbabilityBypassHandler,
    SubAgentDecisionHandler,
)
from .components.service import NeoChatterService
from .utils.prompts import system_prompt, user_prompt

_SYSTEM_TEMPLATE_NAME = "neo_chatter_system_prompt"
_USER_TEMPLATE_NAME = "neo_chatter_user_prompt"


@register_plugin
class NeoChatterPlugin(BasePlugin):
    """Neo-Chatter 插件。

    会话逻辑中台：事件驱动预处理 + 原生多模态。
    注册提示词模板并把 Chatter / Service / 三个默认 Action 暴露给框架。
    """

    plugin_name = "neo_chatter"
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

    def get_components(self) -> list[type]:
        """返回插件注册的组件类。"""
        return [
            NeoChatter,
            NeoChatterService,
            SendTextAction,
            PassAndWaitAction,
            StopConversationAction,
            ProbabilityBypassHandler,
            SubAgentDecisionHandler,
        ]
