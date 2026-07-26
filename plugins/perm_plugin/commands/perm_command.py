"""perm_plugin 权限管理命令。

提供 /perm 命令，允许机器人主人（OWNER）在聊天中查询和修改用户权限。

支持通过 @提及 或 platform:user_id 两种方式指定用户。
allow/deny/clear 支持按插件名批量操作，或使用 all 覆盖全部命令。

支持的子命令（英文 / 中文均可）：
    status / 查看  @用户                              — 查看用户权限状态
    set    / 设置  @用户 <owner|operator|user|guest>  — 设置全局权限级别
    reset  / 重置  @用户                              — 恢复默认权限
    allow  / 授权  @用户 [插件名|all]                 — 为用户授权插件的所有命令
    deny   / 禁止  @用户 [插件名|all]                 — 为用户禁止插件的所有命令
    clear  / 清除  @用户 [插件名|all]                 — 移除插件命令的覆盖设置
    list   / 名单  <插件名>                            — 查看谁有该插件权限
    plugins / 插件                                     — 列出所有插件名
    help   / 帮助                                      — 显示帮助信息

@用户 也可替换为 platform:user_id（如 qq:1919810）
"""

from __future__ import annotations

import re
from typing import cast

from src.app.plugin_system.api import command_api, permission_api
from src.app.plugin_system.api.database_api import query as db_query
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.api.stream_api import get_stream_info
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

logger = get_logger("perm_plugin.command")

# ──────────────────────────────────────────────────────────────────────────────
#  常量
# ──────────────────────────────────────────────────────────────────────────────

# 匹配框架展开后的 AT 段，格式：@<昵称:用户ID>
_AT_PATTERN = re.compile(r"^@<([^>:]*):([^>]+)>$")

# 帮助文本
_USAGE = """\
/权限 用法（@用户 也可写成 platform:user_id，如 qq:1919810）：
  查看   @用户                         — 查看权限状态
  设置   @用户 <owner|operator|user|guest> — 设置全局权限
  重置   @用户                         — 恢复默认权限
  授权   @用户 [插件名|all]             — 授权插件所有命令
  禁止   @用户 [插件名|all]             — 禁止插件所有命令
  清除   @用户 [插件名|all]             — 移除命令覆盖
  名单   <插件名>                       — 查看谁有该插件权限
  插件                                  — 列出所有插件名

提示：不知道插件名？先发 /权限 插件"""


# ──────────────────────────────────────────────────────────────────────────────
#  命令类
# ──────────────────────────────────────────────────────────────────────────────


