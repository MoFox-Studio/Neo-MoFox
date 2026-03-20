"""DefaultChatter 插件。

提供默认的聊天对话逻辑，包含三个核心 Action：
- send_text: 发送文本消息给用户
- pass_and_wait: 跳过本次动作，等待新消息
- stop_conversation: 结束当前对话轮次，设置冷却时间

使用 personality 配置动态构建系统提示词。
"""

from __future__ import annotations

import datetime
from typing import AsyncGenerator

from src.core.components.types import ChatType
from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import (
    BaseChatter,
    BasePlugin,
    Wait,
    Success,
    Failure,
    Stop,
)
from src.core.models.stream import ChatStream
from src.core.models.message import MessageType
from src.core.components.base.action import BaseAction
from src.core.components.loader import register_plugin
from src.core.config import get_core_config
from src.core.prompt import get_prompt_manager
from src.core.models.message import Message
from src.kernel.llm import LLMPayload, ROLE, Text

from .config import DefaultChatterConfig
from .decision_agent import decide_should_respond
from .prompt_builder import DefaultChatterPromptBuilder
from .runners import run_classical, run_enhanced
from .type_defs import LLMConversationState, LLMResponseLike, SubAgentDecision

logger = get_logger("default_chatter")

# ─── 系统提示词构建 ─────────────────────────────────────────────
system_prompt = """# 关于你
你的名字是** {nickname}**，也有人叫你 *{alias_names}*。
你{personality_core}
{personality_side}。
你的身份是{identity}。

- 请时刻铭记你的身份，只有明确叫道到你的名字才是真的在叫你，不要误以为所有对话都是在和你说话，除非对方明确提到你的名字或代称。

{background_story}

# 表达风格
*你需要参考你的回复风格：*：{reply_style}。

- 为了更好地塑造你的人设，你必须保持上述人格特质和表达风格，在任何情况下都要用符合你性格的方式回复用户。
- 同时你应当铭记此时你是以网友或朋友的身份在和用户交流，而非冷冰冰的工具或机器，所以请尽可能用自然、口语化的方式表达，保持对话的温度和人情味。
- 你应当尽量避免重复使用同样的回复、口癖或表达，保持回复的新鲜感和多样性。

**任何时候你都应当严格遵守你的人设和表达风格，哪怕你可能在先前的对话中有所偏离，但是你应当避免被之前的行为过度干扰，保证现在的对话质量。**

# 安全准则
在任何情况下，你都**必须**遵守以下原则：
{safety_guidelines}
如果遇到违反上述原则的请求，请在保持你核心人设的同时，以合适的方式进行回应。

# 负面行为
以下是你**绝对不允许**做的事情，无论任何情况下你都***必须遵守***以下原则：
{negative_behaviors}

# 场景引导
{theme_guide}

# 你的行为准则
- 保持你的人设和表达风格，用符合你性格的方式回复。
- 后续的消息都遵循根据原始网络数据解析后标准化格式。这个格式是给你看的，请**不要模仿其格式与用户对话**。
- 你的回复必须有理有据，禁止无根据地编造信息或胡乱回复。如果你不确定如何回复，可以跟风或转移话题，但是前提是足够自然不机械。
- 不要刨根问底，对于不重要的事情，不要过度追问，保持对话的自然流畅。

# 工具介绍
- Action：action通常是你在对话中需要执行的动作，例如发送消息、结束对话等。你可以调用 action 来完成这些任务，调用时请务必按照规定的格式提供必要的信息。这类工具通常不会提供任何信息，因此如果当你调用action并收到返回结果后，你只需要输出"__SUSPEND__"表示挂起对话等待下一步指令即可。
- Tool：tool通常是你在对话中需要查询信息或执行特定功能时调用的工具，例如查询天气、计算器等。你可以调用 tool 来获取这些信息或功能，调用时请务必按照规定的格式提供必要的信息。这类工具通常会返回一些结果信息，因此当你调用tool并收到返回结果后，你应该根据结果信息继续进行合理的回复或进一步执行其他工具。
- Agent：agent通常是你在对话中需要调用的智能体，例如执行复杂任务、处理多轮对话等。你可以调用 agent 来完成这些任务，调用时请务必按照规定的格式提供必要的信息。这类工具通常会返回一些结果信息，因此当你调用agent并收到返回结果后，你应该根据结果信息继续进行合理的回复或进一步执行其他工具。

你可以一次调用多个工具组合使用，善用工具组合往往可以让你的行为更丰富，达到事半功倍的效果。
多工具组合调用时，你需要自行决定调用顺序，通常回复动作应当优先，除非有明确的理由需要先执行其他工具。

# 其他信息
你目前正在聊天的平台是：{platform}，聊天类型是 {chat_type}。
*你的行为应当与当前的平台和聊天类型相匹配，例如你不应该在群聊中过于热情，也不应该在私聊中过于冷淡。*

在该平台你的信息：
- 昵称：{nickname}
- id：{bot_id}

{extra_info}
"""

