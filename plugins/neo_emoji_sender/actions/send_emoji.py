"""neo_emoji_sender Action：发送与删除表情包。"""

from __future__ import annotations

from typing import Annotated, AsyncGenerator, cast

from src.app.plugin_system.api.service_api import get_service
from src.app.plugin_system.base import BaseAction

from ..manager.service import EMOTION_TAG_PRESET, EmojiService

_EMOTION_TAG_VALUES = "、".join(EMOTION_TAG_PRESET)
_EMOTION_TAG_DESC = (
    f"情感标签（可多个，可为空）。可选值：{_EMOTION_TAG_VALUES}。"
    "若为空则不按 tag 过滤，直接全库向量检索。"
)


class SendEmojiMemeAction(BaseAction):
    """直接检索并发送一张表情包。"""

    name = "send_emoji_meme"
    description = (
        "根据目标描述与情感标签，检索并发送一张符合当前情景的表情包来生动地表达情绪。"
        "不要忘记在聊天时使用这个动作，比起简单的文字它往往更受欢迎。"
        "此动作可以单独使用也可以和发送文字一起使用，更符合日常聊天习惯。"
    )
    primary_action = False
    associated_types = ["emoji"]

    async def execute(
        self,
        description: Annotated[str, "目标表情包的描述文本，用于向量匹配（例如：‘生气地翻白眼’）"],
        emotion_tags: Annotated[list[str] | None, _EMOTION_TAG_DESC] = None,
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        """执行发送表情包动作。

        Args:
            description: 目标表情包描述。
            emotion_tags: 情感标签过滤。

        Yields:
            None 表示开始执行，随后返回 (是否成功, 详情)。
        """
        service = get_service("neo_emoji_sender:service:emoji")
        if service is None:
            yield False, "neo_emoji_sender service 未加载"
            return

        yield None
        ok, result, reason = await cast(EmojiService, service).send_best_detailed(
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
            description_query=description,
            emotion_tags=emotion_tags,
        )
        if not ok:
            yield False, reason
            return
        if not result:
            yield True, "已发送表情包"
            return

        tag = str(result.get("tag") or "").strip()
        desc = str(result.get("description") or "").strip()
        distance = result.get("distance")
        fallback_used = bool(result.get("fallback_used"))
        dist_text = f"{float(distance):.4f}" if isinstance(distance, (int, float)) else "unknown"
        fallback_text = "（已触发fallback：未满足阈值但仍在指定标签内选最相似）" if fallback_used else ""
        yield True, f"已发送表情包{fallback_text}\n- 标签: {tag}\n- 描述: {desc}\n- 距离: {dist_text}"


class SendEmojiMemeByIdAction(BaseAction):
    """按 id 精确发送表情包。"""

    name = "send_emoji_meme_by_id"
    description = (
        "按 id 发送一张表情包库中的指定表情包。"
        "id 必须来自 search_emoji_memes 返回的候选列表；"
        "此动作可以单独使用也可以和发送文字一起使用，更符合日常聊天习惯。"
    )
    primary_action = False
    associated_types = ["emoji"]

    async def execute(
        self,
        meme_id: Annotated[str, "要发送的表情包 id（来自 search_emoji_memes 返回的候选列表）"],
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        """执行按 id 发送表情包动作。

        Args:
            meme_id: 表情包完整 ID 或前缀。

        Yields:
            None 表示开始执行，随后返回 (是否成功, 详情)。
        """
        service = get_service("neo_emoji_sender:service:emoji")
        if service is None:
            yield False, "neo_emoji_sender service 未加载"
            return

        yield None
        ok, result, reason = await cast(EmojiService, service).send_by_id(
            short_id=meme_id,
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
        )
        if not ok:
            yield False, reason
            return
        if not result:
            yield True, "已发送表情包"
            return
        tag = str(result.get("tag") or "").strip()
        desc = str(result.get("description") or "").strip()
        yield True, (
            f"已发送表情包\n- 标签: {tag}\n- 描述: {desc}"
            if desc
            else f"已发送表情包\n- 标签: {tag}"
        )


class DeleteEmojiMemeByIdAction(BaseAction):
    """按完整 id 删除表情包。"""

    name = "delete_emoji_meme_by_id"
    description = (
        "从表情包库中彻底删除一张表情包，同时移除磁盘文件与向量库记录，删除后不可恢复。"
        "meme_id 必须是 search_emoji_memes 返回的完整 meme_id"
        "（64 位十六进制，仅限 0-9a-f），不接受前缀。"
        "仅在你明确不想再使用这张表情包时调用。"
    )
    primary_action = False
    associated_types = ["emoji"]

    async def execute(
        self,
        meme_id: Annotated[str, "要删除的表情包完整 id（来自 search_emoji_memes 返回的完整 meme_id）"],
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        """执行按完整 id 删除表情包动作。

        Args:
            meme_id: 表情包完整 ID。

        Yields:
            None 表示开始执行，随后返回 (是否成功, 详情)。
        """
        service = get_service("neo_emoji_sender:service:emoji")
        if service is None:
            yield False, "neo_emoji_sender service 未加载"
            return

        yield None
        result = await cast(EmojiService, service).delete(meme_id)
        if not result.db_deleted and not result.file_deleted:
            yield False, result.message
            return
        detail = (
            f"已删除表情包\n- ID: {result.emoji_id}\n"
            f"- 向量清理: {'完成' if result.vector_deleted else '待对账'}\n"
            f"- 文件清理: {'完成' if result.file_deleted else '待对账'}"
        )
        yield True, detail


__all__ = [
    "SendEmojiMemeAction",
    "SendEmojiMemeByIdAction",
    "DeleteEmojiMemeByIdAction",
]