class PermCommand(BaseCommand):
    """权限管理命令。

    仅 OWNER 级别用户可使用。支持 @提及 用户或直接输入 platform:user_id。
    allow/deny/clear 按插件名批量操作，不填插件名时列出有命令的插件供选择。

    使用 @cmd_route 注册子命令路由，中文别名通过独立路由方法代理实现。
    """

    name: str = "perm"
    description: str = "权限管理命令（仅主人可用）：查看/修改用户权限及命令覆盖"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """匹配命令名，同时支持 perm 和 权限 两种触发词。

        Args:
            parts: 命令片段列表

        Returns:
            匹配长度，不匹配返回 0
        """
        if not parts:
            return 0
        if parts[0] in ("perm", "权限"):
            return 1
        return 0

    # ── 私有工具方法 ──────────────────────────────────────────────────────────

    async def _reply(self, text: str) -> None:
        """向当前聊天流发送文本回复。

        Args:
            text: 要发送的文本内容
        """
        await send_text(text, stream_id=self.stream_id)

    async def _parse_user(self, user_arg: str) -> tuple[str, str] | None:
        """解析用户参数，返回 (platform, user_id)。

        支持两种格式：
        - @<昵称:用户ID>  —— 自动从当前聊天流获取平台
        - platform:user_id —— 显式指定平台和用户 ID

        Args:
            user_arg: 单个用户参数 token

        Returns:
            (platform, user_id) 元组；解析失败则向用户回复错误并返回 None
        """
        # 尝试 AT 格式
        at_match = _AT_PATTERN.fullmatch(user_arg)
        if at_match:
            user_id = at_match.group(2)
            info = await get_stream_info(self.stream_id)
            if not info or not info.get("platform"):
                await self._reply("无法获取当前会话的平台信息，请改用 platform:user_id 格式")
                return None
            return info["platform"], user_id

        # 尝试 platform:user_id 格式
        parts = user_arg.split(":", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()

        await self._reply(
            f"无法识别用户标识: {user_arg!r}\n"
            "请 @提及用户，或使用格式 platform:user_id（如 qq:1919810）"
        )
        return None

    async def _resolve_command_sigs(self, plugin_target: str) -> list[str] | None:
        """将插件目标解析为完整命令签名列表。

        Args:
            plugin_target: 插件名、"all" 或空字符串（显示插件列表后返回 None）

        Returns:
            完整命令签名列表；需要向用户显示选择提示时返回 None
        """
        all_cmds = command_api.get_all_commands()

        if not plugin_target:
            # 未指定：列出有命令的插件供用户选择
            plugins = sorted(
                {sig.split(":")[0] for sig in all_cmds if not sig.startswith("perm_plugin:")}
            )
            if plugins:
                plugin_list = "\n".join(f"  · {p}" for p in plugins)
                await self._reply(
                    f"请指定插件名或 all：\n{plugin_list}\n\n"
                    "示例：/权限 授权 @用户 greeting_plugin\n"
                    "      /权限 授权 @用户 all"
                )
            else:
                await self._reply("当前没有已注册的命令插件")
            return None

        if plugin_target.lower() == "all":
            return [sig for sig in all_cmds if not sig.startswith("perm_plugin:")]

        # 指定插件名：按 "plugin_name:command:" 前缀过滤
        prefix = f"{plugin_target}:command:"
        sigs = [sig for sig in all_cmds if sig.startswith(prefix)]
        if not sigs:
            plugins = sorted(
                {sig.split(":")[0] for sig in all_cmds if not sig.startswith("perm_plugin:")}
            )
            plugin_list = "\n".join(f"  · {p}" for p in plugins)
            await self._reply(
                f"插件 {plugin_target!r} 没有已注册的命令，或插件名有误。\n"
                f"当前有命令的插件：\n{plugin_list}"
            )
            return None

        return sigs

    # ── 英文子命令路由 ────────────────────────────────────────────────────────

    @cmd_route("status")
    async def handle_status(self, user_arg: str = "") -> tuple[bool, str]:
        """查看用户的全局权限级别及所有命令级别覆盖。

        Args:
            user_arg: 用户标识（@<昵称:ID> 或 platform:user_id）
        """
        if not user_arg:
            await self._reply("用法：/权限 查看 @用户  或  /权限 查看 qq:1919810")
            return False, "missing user"

        parsed = await self._parse_user(user_arg)
        if not parsed:
            return False, "invalid user"
        platform, user_id = parsed

        person_id = permission_api.generate_person_id(platform, user_id)

        level = await permission_api.get_user_permission_level(person_id)
        overrides = await permission_api.get_user_command_overrides(person_id)

        lines = [
            f"用户 {platform}:{user_id}",
            f"  全局权限：{level.to_string().upper()}",
        ]
        if overrides:
            lines.append("  命令级别覆盖：")
            for ov in overrides:
                mark = "✓ 允许" if ov["granted"] else "✗ 禁止"
                reason = f"（{ov['reason']}）" if ov.get("reason") else ""
                lines.append(f"    {mark}  {ov['command_signature']}{reason}")
        else:
            lines.append("  命令级别覆盖：（无）")

        await self._reply("\n".join(lines))
        return True, "ok"

    @cmd_route("set")
    async def handle_set(self, user_arg: str = "", level_str: str = "") -> tuple[bool, str]:
        """设置用户全局权限级别（永久写入数据库）。

        Args:
            user_arg: 用户标识
            level_str: 权限级别（owner/operator/user/guest）
        """
        if not user_arg:
            await self._reply("用法：/权限 设置 @用户 <owner|operator|user|guest>")
            return False, "missing args"

        parsed = await self._parse_user(user_arg)
        if not parsed:
            return False, "invalid user"
        platform, user_id = parsed

        if not level_str:
            await self._reply("请指定权限级别：owner / operator / user / guest")
            return False, "missing level"

        try:
            level = PermissionLevel.from_string(level_str.lower())
        except ValueError:
            await self._reply(
                f"无效的权限级别: {level_str!r}\n可选值：owner / operator / user / guest"
            )
            return False, f"invalid level: {level_str}"

        person_id = permission_api.generate_person_id(platform, user_id)
        ok = await permission_api.set_user_permission_group(
            person_id=person_id,
            level=level,
            reason="聊天命令手动设置",
        )
        if ok:
            await self._reply(f"✓ 已将 {platform}:{user_id} 的权限设为 {level_str.upper()}")
            return True, "ok"
        else:
            await self._reply("设置失败，请检查日志")
            return False, "set failed"

    @cmd_route("reset")
    async def handle_reset(self, user_arg: str = "") -> tuple[bool, str]:
        """移除用户权限记录，使其恢复配置文件中的默认级别。

        Args:
            user_arg: 用户标识
        """
        if not user_arg:
            await self._reply("用法：/权限 重置 @用户  或  /权限 重置 qq:1919810")
            return False, "missing user"

        parsed = await self._parse_user(user_arg)
        if not parsed:
            return False, "invalid user"
        platform, user_id = parsed

        person_id = permission_api.generate_person_id(platform, user_id)
        ok = await permission_api.remove_user_permission_group(person_id)
        if ok:
            await self._reply(
                f"✓ 已移除 {platform}:{user_id} 的权限记录，将使用配置文件的默认级别"
            )
            return True, "ok"
        else:
            await self._reply(f"{platform}:{user_id} 没有自定义权限记录（已是默认）")
            return True, "no record"

    @cmd_route("allow")
    async def handle_allow(self, user_arg: str = "", plugin_target: str = "") -> tuple[bool, str]:
        """为用户授权指定插件的所有命令，或授权全部命令。

        Args:
            user_arg: 用户标识
            plugin_target: 插件名或 "all"（空则显示可选插件列表）
        """
        if not user_arg:
            await self._reply("用法：/权限 授权 @用户 [插件名|all]")
            return False, "missing user"

        parsed = await self._parse_user(user_arg)
        if not parsed:
            return False, "invalid user"
        platform, user_id = parsed

        sigs = await self._resolve_command_sigs(plugin_target)
        if sigs is None:
            return True, "listed plugins"

        person_id = permission_api.generate_person_id(platform, user_id)
        success_count = 0
        for sig in sigs:
            if await permission_api.grant_command_permission(
                person_id=person_id,
                command_signature=sig,
                granted=True,
                reason="聊天命令批量授权",
            ):
                success_count += 1
        total = len(sigs)
        label = f"（插件 {plugin_target}）" if plugin_target and plugin_target.lower() != "all" else "（全部）"
        if success_count == total:
            await self._reply(f"✓ 已为 {platform}:{user_id} 授权 {total} 条命令{label}")
        else:
            await self._reply(f"部分成功：{success_count}/{total} 条命令授权成功")
        return True, f"{success_count}/{total}"

    @cmd_route("deny")
    async def handle_deny(self, user_arg: str = "", plugin_target: str = "") -> tuple[bool, str]:
        """为用户禁止指定插件的所有命令，或禁止全部命令。

        Args:
            user_arg: 用户标识
            plugin_target: 插件名或 "all"（空则显示可选插件列表）
        """
        if not user_arg:
            await self._reply("用法：/权限 禁止 @用户 [插件名|all]")
            return False, "missing user"

        parsed = await self._parse_user(user_arg)
        if not parsed:
            return False, "invalid user"
        platform, user_id = parsed

        sigs = await self._resolve_command_sigs(plugin_target)
        if sigs is None:
            return True, "listed plugins"

        person_id = permission_api.generate_person_id(platform, user_id)
        success_count = 0
        for sig in sigs:
            if await permission_api.grant_command_permission(
                person_id=person_id,
                command_signature=sig,
                granted=False,
                reason="聊天命令批量禁止",
            ):
                success_count += 1
        total = len(sigs)
        label = f"（插件 {plugin_target}）" if plugin_target and plugin_target.lower() != "all" else "（全部）"
        if success_count == total:
            await self._reply(f"✓ 已为 {platform}:{user_id} 禁止 {total} 条命令{label}")
        else:
            await self._reply(f"部分成功：{success_count}/{total} 条命令禁止成功")
        return True, f"{success_count}/{total}"

    @cmd_route("clear")
    async def handle_clear(self, user_arg: str = "", plugin_target: str = "") -> tuple[bool, str]:
        """移除用户对指定插件所有命令的覆盖设置，恢复全局权限判断。

        Args:
            user_arg: 用户标识
            plugin_target: 插件名或 "all"（空则显示可选插件列表）
        """
        if not user_arg:
            await self._reply("用法：/权限 清除 @用户 [插件名|all]")
            return False, "missing user"

        parsed = await self._parse_user(user_arg)
        if not parsed:
            return False, "invalid user"
        platform, user_id = parsed

        sigs = await self._resolve_command_sigs(plugin_target)
        if sigs is None:
            return True, "listed plugins"

        person_id = permission_api.generate_person_id(platform, user_id)
        removed = 0
        for sig in sigs:
            if await permission_api.remove_command_permission_override(person_id, sig):
                removed += 1
        total = len(sigs)
        label = f"（插件 {plugin_target}）" if plugin_target and plugin_target.lower() != "all" else "（全部）"
        await self._reply(f"✓ 已移除 {platform}:{user_id} 的 {removed}/{total} 条命令覆盖{label}")
        return True, f"{removed}/{total}"

    @cmd_route("list")
    async def handle_list(self, plugin_name: str = "") -> tuple[bool, str]:
        """列出对指定插件有权限的所有用户（全局高权限用户 + 命令级授权用户）。

        Args:
            plugin_name: 要查询的插件名
        """
        if not plugin_name:
            await self._reply(
                "用法：/权限 名单 <插件名>\n"
                "示例：/权限 名单 greeting_plugin"
            )
            return False, "missing plugin"

        from collections import defaultdict

        from src.core.models.sql_alchemy import (
            CommandPermissions,
            PermissionGroups,
            PersonInfo,
        )

        # 检查插件是否拥有已注册命令
        all_cmds = command_api.get_all_commands()
        prefix = f"{plugin_name}:command:"
        if not any(sig.startswith(prefix) for sig in all_cmds):
            await self._reply(f"插件 {plugin_name!r} 没有已注册的命令，或插件名有误")
            return False, "unknown plugin"

        lines: list[str] = [f"插件 {plugin_name} 的权限用户："]

        # ── 全局高权限用户（OPERATOR 及以上）
        elevated = cast(
            list[PermissionGroups],
            await db_query(PermissionGroups).filter(
                permission_level__in=["owner", "operator"]
            ).all(),
        )
        if elevated:
            lines.append("\n[ 全局高权限 ]")
            for rec in elevated:
                person = cast(
                    PersonInfo | None,
                    await db_query(PersonInfo).filter(
                        person_id=rec.person_id
                    ).first(),
                )
                if person:
                    name = person.cardname or person.nickname or person.user_id
                    lines.append(
                        f"  {rec.permission_level.upper():<10}"
                        f"{person.platform}:{person.user_id}  （{name}）"
                    )
                else:
                    lines.append(f"  {rec.permission_level.upper():<10}{rec.person_id[:16]}…")

        # ── 命令级单独授权用户
        cmd_overrides = cast(
            list[CommandPermissions],
            await db_query(CommandPermissions).filter(
                command_signature__like=f"{plugin_name}:command:%",
                granted=True,
            ).all(),
        )

        user_sigs: dict[str, list[str]] = defaultdict(list)
        for ov in cmd_overrides:
            user_sigs[ov.person_id].append(ov.command_signature.split(":")[-1])

        if user_sigs:
            lines.append("\n[ 命令级单独授权 ]")
            for person_id, cmds in user_sigs.items():
                person = cast(
                    PersonInfo | None,
                    await db_query(PersonInfo).filter(
                        person_id=person_id
                    ).first(),
                )
                if person:
                    name = person.cardname or person.nickname or person.user_id
                    lines.append(
                        f"  {person.platform}:{person.user_id}  （{name}）"
                        f"  → {', '.join(cmds)}"
                    )
                else:
                    lines.append(f"  {person_id[:16]}…  → {', '.join(cmds)}")

        if len(lines) == 1:
            lines.append("  （暂无非默认权限用户）")

        await self._reply("\n".join(lines))
        return True, "ok"

    @cmd_route("plugins")
    async def handle_plugins(self) -> tuple[bool, str]:
        """列出所有已注册插件及其命令名，帮助用户填写 allow/deny/clear 的插件参数。"""
        all_cmds = command_api.get_all_commands()

        # 按插件分组，排除 perm_plugin 自身
        plugins: dict[str, list[str]] = {}
        for sig in all_cmds:
            if sig.startswith("perm_plugin:"):
                continue
            plugin_name_part = sig.split(":")[0]
            cmd_name_part = sig.split(":")[-1]
            plugins.setdefault(plugin_name_part, []).append(cmd_name_part)

        if not plugins:
            await self._reply("当前没有已注册的命令插件")
            return True, "no plugins"

        lines = ["已注册插件及命令列表："]
        for pname in sorted(plugins):
            cmds = sorted(plugins[pname])
            lines.append(f"\n  【{pname}】")
            for cmd in cmds:
                lines.append(f"    ○ {cmd}")
        lines.append("\n使用示例：")
        lines.append("  /权限 授权 @用户 <插件名>    — 授权该插件的全部命令")
        lines.append("  /权限 禁止 @用户 <插件名>    — 禁止该插件的全部命令")
        lines.append("  /权限 名单 <插件名>           — 查看谁有该插件的权限")

        await self._reply("\n".join(lines))
        return True, "ok"

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """无子命令时显示帮助（/权限 或 /perm）。"""
        await self._reply(_USAGE)
        return True, "help"

    @cmd_route("help")
    async def handle_help(self) -> tuple[bool, str]:
        """显示帮助信息。"""
        await self._reply(_USAGE)
        return True, "help"

    # ── 中文别名路由 ──────────────────────────────────────────────────────────

    @cmd_route("查看")
    async def handle_status_cn(self, user_arg: str = "") -> tuple[bool, str]:
        """查看用户权限（中文别名：/权限 查看）。"""
        return await self.handle_status(user_arg)

    @cmd_route("设置")
    async def handle_set_cn(self, user_arg: str = "", level_str: str = "") -> tuple[bool, str]:
        """设置用户权限（中文别名：/权限 设置）。"""
        return await self.handle_set(user_arg, level_str)

    @cmd_route("重置")
    async def handle_reset_cn(self, user_arg: str = "") -> tuple[bool, str]:
        """重置用户权限（中文别名：/权限 重置）。"""
        return await self.handle_reset(user_arg)

    @cmd_route("授权")
    async def handle_allow_cn(self, user_arg: str = "", plugin_target: str = "") -> tuple[bool, str]:
        """授权插件命令（中文别名：/权限 授权）。"""
        return await self.handle_allow(user_arg, plugin_target)

    @cmd_route("禁止")
    async def handle_deny_cn(self, user_arg: str = "", plugin_target: str = "") -> tuple[bool, str]:
        """禁止插件命令（中文别名：/权限 禁止）。"""
        return await self.handle_deny(user_arg, plugin_target)

    @cmd_route("清除")
    async def handle_clear_cn(self, user_arg: str = "", plugin_target: str = "") -> tuple[bool, str]:
        """清除命令覆盖（中文别名：/权限 清除）。"""
        return await self.handle_clear(user_arg, plugin_target)

    @cmd_route("名单")
    async def handle_list_cn(self, plugin_name: str = "") -> tuple[bool, str]:
        """查看插件权限名单（中文别名：/权限 名单）。"""
        return await self.handle_list(plugin_name)

    @cmd_route("插件")
    async def handle_plugins_cn(self) -> tuple[bool, str]:
        """列出所有插件（中文别名：/权限 插件）。"""
        return await self.handle_plugins()

    @cmd_route("插件列表")
    async def handle_plugins_cn2(self) -> tuple[bool, str]:
        """列出所有插件（中文别名：/权限 插件列表）。"""
        return await self.handle_plugins()

    @cmd_route("帮助")
    async def handle_help_cn(self) -> tuple[bool, str]:
        """显示帮助信息（中文别名：/权限 帮助）。"""
        return await self.handle_help()