user_prompt = """你当前正在名为"{stream_name}"的对话中。
消息格式说明：【时间】<群组角色> [平台ID] 昵称$群名片 [消息ID]： 消息内容
    
{history}
    
{unreads}
    
{extra}
---
请基于上述信息决定接下来的动作。
请务必保持你的回复符合你的人设和表达风格，
同时请确保你的回复有理有据，禁止无根据地编造信息或胡乱回复。
"""

sub_agent_system_prompt = """你是一个聊天意图识别助手。
你的任务是分析新收到的聊天消息，结合历史上下文，判断主机器人是否有必要进行响应。

# 关于主机器人
主机器人的名字是 {nickname}。
{personality_core_section}{personality_side_section}
# 判定准则
你应该在以下情况判定为 "需要回复" (should_respond = true)：
1. 明确提及：消息中明确提到了机器人的名字({nickname})或代称。
2. 话题相关：消息内容与当前正在进行的话题高度相关，需要机器人进一步说明、回答或参与。
3. 话语完整：对方的话已经说完，或者是一个完整的问题/指令。
4. 情感互动：对方在表达某种需要回应的情绪（如问候、告别、称赞、抱怨等）。

你应该在以下情况判定为 "不需要回复" (should_respond = false)：
1. 话题无关：消息是群聊中的闲聊，且机器人并非话题参与者。
2. 话未说完：明显是一连串消息中的中间部分，可以继续等待后续。
3. 机器博弈：检测到是其他 Bot 的自动回复或无意义的刷屏消息。
4. 纯粹表情：只有单个表情且不携带任何需要回复的语义。

# 输出格式
请务必返回 JSON 格式，如下所示：
```json
{{
    "reason": "简短的判定理由",
    "should_respond": true/false
}}
```
"""

# ─── Actions ────────────────────────────────────────────────


