"""``:compute_cooldown`` 默认实现——按 config.enable_cooldown 算冷却秒数。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....components.config import NeoChatterConfig
from ....utils.event_publisher import NdfcEvent


class ComputeCooldownDefaultHandler(BaseEventHandler):
    """:compute_cooldown 的默认实现。

    若 ``config.plugin.enable_cooldown`` 开启，则 ``cooldown_seconds = int(minutes * 60)``；
    否则 ``cooldown_seconds = 0``（不冷却，立即可重启对话）。
    """

    name = "compute_cooldown_default"
    description = "默认 compute_cooldown：按 enable_cooldown 算冷却秒数"
    weight = 0
    init_subscribe = [NdfcEvent.COMPUTE_COOLDOWN]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 compute_cooldown，把 cooldown_seconds 填入 payload。"""
        try:
            config = params.get("config")

            if isinstance(config, NeoChatterConfig) and bool(
                config.plugin.enable_cooldown
            ):
                minutes_raw = params.get("minutes")
                try:
                    minutes_val = (
                        float(minutes_raw) if minutes_raw is not None else 0.0
                    )
                except (TypeError, ValueError):
                    minutes_val = 0.0
                params["cooldown_seconds"] = int(minutes_val * 60)
            else:
                params["cooldown_seconds"] = 0
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["ComputeCooldownDefaultHandler"]
