"""utility_commands 清空上下文命令。

提供 /清空上下文 命令，仅主人（OWNER）可用，清空聊天流的对话上下文。
清空后，机器人将不再加载该时间点之前的历史消息，效果在重启后依然生效。
消息记录仍保留在数据库中，不会被删除。

支持的子命令（英文 / 中文均可）：
    /清空上下文                       — 清空当前聊天流的上下文
    /清空上下文 群                     — 清空所有群聊上下文
    /清空上下文 群 <群号>              — 清空指定群的上下文
    /清空上下文 私                     — 清空所有私聊上下文
    /清空上下文 私 <QQ号>              — 清空指定私聊的上下文
    /清空上下文 all / 全部             — 清空所有聊天流的上下文
    /clearctx [群|私|all|全部] [...]  — 英文别名
"""

from __future__ import annotations

from src.app.plugin_system.api import stream_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import ChatStream, PermissionLevel

logger = get_logger("utility_commands.clear_command")


class ClearContextCommand(BaseCommand):
    """清空聊天上下文命令（仅主人可用）。

    支持清空当前流、指定群/私聊流、按类型批量清空或全量清空。
    """

    command_name: str = "清空上下文"
    command_description: str = "清空聊天流内存上下文（仅主人可用，不删除数据库记录）"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """匹配命令名，同时支持 清空上下文 和 clearctx 两种触发词。

        Args:
            parts: 命令片段列表

        Returns:
            匹配长度，不匹配返回 0
        """
        if not parts:
            return 0
        if parts[0] in ("清空上下文", "clearctx"):
            return 1
        return 0

    async def _reply(self, text: str) -> None:
        """向当前聊天流发送文本回复。

        Args:
            text: 要发送的文本内容
        """
        await send_text(text, stream_id=self.stream_id)

    def _current_platform(self) -> str:
        """获取当前消息所在平台。

        Returns:
            平台标识字符串，无法获取时返回空字符串
        """
        if self._message is None:
            return ""
        return self._message.platform or ""

    async def _clear_by_chat_type(self, chat_type: str) -> int:
        """清空指定类型的所有聊天流上下文（持久化，包含数据库中未激活的流）。

        Args:
            chat_type: 聊天类型（"group"/"private"）

        Returns:
            成功清空的流数量
        """
        return await stream_api.bulk_clear_streams(chat_type)

    @cmd_route()
    async def handle_clear_current(self) -> tuple[bool, str]:
        """清空当前聊天流的内存上下文。"""
        await stream_api.load_and_clear_context(self.stream_id)
        await self._reply("✓ 当前聊天上下文已清空，新消息将从空白开始积累。")
        logger.info(f"已清空流上下文: {self.stream_id}")
        return True, "cleared"

    @cmd_route("群")
    async def handle_clear_group(self, group_id: str = "") -> tuple[bool, str]:
        """清空指定群或所有群的聊天上下文。

        Args:
            group_id: 群号；留空则清空所有群聊
        """
        if group_id:
            platform = self._current_platform()
            if not platform:
                await self._reply("无法获取当前平台信息，请确认消息来源。")
                return False, "missing platform"
            sid = ChatStream.generate_stream_id(platform, group_id=group_id)
            await stream_api.load_and_clear_context(sid)
            await self._reply(f"✓ 群 {group_id} 的聊天上下文已清空。")
            logger.info(f"已清空群 {group_id} 流上下文: {sid}")
            return True, "cleared"

        count = await self._clear_by_chat_type("group")
        await self._reply(f"✓ 已清空 {count} 个群聊的聊天上下文。")
        logger.info(f"已批量清空所有群聊上下文，共 {count} 个")
        return True, f"cleared {count} group streams"

    @cmd_route("私")
    async def handle_clear_private(self, user_id: str = "") -> tuple[bool, str]:
        """清空指定用户的私聊上下文或所有私聊上下文。

        Args:
            user_id: 用户 QQ 号；留空则清空所有私聊
        """
        if user_id:
            platform = self._current_platform()
            if not platform:
                await self._reply("无法获取当前平台信息，请确认消息来源。")
                return False, "missing platform"
            sid = ChatStream.generate_stream_id(platform, user_id=user_id)
            await stream_api.load_and_clear_context(sid)
            await self._reply(f"✓ 用户 {user_id} 的私聊上下文已清空。")
            logger.info(f"已清空私聊 {user_id} 流上下文: {sid}")
            return True, "cleared"

        count = await self._clear_by_chat_type("private")
        await self._reply(f"✓ 已清空 {count} 个私聊的聊天上下文。")
        logger.info(f"已批量清空所有私聊上下文，共 {count} 个")
        return True, f"cleared {count} private streams"

    @cmd_route("all")
    async def handle_clear_all(self) -> tuple[bool, str]:
        """清空所有聊天流的内存上下文（持久化，包含数据库中未激活的流）。"""
        count = await stream_api.bulk_clear_streams()
        await self._reply(f"✓ 已清空 {count} 个聊天流的上下文。")
        logger.info(f"已批量清空所有流上下文，共 {count} 个")
        return True, f"cleared {count} streams"

    @cmd_route("全部")
    async def handle_clear_all_cn(self) -> tuple[bool, str]:
        """清空所有聊天流的内存上下文（中文别名：全部）。"""
        return await self.handle_clear_all()