class SendTextAction(BaseAction):
    """发送文本消息"""

    action_name = "send_text"
    action_description = "发送一段文本消息给用户。你可以一次调用多个 send_text 来分多段回复，但每次调用必须提供你想说的话的文本内容，不要添加任何标记或格式，只写纯文本即可。你也可以选择引用之前某条消息作为背景，使用 reply_to 参数指定。注意：本工具无法发送表情包等非文本内容。"

    chatter_allow: list[str] = ["default_chatter"]

    async def execute(self, content: str, reply_to: str | None = None) -> tuple[bool, str]:
        """执行发送文本消息的逻辑

        Args:
            content: 要发送的文本内容，不用添加标记，只写你想说的话即可
            reply_to: 可选，要引用回复的目标消息 ID。若指定此参数，发送的消息将作为对该消息的回复
        """
        import re
        from src.core.models.message import Message

        # 清洗 LLM 可能侧漏的 reason 字段
        if content:
            # 匹配 ,reason: 或 reason: 及其后的所有内容
            content = re.split(r'[,，]?\s*reason[:：]', content, flags=re.IGNORECASE)[0].strip()

        if not content:
            return True, "内容为空，跳过发送"
        
        # 如果需要引用消息，创建带reply_to的Message对象
        if reply_to:
            target_stream_id = self.chat_stream.stream_id
            platform = self.chat_stream.platform
            chat_type = self.chat_stream.chat_type
            context = self.chat_stream.context
            
            from src.core.managers.adapter_manager import get_adapter_manager
            from uuid import uuid4
            
            bot_info = await get_adapter_manager().get_bot_info_by_platform(platform)
            
            target_user_id = None
            target_group_id = None
            target_user_name = None
            target_group_name = None
            
            def _get_last_context_message() -> Message | None:
                if context.unread_messages:
                    return context.unread_messages[-1]
                if context.history_messages:
                    return context.history_messages[-1]
                return context.current_message
            
            last_msg = _get_last_context_message()
            
            if chat_type == "group":
                if last_msg:
                    target_group_id = last_msg.extra.get("group_id")
                    target_group_name = last_msg.extra.get("group_name")
            else:
                target_user_id = context.triggering_user_id
                if not target_user_id and last_msg:
                    target_user_id = last_msg.sender_id
                    target_user_name = last_msg.sender_name
            
            extra: dict[str, str] = {}
            if target_user_id:
                extra["target_user_id"] = target_user_id
            if target_user_name:
                extra["target_user_name"] = target_user_name
            if target_group_id:
                extra["target_group_id"] = target_group_id
            if target_group_name:
                extra["target_group_name"] = target_group_name
            
            message = Message(
                message_id=f"action_{self.action_name}_{uuid4().hex}",
                content=content,
                processed_plain_text=content,
                message_type=MessageType.TEXT,
                sender_id=bot_info.get("bot_id", "") if bot_info else "",
                sender_name=bot_info.get("bot_nickname", "Bot") if bot_info else "Bot",
                platform=platform,
                chat_type=chat_type,
                stream_id=target_stream_id,
                reply_to=reply_to,
            )
            message.extra.update(extra)
            
            from src.core.transport.message_send import get_message_sender
            sender = get_message_sender()
            success = await sender.send_message(message)
            return success, f"已发送消息:{content}"
        else:
            await self._send_to_stream(content)
            return True, f"已发送消息:{content}"


class PassAndWaitAction(BaseAction):
    """跳过本次动作，等待新消息"""

    action_name = "pass_and_wait"
    action_description = "跳过本次动作，不进行任何操作，但保持对话继续，等待用户新消息。若当前不需要回复，但对话还在进行中，使用本工具等待用户的下一条消息。请不要和结束对话混淆，除非你非常确定你和用户的对话没有结束，或者你需要等待用户提供更多信息来决定下一步怎么做，否则你通常应该直接结束对话，等待下一轮新消息触发新的对话。"

    chatter_allow: list[str] = ["default_chatter"]

    async def execute(self) -> tuple[bool, str]:
        """跳过本次动作，不执行任何操作"""
        return True, "已跳过，等待新消息"


class StopConversationAction(BaseAction):
    """结束当前对话轮次"""

    action_name = "stop_conversation"
    action_description = "结束当前对话，过一段时间后再允许开启新对话。如果对话已经自然结束，或者你认为本轮对话可以告一段落，或者你暂时不想继续对话，使用本工具结束这轮对话。通常当你已经做出回应，且后续的消息很可能是新的话题时，使用本工具结束对话。你可以指定一个冷却时间（分钟），在此期间即使有新消息也不会触发新的对话，直到冷却时间结束后才会重新允许开启新对话。"

    chatter_allow: list[str] = ["default_chatter"]

    async def execute(self, minutes: float) -> tuple[bool, str]:
        """结束对话并设置冷却时间

        Args:
            minutes: 冷却时间（分钟），在此期间不会开启新对话
        """
        return True, f"对话已结束，将在 {minutes} 分钟后允许新对话"


# ─── Chatter ────────────────────────────────────────────────

