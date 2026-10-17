"""``:build_resume_prompt`` 默认实现——按 source 分发到 timer / generic 构造器。"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent

if TYPE_CHECKING:
    from src.app.plugin_system.base import WaitResumeEvent

#: ``WaitResumeEvent.source`` 为 ``"message"`` 时，提示文本留空——消息本身走未读路径，
#: 不重复注入（依据 ``session._is_message_resume`` 注释）。
_MESSAGE_SOURCE = "message"
_TIMER_SOURCE = "timer"


def _build_timer_resume_prompt(event: WaitResumeEvent) -> str:
    """构造定时器恢复时塞进对话历史的 USER 提示文本。

    通知 LLM「上一次设置的等待已经结束、当前没有新用户消息」，并要求它基于
    现有上下文主动决策：要么再次调用 ``pass_and_wait`` 推迟回复，
    要么直接产出回复 / 调用工具。

    Args:
        event: ``WaitResumeEvent``，``event.source`` 为 ``"timer"``。

    Returns:
        str: 直接作为 USER payload 追加到 response 的提示文本。
    """
    waited_text = (
        "你之前设置的等待时间已经结束。"
        if event.wait_time is None
        else f"你之前设置的等待 {event.wait_time} 秒已经结束。"
    )
    return (
        f"系统事件：{waited_text} 当前没有新的用户消息。"
        "请基于已有上下文主动决定下一步。"
        "如果现在不应继续，请再次调用 pass_and_wait；"
        "如果需要回复或执行动作，请直接使用相应工具。"
    )


def _build_generic_resume_prompt(event: WaitResumeEvent) -> str:
    """未知 / 子代理 / 内部上下文等 source 的通用恢复提示。

    当 ``WaitResumeEvent.source`` 既不是 ``"timer"`` 也不是 ``"message"`` 时调用，
    例如子代理 / 内部上下文 / 自定义事件源。若上游通过 ``event.extra["resume_prompt"]``
    显式提供了提示文本则直接使用；否则按 source 标签生成一段默认提示。

    Args:
        event: ``WaitResumeEvent``，``event.source`` 不是 ``"timer"`` / ``"message"``。

    Returns:
        str: 直接作为 USER payload 追加到 response 的提示文本。
    """
    custom = event.extra.get("resume_prompt")
    if isinstance(custom, str) and custom.strip():
        return custom
    source_label = event.source or "未知来源"
    return (
        f"系统事件：收到来自 '{source_label}' 的恢复请求。"
        "请基于已有上下文主动决定下一步。"
        "如果现在无需继续处理，请调用 pass_and_wait；"
        "如果需要继续回复或执行动作，请直接使用相应工具。"
    )


class BuildResumePromptDefaultHandler(BaseEventHandler):
    """:build_resume_prompt 的默认实现。

    按 ``source`` 分发：

    - ``"timer"`` → 调 ``_build_timer_resume_prompt``
    - ``"message"`` → 留空（消息本身走未读路径）
    - 其他 → 调 ``_build_generic_resume_prompt``
    """

    name = "build_resume_prompt_default"
    description = "默认 build_resume_prompt：按 source 分发到 timer / generic 构造器"
    weight = 0
    init_subscribe = [NdfcEvent.BUILD_RESUME_PROMPT]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 build_resume_prompt，把提示文本填入 payload。"""
        try:
            source = params.get("source") or ""
            resume_event = params.get("resume_event")

            if source == _MESSAGE_SOURCE:
                params["prompt"] = ""
            elif source == _TIMER_SOURCE and resume_event is not None:
                params["prompt"] = _build_timer_resume_prompt(resume_event)
            elif resume_event is not None:
                params["prompt"] = _build_generic_resume_prompt(resume_event)
            else:
                params["prompt"] = ""
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["BuildResumePromptDefaultHandler"]
