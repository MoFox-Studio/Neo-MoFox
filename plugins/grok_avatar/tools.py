"""grok_avatar LLM Tool。

供 LLM 直接调用的头像生成工具：支持 QQ 头像、聊天记录图片和指定图片，
走「图片 → 图像模型 → 可选谷歌圆环」链路，生成后直接发送到当前聊天流。
"""

from __future__ import annotations

from typing import Annotated, Literal

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image
from src.core.components.base.tool import BaseTool

from .config import GrokAvatarConfig
from .service import GrokAvatarService

logger = get_logger("grok_avatar.tool")

#: GrokAvatarService 服务签名
_SERVICE_SIGNATURE = "grok_avatar:service:grok_avatar"


class GrokAvatarTool(BaseTool):
    """生成 Grok bot 风格头像并发送。"""

    name = "grok_avatar"
    description = (
        "把 QQ 头像、聊天记录图片或指定图片转换成 Grok bot 风格的极简 2D 机器人图标并发送。"
        "两种模式：plain=直接发送生成结果；google_ring=生成后再叠加 Google 四色圆环发送。"
        "默认使用当前聊天对象的 QQ 头像；source=chat_history 时使用聊天记录图片；"
        "source=specified 时使用 image 参数。"
        "当用户说把刚发的图片、这张图或聊天记录中的图片变成头像时，"
        "直接调用 source=chat_history，不要先调用 get_image、网页下载或图片反搜工具。"
    )

    async def execute(
        self,
        mode: Annotated[
            Literal["plain", "google_ring"],
            "plain=直接发送 Grok bot 头像；google_ring=叠加谷歌四色圆环后再发送",
        ] = "plain",
        qq_number: Annotated[
            str | None,
            "目标 QQ 号（纯数字）。省略时使用当前聊天流关联的用户",
        ] = None,
        source: Annotated[
            Literal["avatar", "chat_history", "specified"],
            "图片来源：avatar=QQ头像；chat_history=当前聊天记录图片；specified=指定图片",
        ] = "avatar",
        image: Annotated[
            str | None,
            "指定图片：URL、data URL、base64、base64|... 或本地路径；source=specified 时必填",
        ] = None,
        history_index: Annotated[
            int,
            "聊天记录图片索引，0 表示最新一张，1 表示上一张；仅 source=chat_history 生效",
        ] = 0,
    ) -> tuple[bool, str]:
        """生成头像并发送到当前聊天流。

        Args:
            mode: 输出模式。
            qq_number: 目标 QQ 号，可选。
            source: 图片来源。
            image: source=specified 时的图片内容。
            history_index: 聊天记录图片索引，按最新图片倒序。

        Returns:
            (是否成功, 结果说明)
        """
        cfg = self.plugin.config
        if not isinstance(cfg, GrokAvatarConfig) or not cfg.plugin.enabled:
            return False, "grok_avatar 插件未启用。"

        from src.app.plugin_system.api import service_api

        service = service_api.get_service(_SERVICE_SIGNATURE)
        if not isinstance(service, GrokAvatarService):
            return False, "GrokAvatarService 服务未注册。"

        output_mode = "google_ring" if mode == "google_ring" else "plain"
        target = self._resolve_qq(qq_number)
        if source == "avatar":
            if not target:
                return False, "无法确定目标 QQ 号，请显式提供 qq_number 参数。"
            if not target.isdigit():
                return False, f"QQ 号必须是纯数字：{target}"
            ok, result = await service.make_avatar(target, cfg, output_mode)
            source_label = f"{target} 的头像"
        elif source == "specified":
            if not image or not image.strip():
                return False, "source=specified 时必须提供 image 参数。"
            try:
                image_bytes = await service.resolve_specified_image(image)
            except RuntimeError as e:
                return False, f"指定图片解析失败：{e}"
            ok, result = await service.make_avatar_from_bytes(image_bytes, cfg, output_mode)
            source_label = "指定图片"
        elif source == "chat_history":
            stream_id = self.get_current_stream_id()
            try:
                image_bytes = await service.resolve_chat_image(stream_id, history_index)
            except RuntimeError as e:
                return False, f"聊天记录图片解析失败：{e}"
            ok, result = await service.make_avatar_from_bytes(image_bytes, cfg, output_mode)
            source_label = f"聊天记录图片（第 {history_index + 1} 张最新图片）"
        else:
            return False, f"不支持的图片来源：{source}"
        if not ok:
            return False, f"生成失败：{result}"

        stream_id = self.get_current_stream_id()
        if not stream_id:
            return False, "无法确定发送目标聊天流。"

        reply_to = None
        message = self.trigger_message
        if message is not None:
            reply_to = str(getattr(message, "message_id", "") or "") or None

        sent = await send_image(
            image_data=result,
            stream_id=stream_id,
            reply_to=reply_to,
        )
        if not sent:
            return False, "图片发送失败。"
        return True, f"已发送 {source_label} 的 Grok bot 头像（{mode}）。"

    def _resolve_qq(self, qq_number: str | None) -> str:
        """解析目标 QQ 号：显式参数优先，其次触发消息发送者。"""
        explicit = (qq_number or "").strip()
        if explicit:
            return explicit
        message = self.trigger_message
        if message is not None:
            sender = str(getattr(message, "sender_id", "") or "").strip()
            if sender:
                return sender
        return ""
