"""``:build_resume_prompt`` 默认实现——按 source 分发到 timer / generic 构造器。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent
from ....session import (
    _build_generic_resume_prompt,
    _build_timer_resume_prompt,
)

#: ``WaitResumeEvent.source`` 为 ``"message"`` 时，提示文本留空——消息本身走未读路径，
#: 不重复注入（依据 ``session._is_message_resume`` 注释）。
_MESSAGE_SOURCE = "message"
_TIMER_SOURCE = "timer"


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
