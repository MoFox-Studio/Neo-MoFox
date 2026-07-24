"""SendTextAction：发送文本消息。

支持纯文本发送、引用回复、@ 用户，以及发送前的打字延迟模拟。
打字延迟仅在非首条消息时生效，按距上次发送的时间差等待。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Annotated
from uuid import uuid4

from src.app.plugin_system.api.adapter_api import get_bot_info_by_platform
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_message
from src.app.plugin_system.base import BaseAction
from src.app.plugin_system.types import Message, MessageType

logger = get_logger("neo_default_chatter")

_TYPING_DELAY_PER_CHAR = 0.5
_TYPING_DELAY_MAX_SECONDS = 10.0
_LAST_SEND_TIME_ATTR = "_neo_default_chatter_last_send_text_time"


class SendTextAction(BaseAction):
    """发送文本消息。"""

    name = "send_text"
    description = (
        "发送一段文本消息给用户。这是你唯一发送文本消息的方式。你可以一次调用多个 send_text 来分多段回复，"
        "但每次调用必须提供你想说的话的文本内容，不要添加任何标记或格式，只写纯文本即可。"
        "content 参数只能包含发送给用户的正文，严禁将行为理由、内心独白或格式说明混入 content。"
        "你也可以选择引用或回复之前某条消息作为背景，使用 reply_to 参数指定；"
        "若不引用消息，可用 at 参数指定要@的对象。"
        "注意：本工具无法发送表情包等非文本内容。所有@对象都应该通过 at 参数而不是直接写在文本里，以确保正确解析和发送。"
    )

    chatter_allow: list[str] = ["neo_default_chatter"]
    associated_types = ["text"]

    @staticmethod
    def _typing_delay_seconds(content: str) -> float:
        """根据文本长度估算发送前的打字等待时间。"""
        return min(len(content) * _TYPING_DELAY_PER_CHAR, _TYPING_DELAY_MAX_SECONDS)

    async def _sleep_for_typing_delay(self, content: str) -> None:
        """在连续发送文本时等待剩余的模拟打字时间。"""
        last_send_time = float(
            getattr(self.chat_stream.context, _LAST_SEND_TIME_ATTR, 0.0)
        )
        if last_send_time <= 0:
            return
        remaining = self._typing_delay_seconds(content) - (
            time.monotonic() - last_send_time
        )
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _record_send_time(self) -> None:
        """记录本次文本发送完成的单调时钟时间。"""
        setattr(self.chat_stream.context, _LAST_SEND_TIME_ATTR, time.monotonic())

    async def _send_plain_text(self, content: str) -> bool:
        """按当前聊天流发送普通文本。"""
        await self._sleep_for_typing_delay(content)
        success = await self._send_to_stream(content)
        if success:
            self._record_send_time()
        return success

    async def execute(
        self,
        content: Annotated[str, "要发送的文本内容，不用添加标记，只写你想说的话即可"],
        reply_to: Annotated[str | None, "可选，要引用回复的目标消息 ID"] = None,
        at: Annotated[str | None, "可选，不使用 reply_to 时指定要 @ 的对象（用户 ID）"] = None,
    ) -> tuple[bool, str]:
        """执行发送文本消息的逻辑。

        Args:
            content: 要发送的文本内容，不用添加标记，只写你想说的话即可
            reply_to: 可选，要引用回复的目标消息 ID。若指定此参数，发送的消息将作为对该消息的回复
            at: 可选，不使用 reply_to 时指定要 @ 的对象（用户 ID）
        """
        if content:
            content = re.split(
                r"[,，]?\s*reason[:：]",
                content,
                flags=re.IGNORECASE,
            )[0].strip()

        at_prefix_hint: str | None = None
        if content:
            at_match = re.match(r"^\s*@([^\s]+)\s*", content)
            if at_match:
                at_prefix_hint = at_match.group(1).strip()
                content = content[at_match.end():].lstrip()

        if not (content or at_prefix_hint):
            return True, "内容为空，跳过发送"
        if not content:
            return True, "内容为空，跳过发送"

        if reply_to:
            return await self._send_with_reply(content, reply_to)
        return await self._send_with_at(content, at or at_prefix_hint)

    async def _send_with_reply(self, content: str, reply_to: str) -> tuple[bool, str]:
        """以引用回复方式发送。"""
        target_stream_id = self.chat_stream.stream_id
        platform = self.chat_stream.platform
        chat_type = self.chat_stream.chat_type
        context = self.chat_stream.context
        bot_info = await get_bot_info_by_platform(platform)

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
            message_id=f"action_{self.name}_{uuid4().hex}",
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

        await self._sleep_for_typing_delay(content)
        success = await send_message(message)
        if success:
            self._record_send_time()
        return success, f"已发送消息:{content}"

    async def _send_with_at(self, content: str, at_hint: str | None) -> tuple[bool, str]:
        """以 @ 用户或普通文本方式发送。"""
        at_hint = (at_hint or "").strip().lstrip("@").strip()
        if not at_hint:
            success = await self._send_plain_text(content)
            return success, f"已发送消息:{content}"

        target_stream_id = self.chat_stream.stream_id
        platform = self.chat_stream.platform
        chat_type = self.chat_stream.chat_type

        if chat_type != "group":
            success = await self._send_plain_text(content)
            return success, f"已发送消息:{content}"

        from src.core.utils.user_query_helper import get_user_query_helper

        bot_info = await get_bot_info_by_platform(platform)
        if at_hint.isdigit():
            at_user_id = at_hint
        else:
            at_user_id = await get_user_query_helper().resolve_user_id(platform, at_hint)

        if not at_user_id:
            logger.info(f"无法定位 at 目标: {at_hint}，按普通文本发送")
            success = await self._send_plain_text(content)
            return success, f"已发送消息:{content}"

        target_group_id = None
        target_group_name = None
        last_msg = self._get_context_message_for_target()
        if last_msg:
            target_group_id = last_msg.extra.get("group_id")
            target_group_name = last_msg.extra.get("group_name")

        extra: dict[str, str] = {"at_user_id": str(at_user_id)}
        if target_group_id:
            extra["target_group_id"] = target_group_id
        if target_group_name:
            extra["target_group_name"] = target_group_name

        message = Message(
            message_id=f"action_{self.name}_{uuid4().hex}",
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

        await self._sleep_for_typing_delay(content)
        success = await send_message(message)
        if success:
            self._record_send_time()
        return success, f"已发送消息:{content}"
