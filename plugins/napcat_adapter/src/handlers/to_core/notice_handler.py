"""通知事件处理器"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

from mofox_wire import MessageBuilder, SegPayload, UserInfoPayload

from src.app.plugin_system.api.log_api import get_logger

from ...event_models import ACCEPT_FORMAT, QQ_FACE, NoticeType, RealMessageType
from ..utils import get_group_info, get_member_info, get_message_detail, get_self_info, get_stranger_info

if TYPE_CHECKING:
    from ....plugin import NapcatAdapter

logger = get_logger("napcat_adapter")


class NoticeHandler:
    """处理 Napcat 通知事件（戳一戳、表情回复、禁言、文件上传等）"""

    def __init__(self, adapter: "NapcatAdapter"):
        self.adapter = adapter
        # 戳一戳防抖时间戳
        self.last_poke_time: float = 0.0

    async def handle_notice(self, raw: dict[str, Any]):
        """
        处理通知事件

        Args:
            raw: OneBot 原始通知数据

        Returns:
            MessageEnvelope (dict) or None
        """
        notice_type = raw.get("notice_type")
        message_time: float = time.time()

        self_id = raw.get("self_id")
        group_id = raw.get("group_id")
        user_id = raw.get("user_id")
        target_id = raw.get("target_id")

        handled_segment: SegPayload | None = None
        user_info: UserInfoPayload | None = None
        notice_config: dict[str, Any] = {
            "is_notice": False,
            "is_public_notice": False,
            "target_id": target_id,
        }

        match notice_type:
            case NoticeType.friend_recall:
                logger.info("好友撤回一条消息")
                logger.info(f"撤回消息ID：{raw.get('message_id')}, 撤回时间：{raw.get('time')}")
                logger.warning("暂时不支持撤回消息处理")
                return None

            case NoticeType.group_recall:
                logger.info("群内用户撤回一条消息")
                logger.info(f"撤回消息ID：{raw.get('message_id')}, 撤回时间：{raw.get('time')}")
                logger.warning("暂时不支持撤回消息处理")
                return None

            case NoticeType.notify:
                sub_type = raw.get("sub_type")
                match sub_type:
                    case NoticeType.Notify.poke:
                        # 检查是否启用戳一戳功能
                        if (
                            not self.adapter.plugin
                            or not self.adapter.plugin.config
                            or self.adapter.plugin.config.features.enable_poke
                        ):
                            logger.debug("处理戳一戳消息")
                            handled_segment, user_info = await self._handle_poke_notify(raw, group_id, user_id)
                            if handled_segment and user_info:
                                notice_config["notice_type"] = "poke"
                                notice_config["is_notice"] = True
                        else:
                            logger.warning("戳一戳消息被禁用，取消戳一戳处理")
                            return None

                    case NoticeType.Notify.input_status:
                        ...

                    case _:
                        logger.warning(f"不支持的notify类型: {notice_type}.{sub_type}")
                        return None

            case NoticeType.group_msg_emoji_like:
                # 检查是否启用表情回复功能
                if (
                    not self.adapter.plugin
                    or not self.adapter.plugin.config
                    or self.adapter.plugin.config.features.enable_emoji_like
                ):
                    # 过滤机器人自己贴表情的回声，避免重复动作
                    if str(user_id) == str(self_id):
                        logger.debug("忽略机器人自己贴表情的notice回声")
                        return None
                    logger.debug("处理群聊表情回复")
                    handled_segment, user_info = await self._handle_group_emoji_like_notify(
                        raw, group_id, user_id
                    )
                    if handled_segment and user_info:
                        notice_config["notice_type"] = "emoji_like"
                        notice_config["is_notice"] = True
                else:
                    logger.warning("群聊表情回复被禁用，取消群聊表情回复处理")
                    return None

            case NoticeType.group_ban:
                sub_type = raw.get("sub_type")
                match sub_type:
                    case NoticeType.GroupBan.ban:
                        logger.info("处理群禁言")
                        handled_segment, user_info = await self._handle_ban_notify(raw, group_id)
                        if handled_segment and user_info:
                            user_id_in_ban = raw.get("user_id")
                            if user_id_in_ban == 0:
                                notice_config["notice_type"] = "group_whole_ban"
                            else:
                                notice_config["notice_type"] = "group_ban"
                            notice_config["is_notice"] = True

                    case NoticeType.GroupBan.lift_ban:
                        logger.info("处理解除群禁言")
                        handled_segment, user_info = await self._handle_lift_ban_notify(raw, group_id)
                        if handled_segment and user_info:
                            user_id_in_ban = raw.get("user_id")
                            if user_id_in_ban == 0:
                                notice_config["notice_type"] = "group_whole_lift_ban"
                            else:
                                notice_config["notice_type"] = "group_lift_ban"
                            notice_config["is_notice"] = True

                    case _:
                        logger.warning(f"不支持的group_ban类型: {notice_type}.{sub_type}")
                        return None

            case NoticeType.group_upload:
                logger.info("群文件上传")
                if user_id == self_id:
                    logger.info("检测到机器人自己上传文件，忽略此通知")
                    return None
                handled_segment, user_info = await self._handle_group_upload_notify(
                    raw, group_id, user_id, self_id
                )
                if handled_segment and user_info:
                    notice_config["notice_type"] = "group_upload"
                    notice_config["is_notice"] = True

            case _:
                logger.warning(f"不支持的notice类型: {notice_type}")
                return None

        if not handled_segment or not user_info:
            logger.warning("notice处理失败或不支持")
            return None

        # 生成可读的 text_description 供下游插件（如 notice_injector）读取
        seg_type = handled_segment.get("type", "text")
        seg_data_content = handled_segment.get("data", "")
        if seg_type == "text" and isinstance(seg_data_content, str):
            notice_config["text_description"] = seg_data_content
        elif seg_type == "notify" and isinstance(seg_data_content, dict):
            operator_name = (
                user_info.get("user_nickname")
                or user_info.get("user_cardname")
                or "某人"
            )
            sub_type_in_data = seg_data_content.get("sub_type", "")
            if sub_type_in_data == "ban":
                banned = seg_data_content.get("banned_user_info") or {}
                banned_name = banned.get("user_nickname") or "某人"
                duration = seg_data_content.get("duration", 0)
                notice_config["text_description"] = (
                    f"{operator_name}将{banned_name}禁言了{duration}秒"
                )
            elif sub_type_in_data == "whole_ban":
                notice_config["text_description"] = f"{operator_name}开启了全体禁言"
            elif sub_type_in_data == "lift_ban":
                lifted = seg_data_content.get("lifted_user_info") or {}
                lifted_name = lifted.get("user_nickname") or "某人"
                notice_config["text_description"] = (
                    f"{operator_name}解除了{lifted_name}的禁言"
                )
            elif sub_type_in_data == "whole_lift_ban":
                notice_config["text_description"] = f"{operator_name}关闭了全体禁言"
            else:
                notice_config["text_description"] = str(seg_data_content)
        else:
            notice_config["text_description"] = str(seg_data_content)

        # 使用 MessageBuilder 构建消息
        msg_builder = MessageBuilder()

        # 生成唯一 notice ID：notice类型 + 用户ID + 群ID + 时间戳，避免主键冲突
        _notice_id_raw = f"notice_{notice_type}_{user_id}_{group_id}_{message_time}"
        unique_notice_id = "notice_" + hashlib.md5(_notice_id_raw.encode()).hexdigest()[:16]

        (
            msg_builder.direction("incoming")
            .message_id(unique_notice_id)
            .timestamp_ms(int(message_time * 1000))
            .from_user(
                user_id=str(user_info.get("user_id", "")),
                platform="qq",
                nickname=user_info.get("user_nickname", ""),
                cardname=user_info.get("user_cardname", ""),
            )
        )

        # 如果是群消息，添加群信息
        if group_id:
            fetched_group_info = await get_group_info(group_id)
            group_name: str | None = None
            if fetched_group_info:
                group_name = fetched_group_info.get("group_name")
            else:
                logger.warning("无法获取notice消息所在群的名称")
            msg_builder.from_group(
                group_id=str(group_id),
                platform="qq",
                name=group_name or "",
            )

        # 设置格式信息
        content_format = [handled_segment.get("type", "text")]
        if "notify" not in content_format:
            content_format.append("notify")
        msg_builder.format_info(
            content_format=content_format,
            accept_format=ACCEPT_FORMAT,
        )

        # 设置消息段
        msg_builder.seg_list([handled_segment])

        # 设置 extra（包含 notice 相关配置）
        envelope = msg_builder.build()
        envelope["message_info"]["extra"] = notice_config
        # 显式标记消息类型为 notice，使 receiver 路由到 _handle_other 而非普通消息路径
        envelope["message_info"]["message_type"] = "notice"
        return envelope

    async def _handle_poke_notify(
        self, raw: dict[str, Any], group_id: Any, user_id: Any
    ) -> tuple[SegPayload | None, UserInfoPayload | None]:
        """处理戳一戳通知"""
        self_info: dict | None = await get_self_info()

        if not self_info:
            logger.error("自身信息获取失败")
            return None, None

        self_id = raw.get("self_id")
        target_id = raw.get("target_id")

        # 防抖检查：如果是针对机器人的戳一戳，检查防抖时间
        if self_id == target_id:
            current_time = time.time()
            # 获取防抖时间配置
            debounce_seconds = 2.0
            if self.adapter.plugin and self.adapter.plugin.config:
                debounce_seconds = self.adapter.plugin.config.features.poke_debounce_seconds

            if self.last_poke_time > 0:
                time_diff = current_time - self.last_poke_time
                if time_diff < debounce_seconds:
                    logger.debug(
                        f"戳一戳防抖：用户 {user_id} 的戳一戳被忽略（距离上次戳一戳 {time_diff:.2f} 秒）"
                    )
                    return None, None

            self.last_poke_time = current_time

        target_name: str | None = None
        raw_info: list = raw.get("raw_info", [])

        if group_id:
            user_qq_info: dict | None = await get_member_info(group_id, user_id)
        else:
            user_qq_info: dict | None = await get_stranger_info(user_id)

        if user_qq_info:
            user_name = user_qq_info.get("nickname", "QQ用户")
            user_cardname = user_qq_info.get("card", "")
        else:
            user_name = "QQ用户"
            user_cardname = ""
            logger.debug("无法获取戳一戳对方的用户昵称")

        # 计算显示名称
        display_name = ""
        if self_id == target_id:
            target_name = self_info.get("nickname", "")
        elif self_id == user_id:
            # 不发送机器人戳别人的消息
            return None, None
        else:
            # 如果配置为忽略不是针对自己的戳一戳，则直接返回None
            if (
                self.adapter.plugin
                and self.adapter.plugin.config
                and self.adapter.plugin.config.features.ignore_non_self_poke
            ):
                logger.debug("忽略不是针对自己的戳一戳消息")
                return None, None

            if group_id:
                fetched_member_info: dict | None = await get_member_info(group_id, target_id)
                if fetched_member_info:
                    target_name = fetched_member_info.get("nickname", "QQ用户")
                else:
                    target_name = "QQ用户"
                    logger.debug("无法获取被戳一戳方的用户昵称")
                display_name = user_name
            else:
                return None, None

        # 解析戳一戳文本
        first_txt: str = "戳了戳"
        second_txt: str = ""
        try:
            if len(raw_info) > 2:
                first_txt = raw_info[2].get("txt", "戳了戳")
            if len(raw_info) > 4:
                second_txt = raw_info[4].get("txt", "")
        except Exception as e:
            logger.warning(f"解析戳一戳消息失败: {e!s}，将使用默认文本")

        user_info: UserInfoPayload = {
            "platform": "qq",
            "user_id": str(user_id),
            "user_nickname": user_name,
            "user_cardname": user_cardname,
        }

        seg_data: SegPayload = {
            "type": "text",
            "data": f"{display_name}{first_txt}{target_name}{second_txt}（这是QQ的一个功能，用于提及某人，但没那么明显）",
        }
        return seg_data, user_info

    async def _handle_group_emoji_like_notify(
        self, raw: dict[str, Any], group_id: Any, user_id: Any
    ) -> tuple[SegPayload | None, UserInfoPayload | None]:
        """处理群聊表情回复通知"""
        if not group_id:
            logger.error("群ID不能为空，无法处理群聊表情回复通知")
            return None, None

        user_qq_info: dict | None = await get_member_info(group_id, user_id)
        if user_qq_info:
            user_name = user_qq_info.get("nickname", "QQ用户")
            user_cardname = user_qq_info.get("card", "")
        else:
            user_name = "QQ用户"
            user_cardname = ""
            logger.debug("无法获取表情回复对方的用户昵称")

        # 触发事件
        from src.app.plugin_system.api import event_api

        from ...event_types import NapcatEvent

        target_message = await get_message_detail(raw.get("message_id", ""))
        if not target_message:
            logger.error("未找到对应消息")
            return None, None

        target_message_text = await self._extract_message_preview(target_message)

        user_info: UserInfoPayload = {
            "platform": "qq",
            "user_id": str(user_id),
            "user_nickname": user_name,
            "user_cardname": user_cardname,
        }

        likes_list = raw.get("likes", [])
        like_emoji_id = ""
        if likes_list and len(likes_list) > 0:
            like_emoji_id = str(likes_list[0].get("emoji_id", ""))

        # 触发表情回复事件
        await event_api.publish_event(
            NapcatEvent.ON_RECEIVED.EMOJI_LIEK,
            {
                "message_id": raw.get("message_id", ""), 
                "emoji_id": like_emoji_id, 
                "group_id": group_id, 
                "user_id": user_id
             }
        )

        emoji_text = QQ_FACE.get(like_emoji_id, f"[表情{like_emoji_id}]")
        seg_data: SegPayload = {
            "type": "text",
            "data": f"{user_name}使用Emoji表情{emoji_text}回应了消息[{target_message_text}]",
        }
        return seg_data, user_info

    async def _extract_message_preview(self, message_detail: dict[str, Any], depth: int = 0) -> str:
        """提取被表情回应消息的可读摘要，支持多层嵌套"""
        if depth > 3:
            return "..."

        preview_parts: list[str] = []
        for seg in message_detail.get("message", []):
            seg_type = seg.get("type")
            seg_data = seg.get("data", {})

            if seg_type == RealMessageType.text:
                preview_parts.append(seg_data.get("text", ""))
            elif seg_type == RealMessageType.face:
                face_id = str(seg_data.get("id", ""))
                preview_parts.append(QQ_FACE.get(face_id, f"[表情{face_id}]"))
            elif seg_type == RealMessageType.image:
                preview_parts.append("[图片]" if seg_data.get("sub_type") == 0 else "[表情包]")
            elif seg_type == RealMessageType.at:
                at_name = seg_data.get("text") or seg_data.get("qq") or "未知对象"
                preview_parts.append(f"@{at_name}")
            elif seg_type == RealMessageType.reply:
                reply_id = seg_data.get("id")
                if reply_id:
                    reply_detail = await get_message_detail(reply_id)
                    nested_preview = await self._extract_message_preview(reply_detail or {}, depth + 1)
                    preview_parts.append(f"[回复:{nested_preview}]")
            elif seg_type == RealMessageType.forward:
                preview_parts.append("[转发消息]")
            elif seg_type == RealMessageType.file:
                file_name = seg_data.get("file") or seg_data.get("name") or "文件"
                preview_parts.append(f"[文件:{file_name}]")
            elif seg_type == RealMessageType.json:
                preview_parts.append("[卡片消息]")
            elif seg_type:
                preview_parts.append(f"[{seg_type}]")

        preview = "".join(preview_parts).strip() or "[消息]"
        max_length = 60
        if len(preview) > max_length:
            preview = preview[:max_length] + "..."
        return preview

    async def _handle_group_upload_notify(
        self, raw: dict[str, Any], group_id: Any, user_id: Any, self_id: Any
    ) -> tuple[SegPayload | None, UserInfoPayload | None]:
        """处理群文件上传通知"""
        if not group_id:
            logger.error("群ID不能为空，无法处理群文件上传通知")
            return None, None

        user_qq_info: dict | None = await get_member_info(group_id, user_id)
        if user_qq_info:
            user_name = user_qq_info.get("nickname", "QQ用户")
            user_cardname = user_qq_info.get("card", "")
        else:
            user_name = "QQ用户"
            user_cardname = ""
            logger.debug("无法获取上传文件的用户昵称")

        file_info = raw.get("file")
        if not file_info:
            logger.error("群文件上传通知中缺少文件信息")
            return None, None

        user_info: UserInfoPayload = {
            "platform": "qq",
            "user_id": str(user_id),
            "user_nickname": user_name,
            "user_cardname": user_cardname,
        }

        file_name = file_info.get("name", "未知文件")
        file_size = file_info.get("size", 0)

        seg_data: SegPayload = {
            "type": "text",
            "data": f"{user_name} 上传了文件: {file_name} (大小: {file_size} 字节)",
        }
        return seg_data, user_info

    async def _handle_ban_notify(
        self, raw: dict[str, Any], group_id: Any
    ) -> tuple[SegPayload | None, UserInfoPayload | None]:
        """处理群禁言通知"""
        if not group_id:
            logger.error("群ID不能为空，无法处理禁言通知")
            return None, None

        # 获取操作者信息
        operator_id = raw.get("operator_id")
        operator_nickname: str = "QQ用户"
        operator_cardname: str = ""

        member_info: dict | None = await get_member_info(group_id, operator_id)
        if member_info:
            operator_nickname = member_info.get("nickname", "QQ用户")
            operator_cardname = member_info.get("card", "")
        else:
            logger.warning("无法获取禁言执行者的昵称，消息可能会无效")

        operator_info: UserInfoPayload = {
            "platform": "qq",
            "user_id": str(operator_id),
            "user_nickname": operator_nickname,
            "user_cardname": operator_cardname,
        }

        # 获取被禁言者信息
        user_id = raw.get("user_id")
        banned_user_info: dict[str, Any] | None = None
        user_nickname: str = "QQ用户"
        user_cardname: str = ""
        sub_type: str = ""

        duration = raw.get("duration")
        if duration is None:
            logger.error("禁言时长不能为空，无法处理禁言通知")
            return None, None

        if user_id == 0:  # 全体禁言
            sub_type = "whole_ban"
        else:  # 单人禁言
            sub_type = "ban"
            fetched_member_info: dict | None = await get_member_info(group_id, user_id)
            if fetched_member_info:
                user_nickname = fetched_member_info.get("nickname", "QQ用户")
                user_cardname = fetched_member_info.get("card", "")
            banned_user_info = {
                "platform": "qq",
                "user_id": str(user_id),
                "user_nickname": user_nickname,
                "user_cardname": user_cardname,
            }

        seg_data: SegPayload = {
            "type": "notify",
            "data": {
                "sub_type": sub_type,
                "duration": duration,
                "banned_user_info": banned_user_info,
            },
        }

        return seg_data, operator_info

    async def _handle_lift_ban_notify(
        self, raw: dict[str, Any], group_id: Any
    ) -> tuple[SegPayload | None, UserInfoPayload | None]:
        """处理解除群禁言通知"""
        if not group_id:
            logger.error("群ID不能为空，无法处理解除禁言通知")
            return None, None

        # 获取操作者信息
        operator_id = raw.get("operator_id")
        operator_nickname: str = "QQ用户"
        operator_cardname: str = ""

        member_info: dict | None = await get_member_info(group_id, operator_id)
        if member_info:
            operator_nickname = member_info.get("nickname", "QQ用户")
            operator_cardname = member_info.get("card", "")
        else:
            logger.warning("无法获取解除禁言执行者的昵称，消息可能会无效")

        operator_info: UserInfoPayload = {
            "platform": "qq",
            "user_id": str(operator_id),
            "user_nickname": operator_nickname,
            "user_cardname": operator_cardname,
        }

        # 获取被解除禁言者信息
        sub_type: str = ""
        user_nickname: str = "QQ用户"
        user_cardname: str = ""
        lifted_user_info: dict[str, Any] | None = None

        user_id = raw.get("user_id")
        if user_id == 0:  # 全体禁言解除
            sub_type = "whole_lift_ban"
        else:  # 单人禁言解除
            sub_type = "lift_ban"
            fetched_member_info: dict | None = await get_member_info(group_id, user_id)
            if fetched_member_info:
                user_nickname = fetched_member_info.get("nickname", "QQ用户")
                user_cardname = fetched_member_info.get("card", "")
            else:
                logger.warning("无法获取解除禁言消息发送者的昵称，消息可能会无效")
            lifted_user_info = {
                "platform": "qq",
                "user_id": str(user_id),
                "user_nickname": user_nickname,
                "user_cardname": user_cardname,
            }

        seg_data: SegPayload = {
            "type": "notify",
            "data": {
                "sub_type": sub_type,
                "lifted_user_info": lifted_user_info,
            }
        }
        return seg_data, operator_info
