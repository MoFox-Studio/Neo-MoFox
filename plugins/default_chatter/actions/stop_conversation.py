"""StopConversationAction：结束当前对话轮次，设置冷却时间。"""

from __future__ import annotations

from src.core.components.base.action import BaseAction


class StopConversationAction(BaseAction):
    """结束当前对话轮次。"""

    action_name = "stop_conversation"
    action_description = "结束当前对话，过一段时间后再允许开启新对话。如果对话已经自然结束，或者你认为本轮对话可以告一段落，或者你暂时不想继续对话，使用本工具结束这轮对话。通常当你已经做出回应，且后续的消息很可能是新的话题时，使用本工具结束对话。你可以指定一个冷却时间（分钟），在此期间即使有新消息也不会触发新的对话，直到冷却时间结束后才会重新允许开启新对话。"

    chatter_allow: list[str] = ["default_chatter"]
    associated_types = ["text"]

    async def execute(self, minutes: float) -> tuple[bool, str]:
        """结束对话并设置冷却时间。

        Args:
            minutes: 冷却时间（分钟），在此期间不会开启新对话
        """
        return True, f"对话已结束，将在 {minutes} 分钟后允许新对话"
