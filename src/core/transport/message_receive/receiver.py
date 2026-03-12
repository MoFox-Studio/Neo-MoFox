"""消息接收器。

``MessageReceiver`` 是消息接收管线的入口。当 ``CoreSink`` 收到适配器传入的
``MessageEnvelope`` 后，通过本模块完成：

1. 方向校验（必须为 incoming）
2. 区分普通消息与其他类型
3. 调用 ``MessageConverter`` 转换为 ``Message``
4. 通过事件系统分发给下游

对于非标准消息类型（notice、request 等），触发 ``on_received_other_message``
事件并检查订阅者是否填充了 ``processed`` 字段；若有则构建简化 Message 继续分发。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from mofox_wire import MessageEnvelope
from rich.markup import escape

from src.core.components.types import EventType
from src.core.models.message import Message, MessageType
from src.core.transport.message_receive.converter import MessageConverter
from src.core.transport.message_receive.utils import (
    extract_stream_id,
    infer_chat_type,
)
from src.kernel.logger import get_logger, COLOR

logger = get_logger("message_receiver", display="消息接收器", color=COLOR.CYAN)

# 核心能够作为标准消息解析处理的 message_type 白名单。
# 只有 message_info["message_type"] 在此集合内的 envelope 才会进入 _handle_message；
# 其余已知类型（notice、request、meta_event 等）以及未知类型均路由至 _handle_other。
# 对于未显式设置 message_type 的老式 envelope，回退到 has_segments 判断以保持兼容。
_STANDARD_MESSAGE_TYPES: frozenset[str] = frozenset({"message", "group", "private"})


class MessageReceiver:
    """消息接收器。

    负责接收并分发来自适配器的 ``MessageEnvelope``。

    Attributes:
        _converter: 消息转换器实例
        _event_manager: 事件管理器引用（延迟获取以避免循环导入）

    Examples:
        >>> receiver = MessageReceiver()
        >>> await receiver.receive_envelope(envelope, "my_plugin:adapter:qq")
    """

    def __init__(self, converter: MessageConverter | None = None) -> None:
        """初始化消息接收器。

        Args:
            converter: 消息转换器实例，为 None 时内部自动创建
        """
        self._converter = converter or MessageConverter()
        self._event_manager: Any = None
        logger.info("MessageReceiver 初始化完成")

    def _get_event_manager(self) -> Any:
        """延迟获取事件管理器（避免模块导入时的循环依赖）。"""
        if self._event_manager is None:
            from src.core.managers.event_manager import get_event_manager

            self._event_manager = get_event_manager()
        return self._event_manager

    async def _update_person_info(self, message: Message) -> None:
        """更新用户信息。

        Args:
            message: 待写入数据库的 Message 实例
        """
        from src.core.utils.user_query_helper import get_user_query_helper

        # 1. 更新用户信息
        await get_user_query_helper().update_person_info(
            platform=message.platform,
            user_id=message.sender_id,
            nickname=message.sender_name,
            cardname=message.sender_cardname,
        )

    # ──────────────────────────────────────────
    # 公共接口
    # ──────────────────────────────────────────

    async def receive_envelope(
        self,
        envelope: MessageEnvelope,
        adapter_signature: str,
    ) -> None:
        """接收来自适配器的 MessageEnvelope 并处理。

        这是与 ``SinkManager`` 集成的唯一入口。

        Args:
            envelope: mofox-wire 消息信封
            adapter_signature: 发送方适配器签名（如 ``"my_plugin:adapter:qq"``）
        """
        # 方向校验
        direction = envelope.get("direction", "incoming")
        if direction not in ("incoming",):
            logger.debug(
                f"忽略非 incoming 方向的消息: direction={direction}, "
                f"adapter={adapter_signature}"
            )
            return

        msg_info = envelope.get("message_info")
        if msg_info is None:
            logger.warning(f"收到缺少 message_info 的 envelope: adapter={adapter_signature}")
            return

        message_id = msg_info.get("message_id", "?")
        platform = msg_info.get("platform", "?")
        logger.debug(
            f"收到消息: id={message_id}, platform={platform}, "
            f"adapter={adapter_signature}"
        )

        # 检查是否为adapter_response类型
        message_segment = envelope.get("message_segment")
        if isinstance(message_segment, dict) and message_segment.get("type") == "adapter_response":
            await self._handle_adapter_response(envelope)
            return

        # 优先根据 message_type 路由：
        #   - 在白名单内 → _handle_message（核心可解析的标准消息）
        #   - 已设置但不在白名单 → _handle_other（notice / request / meta_event 等）
        #   - 未设置 → 退化到 has_segments 判断，保持对旧格式适配器的兼容
        message_type = msg_info.get("message_type")
        if message_type is not None:
            if message_type in _STANDARD_MESSAGE_TYPES:
                await self._handle_message(envelope, adapter_signature)
            else:
                await self._handle_other(envelope, adapter_signature)
            return

        # message_type 未设置时：有消息段则视为标准消息，否则路由至 _handle_other
        has_segments = (
            envelope.get("message_segment") is not None  # type: ignore[arg-type]
            or envelope.get("message_chain") is not None  # type: ignore[arg-type]
        )
        if has_segments:
            await self._handle_message(envelope, adapter_signature)
        else:
            await self._handle_other(envelope, adapter_signature)

    # ──────────────────────────────────────────
    # 内部处理
    # ──────────────────────────────────────────

    async def _handle_message(
        self,
        envelope: MessageEnvelope,
        adapter_signature: str,
    ) -> None:
        """处理标准消息：转换并触发 ON_MESSAGE_RECEIVED 事件。"""
        try:
            message = await self._converter.envelope_to_message(envelope)
        except Exception as e:
            msg_id = envelope.get("message_info", {}).get("message_id", "?")
            logger.error(
                f"消息转换失败: id={msg_id}, adapter={adapter_signature}, "
                f"error={e}",
                exc_info=True,
            )
            return

        # 构建人类可读的日志 (Rich 格式)
        msg_info = envelope.get("message_info", {})
        platform = str(msg_info.get("platform", "unknown"))
        group_info = msg_info.get("group_info")
        sender_display = escape(message.sender_cardname or message.sender_name or message.sender_id)
        sender_colored = f"[#89DCEB]{sender_display}[/#89DCEB]"

        if group_info:
            stream_name = escape(group_info.get("group_name") or group_info.get("group_id", ""))
            location = f"[#F092B0]{stream_name}[/#F092B0] | {sender_colored}"
        else:
            location = sender_colored

        content_preview = escape((message.processed_plain_text or str(message.content) or "")[:80])
        content_colored = f"[#A6E3A1]{content_preview}[/#A6E3A1]"
        logger.info(f"<[b]{escape(platform)}[/b]> {location}: {content_colored}")

        await self._update_person_info(message)

        event_manager = self._get_event_manager()
        await event_manager.publish_event(
            EventType.ON_MESSAGE_RECEIVED,
            {
                "message": message,
                "envelope": envelope,
                "adapter_signature": adapter_signature,
            },
        )

    async def _handle_adapter_response(self, envelope: MessageEnvelope) -> None:
        """处理适配器响应消息。
        
        Args:
            envelope: 响应消息信封
        """
        from src.core.managers.adapter_manager import _set_adapter_response
        
        message_segment = envelope.get("message_segment")
        if not isinstance(message_segment, dict):
            return
        
        seg_data = message_segment.get("data")
        if not isinstance(seg_data, dict):
            return
            
        request_id = seg_data.get("request_id")
        response = seg_data.get("response", {})
        
        if request_id:
            logger.debug(f"收到适配器响应: request_id={request_id}")
            _set_adapter_response(str(request_id), response)
        else:
            logger.warning("收到没有request_id的adapter_response")

    async def _handle_other(
        self,
        envelope: MessageEnvelope,
        adapter_signature: str,
    ) -> None:
        """处理非标准消息：触发 ON_RECEIVED_OTHER_MESSAGE 事件。

        订阅者可以通过填充 ``params["processed"]`` 字段将消息纳入标准流程。
        """
        params: Dict[str, Any] = {
            "raw": dict(envelope),
            "processed": "",
        }

        event_manager = self._get_event_manager()
        result = await event_manager.publish_event(
            EventType.ON_RECEIVED_OTHER_MESSAGE,
            params,
        )

        final_params: Dict[str, Any] = result.get("params", params)
        processed: str = final_params.get("processed", "")

        if not processed:
            logger.debug(
                f"其他类型消息未被处理，已丢弃: adapter={adapter_signature}"
            )
            return

        # processed 非空 → 构建简化 Message 并触发 ON_MESSAGE_RECEIVED
        msg_info = envelope.get("message_info", {})
        user_info = msg_info.get("user_info") or {}
        extra_data = msg_info.get("extra") or {}

        simple_message = Message(
            message_id=msg_info.get("message_id", ""),
            time=msg_info.get("time", time.time()),
            content=processed,
            processed_plain_text=processed,
            message_type=MessageType.UNKNOWN,
            sender_id=user_info.get("user_id", ""),
            sender_name=user_info.get("user_nickname", ""),
            sender_cardname=user_info.get("user_cardname"),
            platform=msg_info.get("platform", ""),
            chat_type=infer_chat_type(msg_info),
            stream_id=extract_stream_id(msg_info),
            raw_data=envelope.get("raw_message"),
            **extra_data,
        )

        logger.info(
            f"其他类型消息经事件处理器转换: id={simple_message.message_id}, "
            f"processed_len={len(processed)}"
        )

        await self._update_person_info(simple_message)

        await event_manager.publish_event(
            EventType.ON_MESSAGE_RECEIVED,
            {
                "message": simple_message,
                "envelope": envelope,
                "adapter_signature": adapter_signature,
            },
        )
