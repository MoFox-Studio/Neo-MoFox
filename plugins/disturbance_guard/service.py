"""disturbance_guard 服务层。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.core.models.message import Message
from src.kernel.llm import ROLE, LLMPayload, LLMRequest, Text

from .config import DisturbanceGuardConfig

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers.stream_manager import StreamManager
    from src.core.models.stream import ChatStream

logger = get_logger("disturbance_guard_service")

_INTENT_SYSTEM_PROMPT = """\
你是一个意图分类器。判断用户消息是否属于以下两种意图之一：

1. **quiet**：用户表示自己要忙、不想被打扰、暂时离开。
   例如："我先忙一下"、"别吵"、"晚点聊"、"先不聊了"、"我去忙了"、"先别烦我"。

2. **wake**：用户表示自己回来了、可以继续聊天或者直接发新消息。
   例如："我回来了"、"继续聊"、"现在有空"、"在吗"、"可以聊了"。

如果都不属于，回复 **none**。

只回复一个词：quiet、wake 或 none。不要解释。\
"""


class _Intent(Enum):
    """LLM 判定的用户意图。"""

    QUIET = "quiet"
    WAKE = "wake"
    NONE = "none"


@dataclass(slots=True)
class DisturbanceGuardDecision:
    """打扰感知判定结果。"""

    should_suppress: bool
    reason: str


class DisturbanceGuardService:
    """打扰感知服务。

    职责：
    1. 通过 LLM 识别用户消息中的免打扰/唤醒意图。
    2. 通过 StreamManager 公共 API 维护流的免打扰状态。
    3. 在需要静默时将消息直接写入 history，而不是进入 unread。
    """

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化打扰感知服务。"""
        self.plugin = plugin

    def _get_config(self) -> DisturbanceGuardConfig:
        """获取已绑定插件配置。

        Raises:
            TypeError: 插件配置类型不匹配时，说明组装层存在问题。
        """
        if not isinstance(self.plugin.config, DisturbanceGuardConfig):
            raise TypeError(
                f"disturbance_guard 插件配置类型错误: "
                f"期望 DisturbanceGuardConfig, 实际 {type(self.plugin.config).__name__}"
            )
        return self.plugin.config

    @staticmethod
    def _normalize_text(message: Message) -> str:
        """提取并规范化消息纯文本。"""
        raw_text = message.processed_plain_text
        if not raw_text and isinstance(message.content, str):
            raw_text = message.content
        if not raw_text:
            return ""
        return " ".join(str(raw_text).strip().split())

    def _is_scope_enabled(self, chat_type: str) -> bool:
        """检查当前聊天类型是否启用了打扰感知。"""
        guard = self._get_config().guard
        normalized_chat_type = str(chat_type or "").lower()
        if normalized_chat_type == "private":
            return bool(guard.apply_to_private_chat)
        if normalized_chat_type == "group":
            return bool(guard.apply_to_group_chat)
        return False

    async def _classify_intent(self, text: str) -> _Intent:
        """通过 LLM 判定用户消息的意图。

        Args:
            text: 规范化后的用户消息文本

        Returns:
            _Intent: quiet / wake / none
        """
        from src.core.config import get_model_config

        config = self._get_config()
        model_set = get_model_config().get_task(config.guard.model_task)

        request = LLMRequest(model_set, request_name="disturbance_guard_intent")
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(_INTENT_SYSTEM_PROMPT)))
        request.add_payload(LLMPayload(ROLE.USER, Text(text)))

        try:
            response = await asyncio.wait_for(
                request.send(stream=False),
                timeout=config.guard.llm_timeout,
            )
            response_text: str = await response
            result = response_text.strip().lower()

            if "quiet" in result:
                return _Intent.QUIET
            if "wake" in result:
                return _Intent.WAKE
            return _Intent.NONE

        except asyncio.TimeoutError:
            logger.warning("LLM 意图判定超时，默认放行")
            return _Intent.NONE
        except Exception as exc:
            logger.warning(f"LLM 意图判定异常: {exc}，默认放行")
            return _Intent.NONE

    async def _get_chat_stream(self, message: Message) -> ChatStream:
        """获取或创建消息所属聊天流。"""
        stream_manager = self._get_stream_manager()
        group_id = ""
        group_name = ""
        if isinstance(message.extra, dict):
            raw_group_id = message.extra.get("group_id")
            raw_group_name = message.extra.get("group_name")
            group_id = str(raw_group_id or "")
            group_name = str(raw_group_name or "")

        user_id = message.sender_id if str(message.chat_type).lower() != "group" else ""
        return await stream_manager.get_or_create_stream(
            stream_id=message.stream_id,
            platform=message.platform,
            user_id=user_id,
            group_id=group_id,
            group_name=group_name,
            chat_type=message.chat_type,
        )

    @staticmethod
    def _get_stream_manager() -> StreamManager:
        """获取 StreamManager 实例。

        延迟导入以避免循环依赖。
        """
        from src.core.managers import get_stream_manager

        return get_stream_manager()

    async def _persist_silently(self, message: Message) -> None:
        """将消息静默写入历史，不进入 unread。"""
        stream_manager = self._get_stream_manager()
        await stream_manager.add_received_message_to_history(message)

    async def handle_message(
        self,
        message: Message,
    ) -> DisturbanceGuardDecision:
        """处理单条消息并返回是否需要抑制分发。"""
        config = self._get_config()
        if not config.plugin.enabled:
            return DisturbanceGuardDecision(False, "plugin disabled")

        if not self._is_scope_enabled(message.chat_type):
            return DisturbanceGuardDecision(False, "chat scope disabled")

        normalized_text = self._normalize_text(message)
        if not normalized_text:
            return DisturbanceGuardDecision(False, "empty text")

        # 确保流已创建
        await self._get_chat_stream(message)
        stream_manager = self._get_stream_manager()
        stream_id = message.stream_id
        now = time.time()

        dnd_active = stream_manager.is_stream_do_not_disturb_active(stream_id)

        # 免打扰已过期，通过 manager 清理残留字段
        if not dnd_active:
            stream_manager.clear_stream_do_not_disturb(stream_id)

        # 当前处于免打扰状态
        if dnd_active:
            intent = await self._classify_intent(normalized_text)
            if intent is _Intent.WAKE:
                stream_manager.clear_stream_do_not_disturb(stream_id)
                return DisturbanceGuardDecision(False, "wake intent matched")

            await self._persist_silently(message)
            return DisturbanceGuardDecision(True, "stream is in do-not-disturb")

        # 当前未处于免打扰状态，判断是否要进入
        intent = await self._classify_intent(normalized_text)
        if intent is not _Intent.QUIET:
            return DisturbanceGuardDecision(False, "no quiet intent matched")

        quiet_until = now + float(config.guard.quiet_minutes) * 60.0
        stream_manager.set_stream_do_not_disturb(
            stream_id,
            until=quiet_until,
            reason=normalized_text,
            trigger_message_id=message.message_id or None,
        )
        await self._persist_silently(message)
        return DisturbanceGuardDecision(True, "quiet intent matched")

