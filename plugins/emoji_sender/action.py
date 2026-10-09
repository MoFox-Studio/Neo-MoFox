"""emoji_sender Action：发送、收藏和重新识别表情包。

提供以下 Action：
- SendEmojiMemeAction（direct 模式）：输入目标描述 + 情感 tag，一步完成检索与发送
- SendEmojiMemeByIdAction（picker 模式）：按 search_emoji_memes 返回的 id 精确发送指定表情包
- CollectEmojiMemeAction：收藏当前聊天中的指定图片或表情包
- RefreshEmojiMemeAction：带可选提示重新识别聊天图片或已收藏的表情包
"""

from __future__ import annotations

from typing import Annotated, AsyncGenerator
from typing import cast

from src.app.plugin_system.api.service_api import get_service
from src.app.plugin_system.base import BaseAction
from src.app.plugin_system.types import ChatStream

from .service import EMOTION_TAG_PRESET, EmojiSenderService

_EMOTION_TAG_VALUES = "、".join(EMOTION_TAG_PRESET)
_EMOTION_TAG_DESC = (
    f"情感标签（可多个，可为空）。可选值：{_EMOTION_TAG_VALUES}。"
    "若为空则不按 tag 过滤，直接全库向量检索。"
)


def _has_chat_media(chat_stream: ChatStream, media_id: str) -> bool:
    """检查图片是否出现在当前聊天的历史、未读或当前消息中。"""
    context = chat_stream.context
    messages = [*context.history_messages, *context.unread_messages]
    if context.current_message is not None:
        messages.append(context.current_message)
    for message in messages:
        for container in (message.content, message.extra):
            if not isinstance(container, dict):
                continue
            media = container.get("media")
            if not isinstance(media, list):
                continue
            if any(
                isinstance(item, dict)
                and item.get("type") in ("image", "emoji")
                and item.get("image_id") == media_id
                for item in media
            ):
                return True
    return False


class SendEmojiMemeAction(BaseAction):
    """发送表情包动作。"""

    name: str = "send_emoji_meme"
    description: str = "根据目标描述与情感标签，检索并发送一张符合当前情景的表情包来生动地表达情绪。不要忘记在聊天时使用这个动作，比起简单的文字它往往更受欢迎。此动作可以单独使用也可以和发送文字一起使用，更符合日常聊天习惯。"
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


class CollectEmojiMemeAction(BaseAction):
    """收藏当前聊天中的指定图片或表情包。"""

    name: str = "collect_emoji_meme"
    description: str = (
        "当你在聊天中看到自己喜欢、符合自己的人设和表达习惯、以后用得上的表情包时，主动收藏，不必等用户要求。"
        "想想以后会用它表达什么情绪、回应什么场景；有选择地收藏，不是看到图片就收。"
        "收藏是留着以后使用，不代表现在就要发送，也不改变表情包的发送频率限制。"
        "media_id 从当前聊天的 [图片(media_id):描述] 或 [表情包(media_id):描述] 中提取，"
        "必须使用括号中的完整媒体哈希，不是消息 id，也不是图片描述。"
        "收藏后返回可用于重新识别的 id；已经收藏的图片不会重复入库。"
        "这个动作只收藏，不发送图片，也不自动发送收藏通知。"
    )
    primary_action: bool = False
    associated_types: list[str] = ["image", "emoji"]

    async def execute(
        self,
        media_id: Annotated[str, "当前聊天中图片或表情包占位符括号里的完整 media_id"],
    ) -> tuple[bool, str]:
        """验证媒体属于当前上下文后收藏，不接受任意路径或其他会话的图片。"""
        requested_id = media_id.strip().lower()
        if not _has_chat_media(self.chat_stream, requested_id):
            return False, "当前聊天上下文中没有这张图片，请使用当前聊天中的完整 media_id"

        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            return False, "emoji_sender service 未加载"
        return await cast(EmojiSenderService, service).collect_meme(media_id=requested_id)


class RefreshEmojiMemeAction(BaseAction):
    """带可选复核提示重新识别聊天图片或已收藏的表情包。"""

    name: str = "refresh_emoji_meme"
    description: str = (
        "当你发现图片或表情包的描述、图中文字或情绪标签可能认错时，"
        "重新调用 VLM 看图并更新识别描述，不需要先收藏。"
        "meme_id 使用当前聊天的 [图片(media_id):描述] 或 [表情包(media_id):描述] 中的完整 media_id，"
        "也可以使用收藏返回的完整 id 或 search_emoji_memes 返回的 12 位 id；"
        "不能使用消息 id 或文件路径。已收藏的图片同时更新标签和检索数据。"
        "extra_prompt 可提醒 VLM 重点检查疑点，例如‘请核对这是撒娇还是生气’。"
        "缺图明确报错，请聊天对象重新发送图片；失败保留旧识别结果，不删除图片，不自动收藏。"
    )
    primary_action: bool = False
    associated_types: list[str] = ["image", "emoji"]

    async def execute(
        self,
        meme_id: Annotated[str, "当前聊天图片的完整 media_id、收藏返回的完整 id，或检索结果中的 12 位 id"],
        extra_prompt: Annotated[str, "额外传给 VLM 的识别提示，可指出疑似认错的内容"] = "",
    ) -> tuple[bool, str]:
        """重新识别图片，并限制未收藏的媒体必须来自当前聊天。"""
        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            return False, "emoji_sender service 未加载"
        requested_id = meme_id.strip().lower()
        return await cast(EmojiSenderService, service).refresh_meme(
            meme_id=requested_id,
            extra_prompt=extra_prompt,
            allow_chat_media=len(requested_id) == 64 and _has_chat_media(self.chat_stream, requested_id),
        )


class SendEmojiMemeByIdAction(BaseAction):
    """按 id 精确发送表情包动作。"""

    name: str = "send_emoji_meme_by_id"
    description: str = (
        "按 id 发送一张表情包库中的指定表情包。"
        "id 必须来自 search_emoji_memes 返回的候选列表；"
        "此动作可以单独使用也可以和发送文字一起使用，更符合日常聊天习惯。"
    )
    primary_action: bool = False
    associated_types = ["emoji"]

    async def execute(
        self,
        meme_id: Annotated[str, "要发送的表情包 id（来自 search_emoji_memes 返回的候选列表）"],
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        """执行按 id 发送表情包动作。"""
        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            yield False, "emoji_sender service 未加载"
            return

        service = cast(EmojiSenderService, service)

        yield None
        ok, result, reason = await service.send_by_id(
            short_id=meme_id,
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
        )

        if ok:
            if not result:
                yield True, "已发送表情包"
                return

            tag = str(result.get("tag") or "").strip()
            desc = str(result.get("description") or "").strip()
            detail = f"已发送表情包\n- 标签: {tag}\n- 描述: {desc}" if desc else f"已发送表情包\n- 标签: {tag}"
            yield True, detail
            return

        yield False, reason
        return
