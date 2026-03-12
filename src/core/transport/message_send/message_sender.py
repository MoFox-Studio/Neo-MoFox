"""消息发送器。

负责将 Message 发送到正确的 Adapter。
参考 old/chat/message_receive/uni_message_sender.py 的设计。
"""

from typing import TYPE_CHECKING, Any

from mofox_wire import MessageEnvelope

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.message import Message

logger = get_logger("message_sender")


class MessageSender:
    """消息发送器。

    负责将 Message 发送到 Adapter。

    职责：
    1. 使用 MessageConverter 将 Message 转换为 MessageEnvelope
    2. 根据 platform 推断目标 Adapter
    3. 通过 AdapterManager 获取 Adapter 实例
    4. 调用 Adapter._send_platform_message() 发送消息
    5. 触发发送事件

    Attributes:
        _converter: 消息转换器
        _adapter_manager: 适配器管理器引用

    Examples:
        >>> sender = MessageSender()
        >>> success = await sender.send_message(message)
    """

    def __init__(self) -> None:
        """初始化消息发送器。"""
        from src.core.transport.message_receive.converter import MessageConverter

        self._converter = MessageConverter()
        self._adapter_manager: Any = None
        logger.info("MessageSender 初始化完成")

    def set_adapter_manager(self, adapter_manager: Any) -> None:
        """设置适配器管理器引用。

        Args:
            adapter_manager: 适配器管理器实例

        Examples:
            >>> sender.set_adapter_manager(get_adapter_manager())
        """
        self._adapter_manager = adapter_manager
        logger.debug("MessageSender 设置适配器管理器")

    async def send_message(
        self,
        message: "Message",
        adapter_signature: str | None = None,
    ) -> bool:
        """发送消息到 Adapter。

        Args:
            message: 待发送的消息
            adapter_signature: 目标适配器签名（None 表示自动推断）

        Returns:
            bool: 是否发送成功

        Raises:
            ValueError: 如果消息格式不正确或无法确定目标 Adapter

        Examples:
            >>> success = await sender.send_message(message)
            >>> success = await sender.send_message(message, "my_plugin:adapter:qq")
        """
        try:
            # 1. 确定目标 Adapter
            if not adapter_signature:
                adapter_signature = self._infer_adapter_signature(message)

            if not adapter_signature:
                logger.error(
                    f"无法确定目标 Adapter: platform={message.platform}, "
                    f"message_id={message.message_id}"
                )
                return False

            # 2. 获取 Adapter 实例
            if not self._adapter_manager:
                from src.core.managers.adapter_manager import get_adapter_manager

                self._adapter_manager = get_adapter_manager()

            adapter = self._adapter_manager.get_adapter(adapter_signature)

            if not adapter:
                logger.error(
                    f"Adapter 未找到: {adapter_signature}, "
                    f"message_id={message.message_id}"
                )
                return False

            # 3. 使用 bot 信息覆盖 sender 字段
            await self._apply_bot_sender_info(message, adapter)

            # 4. 转换为 MessageEnvelope
            envelope = await self._converter.message_to_envelope(message)

            # 5. 触发发送事件，检查是否被拦截
            should_send = await self._emit_send_event(message, envelope, adapter_signature)
            
            if not should_send:
                logger.info(
                    f"消息被事件处理器拦截，取消发送: {message.message_id}"
                )
                return True  # 返回成功，因为拦截是预期行为

            # 6. 发送
            await adapter._send_platform_message(envelope)

            # 7. 写入历史消息
            await self._persist_sent_message_to_history(message)

            # 提取消息文本用于日志
            msg_text = (
                message.processed_plain_text
                or (message.content if isinstance(message.content, str) else "")
                or "(无文本内容)"
            )
            # 日志中截断超长内容
            if len(msg_text) > 100:
                msg_text = msg_text[:100] + "..."

            logger.debug(
                f"消息发送成功: {message.message_id} → {adapter_signature}"
            )
            logger.info(f'消息发送成功: [dim]{msg_text}[/dim]')

            return True

        except ValueError as e:
            logger.error(f"消息格式错误: {e}")
            return False
        except Exception as e:
            logger.error(
                f"发送消息失败: message_id={message.message_id}, error={e}",
                exc_info=True,
            )
            return False

    async def _apply_bot_sender_info(self, message: "Message", adapter: Any) -> None:
        """在发送前将消息发送者信息设置为 Bot 信息。"""
        try:
            bot_info: dict[str, Any] = {}
            bot_info = await adapter.get_bot_info()

            bot_id = str(bot_info.get("bot_id", "") or "")
            bot_nickname = str(bot_info.get("bot_nickname", "") or "")

            if bot_id:
                message.sender_id = bot_id
            if bot_nickname:
                message.sender_name = bot_nickname
                if not message.sender_cardname:
                    message.sender_cardname = bot_nickname
        except Exception as e:
            logger.warning(
                f"获取 Bot sender 信息失败，保留原 sender: message_id={message.message_id}, error={e}"
            )

    async def _persist_sent_message_to_history(self, message: "Message") -> None:
        """发送成功后，将消息写入聊天流历史。"""
        if not message.stream_id:
            logger.warning(
                f"发送消息缺少 stream_id，跳过历史写入: message_id={message.message_id}"
            )
            return

        from src.core.managers.stream_manager import get_stream_manager

        sm = get_stream_manager()

        group_id = str(
            message.extra.get("target_group_id")
            or message.extra.get("group_id")
            or ""
        )
        user_id = str(message.extra.get("target_user_id") or "")

        await sm.get_or_create_stream(
            stream_id=message.stream_id,
            platform=message.platform,
            user_id=user_id,
            group_id=group_id,
            chat_type=message.chat_type,
        )
        await sm.add_sent_message_to_history(message)

    def _infer_adapter_signature(self, message: "Message") -> str | None:
        """推断目标 Adapter 签名。

        根据 message.platform 查找匹配的 Adapter。

        Args:
            message: 消息对象

        Returns:
            str | None: Adapter 签名，如果未找到则返回 None
        """
        try:
            from src.core.components.registry import get_global_registry
            from src.core.components.types import ComponentType

            registry = get_global_registry()
            adapters = registry.get_by_type(ComponentType.ADAPTER)

            # 查找匹配平台的 Adapter
            for sig, adapter_cls in adapters.items():
                if hasattr(adapter_cls, "platform") and adapter_cls.platform == message.platform:
                    logger.debug(
                        f"推断 Adapter 签名: {sig} (platform={message.platform})"
                    )
                    return sig

            logger.warning(
                f"未找到匹配的 Adapter: platform={message.platform}"
            )
            return None

        except Exception as e:
            logger.error(f"推断 Adapter 签名失败: {e}")
            return None

    async def _emit_send_event(
        self,
        message: "Message",
        envelope: MessageEnvelope,
        adapter_signature: str,
    ) -> bool:
        """触发消息发送事件。

        Args:
            message: 消息对象
            envelope: 消息信封
            adapter_signature: 适配器签名
            
        Returns:
            bool: 是否应该继续发送（False 表示被事件处理器拦截）
        """
        try:
            # 尝试从事件管理器获取
            from src.core.managers.event_manager import get_event_manager
            from src.core.components.types import EventType
            from src.kernel.event import EventDecision

            event_mgr = get_event_manager()
            result = await event_mgr.publish_event(
                EventType.ON_MESSAGE_SENT,
                {
                    "message": message,
                    "envelope": envelope,
                    "adapter_signature": adapter_signature,
                },
            )
            
            # 检查事件决策，如果被拦截则返回 False
            decision = result.get("decision")
            if decision == EventDecision.STOP:
                return False
                
            return True
            
        except Exception as e:
            logger.warning(f"触发发送事件失败: {e}")
            return True  # 异常时默认继续发送


# 全局单例
_global_message_sender: "MessageSender | None" = None


def get_message_sender() -> MessageSender:
    """获取全局 MessageSender 单例。

    Returns:
        MessageSender: 全局 MessageSender 单例

    Examples:
        >>> sender = get_message_sender()
    """
    global _global_message_sender
    if _global_message_sender is None:
        _global_message_sender = MessageSender()
    return _global_message_sender


def set_message_sender(sender: MessageSender) -> None:
    """设置全局 MessageSender 单例。

    Args:
        sender: MessageSender 实例

    Examples:
        >>> set_message_sender(MessageSender())
    """
    global _global_message_sender
    _global_message_sender = sender


def reset_message_sender() -> None:
    """重置全局 MessageSender。

    主要用于测试场景，确保测试之间不会相互影响。

    Examples:
        >>> reset_message_sender()
    """
    global _global_message_sender
    _global_message_sender = None


__all__ = [
    "MessageSender",
    "get_message_sender",
    "set_message_sender",
    "reset_message_sender",
]
