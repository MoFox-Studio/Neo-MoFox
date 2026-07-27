"""``:compute_stop_wake`` 默认实现——按 chat_type + config 算 wake_probability。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....components.config import NeoChatterConfig
from ....utils.event_publisher import NdfcEvent

#: 仅私聊场景启用「直接消息唤醒」——群聊里不该被任意成员消息触发提前唤醒。
_PRIVATE_CHAT_TYPE = "private"


class ComputeStopWakeDefaultHandler(BaseEventHandler):
    """:compute_stop_wake 的默认实现。

    若 ``chat_type == "private"`` 且 ``config.plugin.enable_stop_direct_message_wake``
    开启，则 ``probability = clamp(0, 1, stop_direct_message_wake_probability)``；
    否则 ``probability = 0.0``。
    """

    name = "compute_stop_wake_default"
    description = "默认 compute_stop_wake：按 chat_type + config 算 wake_probability"
    weight = 0
    init_subscribe = [NdfcEvent.COMPUTE_STOP_WAKE]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 compute_stop_wake，把 probability 填入 payload。"""
        try:
            config = params.get("config")
            chat_type = params.get("chat_type")
            chat_type_str = self._normalize_chat_type(chat_type)

            if (
                chat_type_str == _PRIVATE_CHAT_TYPE
                and isinstance(config, NeoChatterConfig)
                and bool(config.plugin.enable_stop_direct_message_wake)
            ):
                raw = float(config.plugin.stop_direct_message_wake_probability)
                probability = max(0.0, min(1.0, raw))
            else:
                probability = 0.0
            params["probability"] = probability
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params

    @staticmethod
    def _normalize_chat_type(chat_type: Any) -> str:
        """把 ``chat_type`` 归一化为小写字符串值。

        ``ChatStream.chat_type`` 可能是 ``ChatType`` 枚举（``.value`` 为 ``"private"``）
        或字符串；用 ``getattr(ct, "value", ct)`` 兼容两种形式。
        """
        if chat_type is None:
            return ""
        value = getattr(chat_type, "value", chat_type)
        return str(value).lower()


__all__ = ["ComputeStopWakeDefaultHandler"]
