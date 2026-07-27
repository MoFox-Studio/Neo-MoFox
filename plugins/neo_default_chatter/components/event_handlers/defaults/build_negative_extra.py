"""``:build_negative_extra`` 默认实现——追加内置负面行为约束。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....components.config import NeoChatterConfig
from ....utils.event_publisher import NdfcEvent
from ....utils.prompt_builder import NeoChatterPromptBuilder


class BuildNegativeExtraDefaultHandler(BaseEventHandler):
    """:build_negative_extra 的默认实现——追加内置负面行为约束。

    weight=0 保证第三方先 append 自己的 fragments；返回 ``SUCCESS`` 让链继续。
    """

    name = "build_negative_extra_default"
    description = "默认 negative behaviors：内置约束文案"
    weight = 0
    init_subscribe = [NdfcEvent.BUILD_NEGATIVE_EXTRA]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 build_negative_extra，把约束文本 append 到 fragments。"""
        try:
            config = params.get("config")
            if not isinstance(config, NeoChatterConfig):
                config = getattr(self.plugin, "config", None)
            text = NeoChatterPromptBuilder.build_negative_behaviors_extra(config)
            if text:
                params["fragments"].append(text)
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["BuildNegativeExtraDefaultHandler"]
