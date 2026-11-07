"""统一处理 OneBot 非消息、非 notice 事件。"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any, cast

from mofox_wire import MessageBuilder, MessageEnvelope, SegPayload

from src.app.plugin_system.api.log_api import get_logger

from ...event_models import ACCEPT_FORMAT, EventKind, MetaEventType, RequestEventKind
from .meta_event_handler import MetaEventHandler
from .notice_handler import NoticeHandler

if TYPE_CHECKING:
    from ....plugin import OneBotAdapter

logger = get_logger("onebot_adapter.event_handler")


class EventHandler:
    """将 OneBot 非消息、非 notice 事件统一转换并交给核心。"""

    def __init__(self, adapter: "OneBotAdapter") -> None:
        self.notice_handler = NoticeHandler(adapter)
        self.meta_event_handler = MetaEventHandler(adapter)

    async def handle_event(self, raw: dict[str, Any]) -> MessageEnvelope | None:
        """处理除 message/notice 外的事件；未知事件沿用 notice 原始透传方式。"""
        event_kind = cast(EventKind, raw.get("post_type") or EventKind.UNKNOWN.value)
        match event_kind:
            case EventKind.MESSAGE | EventKind.NOTICE:
                logger.debug(f"通用事件处理器不处理 {event_kind} 类型")
                return None
            case EventKind.META_EVENT:
                event_type = cast(str, raw.get("meta_event_type") or "")
                sub_type = cast(str, raw.get("sub_type") or "")
                match (event_type, sub_type):
                    case (MetaEventType.lifecycle, MetaEventType.Lifecycle.connect) | (
                        MetaEventType.heartbeat,
                        _,
                    ):
                        await self.meta_event_handler.handle_meta_event(raw)
                        return None
                    case _:
                        return await self._handle_unknown_event(raw)
            case EventKind.REQUEST:
                envelope = await self._handle_request(raw)
                if envelope is not None:
                    return envelope
                return await self._handle_unknown_event(raw)
            case EventKind.UNKNOWN:
                return await self._handle_unknown_event(raw)
            case _:
                # cast 只用于静态类型提示；遇到未来新增的 post_type 时仍按未知事件透传。
                return await self._handle_unknown_event(raw)

    async def _handle_request(self, raw: dict[str, Any]) -> MessageEnvelope | None:
        """将支持的好友/群请求事件转换为 MessageEnvelope。"""
        request_type = cast(str, raw.get("request_type") or "").strip().lower()
        sub_type = cast(str, raw.get("sub_type") or "").strip().lower()
        match (request_type, sub_type):
            case ("friend", _):
                request_kind = RequestEventKind.FRIEND_ADD
            case ("group", "add"):
                request_kind = RequestEventKind.GROUP_ADD
            case ("group", "invite"):
                request_kind = RequestEventKind.GROUP_INVITE
            case _:
                request_kind = None

        if request_kind is None:
            logger.debug(
                f"未识别的 request 种类: request_type={request_type}, sub_type={sub_type}"
            )
            return None

        user_id = str(raw.get("user_id") or "").strip()
        if not user_id:
            logger.warning("OneBot request 事件缺少 user_id，无法转换为消息")
            return None

        group_id_value = raw.get("group_id")
        group_id = str(group_id_value).strip() if group_id_value is not None else ""
        if request_type == "group" and not group_id:
            logger.warning("OneBot 群 request 事件缺少 group_id，无法转换为消息")
            return None

        try:
            event_time = float(raw.get("time", time.time()))
        except (TypeError, ValueError):
            event_time = time.time()

        nickname = str(raw.get("nickname") or user_id).strip()
        comment = str(raw.get("comment") or "")
        answer = comment.strip() or "（未填写验证信息）"
        group_name = str(raw.get("group_name") or "").strip()
        group_label = group_name or group_id
        match request_kind:
            case RequestEventKind.FRIEND_ADD:
                text_description = (
                    f"〖好友申请〗{nickname}（{user_id}）申请添加机器人为好友，"
                    f"验证消息：{answer}"
                )
            case RequestEventKind.GROUP_ADD:
                text_description = (
                    f"〖加群申请〗{nickname}（{user_id}）申请加入群 {group_label}，"
                    f"验证消息：{answer}"
                )
            case RequestEventKind.GROUP_INVITE:
                note = comment.strip() or "（无）"
                text_description = (
                    f"〖群邀请〗{nickname}（{user_id}）邀请机器人加入群 {group_label}，"
                    f"邀请说明：{note}"
                )

        message_seed = (
            f"request_{request_kind.value}_{group_id}_{user_id}_"
            f"{event_time}_{raw.get('flag', '')}"
        )
        message_id = "request_" + hashlib.md5(message_seed.encode()).hexdigest()[:16]
        segment: SegPayload = {"type": "text", "data": text_description}
        builder = MessageBuilder()
        (
            builder.direction("incoming")
            .message_id(message_id)
            .timestamp_ms(int(event_time * 1000))
            .from_user(
                user_id=user_id,
                platform="qq",
                nickname=nickname,
                cardname="",
            )
        )
        if group_id:
            builder.from_group(group_id=group_id, platform="qq", name=group_name)
        builder.format_info(
            content_format=["text", "notify"],
            accept_format=ACCEPT_FORMAT,
        ).seg_list([segment])

        envelope = builder.build()
        message_info = cast(dict[str, Any], envelope["message_info"])
        message_info["message_type"] = "request"
        message_info["extra"] = {
            "is_request": True,
            "request_kind": request_kind.value,
            "request_type": request_type,
            "sub_type": sub_type,
            "flag": str(raw.get("flag") or ""),
            "comment": comment,
            "group_id": group_id,
            "text_description": text_description,
            "raw": raw,
        }
        envelope["raw_message"] = raw
        return envelope

    async def _handle_unknown_event(self, raw: dict[str, Any]) -> MessageEnvelope | None:
        """把未支持的事件按未知 notice 透传，保留原始 payload 供下游使用。"""
        post_type = str(raw.get("post_type") or "unknown")
        passthrough = dict(raw)
        passthrough["original_post_type"] = post_type
        passthrough["notice_type"] = "unknown_event"

        envelope = await self.notice_handler.handle_notice(passthrough)
        if envelope is None:
            return None

        message_info = cast(dict[str, Any], envelope["message_info"])
        extra = message_info.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            message_info["extra"] = extra
        payload_text = json.dumps(raw, ensure_ascii=False, default=str)
        if len(payload_text) > 1000:
            payload_text = payload_text[:997] + "..."
        description = f"[未支持的 OneBot 事件: {post_type}] {payload_text}"
        extra["text_description"] = description
        extra["is_unknown_event"] = True
        extra["original_post_type"] = post_type
        extra["raw"] = raw
        segments = envelope.get("message_segment")
        if isinstance(segments, list):
            for segment in segments:
                if isinstance(segment, dict) and segment.get("type") == "text":
                    segment["data"] = description
                    break
        envelope["raw_message"] = raw
        logger.debug(f"未支持的 OneBot 事件已按 notice 透传: post_type={post_type}")
        return envelope
