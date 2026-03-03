"""消息转换器。

负责 ``MessageEnvelope``（wire 层传输格式）与 ``Message``（核心业务模型）之间的
双向转换。核心解析逻辑在 ``_parse_segments`` 中实现递归的 ``SegPayload`` 展开。

设计原则：
- 适配器传入的媒体数据（图片、语音等）**已经是 base64 编码**，转换器不做下载。
- 嵌套 seglist 最多递归 3 层，超出以占位符替代。
- 单个段解析失败不影响整体，用占位符保留位置。
- 图片和表情包会通过 VLM 识别转换为文字描述。
"""

from __future__ import annotations

import time
from typing import Any

from mofox_wire import MessageEnvelope, MessageInfoPayload, SegPayload

from src.core.models.message import Message, MessageType
from src.core.transport.message_receive.utils import (
    extract_stream_id,
    infer_chat_type,
    normalize_base64,
    safe_json_loads,
)
from src.kernel.logger import get_logger

logger = get_logger("message_converter")

# 递归深度硬上限
_MAX_NESTING_DEPTH: int = 5


# ──────────────────────────────────────────────
#  段解析返回结构
# ──────────────────────────────────────────────

class _ParseResult:
    """段解析的聚合结果。

    Attributes:
        text_parts: 纯文本片段列表，最终用空字符串拼接
        media: 媒体资源列表，每项为 ``{"type": str, "data": Any}``
        reply_to: 被回复消息的 ID（仅第一个 reply 段生效）
        at_users: 被 @ 用户列表 ``[{"nickname": str, "user_id": str}]``
        unknown_segments: 无法识别的段类型记录
    """

    __slots__ = ("text_parts", "media", "reply_to", "at_users", "unknown_segments")

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.media: list[dict[str, Any]] = []
        self.reply_to: str | None = None
        self.at_users: list[dict[str, str]] = []
        self.unknown_segments: list[dict[str, Any]] = []

    # ---- 便捷方法 ----

    @property
    def plain_text(self) -> str:
        """拼接所有文本片段。"""
        return "".join(self.text_parts)

    def merge(self, other: "_ParseResult") -> None:
        """将另一个解析结果合并到自身。"""
        self.text_parts.extend(other.text_parts)
        self.media.extend(other.media)
        if other.reply_to and not self.reply_to:
            self.reply_to = other.reply_to
        self.at_users.extend(other.at_users)
        self.unknown_segments.extend(other.unknown_segments)


# ──────────────────────────────────────────────
#  MessageConverter
# ──────────────────────────────────────────────


