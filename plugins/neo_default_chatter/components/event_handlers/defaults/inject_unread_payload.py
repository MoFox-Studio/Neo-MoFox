"""``:inject_unread_payload`` 默认实现——内联图片或纯文本写入 USER payload。

镜像 ``ConversationSession._append_user_payload`` 的原逻辑：原生多模态开启时
从未读消息提取图片按占位符内联，否则把提示文本作为纯 :class:`Text` 追加。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import Content, LLMPayload, LLMUsable, ROLE, Text
from src.kernel.event import EventDecision

from ....components.config import NeoChatterConfig
from ....utils.event_publisher import NdfcEvent
from ....utils.multimodal import extract_images_from_messages, inline_images_into_text

logger = get_logger("neo_default_chatter.defaults.inject_unread_payload")

_DEFAULT_PLACEHOLDER = "[图片-{idx}]"


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
                placeholder = self._read_placeholder()
                images = extract_images_from_messages(unread_msgs)
                content_list: list[Content | LLMUsable] = inline_images_into_text(
                    formatted_text, images, placeholder
                )
                if images:
                    logger.debug(
                        f"已内联 {len(images)} 张图片到占位符位置"
                    )
            else:
                content_list = [Text(formatted_text)]

            response.add_payload(LLMPayload(ROLE.USER, content_list))
            return EventDecision.SUCCESS, params
        except Exception:
            # response.add_payload 失败会让 USER payload 缺失，LLM 请求会缺少本轮输入。
            # 让 EventBus 降级为 PASS，session 不会重复注入。
            return EventDecision.PASS, params

    def _read_placeholder(self) -> str:
        """从插件配置读取图片占位符模板，缺失时回退默认值。"""
        config = getattr(self.plugin, "config", None)
        if isinstance(config, NeoChatterConfig):
            return str(config.plugin.image_placeholder_template)
        return _DEFAULT_PLACEHOLDER


__all__ = ["InjectUnreadPayloadDefaultHandler"]
