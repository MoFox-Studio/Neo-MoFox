"""grok_avatar 插件入口。

组件注册由 manifest ``include`` 驱动：

- ``grok_avatar`` Service：核心生成链路（头像下载 → 模型 → 圆环）；
- ``grok_avatar`` Tool：供 LLM 直接调用的头像生成工具。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import GrokAvatarConfig
from .service import GrokAvatarService
from .tools import GrokAvatarTool

logger = get_logger("grok_avatar.plugin")


@register_plugin
class GrokAvatarPlugin(BasePlugin):
    """Grok bot 风格头像生成插件。"""

    plugin_name = "grok_avatar"
    configs = [GrokAvatarConfig]

    def get_components(self) -> list[type]:
        """按配置返回组件列表。"""
        cfg = self.config if isinstance(self.config, GrokAvatarConfig) else GrokAvatarConfig()
        if not cfg.plugin.enabled:
            logger.info("grok_avatar 插件未启用")
            return []
        components: list[type] = [GrokAvatarService]
        if cfg.plugin.tool_enabled:
            components.append(GrokAvatarTool)
        return components

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后的初始化。"""
        cfg = self.config if isinstance(self.config, GrokAvatarConfig) else None
        if cfg is not None and not cfg.api.base_url.strip():
            logger.warning(
                "grok_avatar 尚未配置图像模型 API 地址；"
                "请在 config/plugins/grok_avatar/config.toml 的 [api] 节填入 base_url / api_key / model"
            )
        logger.info("grok_avatar 插件已加载")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前的清理。"""
        logger.info("grok_avatar 插件已卸载")