# 控制流标记名称，与 BaseAction.to_schema() 生成的 name 保持一致（含 action- 前缀）
_PASS_AND_WAIT = "action-pass_and_wait"
_STOP_CONVERSATION = "action-stop_conversation"
_SEND_TEXT = "action-send_text"

# SUSPEND 占位符：当 LLM 本轮全部调用的都是 action 时，注入此占位防止上下文缺少 assistant 轮次
_SUSPEND_TEXT = "__SUSPEND__"


class DefaultChatter(BaseChatter):
    """默认聊天组件。

    实现完整的对话循环：
    1. 构建 LLM 上下文（系统提示 + 历史消息 + 当前未读消息）
    2. 注册所有可用的 LLMUsable 工具
    3. 循环调用 LLM 并执行其返回的 tool calls
    4. 根据 pass_and_wait / stop_conversation 控制对话流程
    """

    chatter_name: str = "default_chatter"
    chatter_description: str = "默认聊天组件，提供基础的消息处理和回复功能"

    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL

    dependencies: list[str] = []

    def _get_mode(self) -> str:
        """读取 DefaultChatter 执行模式。"""
        plugin_config = getattr(self.plugin, "config", None)
        return DefaultChatterPromptBuilder.get_mode(plugin_config)

    def _build_negative_behaviors_extra(self) -> str:
        """若配置启用，构建用于 user extra 板块的负面行为再次强调文本。

        Returns:
            str: 强调文本；未启用或无负面行为条目时返回空字符串
        """
        plugin_config = getattr(self.plugin, "config", None)
        return DefaultChatterPromptBuilder.build_negative_behaviors_extra(plugin_config)

    async def _build_system_prompt(self, chat_stream: ChatStream) -> str:
        """构建系统提示词"""
        plugin_config = self.plugin.config
        return await DefaultChatterPromptBuilder.build_system_prompt(
            plugin_config if isinstance(plugin_config, DefaultChatterConfig) else None,
            chat_stream,
        )

    async def _build_classical_user_text(
        self,
        chat_stream: ChatStream,
        unread_msgs: list[Message],
    ) -> str:
        """构建 classical 模式 user 提示词。"""
        return await DefaultChatterPromptBuilder.build_classical_user_text(
            chat_stream,
            unread_msgs,
            self.format_message_line,
            self._build_negative_behaviors_extra(),
        )

    def _build_enhanced_history_text(self, chat_stream: ChatStream) -> str:
        """构建 enhanced 模式的历史消息文本。"""
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
    ) -> None:
        """在未发送前合并未读消息到最后一个 USER payload。"""
        if response.payloads:
            last_payload = response.payloads[-1]
            if last_payload.role == ROLE.USER:
                if last_payload.content and isinstance(last_payload.content[-1], Text):
                    existing_text = last_payload.content[-1].text
                    separator = "\n" if existing_text else ""
                    last_payload.content[-1] = Text(
                        f"{existing_text}{separator}{formatted_text}"
                    )
                else:
                    last_payload.content.append(Text(formatted_text))
                return

        response.add_payload(LLMPayload(ROLE.USER, Text(formatted_text)))

    async def sub_agent(
        self,
        unreads_text: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
    ) -> SubAgentDecision:
        """子代理决策：判断是否需要响应未读消息。

        独立构建上下文，只包含历史消息摘要与未读消息，

        Args:
            unreads_text: 格式化后的未读消息文本
            unread_msgs: 未读消息对象列表
            chat_stream: 当前会话流，用于读取历史消息

        Returns:
            dict: 包含 should_respond (bool) 和 reason (str)
        """
        if str(chat_stream.chat_type).lower() == "private":
            return {
                "reason": "私聊场景跳过 sub-agent，直接响应",
                "should_respond": True,
            }

        return await decide_should_respond(
            chatter=self,
            logger=logger,
            unreads_text=unreads_text,
            chat_stream=chat_stream,
            fallback_prompt=sub_agent_system_prompt,
        )

    async def execute(self) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
        """执行聊天器的对话循环。

        一轮对话包含完整的上下文消息（系统提示 + 历史 + 未读 + LLM call history）。
        新的 LLM 交互记录会不断追加到上下文中。当 stop_conversation 被调用后，
        本轮对话结束，下次触发将使用全新的上下文。

        Yields:
            Wait | Success | Failure | Stop: 执行结果
        """
        from src.core.managers.stream_manager import get_stream_manager

        stream_manager = get_stream_manager()
        chat_stream = await stream_manager.activate_stream(self.stream_id)
        if chat_stream is None:
            logger.error(f"无法激活聊天流: {self.stream_id}")
            yield Failure("无法激活聊天流")
            return

        mode = self._get_mode()
        logger.info(f"DefaultChatter 当前模式: {mode}")

        if mode == "classical":
            async for result in self._execute_classical(chat_stream):
                yield result
            return

        async for result in self._execute_enhanced(chat_stream):
            yield result

    async def _execute_enhanced(
        self, chat_stream: ChatStream
    ) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
        """enhanced 模式执行流程（保留原有行为）。"""
        plugin_config = getattr(self.plugin, "config", None)
        enable_cooldown = (
            plugin_config.plugin.enable_cooldown
            if isinstance(plugin_config, DefaultChatterConfig)
            else False
        )
        async for result in run_enhanced(
            chatter=self,
            chat_stream=chat_stream,
            logger=logger,
            pass_call_name=_PASS_AND_WAIT,
            stop_call_name=_STOP_CONVERSATION,
            send_text_call_name=_SEND_TEXT,
            suspend_text=_SUSPEND_TEXT,
            enable_cooldown=enable_cooldown,
        ):
            yield result

    async def _execute_classical(
        self, chat_stream: ChatStream
    ) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
        """classical 模式执行流程。"""
        plugin_config = getattr(self.plugin, "config", None)
        enable_cooldown = (
            plugin_config.plugin.enable_cooldown
            if isinstance(plugin_config, DefaultChatterConfig)
            else False
        )
        async for result in run_classical(
            chatter=self,
            chat_stream=chat_stream,
            logger=logger,
            pass_call_name=_PASS_AND_WAIT,
            stop_call_name=_STOP_CONVERSATION,
            send_text_call_name=_SEND_TEXT,
            suspend_text=_SUSPEND_TEXT,
            enable_cooldown=enable_cooldown,
        ):
            yield result

    async def run_tool_call(
        self,
        call,
        response: LLMResponseLike,
        usable_map,
        trigger_msg: Message | None,
    ) -> tuple[bool, bool]:
        """执行工具调用并将结果写回响应上下文。"""
        return await super().run_tool_call(call, response, usable_map, trigger_msg)


