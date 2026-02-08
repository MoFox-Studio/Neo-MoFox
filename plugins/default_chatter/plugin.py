"""DefaultChatter 插件。

提供默认的聊天对话逻辑，包含三个核心 Action：
- send_text: 发送文本消息给用户
- pass_and_wait: 跳过本次动作，等待新消息
- stop_conversation: 结束当前对话轮次，设置冷却时间

使用 personality 配置动态构建系统提示词。
"""

from __future__ import annotations

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
from src.core.components.base.action import BaseAction
from src.app.plugin_system.api.llm_api import (
    get_model_set_by_task,
    create_llm_request,
    create_tool_registry,
)
from src.core.components.loader import register_plugin
from src.core.config import get_core_config
from src.core.prompt import get_prompt_manager
from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text, ToolResult

logger = get_logger("default_chatter")

# ─── 系统提示词构建 ─────────────────────────────────────────────
system_prompt = """# 关于你
你的名字是 {nickname}，也有人叫你 {alias_names}。
你{personality_core}
{personality_side}。
你的身份是{identity}。

{background_story}

# 表达风格
你的表达风格：{reply_style}。

- 为了更好地塑造你的人设，你必须保持上述人格特质和表达风格，在任何情况下都要用符合你性格的方式回复用户。
- 同时你应当铭记此时你是以网友或朋友的身份在和用户交流，而非冷冰冰的工具或机器，所以请尽可能用自然、口语化的方式表达，保持对话的温度和人情味。

# 安全准则
在任何情况下，你都必须遵守以下原则：
{safety_guidelines}
如果遇到违反上述原则的请求，请在保持你核心人设的同时，以合适的方式进行回应。

# 你的行为准则
- 你通过调用工具来与用户交流和执行动作。
- 在每次决策时，你必须选择一个工具来执行。
- 如果你想回复用户，使用 send_text。
- 如果当前不需要回复，但对话还在进行中，使用 pass_and_wait 等待用户的下一条消息。
- 如果对话已经自然结束，或者你认为本轮对话可以告一段落，或者你暂时不想继续对话，使用 stop_conversation 结束这轮对话。
- 你可以在一次决策中调用多个 send_text，例如先发一段话，再发另一段话。
- 保持你的人设和表达风格，用符合你性格的方式回复。

警告：直接输出文本而不调用 send_text 是不允许的。如果你想回复用户，必须使用 send_text 工具。
"""

# ─── Actions ────────────────────────────────────────────────


class SendTextAction(BaseAction):
    """发送文本消息的 Action"""

    action_name = "send_text"
    action_description = "发送一段文本消息给用户"

    chatter_allow: list[str] = ["default_chatter"]

    async def execute(self, content: str) -> tuple[bool, str]:
        """执行发送文本消息的逻辑

        Args:
            content: 要发送的文本内容，不用添加标记，只写你想说的话即可
        """
        await self._send_to_stream(content)
        return True, f"已发送消息:{content}"


class PassAndWaitAction(BaseAction):
    """跳过本次动作，等待新消息的 Action"""

    action_name = "pass_and_wait"
    action_description = "跳过本次动作，不进行任何操作，但保持对话继续，等待用户新消息"

    chatter_allow: list[str] = ["default_chatter"]

    async def execute(self) -> tuple[bool, str]:
        """跳过本次动作，不执行任何操作"""
        return True, "已跳过，等待新消息"


class StopConversationAction(BaseAction):
    """结束当前对话轮次的 Action"""

    action_name = "stop_conversation"
    action_description = "结束当前对话轮次，过一段时间后再允许开启新对话"

    chatter_allow: list[str] = ["default_chatter"]

    async def execute(self, minutes: float) -> tuple[bool, str]:
        """结束对话并设置冷却时间

        Args:
            minutes: 冷却时间（分钟），在此期间不会开启新对话
        """
        return True, f"对话已结束，将在 {minutes} 分钟后允许新对话"


# ─── Chatter ────────────────────────────────────────────────