class MessageConverter:
    """MessageEnvelope ↔ Message 双向转换器。

    实例无状态，可以作为单例在整个应用中复用。
    
    支持 VLM 图片识别功能，将图片和表情包转换为文字描述。

    Examples:
        >>> converter = MessageConverter()
        >>> message = await converter.envelope_to_message(envelope)
        >>> envelope = await converter.message_to_envelope(message)
    """

    # ─── envelope → message ───────────────────

    async def envelope_to_message(self, envelope: MessageEnvelope) -> Message:
        """将 MessageEnvelope 转换为 Message。

        Args:
            envelope: mofox-wire 消息信封

        Returns:
            Message: 核心业务消息对象

        Raises:
            ValueError: envelope 缺少必要字段（message_info / message_segment）
        """
        message_info: MessageInfoPayload = envelope.get("message_info")  # type: ignore[assignment]
        if message_info is None:
            raise ValueError("MessageEnvelope 缺少 message_info 字段")

        raw_segments = envelope.get("message_segment")  # type: ignore[arg-type]
        if raw_segments is None:
            # 尝试 message_chain 别名
            raw_segments = envelope.get("message_chain")  # type: ignore[arg-type]

        if raw_segments is None:
            raise ValueError("MessageEnvelope 缺少 message_segment/message_chain 字段")

        # 规范化：单个 SegPayload → 列表
        segments: list[SegPayload]
        if isinstance(raw_segments, dict):
            segments = [raw_segments]  # type: ignore[list-item]
        else:
            segments = list(raw_segments)

        # 递归解析段列表
        result = self._parse_segments(segments, depth=0)
        
        # 如果有媒体，检查该聊天流是否需要 VLM 识别
        if result.media:
            # 提前提取 stream_id 用于判断是否跳过 VLM
            stream_id = extract_stream_id(message_info)
            if self._should_skip_vlm_for_stream(stream_id):
                logger.debug(f"聊天流 {stream_id[:8]} 已注册跳过 VLM 识别，保留原始媒体数据")
            else:
                result = await self._recognize_media_with_manager(result)

        # 确定消息类型
        message_type = self._infer_message_type(result)

        # 构建内容
        content = self._build_content(result, message_type)

        # 提取用户/群信息
        user_info = message_info.get("user_info") or {}
        group_info = message_info.get("group_info")
        group_id = group_info.get("group_id") if group_info else None
        group_name = group_info.get("group_name") if group_info else None

        # 提取发送者角色（UserRole 枚举转字符串）
        raw_role = user_info.get("role")
        sender_role: str | None = None
        if raw_role is not None:
            sender_role = raw_role.value

        # 提取 extra 元数据
        extra_data = message_info.get("extra") or {}
        
        return Message(
            message_id=message_info.get("message_id", ""),
            time=message_info.get("time", time.time()),
            reply_to=result.reply_to,
            content=content,
            processed_plain_text=result.plain_text or None,
            message_type=message_type,
            sender_id=user_info.get("user_id", ""),
            sender_name=user_info.get("user_nickname", ""),
            sender_cardname=user_info.get("user_cardname"),
            sender_role=sender_role,
            platform=message_info.get("platform", ""),
            chat_type=infer_chat_type(message_info),
            stream_id=extract_stream_id(message_info),
            raw_data=envelope.get("raw_message"),
            media=result.media,
            at_users=result.at_users,
            unknown_segments=result.unknown_segments,
            group_id=group_id,
            group_name=group_name,
            **extra_data,
        )

    # ─── message → envelope ───────────────────

    async def message_to_envelope(self, message: Message) -> MessageEnvelope:
        """将 Message 转换为 MessageEnvelope（用于向适配器发送）。

        Args:
            message: 核心业务消息对象

        Returns:
            MessageEnvelope: mofox-wire 消息信封
        """
        seg_list: list[SegPayload] = []

        # 非文本类型：根据 message_type 直接构建对应媒体段
        _MEDIA_TYPES = {
            MessageType.IMAGE,
            MessageType.EMOJI,
            MessageType.VOICE,
            MessageType.VIDEO,
            MessageType.FILE,
        }
        if message.message_type in _MEDIA_TYPES:
            content = message.content
            if isinstance(content, str):
                content_data: str | dict[str, Any] = content
            elif isinstance(content, dict):
                # send_file 等 API 传入 dict（如 {"path": "...", "name": "..."}）
                # FILE 类型取 path 字段作为数据
                if message.message_type == MessageType.FILE:
                    content_data = content.get("path", "")
                else:
                    content_data = content
            else:
                content_data = ""
            if content_data:
                seg_list.append({
                    "type": message.message_type.value,
                    "data": content_data,
                })
        else:
            # 文本 / 混合消息
            text = message.processed_plain_text or (
                message.content if isinstance(message.content, str) else ""
            )
            if text:
                seg_list.append({"type": "text", "data": text})

        # 构建额外媒体段（来自 extra["media"]）
        media_list: list[dict[str, Any]] = message.extra.get("media", [])
        for m in media_list:
            seg_list.append({"type": m.get("type", "unknown"), "data": m.get("data", "")})

        if not seg_list:
            seg_list.append({"type": "text", "data": ""})

        # 构建 message_info
        msg_info: MessageInfoPayload = {
            "platform": message.platform,
            "message_id": message.message_id,
            "time": message.time if isinstance(message.time, float) else time.time(),
        }

        target_user_id = message.extra.get("target_user_id")
        target_user_name = message.extra.get("target_user_name")

        stream_info: dict[str, Any] | None = None
        if message.stream_id and (message.chat_type == "group" or not target_user_id):
            from src.core.managers.stream_manager import get_stream_manager

            stream_info = await get_stream_manager().get_stream_info(message.stream_id)

        if not target_user_id and message.chat_type != "group" and stream_info:
            person_id = stream_info.get("person_id")
            if isinstance(person_id, str) and person_id:
                try:
                    from src.core.utils.user_query_helper import get_user_query_helper

                    person = await get_user_query_helper().person_crud.get_by(
                        person_id=person_id
                    )
                    if person and person.user_id:
                        target_user_id = str(person.user_id)
                except Exception:
                    target_user_id = None

        if not target_user_id:
            target_user_id = message.sender_id
        if not target_user_name:
            target_user_name = message.sender_name
        user_info_dict: dict[str, Any] = {
            "platform": message.platform,
            "user_id": target_user_id,
            "user_nickname": target_user_name,
        }
        if message.sender_cardname:
            user_info_dict["user_cardname"] = message.sender_cardname
        msg_info["user_info"] = user_info_dict  # type: ignore[typeddict-unknown-key]

        group_id = message.extra.get("target_group_id") or message.extra.get("group_id")
        group_name = message.extra.get("target_group_name") or message.extra.get("group_name")
        if message.chat_type == "group" and message.stream_id:
            if not group_id and stream_info:
                group_id = stream_info.get("group_id") or ""
                group_name = stream_info.get("group_name") or ""

            if group_id:
                msg_info["group_info"] = {  # type: ignore[typeddict-unknown-key]
                    "platform": message.platform,
                    "group_id": group_id,
                    "group_name": group_name or "",
                }

        envelope: MessageEnvelope = {
            "direction": "outgoing",
            "message_info": msg_info,
            "message_segment": seg_list,  # type: ignore[typeddict-item]
        }

        return envelope

    # ──────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────

    def _parse_segments(
        self,
        segments: list[SegPayload],
        depth: int = 0,
    ) -> _ParseResult:
        """递归解析 SegPayload 列表。

        Args:
            segments: 待解析的段列表
            depth: 当前递归深度

        Returns:
            _ParseResult: 解析聚合结果
        """
        result = _ParseResult()

        if depth >= _MAX_NESTING_DEPTH:
            logger.warning(f"SegPayload 嵌套深度超过 {_MAX_NESTING_DEPTH} 层，截断")
            result.text_parts.append("[嵌套内容过深]")
            return result

        for seg in segments:
            try:
                self._parse_single_segment(seg, result, depth)
            except Exception as e:
                seg_type = seg.get("type", "unknown") if isinstance(seg, dict) else "invalid"
                logger.warning(f"解析消息段失败 (type={seg_type}): {e}")
                result.text_parts.append(f"[解析失败:{seg_type}]")

        return result

    def _parse_single_segment(
        self,
        seg: SegPayload,
        result: _ParseResult,
        depth: int,
    ) -> None:
        """解析单个 SegPayload 并写入 result。

        Args:
            seg: 消息段
            result: 聚合结果（原地修改）
            depth: 当前递归深度
        """
        if not isinstance(seg, dict):
            logger.warning(f"非法消息段类型: {type(seg)}")
            return

        seg_type: str = seg.get("type", "")
        data = seg.get("data", "")

        match seg_type:
            case "text":
                self._handle_text(data, result)
            case "image":
                self._handle_image(data, result)
            case "emoji":
                self._handle_emoji(data, result)
            case "voice":
                self._handle_voice(data, result)
            case "file":
                self._handle_file(data, result)
            case "at":
                self._handle_at(data, result)
            case "reply":
                self._handle_reply(data, seg, result, depth)
            case "seglist":
                self._handle_seglist(data, result, depth)
            case _:
                self._handle_unknown(seg_type, data, result)

    # ─── 段处理器 ─────────────────────────────

    @staticmethod
    def _handle_text(data: Any, result: _ParseResult) -> None:
        """处理文本段。"""
        if isinstance(data, str):
            result.text_parts.append(data)
        elif isinstance(data, list):
            # 理论上 text 的 data 是 str，但防御性处理
            result.text_parts.append(str(data))
        else:
            result.text_parts.append(str(data))

    @staticmethod
    def _handle_image(data: Any, result: _ParseResult) -> None:
        """处理图片段（适配器已编码为 base64）。
        
        如果启用了 VLM，会尝试识别图片内容并添加到文本中。
        """
        if isinstance(data, str):
            normalized_data = normalize_base64(data)
            result.media.append({
                "type": "image",
                "data": normalized_data,
            })
            
            # 添加图片描述占位符，等待异步识别
            result.text_parts.append("[图片]")
        elif isinstance(data, list):
            # data 是嵌套段 — 不常见，但规范允许
            result.media.append({"type": "image", "data": str(data)})
            result.text_parts.append("[图片]")

    @staticmethod
    def _handle_emoji( data: Any, result: _ParseResult) -> None:
        """处理表情包段（适配器已编码为 base64）。
        
        如果启用了 VLM，会尝试识别表情包内容并添加到文本中。
        """
        if isinstance(data, str):
            normalized_data = normalize_base64(data)
            result.media.append({
                "type": "emoji",
                "data": normalized_data,
            })
            
            # 添加表情包描述占位符，等待异步识别
            result.text_parts.append("[表情包]")
        elif isinstance(data, list):
            result.media.append({"type": "emoji", "data": str(data)})
            result.text_parts.append("[表情包]")

    @staticmethod
    def _handle_voice(data: Any, result: _ParseResult) -> None:
        """处理语音段（适配器已编码为 base64）。"""
        if isinstance(data, str):
            result.media.append({
                "type": "voice",
                "data": normalize_base64(data),
            })
            result.text_parts.append("[语音]")

    @staticmethod
    def _handle_file(data: Any, result: _ParseResult) -> None:
        """处理文件段。

        data 可能是 JSON 字符串或已解析的字典。
        """
        parsed = data
        if isinstance(data, str):
            parsed = safe_json_loads(data)

        if isinstance(parsed, dict):
            result.media.append({
                "type": "file",
                "data": {
                    "name": parsed.get("name") or parsed.get("file", ""),
                    "size": parsed.get("size") or parsed.get("file_size"),
                    "id": parsed.get("id") or parsed.get("file_id"),
                },
            })
            file_name = parsed.get("name") or parsed.get("file", "文件")
            result.text_parts.append(f"[文件:{file_name}]")
        else:
            # 无法解析结构，保留原始信息
            result.media.append({"type": "file", "data": parsed})
            result.text_parts.append("[文件]")

    @staticmethod
    def _handle_at(data: Any, result: _ParseResult) -> None:
        """处理 @ 段。

        data 格式约定: ``nickname:user_id``，或 ``user_id``。
        """
        if not isinstance(data, str):
            result.text_parts.append(f"@{data}")
            return

        if ":" in data:
            parts = data.split(":", 1)
            nickname = parts[0]
            user_id = parts[1]
        else:
            nickname = data
            user_id = data

        result.at_users.append({"nickname": nickname, "user_id": user_id})
        result.text_parts.append(f"@<{nickname}:{user_id}> ")

    def _handle_reply(
        self,
        data: Any,
        seg: SegPayload,
        result: _ParseResult,
        depth: int,
    ) -> None:
        """处理回复段。

        reply 段的 data 可以是：
        1. 字符串 — 被回复消息的 ID
        2. 嵌套段列表 — 回复内容的结构化表示
        """
        if isinstance(data, str):
            # data 是消息 ID
            if not result.reply_to:
                result.reply_to = data
            result.text_parts.append(f"[回复:{data}]")
        elif isinstance(data, list):
            # 嵌套段：递归解析
            inner = self._parse_segments(data, depth + 1)
            if not result.reply_to:
                result.reply_to = inner.reply_to
            inner_text = inner.plain_text
            if inner_text:
                result.text_parts.append(f"「回复：{inner_text}」")
            else:
                result.text_parts.append("[回复]")
            # 合并媒体等内容
            result.media.extend(inner.media)
            result.at_users.extend(inner.at_users)

    def _handle_seglist(
        self,
        data: Any,
        result: _ParseResult,
        depth: int,
    ) -> None:
        """处理 seglist 段（嵌套段列表）。"""
        if isinstance(data, list):
            inner = self._parse_segments(data, depth + 1)
            result.merge(inner)
        else:
            logger.warning(f"seglist 的 data 不是列表: {type(data)}")
            result.text_parts.append(str(data))

    @staticmethod
    def _handle_unknown(seg_type: str, data: Any, result: _ParseResult) -> None:
        """处理未知类型的段。"""
        result.unknown_segments.append({"type": seg_type, "data": data})
        result.text_parts.append(f"[{seg_type}]")

    # ─── 辅助方法 ─────────────────────────────

    @staticmethod
    def _infer_message_type(result: _ParseResult) -> MessageType:
        """根据解析结果推断 MessageType。

        优先级：如果有媒体，按第一个媒体类型决定；否则为 TEXT。
        """
        if not result.media:
            return MessageType.TEXT

        first_media_type = result.media[0].get("type", "")

        type_mapping: dict[str, MessageType] = {
            "image": MessageType.IMAGE,
            "emoji": MessageType.EMOJI,
            "voice": MessageType.VOICE,
            "file": MessageType.FILE,
        }

        return type_mapping.get(first_media_type, MessageType.UNKNOWN)

    async def _recognize_media_with_manager(self, result: _ParseResult) -> _ParseResult:
        """使用 MediaManager 识别媒体内容（图片、表情包）并更新文本描述。
        
        Args:
            result: 解析结果
            
        Returns:
            更新后的解析结果
        """
        try:
            from src.core.managers.media_manager import get_media_manager
            
            manager = get_media_manager()
            
            # 收集需要识别的媒体
            media_to_recognize = []
            for i, media in enumerate(result.media):
                if media["type"] in ("image", "emoji"):
                    media_to_recognize.append((i, media))
            
            if not media_to_recognize:
                return result
            
            # 批量识别
            descriptions = []
            for idx, media in media_to_recognize:
                try:
                    description = await manager.recognize_media(
                        media["data"],
                        media["type"],
                        use_cache=True
                    )
                    descriptions.append((idx, description))
                    
                    # 保存媒体信息到数据库
                    if description:
                        # 计算哈希值作为标识
                        media_hash = manager._compute_hash(media["data"])
                        await manager.save_media_info(
                            media_hash=media_hash,
                            media_type=media["type"],
                            description=description,
                            vlm_processed=True
                        )
                except Exception as e:
                    logger.warning(f"识别{media['type']}失败: {e}")
                    descriptions.append((idx, None))
            
            # 重建 text_parts，替换占位符
            new_text_parts = []
            media_idx = 0
            for part in result.text_parts:
                if part in ("[图片]", "[表情包]"):
                    # 找到对应的描述
                    if media_idx < len(descriptions):
                        _, description = descriptions[media_idx]
                        if description:
                            media_type = "图片" if part == "[图片]" else "表情包"
                            new_text_parts.append(f"[{media_type}:{description}]")
                        else:
                            new_text_parts.append(part)
                        media_idx += 1
                    else:
                        new_text_parts.append(part)
                else:
                    new_text_parts.append(part)
            
            result.text_parts = new_text_parts
            
        except Exception as e:
            logger.error(f"MediaManager 识别失败: {e}", exc_info=True)
        
        return result
    
    @staticmethod
    def _should_skip_vlm_for_stream(stream_id: str) -> bool:
        """检查指定聊天流是否已注册跳过 VLM 识别。

        Args:
            stream_id: 聊天流 ID

        Returns:
            True 表示应跳过 VLM 识别
        """
        try:
            from src.core.managers.media_manager import get_media_manager
            return get_media_manager().should_skip_vlm(stream_id)
        except Exception:
            return False

    @staticmethod
    def _build_content(result: _ParseResult, message_type: MessageType) -> str | Any:
        """构建 Message.content 字段。

        - TEXT 类型: 返回纯文本
        - 含媒体: 返回结构化字典
        """
        if message_type == MessageType.TEXT:
            return result.plain_text

        # 含媒体时返回结构化内容
        return {
            "text": result.plain_text,
            "media": result.media,
        }