# ─── Plugin ─────────────────────────────────────────────────


@register_plugin
class DefaultChatterPlugin(BasePlugin):
    """默认聊天插件"""

    plugin_name = "default_chatter"
    plugin_version = "1.0.0"
    plugin_author = "MoFox Team"
    plugin_description = "默认聊天组件，提供基础的消息处理和回复功能"
    configs = [DefaultChatterConfig]

    async def on_plugin_loaded(self) -> None:
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
                        "# 背景故事\\n"
                        "\\n- （以上为背景知识，请理解并作为行动依据，但不要在对话中直接复述。）"
                    )
                ),
                "reply_style": optional(personality.reply_style),
                "safety_guidelines": optional("\n".join(personality.safety_guidelines)),
                "negative_behaviors": optional("\n".join(personality.negative_behaviors)),
                "current_time": optional(
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ),
                "extra_info": optional("")
            },
        )

        get_prompt_manager().get_or_create(
            name="default_chatter_sub_agent_prompt",
            template=sub_agent_system_prompt,
            policies={
                "nickname": optional(personality.nickname),
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

    def get_components(self) -> list[type]:
        """获取插件内所有组件类

        Returns:
            list[type]: 插件内所有组件类的列表
        """
        return [
            DefaultChatter,
            SendTextAction,
            PassAndWaitAction,
            StopConversationAction,
        ]