# 控制流标记名称，用于 Chatter 识别特殊 action
_PASS_AND_WAIT = "pass_and_wait"
_STOP_CONVERSATION = "stop_conversation"


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

    async def sub_agent(self, unreads_text: str) -> dict:
        """子代理逻辑，处理未读消息文本并返回结果

        Args:
            unreads_text: 当前未读消息的文本内容

        Returns:
            dict: 处理结果，可以包含需要传递给 LLM 的信息
        """
        # 这里可以添加对未读消息的预处理逻辑，例如提取关键信息、进行情感分析等
        return {"processed_unreads": unreads_text}
    
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

        # ── 构建 LLM 请求 ──
        try:
            model_set = get_model_set_by_task("actor")
            if model_set:
                first_model = model_set[0]
                logger.debug(
                    f"模型配置: provider={first_model.get('api_provider')}, "
                    f"base_url={first_model.get('base_url')}, "
                    f"timeout={first_model.get('timeout')}"
                )
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            yield Failure(f"模型配置错误: {e}")
            return

        context_manager = LLMContextManager(
            max_payloads=get_core_config().chat.max_context_size
        )
        request = create_llm_request(
            model_set,
            "default_chatter",
            context_manager=context_manager,
        )

        # 系统提示（动态构建）
        system_prompt = get_prompt_manager().get_template("default_chatter_system_prompt").build()
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
       
        # 历史消息（来自 stream context，构成对话背景）
        history_lines = []
        for msg in chat_stream.context.history_messages:  # type: ignore[union-attr]
            history_lines.append(
                f"【{msg.time}】{msg.sender_name}: {msg.processed_plain_text}"
            )
        history_text = "\n".join(history_lines)
        if history_text:
            request.add_payload(LLMPayload(ROLE.USER, Text(history_text)))
        
        # ── 收集可用工具 ──
        usables = await self.get_llm_usables()
        usables = await self.modify_llm_usables(usables)

        usable_map = create_tool_registry(usables)  # 将工具注册到工具注册表中

        if usable_map.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, usable_map.get_all()))  # type: ignore[arg-type]

        # ── 对话循环 ──
        response = request

        while True:
            formatted_text, unread_msgs = await self.fetch_and_flush_unreads()
            
            # 更新 unreads 引用，用于后续 exec_llm_usable 的 trigger_msg
            unreads = unread_msgs

            if formatted_text:
                # 将未读消息组作为一个USER payload
                response.add_payload(LLMPayload(ROLE.USER, Text(formatted_text)))
            else:
                yield Wait()
                continue

            try:
                response = await response.send(stream=False)
                await response
            except Exception as e:
                logger.error(f"LLM 请求失败: {e}", exc_info=True)
                yield Failure(f"LLM 请求失败", e)
                continue

            # LLM 没有调用任何工具 → 对话自然结束
            if not response.call_list:
                # 如果 LLM 返回了文本但没有调用工具，也将其作为消息发送
                if response.message and response.message.strip():
                    logger.warning(
                        "LLM 返回了纯文本而非 tool call: " f"{response.message[:100]}"
                    )
                continue

            # ── 处理 tool calls ──
            should_wait = False
            should_stop = False
            stop_minutes = 0.0

            for call in response.call_list:
                args = call.args if isinstance(call.args, dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(f"LLM 调用 {call.name}，原因: {reason}，参数: {args}")

                if call.name == _PASS_AND_WAIT:
                    # 特殊控制流：标记等待，不执行 action_manager
                    response.add_payload(
                        LLMPayload(
                            ROLE.TOOL_RESULT,
                            ToolResult(  # type: ignore[arg-type]
                                value="已跳过，等待用户新消息",
                                call_id=call.id,
                                name=call.name,
                            ),
                        )
                    )
                    should_wait = True

                elif call.name == _STOP_CONVERSATION:
                    # 特殊控制流：结束对话并设置冷却
                    stop_minutes = float(args.get("minutes", 5.0))
                    response.add_payload(
                        LLMPayload(
                            ROLE.TOOL_RESULT,
                            ToolResult(  # type: ignore[arg-type]
                                value=f"对话已结束，将在 {stop_minutes} 分钟后允许新对话",
                                call_id=call.id,
                                name=call.name,
                            ),
                        )
                    )
                    should_stop = True

                else:
                    # 普通 action/tool：通过 exec_llm_usable 执行
                    usable_cls = usable_map.get(call.name)
                    if not usable_cls:
                        result_text = f"未知的工具: {call.name}"
                        logger.warning(result_text)
                    else:
                        try:
                            # 使用最后一条未读消息作为触发消息；若为空跳过
                            trigger_msg = unreads[-1] if unreads else None
                            if trigger_msg is None:
                                continue
                            else:
                                success, result = await self.exec_llm_usable(
                                    usable_cls, trigger_msg, **args  # type: ignore[arg-type]
                                )
                                result_text = (
                                    str(result) if success else f"执行失败: {result}"
                                )
                        except Exception as e:
                            result_text = f"执行异常: {e}"
                            logger.error(f"执行 {call.name} 异常: {e}", exc_info=True)

                    response.add_payload(
                        LLMPayload(
                            ROLE.TOOL_RESULT,
                            ToolResult(  # type: ignore[arg-type]
                                value=result_text,
                                call_id=call.id,
                                name=call.name,
                            ),
                        )
                    )
            # ── 处理控制流结果 ──
            if should_stop:
                # 设置冷却时间
                logger.info(f"对话已结束，冷却 {stop_minutes} 分钟")
                yield Stop(stop_minutes * 60)
                return

            if should_wait:
                # 等待新消息到来
                yield Wait()
                # 继续循环，让 LLM 基于更新后的上下文重新决策
                continue
            # 没有特殊控制流，继续让 LLM 决策（LLM 可能连续调用多轮工具）
            continue


# ─── Plugin ─────────────────────────────────────────────────


@register_plugin
class DefaultChatterPlugin(BasePlugin):
    """默认聊天插件"""

    plugin_name = "default_chatter"
    plugin_version = "1.0.0"
    plugin_author = "MoFox Team"
    plugin_description = "默认聊天组件，提供基础的消息处理和回复功能"

    async def on_plugin_loaded(self) -> None:
        from src.core.prompt import optional, wrap, min_len
        config = get_core_config()
        personality = config.personality

        template = get_prompt_manager().get_or_create(
            name="default_chatter_system_prompt",
            template=system_prompt,
            policies={
                "nickname": optional(personality.nickname),
                "alias_names": optional("、".join(personality.alias_names)),
                "personality_core": optional(personality.personality_core),
                "personality_side": optional(personality.personality_side),
                "identity": optional(personality.identity),
                "background_story": optional(personality.background_story).then(min_len(10)).then(wrap("# 背景故事\\n" "\\n- （以上为背景知识，请理解并作为行动依据，但不要在对话中直接复述。）")),
                "reply_style": optional(personality.reply_style),
                "safety_guidelines": optional("\n".join(personality.safety_guidelines)),
            }   
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
