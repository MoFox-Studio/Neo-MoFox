"""SendTextAction：发送文本消息。

支持纯文本发送、引用回复、@ 用户，以及发送前的打字延迟模拟。
打字延迟仅在非首条消息时生效，按距上次发送的时间差等待。
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator, cast

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base.action import BaseAction
from src.core.models.message import MessageType

from ..config import DefaultChatterConfig
from ..probability_gate import (
    _SEND_TEXT_TYPING_DELAY_MAX_SECONDS,
    _SEND_TEXT_TYPING_DELAY_PER_CHAR,
    set_next_tick_sub_agent_bonus,
)

logger = get_logger("default_chatter")


class SendTextAction(BaseAction):
    """发送文本消息。"""

    action_name = "send_text"
    action_description = "发送一段文本消息给用户。这是你唯一发送文本消息的方式。你可以一次调用多个 send_text 来分多段回复，但每次调用必须提供你想说的话的文本内容，不要添加任何标记或格式，只写纯文本即可。content 参数只能包含发送给用户的正文，严禁将行为理由、内心独白或格式说明混入 content。你也可以选择引用或回复之前某条消息作为背景，使用 reply_to 参数指定；若不引用消息，可用 at 参数指定要@的对象。注意：本工具无法发送表情包等非文本内容。所有@对象都应该通过at参数而不是直接写在文本里，以确保正确解析和发送。"

    chatter_allow: list[str] = ["default_chatter"]
    associated_types = ["text"]

    def _is_programmatic_controller_enabled(self) -> bool:
        """读取程序化控制器开关。"""
        plugin_config = getattr(self.plugin, "config", None)
        return not isinstance(plugin_config, DefaultChatterConfig) or bool(
            plugin_config.plugin.enable_programmatic_controller
        )

    def _mark_sub_agent_bonus_on_success(self, success: bool) -> None:
        """发送成功后提高下一次 tick 的 sub-agent 直通概率。"""
        if not success:
            return

        # 标记回复成功，供兴趣值计算器在下一次决策时重置 boost
        setattr(self.chat_stream.context, "_interest_reply_sent", True)

        if self._is_programmatic_controller_enabled():
            plugin_config = cast(DefaultChatterConfig, self.plugin.config)
            bonus = plugin_config.plugin.programmatic_probability.next_tick_reply_bonus
            set_next_tick_sub_agent_bonus(
                self.chat_stream,
                bonus,
            )

    @staticmethod
    def _typing_delay_seconds(content: str) -> float:
        """根据文本长度估算发送前的打字等待时间。"""
        delay = len(content) * _SEND_TEXT_TYPING_DELAY_PER_CHAR
        return min(delay, _SEND_TEXT_TYPING_DELAY_MAX_SECONDS)

    async def _sleep_for_typing_delay(self, content: str) -> None:
        """模拟打字延迟后发送。

        首条消息不等待；后续消息按距上次发送的间隔与打字时长的差值等待。
        """
        delay = self._typing_delay_seconds(content)
        last_time = getattr(self.chat_stream.context, "_last_send_text_time", 0.0)

        if last_time > 0:
            elapsed = time.time() - last_time
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)

        setattr(self.chat_stream.context, "_last_send_text_time", time.time())

    async def execute(
        self,
        content: str,
        reply_to: str | None = None,
        at: str | None = None,
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        """执行发送文本消息的逻辑。

        Args:
            content: 要发送的文本内容，不用添加标记，只写你想说的话即可
            reply_to: 可选，要引用回复的目标消息 ID。若指定此参数，发送的消息将作为对该消息的回复
            at: 可选，不使用 reply_to 时指定要 @ 的对象（用户 ID）
        """
        import re
        from src.core.models.message import Message

        # 清洗 LLM 可能侧漏的 reason 字段
        if content:
            # 匹配 ,reason: 或 reason: 及其后的所有内容
            content = re.split(r'[,，]?\s*reason[:：]', content, flags=re.IGNORECASE)[0].strip()

        # 解析 content 开头的 @对象，并从正文中移除。
        # 示例: "@小明 你好" -> at_prefix_hint="小明", content="你好"
        at_prefix_hint: str | None = None
        if content:
            at_match = re.match(r"^\s*@([^\s]+)\s*", content)
            if at_match:
                at_prefix_hint = at_match.group(1).strip()
                content = content[at_match.end():].lstrip()

        if not (content or at_prefix_hint):
            yield True, "内容为空，跳过发送"
            return

        if not content:
            yield True, "内容为空，跳过发送"
            return
        
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
            
            target_msg = self._get_context_message_for_target(reply_to)
            
            if chat_type == "group":
                if target_msg:
                    target_group_id = target_msg.extra.get("group_id")
                    target_group_name = target_msg.extra.get("group_name")
            else:
                if target_msg:
                    target_user_id = target_msg.sender_id
                    target_user_name = target_msg.sender_name
                if not target_user_id:
                    target_user_id = context.triggering_user_id
            
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
                sender_name=bot_info.get("bot_name", "Bot") if bot_info else "Bot",
                platform=platform,
                chat_type=chat_type,
                stream_id=target_stream_id,
                reply_to=reply_to,
            )
            message.extra.update(extra)
            
            from src.core.transport.message_send import get_message_sender
            sender = get_message_sender()
            yield None
            await self._sleep_for_typing_delay(content)
            success = await sender.send_message(message)
            self._mark_sub_agent_bonus_on_success(success)
            yield success, f"已发送消息:{content}"
            return
        else:
            # 非引用回复时可使用显式 at 参数；reply_to 存在时已在上分支处理并忽略 at。
            at_hint = (at or at_prefix_hint or "").strip().lstrip("@").strip()

            if not at_hint:
                yield None
                await self._sleep_for_typing_delay(content)
                success = await self._send_to_stream(content)
                self._mark_sub_agent_bonus_on_success(success)
                yield success, f"已发送消息:{content}"
                return

            target_stream_id = self.chat_stream.stream_id
            platform = self.chat_stream.platform
            chat_type = self.chat_stream.chat_type
            context = self.chat_stream.context

            if chat_type != "group":
                # 私聊场景不需要显式 @，按普通发送处理。
                yield None
                await self._sleep_for_typing_delay(content)
                success = await self._send_to_stream(content)
                self._mark_sub_agent_bonus_on_success(success)
                yield success, f"已发送消息:{content}"
                return

            from src.core.managers.adapter_manager import get_adapter_manager
            from src.core.utils.user_query_helper import get_user_query_helper
            from uuid import uuid4

            bot_info = await get_adapter_manager().get_bot_info_by_platform(platform)

            if at_hint.isdigit():
                at_user_id = at_hint
            else:
                at_user_id = await get_user_query_helper().resolve_user_id(platform, at_hint)

            if not at_user_id:
                logger.info(f"无法定位 at 目标: {at_hint}，降级为普通回复")
                yield None
                await self._sleep_for_typing_delay(content)
                success = await self._send_to_stream(content)
                self._mark_sub_agent_bonus_on_success(success)
                yield success, f"已发送消息:{content}"
                return

            target_group_id = None
            target_group_name = None
            last_msg = self._get_context_message_for_target()
            if last_msg:
                target_group_id = last_msg.extra.get("group_id")
                target_group_name = last_msg.extra.get("group_name")

            extra: dict[str, str] = {
                "at_user_id": str(at_user_id),
            }
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
                sender_name=bot_info.get("bot_name", "Bot") if bot_info else "Bot",
                platform=platform,
                chat_type=chat_type,
                stream_id=target_stream_id,
            )
            message.extra.update(extra)

            from src.core.transport.message_send import get_message_sender

            sender = get_message_sender()
            yield None
            await self._sleep_for_typing_delay(content)
            success = await sender.send_message(message)
            self._mark_sub_agent_bonus_on_success(success)
            yield success, f"已发送消息:{content}"
            return
