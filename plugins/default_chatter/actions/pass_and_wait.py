"""PassAndWaitAction：跳过本次动作，等待新消息或定时继续。"""

from __future__ import annotations

from src.core.components.base.action import BaseAction


class PassAndWaitAction(BaseAction):
    """跳过本次动作，等待新消息或定时继续。"""

    action_name = "pass_and_wait"
    action_description = "为当前对话登记一个等待点。你可以单独调用它，让本轮什么都不做直接等待；也可以在同一轮先调用其他 action（例如 send_text、发送表情等），再调用本工具，表示这些动作执行完成后进入等待。默认会等待用户新消息；如果传入 seconds 参数，则会在指定秒数到达后由框架主动恢复对话流程，即使期间没有收到新消息。适合需要回复后稍后主动继续、定时追问或延时确认的场景。"

    chatter_allow: list[str] = ["default_chatter"]
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
