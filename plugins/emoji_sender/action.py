"""emoji_sender Action：发送表情包。

该 Action 面向 LLM Tool Calling：
- 输入：目标表情包描述文本 + 情感 tag（可多个）
- 行为：按 tag 过滤候选后向量检索 topN，选择距离满足阈值的最佳表情包发送
"""

from __future__ import annotations

from typing import Annotated, AsyncGenerator
from typing import cast

from src.app.plugin_system.api.service_api import get_service
from src.core.components.base.action import BaseAction

from .service import EMOTION_TAG_PRESET, EmojiSenderService

_EMOTION_TAG_VALUES = "、".join(EMOTION_TAG_PRESET)
_EMOTION_TAG_DESC = (
    f"情感标签（可多个，可为空）。可选值：{_EMOTION_TAG_VALUES}。"
    "若为空则不按 tag 过滤，直接全库向量检索。"
)


class SendEmojiMemeAction(BaseAction):
    """发送表情包动作。"""

    action_name: str = "send_emoji_meme"
    action_description: str = "根据目标描述与情感标签，检索并发送一张符合当前情景的表情包来生动地表达情绪。不要忘记在聊天时使用这个动作，比起简单的文字它往往更受欢迎。此动作可以单独使用也可以和发送文字一起使用，更符合日常聊天习惯。"
    primary_action: bool = False
    associated_types = ["emoji"]

    async def execute(
        self,
        description: Annotated[str, "目标表情包的描述文本，用于向量匹配（例如：‘生气地翻白眼’）"],
        emotion_tags: Annotated[
            list[str] | None,
            _EMOTION_TAG_DESC,
        ] = None,
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        """执行发送表情包动作。"""
        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            yield False, "emoji_sender service 未加载"
            return

        service = cast(EmojiSenderService, service)

        yield None
        ok, result, reason = await service.send_best_detailed(
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
            description_query=description,
            emotion_tags=emotion_tags,
        )

        if ok:
            if not result:
                yield True, "已发送表情包"
                return

            tag = str(result.get("tag") or "").strip()
            desc = str(result.get("description") or "").strip()
            distance = result.get("distance")
            fallback_used = bool(result.get("fallback_used"))

            dist_text = f"{float(distance):.4f}" if isinstance(distance, (int, float)) else "unknown"
            fallback_text = "（已触发fallback：未满足阈值但仍在指定标签内选最相似）" if fallback_used else ""

            detail = f"已发送表情包{fallback_text}\n- 标签: {tag}\n- 描述: {desc}\n- 距离: {dist_text}"
            yield True, detail
            return

        # 失败：尽量带上原因
        yield False, reason
        return
