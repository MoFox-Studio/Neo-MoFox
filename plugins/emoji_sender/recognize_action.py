"""emoji_sender Action：识别用户发送的表情包。

该 Action 面向 LLM Tool Calling：
- 从当前消息中获取表情包数据
- 对 GIF 动态表情包提取多帧，综合识别内容并返回详细描述
- 输出：表情包的描述、情感标签、是否为动图

主要用于 AI 主动识别用户发送的表情包，特别是 GIF 动图。
"""

from __future__ import annotations

from typing import cast

from src.app.plugin_system.api.service_api import get_service
from src.core.components.base.action import BaseAction


class RecognizeEmojiAction(BaseAction):
    """识别表情包动作。

    用于识别用户发送的表情包，支持 GIF 动态表情包的多帧识别。
    当用户发送表情包时，AI 可以调用此动作获取详细描述。
    """

    action_name: str = "recognize_emoji"
    action_description: str = (
        "识别用户发送的表情包内容，支持 GIF 动图。"
        "当用户发送表情包而你想要了解其具体内容时，调用此动作。"
        "返回表情包的详细描述、情感标签以及是否为动图。"
        "无需传入参数，会自动从当前消息中获取表情包数据。"
    )
    primary_action: bool = False

    async def execute(self) -> tuple[bool, str]:
        """执行识别表情包动作。

        从当前聊天消息中获取表情包数据并识别。

        Returns:
            (成功与否, 描述信息或错误原因)
        """
        current_message = self.chat_stream.context.current_message
        if current_message is None:
            return False, "当前没有消息"

        media_list: list[dict] | None = getattr(current_message, "media", None)
        if not media_list:
            return False, "当前消息中没有媒体数据"

        emoji_data = None
        for media in media_list:
            if media.get("type") in ("emoji", "image"):
                emoji_data = media.get("data")
                break

        if not emoji_data:
            return False, "当前消息中没有表情包或图片"

        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            return False, "emoji_sender service 未加载"

        from .service import EmojiSenderService

        service = cast(EmojiSenderService, service)

        result = await service.recognize_emoji(emoji_data)

        if result is None:
            return False, "表情包识别失败，可能是 VLM 服务不可用或图片格式不支持"

        description = result.get("description", "")
        emotion_tags = result.get("emotion_tags", [])
        is_gif = result.get("is_gif", False)

        tags_str = "、".join(emotion_tags) if emotion_tags else "无"
        gif_hint = "（GIF 动图）" if is_gif else "（静态图）"

        detail = (
            f"表情包识别结果{gif_hint}：\n"
            f"- 描述：{description}\n"
            f"- 情感标签：{tags_str}"
        )

        return True, detail
