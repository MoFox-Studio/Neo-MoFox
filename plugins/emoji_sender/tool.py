"""emoji_sender Tool：检索表情包候选列表。

该 Tool 面向 LLM Tool Calling（picker 模式）：
- 输入：目标表情包描述文本 + 情感 tag（可多个）+ 页码
- 行为：按 tag 过滤候选后向量检索，按 meme 去重、过滤最近已发送，分页返回
- Tool 结果会触发 FOLLOW_UP 轮，AI 可从候选列表挑选 id 后调用 send_emoji_meme_by_id 发送
"""

from __future__ import annotations

from typing import Annotated, cast

from src.app.plugin_system.api.service_api import get_service
from src.app.plugin_system.base import BaseTool

from .service import EMOTION_TAG_PRESET, EmojiSenderService

_EMOTION_TAG_VALUES = "、".join(EMOTION_TAG_PRESET)
_EMOTION_TAG_DESC = (
    f"情感标签（可多个，可为空）。可选值：{_EMOTION_TAG_VALUES}。"
    "若为空则不按 tag 过滤，直接全库向量检索。"
)


class SearchEmojiMemesTool(BaseTool):
    """检索表情包候选列表。"""

    tool_description = "按目标描述与情感标签检索表情包库，返回候选列表供挑选"
    tool_name = "search_emoji_memes"

    name: str = "search_emoji_memes"
    description: str = (
        "根据目标描述与情感标签检索表情包库，返回候选列表（每项含 id、标签、描述与距离，距离越小与你想要的越贴切）。"
        "当你想发表情包时，先用本工具查看有哪些候选，挑选最契合当前语境的一张，"
        "再用它的 id 调用 send_emoji_meme_by_id 发送。对候选不满意时可换更具体的描述重新查询，或翻页查看更多。"
    )

    async def execute(
        self,
        description: Annotated[str, "目标表情包的描述文本，用于向量匹配（例如：‘生气地翻白眼’）"],
        emotion_tags: Annotated[
            list[str] | None,
            _EMOTION_TAG_DESC,
        ] = None,
        page: Annotated[int, "页码（从 1 开始），候选不足或想看更多时翻页"] = 1,
    ) -> tuple[bool, str]:
        """检索候选表情包列表。

        Returns:
            (是否成功, 候选列表文本)
        """
        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            return False, "emoji_sender service 未加载"

        service = cast(EmojiSenderService, service)
        stream_id = self.get_current_stream_id() or ""

        result = await service.search_candidates(
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
            tag = str(item.get("tag") or "")
            desc = str(item.get("description") or "")
            distance = item.get("distance")
            dist_text = f"{float(distance):.2f}" if isinstance(distance, (int, float)) else "?"
            lines.append(f"- id={short_id} | 标签: {tag} | 描述: {desc} | 距离: {dist_text}")

        lines.append(
            "用 send_emoji_meme_by_id(id) 发送选中的表情包；"
            "候选都不合适时可换更具体的描述重新查询，或传更大 page 翻页。"
        )
        return True, "\n".join(lines)
