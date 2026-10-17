"""Neo-Default-Chatter 预处理事件处理器：私聊直通。

订阅 ``neo_default_chatter:preprocess`` 事件，在事件链最前端检查当前聊天流是否
为私聊（``chat_type == "private"``）。若是，则直接放行给主 chatter 处理，
``STOP`` 阻断后续 ``probability_bypass`` / ``sub_agent_decision`` 处理器——
私聊场景下用户消息天然需要即时回复，不应再走概率门或轻量 LLM 判定。

判定逻辑：

- 私聊 → ``proceed=True``、``reason="私聊直通放行"``，返回 ``EventDecision.STOP``
- 非私聊（group/discuss 等）→ 返回 ``EventDecision.SUCCESS``，``proceed`` 保持
  默认 ``False``，让后续 ``probability_bypass`` 与 ``sub_agent_decision`` 继续判定。

所有判定逻辑均自包含在本处理器内部，不依赖额外配置或工具模块。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import ChatStream
from src.kernel.event import EventDecision

from .....utils.event_publisher import NdfcEvent

logger = get_logger("neo_default_chatter.preprocess.private_chat_bypass")

#: NFC 预处理事件名。
_PREPROCESS_EVENT = NdfcEvent.PREPROCESS

#: 私聊聊天流类型标识。
_PRIVATE_CHAT_TYPE = "private"


class PrivateChatBypassHandler(BaseEventHandler):
    """私聊直通处理器。

    在 ``neo_default_chatter:preprocess`` 事件链最前端检查 ``chat_type``，
    私聊场景下直接放行给主 chatter，跳过概率门与 sub_agent LLM 判定——
    私聊消息天然需要即时回复，不应再走默认的预处理门控流程。

    Class Attributes:
        weight: 2，高于 ``ProbabilityBypassHandler``（1）与 ``SubAgentDecisionHandler``
            （0），确保私聊判定最先执行。
        init_subscribe: 订阅 ``neo_default_chatter:preprocess`` 事件。
    """

    name = "private_chat_bypass"
    description = "私聊直通处理器 - 私聊场景跳过概率门与 sub_agent 判定直接放行"
    weight = 2
    init_subscribe = [_PREPROCESS_EVENT]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行私聊直通判定。

        Args:
            event_name: 事件名称（由 EventBus 传入）。
            params: 事件参数字典，包含 ``chat_stream`` / ``chat_type``
                以及预填的决策字段 ``proceed`` / ``reason`` 等。

        Returns:
            ``(EventDecision, params)``：

            - 私聊聊天流 → ``(STOP, params)``，``proceed=True``、``reason`` 写入
              "私聊直通放行"，阻断后续 ``probability_bypass`` / ``sub_agent`` 处理器；
            - 非私聊 / ``chat_stream`` 缺失 → ``(SUCCESS, params)``，``proceed`` 保持
              默认 ``False``，让后续处理器继续判定。
        """
        chat_stream = params.get("chat_stream")
        if not isinstance(chat_stream, ChatStream):
            return EventDecision.SUCCESS, params

        if not self._is_private_chat(chat_stream, params):
            return EventDecision.SUCCESS, params

        params["proceed"] = True
        params["reason"] = "私聊直通放行"
        logger.info(
            f"[私聊直通] 私聊场景直接放行 stream={chat_stream.stream_id[:8]}"
        )
        return EventDecision.STOP, params

    # ==================== 私有辅助：所有处理逻辑自包含 ====================

    @staticmethod
    def _is_private_chat(chat_stream: ChatStream, params: dict[str, Any]) -> bool:
        """判断当前聊天流是否为私聊。

        优先用 ``params['chat_type']``（由 :meth:`NdfcPublisher.preprocess` 预填），
        缺失时回退到 ``chat_stream.chat_type``，二者均与 ``"private"`` 比对。

        Args:
            chat_stream: 当前聊天流。
            params: 事件参数字典。

        Returns:
            私聊场景返回 ``True``，否则 ``False``。
        """
        chat_type = params.get("chat_type")
        if not isinstance(chat_type, str) or not chat_type:
            chat_type = str(chat_stream.chat_type or "")
        return chat_type == _PRIVATE_CHAT_TYPE


__all__ = ["PrivateChatBypassHandler"]
