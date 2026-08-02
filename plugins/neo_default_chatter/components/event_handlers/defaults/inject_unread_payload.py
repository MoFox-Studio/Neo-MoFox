"""``:inject_unread_payload`` 默认实现——内联图片或纯文本写入 USER payload。

镜像 DFC 的多模态定位思路：原生多模态开启时，先把 ``[图片(media_id)]`` /
``[图片(media_id):description]`` 占位符转换为内部标记，再按 media_id 在所有
未读消息中精确查找对应图片并内联，避免全局顺序匹配导致的多模态错位。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import Content, LLMPayload, LLMUsable, ROLE, Text
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent
from ....utils.multimodal import (
    get_image_media_list,
    inline_message_images_into_text,
    tokenize_message_scoped_image_placeholders,
)

logger = get_logger("neo_default_chatter.defaults.inject_unread_payload")


class InjectUnreadPayloadDefaultHandler(BaseEventHandler):
    """:inject_unread_payload 的默认实现。

    若 ``skip=False``，按 ``native_multimodal`` 开关把 USER payload 注入到共享的
    ``response`` 对象上（直接调用 ``response.add_payload`` 修改其内部状态）。
    """

    name = "inject_unread_payload_default"
    description = "默认 inject_unread_payload：内联图片或纯文本写入 USER payload"
    weight = 0
    init_subscribe = [NdfcEvent.INJECT_UNREAD_PAYLOAD]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 inject_unread_payload，把 USER payload 写入 response。"""
        if params.get("skip"):
            return EventDecision.SUCCESS, params
        try:
            response = params["response"]
            formatted_text = params.get("formatted_text") or ""
            unread_msgs = params.get("unread_msgs") or []
            native_multimodal = bool(params.get("native_multimodal"))

            if native_multimodal and unread_msgs:
                scoped_text = tokenize_message_scoped_image_placeholders(
                    formatted_text, unread_msgs
                )
                content_list: list[Content | LLMUsable] = (
                    inline_message_images_into_text(scoped_text, unread_msgs)
                )
                image_count = sum(
                    len(get_image_media_list(msg)) for msg in unread_msgs
                )
                if image_count:
                    logger.debug(f"已按 media_id 内联 {image_count} 张图片")
            else:
                content_list = [Text(formatted_text)]

            response.add_payload(LLMPayload(ROLE.USER, content_list))
            return EventDecision.SUCCESS, params
        except Exception:
            # response.add_payload 失败会让 USER payload 缺失，LLM 请求会缺少本轮输入。
            # 让 EventBus 降级为 PASS，session 不会重复注入。
            return EventDecision.PASS, params


__all__ = ["InjectUnreadPayloadDefaultHandler"]
