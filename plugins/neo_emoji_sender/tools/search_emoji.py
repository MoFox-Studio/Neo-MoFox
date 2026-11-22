"""neo_emoji_sender Tool：检索表情包候选列表。"""

from __future__ import annotations

from typing import Annotated, cast

from src.app.plugin_system.api.service_api import get_service
from src.app.plugin_system.base import BaseTool

from ..manager.service import EMOTION_TAG_PRESET, EmojiService

_EMOTION_TAG_VALUES = "、".join(EMOTION_TAG_PRESET)
_EMOTION_TAG_DESC = (
    f"情感标签（可多个，可为空）。可选值：{_EMOTION_TAG_VALUES}。"
    "若为空则不按 tag 过滤，直接全库向量检索。"
)


class SearchEmojiMemesTool(BaseTool):
    """检索表情包候选列表。"""

    name = "search_emoji_memes"
    description = (
        "根据目标描述与情感标签检索表情包库，返回候选列表（每项含 id、标签、描述与距离）。"
        "当你想发表情包时，先用本工具查看有哪些候选，挑选最契合当前语境的一张，"
        "再用它的 id 调用 send_emoji_meme_by_id 发送。"
        "每项候选还带完整 meme_id，若候选明显不合适可用 delete_emoji_meme_by_id 彻底删除。"
        "对候选不满意时可换更具体的描述重新查询，或翻页查看更多。"
    )

    async def execute(
        self,
        description: Annotated[str, "目标表情包的描述文本，用于向量匹配（例如：‘生气地翻白眼’）"],
        emotion_tags: Annotated[list[str] | None, _EMOTION_TAG_DESC] = None,
        page: Annotated[int, "页码（从 1 开始），候选不足或想看更多时翻页"] = 1,
    ) -> tuple[bool, str]:
        """检索候选表情包列表。

        Args:
            description: 目标描述。
            emotion_tags: 情感标签过滤。
            page: 页码。

        Returns:
            (是否成功, 候选列表文本)。
        """
        service = get_service("neo_emoji_sender:service:emoji")
        if service is None:
            return False, "neo_emoji_sender service 未加载"

        stream_id = self.get_current_stream_id() or ""
        result = await cast(EmojiService, service).search_candidates(
            description_query=description,
            emotion_tags=emotion_tags,
            page=page,
            stream_id=stream_id,
        )
        if not result:
            return (
                True,
                "没有找到符合条件的表情包。可尝试：换更具体的描述、去掉情感标签过滤、或翻回前一页。",
            )

        candidates: list[dict] = list(result.get("candidates") or [])
        current_page = int(result.get("page") or 1)
        total = int(result.get("total") or 0)
        lines: list[str] = [f"候选表情包列表（第 {current_page} 页，共 {total} 张可选）："]
        for item in candidates:
            short_id = str(item.get("short_id") or "")
            meme_id = str(item.get("meme_id") or "")
            tag = str(item.get("tag") or "")
            desc = str(item.get("description") or "")
            distance = item.get("distance")
            dist_text = f"{float(distance):.2f}" if isinstance(distance, (int, float)) else "?"
            lines.append(
                f"- id={short_id} | 标签: {tag} | 描述: {desc} | 距离: {dist_text}\n"
                f"  meme_id={meme_id}"
            )
        lines.append(
            "用 send_emoji_meme_by_id(id) 发送选中的表情包；"
            "若候选明显不合适，可用 delete_emoji_meme_by_id(meme_id) 彻底删除它"
            "（需传上方完整的 meme_id，删除后不可恢复）；"
            "候选都不合适时可换更具体的描述重新查询，或传更大 page 翻页。"
        )
        return True, "\n".join(lines)


__all__ = ["SearchEmojiMemesTool"]
