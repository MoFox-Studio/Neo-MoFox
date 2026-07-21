"""skill_manager 命令组件。"""

from __future__ import annotations

from typing import Protocol, cast

from ..models import SkillEntry

from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel


class _SkillManagerPluginProtocol(Protocol):
    """SkillManagerCommand 依赖的最小插件接口。"""

    skills: dict[str, SkillEntry]

    async def refresh_skill_catalog(self) -> None:
        """刷新 skill 索引。"""


_USAGE = """/skill 用法：
  /skill list | 列表      - 列出当前已索引的 skill
  /skill refresh | 刷新   - 重新扫描并刷新 skill 索引
  /skill help | 帮助      - 显示本帮助"""


class SkillManagerCommand(BaseCommand):
    """SkillManager 管理命令。"""

    name: str = "skill"
    description: str = "管理 SkillManager 索引：列出、刷新技能"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """匹配命令名，同时支持 skill 和 技能。"""

        if not parts:
            return 0
        if parts[0] in ("skill", "技能"):
            return 1
        return 0

    async def _reply(self, text: str) -> None:
        """向当前聊天流发送文本回复。"""

        await send_text(text, stream_id=self.stream_id)

    def _get_plugin(self) -> _SkillManagerPluginProtocol:
        """返回带具体类型的插件实例。"""

        return cast(_SkillManagerPluginProtocol, self.plugin)

    def _render_skill_list(self) -> str:
        """渲染当前 skill 列表文本。"""

        plugin = self._get_plugin()
        if not plugin.skills:
            return "当前没有已索引的 skill，可先执行 /skill refresh 刷新。"

        entries = sorted(plugin.skills.values(), key=lambda item: item.name.lower())
        lines = [f"当前已索引 {len(entries)} 个 skill："]
        for entry in entries:
            lines.append(
                f"- {entry.name}: {entry.description} "
                f"(文件 {len(entry.files)}，路径 {entry.root_dir.name})"
            )
        return "\n".join(lines)

    @cmd_route()
    async def handle_default(self) -> tuple[bool, str]:
        """显示帮助和当前 skill 数量。"""

        plugin = self._get_plugin()
        summary = f"当前已索引 {len(plugin.skills)} 个 skill。"
        await self._reply(f"{summary}\n\n{_USAGE}")
        return True, summary

    @cmd_route("help")
    async def handle_help(self) -> tuple[bool, str]:
        """显示帮助信息。"""

        await self._reply(_USAGE)
        return True, "help"

    @cmd_route("帮助")
    async def handle_help_cn(self) -> tuple[bool, str]:
        """显示帮助信息（中文别名）。"""

        return await self.handle_help()

    @cmd_route("list")
    async def handle_list(self) -> tuple[bool, str]:
        """列出当前已索引的 skill。"""

        rendered = self._render_skill_list()
        await self._reply(rendered)
        return True, rendered

    @cmd_route("列表")
    async def handle_list_cn(self) -> tuple[bool, str]:
        """列出当前已索引的 skill（中文别名）。"""

        return await self.handle_list()

    @cmd_route("refresh")
    async def handle_refresh(self) -> tuple[bool, str]:
        """重新扫描配置路径并刷新 skill 索引。"""

        plugin = self._get_plugin()
        await plugin.refresh_skill_catalog()
        message = f"已刷新 skill 索引，共 {len(plugin.skills)} 个。"
        await self._reply(message)
        return True, message

    @cmd_route("刷新")
    async def handle_refresh_cn(self) -> tuple[bool, str]:
        """重新扫描配置路径并刷新 skill 索引（中文别名）。"""

        return await self.handle_refresh()