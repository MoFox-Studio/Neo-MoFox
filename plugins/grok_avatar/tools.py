"""grok_avatar LLM Tool。

供 LLM 直接调用的头像生成工具：以当前聊天流绑定的触发消息发送者
（或显式传入的 QQ 号）为对象，走「头像下载 → 图像模型 → 可选谷歌圆环」
链路，生成后直接发送到当前聊天流。
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
        "把一个 QQ 号的当前头像转换成 Grok bot 风格的极简 2D 机器人图标并发送。"
        "两种模式：plain=直接发送生成结果；google_ring=生成后再叠加 Google 四色圆环发送。"
        "省略 QQ 号时默认转换当前聊天对象（私聊为对方，群聊为触发消息的发送者）。"
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
    ) -> tuple[bool, str]:
        """生成头像并发送到当前聊天流。

        Args:
            mode: 输出模式。
            qq_number: 目标 QQ 号，可选。

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

        target = self._resolve_qq(qq_number)
        if not target:
            return False, "无法确定目标 QQ 号，请显式提供 qq_number 参数。"
        if not target.isdigit():
            return False, f"QQ 号必须是纯数字：{target}"

        ok, result = await service.make_avatar(
            qq_number=target,
            config=cfg,
            mode="google_ring" if mode == "google_ring" else "plain",
        )
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
        return True, f"已发送 {target} 的 Grok bot 头像（{mode}）。"

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
