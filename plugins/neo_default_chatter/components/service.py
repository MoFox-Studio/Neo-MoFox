"""Neo-Default-Chatter 会话工厂。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from ..session import ConversationSession

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

logger = get_logger("neo_default_chatter")


class NeoChatterService(BaseService):
    """Neo-Default-Chatter 会话工厂。

    暴露主会话逻辑给其他插件：第三方插件通过 ``service_api.get_service``
    拿到本 Service 实例（每次新建，非单例），再调用 :meth:`create_session`
    得到一个可 ``async for`` 的 :class:`ConversationSession`。

    会话行为完全自包含，不暴露任何运行时替换点；需要差异化「是否响应」
    或「响应前注入什么」时，通过订阅 ``neo_default_chatter:preprocess`` 事件实现。
    """

    name = "chat_core"
    description = "Neo-Default-Chatter 会话工厂，复用主会话逻辑构建自定义聊天器"

    def create_session(
        self,
        *,
        stream_id: str,
        plugin: "BasePlugin | None" = None,
    ) -> ConversationSession:
        """创建一个由 NFC 主会话逻辑驱动的会话。

        Args:
            stream_id: 目标聊天流 ID。
            plugin: 可选插件实例；为 None 时回退到本 Service 所属的 NFC 插件实例。
                会话读取 :class:`NeoChatterConfig` 与构造私有 chatter 时使用该插件。

        Returns:
            ConversationSession: 可直接 ``async for`` 驱动的会话对象，
            产出 ``Wait / Success / Failure / Stop``，接收 ``WaitResumeEvent``。
        """
        owner = plugin or self.plugin
        return ConversationSession(stream_id=stream_id, plugin=owner)
