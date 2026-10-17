"""Neo-Default-Chatter 对话控制动作。"""

from __future__ import annotations

from src.app.plugin_system.base import BaseAction


class PassAndWaitAction(BaseAction):
    """跳过本次动作，等待新消息或定时继续。"""

    name = "pass_and_wait"
    description = (
        "为当前对话登记一个等待点。你可以单独调用它，让本轮什么都不做直接等待；"
        "也可以在同一轮先调用其他 action（例如 send_text、发送表情等），再调用本工具，"
        "表示这些动作执行完成后进入等待。默认会等待用户新消息；如果传入 seconds 参数，"
        "则会在指定秒数到达后由框架主动恢复对话流程，即使期间没有收到新消息。"
        "适合需要回复后稍后主动继续、定时追问或延时确认的场景。"
    )

    chatter_allow: list[str] = ["neo_default_chatter"]
    associated_types = ["text"]

    async def execute(self, seconds: float | None = None) -> tuple[bool, str]:
        """跳过本次动作，不执行任何操作。

        Args:
            seconds: 等待秒数；为 None 时等待新消息，为数字时到时主动继续。
                可与其他 action 同轮组合，表示本轮动作完成后再进入等待。
        """
        if seconds is None:
            return True, "已登记等待，将在本轮动作完成后等待新消息"
        return True, f"已登记等待，将在本轮动作完成后等待 {seconds} 秒再继续对话"


class StopConversationAction(BaseAction):
    """结束当前对话轮次。"""

    name = "stop_conversation"
    description = (
        "结束当前对话，过一段时间后再允许开启新对话。如果对话已经自然结束，"
        "或者你认为本轮对话可以告一段落，或者你暂时不想继续对话，使用本工具结束这轮对话。"
        "通常当你已经做出回应，且后续的消息很可能是新的话题时，使用本工具结束对话。"
        "你可以指定一个冷却时间（分钟），在此期间即使有新消息也不会触发新的对话，"
        "直到冷却时间结束后才会重新允许开启新对话。"
    )

    chatter_allow: list[str] = ["neo_default_chatter"]
    associated_types = ["text"]

    async def execute(self, minutes: float | None = None) -> tuple[bool, str]:
        """结束对话并设置冷却时间。

        Args:
            minutes: 冷却时间（分钟），在此期间不会开启新对话。
                未提供时使用插件配置的默认冷却分钟数。
        """
        if minutes is None:
            return True, "已登记结束对话，将使用默认冷却时间"
        return True, f"对话已结束，将在 {minutes} 分钟后允许新对话"
