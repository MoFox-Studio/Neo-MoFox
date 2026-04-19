"""disturbance_guard 服务层。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.core.models.message import Message

from .config import DisturbanceGuardConfig

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream

logger = get_logger("disturbance_guard_service")


@dataclass(slots=True)
class DisturbanceGuardDecision:
    """打扰感知判定结果。"""

    should_suppress: bool
    reason: str


class DisturbanceGuardService:
    """打扰感知服务。

    职责：
    1. 识别“先忙/别吵/晚点聊”等免打扰意图。
    2. 识别“我回来了/继续聊”等唤醒意图。
    3. 维护 stream 上下文中的免打扰状态。
    4. 在需要静默时将消息直接写入 history，而不是进入 unread。
    """

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化打扰感知服务。"""
        self.plugin = plugin
        self._quiet_patterns: list[re.Pattern[str]] | None = None
        self._wake_patterns: list[re.Pattern[str]] | None = None

    def _get_config(self) -> DisturbanceGuardConfig:
        """获取已绑定插件配置。"""
        if isinstance(self.plugin.config, DisturbanceGuardConfig):
            return self.plugin.config
        return DisturbanceGuardConfig()

    def _get_quiet_patterns(self) -> list[re.Pattern[str]]:
        """获取免打扰触发模式列表。"""
        if self._quiet_patterns is None:
            self._quiet_patterns = self._compile_patterns(
                self._get_config().guard.quiet_intent_patterns
            )
        return self._quiet_patterns

    def _get_wake_patterns(self) -> list[re.Pattern[str]]:
        """获取唤醒模式列表。"""
        if self._wake_patterns is None:
            self._wake_patterns = self._compile_patterns(
                self._get_config().guard.wake_intent_patterns
            )
        return self._wake_patterns

    @staticmethod
    def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
        """编译正则表达式列表。"""
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning(f"忽略非法正则 {pattern!r}: {exc}")
        return compiled

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

    @staticmethod
    def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
        """检查文本是否命中任一正则。"""
        return any(pattern.search(text) for pattern in patterns)

    async def _get_chat_stream(self, message: Message) -> ChatStream:
        """获取或创建消息所属聊天流。"""
        from src.core.managers import get_stream_manager

        stream_manager = get_stream_manager()
        group_id = ""
        group_name = ""
        if hasattr(message, "extra") and isinstance(message.extra, dict):
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

    async def _get_stream_manager(self):
        """获取 StreamManager 实例。

        延迟导入以避免循环依赖。
        """
        from src.core.managers import get_stream_manager

        return get_stream_manager()

    async def _persist_silently(self, message: Message) -> None:
        """将消息静默写入历史，不进入 unread。"""
        stream_manager = await self._get_stream_manager()
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
        stream_manager = await self._get_stream_manager()
        stream_id = message.stream_id
        now = time.time()

        # 免打扰已过期，通过 manager 清理
        if not stream_manager.is_stream_do_not_disturb_active(stream_id):
            stream_manager.clear_stream_do_not_disturb(stream_id)

        if stream_manager.is_stream_do_not_disturb_active(stream_id):
            if self._matches_any(normalized_text, self._get_wake_patterns()):
                stream_manager.clear_stream_do_not_disturb(stream_id)
                return DisturbanceGuardDecision(False, "wake intent matched")

            await self._persist_silently(message)
            return DisturbanceGuardDecision(True, "stream is in do-not-disturb")

        if not self._matches_any(normalized_text, self._get_quiet_patterns()):
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

