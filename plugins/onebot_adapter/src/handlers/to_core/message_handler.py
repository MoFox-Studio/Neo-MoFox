"""消息处理器 - 将 OneBot 消息转换为 MessageEnvelope。

本模块负责将 OneBot 协议的原始消息段转换为 mofox-wire 的 SegPayload。
由于 OneBot 的部分消息类型（如视频、文件、JSON 回声）需要在 data 字段
携带结构化字典，而上游 SegPayload.data 仅声明为 str | List[SegPayload]，
因此本模块内部使用宽松类型别名 SegData 来承载这些扩展形态，并在构建
最终 MessageEnvelope 前通过 cast 对齐到 SegPayload 契约。
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union, cast

import orjson
from mofox_wire import MessageBuilder, SegPayload
from mofox_wire.types import UserRole

from src.app.plugin_system.api.log_api import get_logger
from src.core.utils.base64_helper import base64_encode_bytes

from ....config import OneBotAdapterConfig
from ...event_models import ACCEPT_FORMAT, QQ_FACE, RealMessageType
from ..utils import (
    get_forward_message,
    get_group_info,
    get_image_base64,
    get_member_info,
    get_message_detail,
    get_record_detail,
    get_self_info,
    sanitize_text,
)

if TYPE_CHECKING:
    from ....plugin import OneBotAdapter

logger = get_logger("onebot_adapter")

# OneBot 适配器内部使用的宽松消息段类型。
# 上游 SegPayload.data 仅允许 str | List[SegPayload]，但 OneBot 的视频、
# 文件、JSON 回声等场景需要在 data 中携带结构化字典，故在此扩展为
# str | dict | list，并在产出 MessageEnvelope 前 cast 回 SegPayload。
SegData = Union[str, dict[str, Any], list[Any], "SegPayload"]
Seg = dict[str, SegData]


class MessageHandler:
    """处理来自 OneBot 的消息事件"""

    def __init__(self, adapter: "OneBotAdapter"):
        self.adapter = adapter
        self._video_downloader = None

    def _get_video_io_timeout(self) -> float:
        """获取视频 IO 相关操作的超时时间。"""
        default_timeout = 30.0
        if not self.adapter.plugin or not self.adapter.plugin.config:
            return default_timeout

        config = cast(OneBotAdapterConfig, self.adapter.plugin.config)
        return max(1.0, float(config.features.video_download_timeout))

    def _get_forward_image_threshold(self) -> int:
        """获取转发消息图片 base64 解析阈值。

        转发消息内的图片总数达到该值时，不再解析为 base64，
        而是统一替换为占位符；0 表示始终使用占位符。
        """
        default_threshold = 5
        if not self.adapter.plugin or not self.adapter.plugin.config:
            return default_threshold
        config = cast(OneBotAdapterConfig, self.adapter.plugin.config)
        return max(0, int(config.features.forward_image_threshold))

    def _get_forward_max_depth(self) -> int:
        """获取转发消息最大递归解析层数。"""
        default_depth = 3
        if not self.adapter.plugin or not self.adapter.plugin.config:
            return default_depth
        config = cast(OneBotAdapterConfig, self.adapter.plugin.config)
        return max(1, int(config.features.forward_max_depth))

    def _init_video_downloader(self) -> None:
        """根据配置初始化视频下载器"""
        # 通过 adapter.plugin 访问配置
        if not self.adapter.plugin or not self.adapter.plugin.config:
            return

        config = cast(OneBotAdapterConfig, self.adapter.plugin.config)

        # 如果启用了视频处理，根据配置初始化视频下载器
        if config.features.enable_video_processing:
            from ..video_handler import VideoDownloader

            max_size = config.features.video_max_size_mb
            timeout = config.features.video_download_timeout

            self._video_downloader = VideoDownloader(max_size_mb=max_size, download_timeout=timeout)
            logger.debug(f"视频下载器已初始化: max_size={max_size}MB, timeout={timeout}s")

    async def handle_raw_message(self, raw: dict[str, Any]):
        """
        处理原始消息并转换为 MessageEnvelope

        Args:
            raw: OneBot 原始消息数据

        Returns:
            MessageEnvelope (dict) or None

        Note:
            黑白名单过滤已移动到 OneBotAdapter.from_platform_message 顶层执行，
            确保所有类型的事件（消息、通知等）都能被统一过滤。
        """

        message_type = raw.get("message_type")
        message_id = str(raw.get("message_id", ""))
        message_time = time.time()

        msg_builder = MessageBuilder()

        # 构造用户信息
        sender_info = raw.get("sender", {})
        role = sender_info.get("role", "")
        if role == "owner":
            sender_info["role"] = UserRole.OWNER
        elif role == "admin":
            sender_info["role"] = UserRole.OPERATOR
        elif role == "member":
            sender_info["role"] = UserRole.MEMBER

        (
            msg_builder.direction("incoming")
            .message_id(message_id)
            .timestamp_ms(int(message_time * 1000))
            .from_user(
                user_id=str(sender_info.get("user_id", "")),
                platform="qq",
                nickname=sanitize_text(sender_info.get("nickname", "")),
                cardname=sanitize_text(sender_info.get("card", "")),
                user_avatar=sender_info.get("avatar", ""),
                role=sender_info.get("role", ""),
            )
        )

        # 构造群组信息（如果是群消息）
        if message_type == "group":
            group_id = raw.get("group_id")
            if group_id:
                fetched_group_info = await get_group_info(group_id)
                (
                    msg_builder.from_group(
                        group_id=str(group_id),
                        platform="qq",
                        name=(
                            fetched_group_info.get("group_name", "")
                            if fetched_group_info
                            else raw.get("group_name", "")
                        ),
                    )
                )

        # 解析消息段
        message_segments = raw.get("message", [])
        seg_list: list[Seg] = []

        for segment in message_segments:
            seg_message = await self.handle_single_segment(segment, raw)
            if seg_message:
                seg_list.append(seg_message)

        # 防御性检查：确保至少有一个消息段，避免消息为空导致构建失败
        if not seg_list:
            logger.warning("消息内容为空，添加占位符文本")
            seg_list.append({"type": "text", "data": "[消息内容为空]"})

        msg_builder.format_info(
            content_format=[cast(str, seg["type"]) for seg in seg_list],
            accept_format=ACCEPT_FORMAT,
        )

        msg_builder.seg_list(cast(list[SegPayload], seg_list))

        return msg_builder.build()

    async def handle_single_segment(
        self, segment: dict, raw_message: dict, in_reply: bool = False
    ) -> Seg | None:
        """
        处理单一消息段并转换为 MessageEnvelope

        Args:
            segment: 单一原始消息段
            raw_message: 完整的原始消息数据

        Returns:
            SegPayload | None
        """
        seg_type = segment.get("type")

        match seg_type:
            case RealMessageType.text:
                return await self._handle_text_message(segment)
            case RealMessageType.image:
                return await self._handle_image_message(segment)
            case RealMessageType.face:
                return await self._handle_face_message(segment)
            case RealMessageType.at:
                return await self._handle_at_message(segment, raw_message)
            case RealMessageType.reply:
                return await self._handle_reply_message(segment, raw_message, in_reply)
            case RealMessageType.record:
                return await self._handle_record_message(segment)
            case RealMessageType.video:
                # 检查是否启用了视频处理
                plugin = self.adapter.plugin
                if plugin and plugin.config:
                    video_config = cast(OneBotAdapterConfig, plugin.config)
                    if not video_config.features.enable_video_processing:
                        logger.debug("视频消息处理已禁用，跳过")
                        return {"type": "text", "data": "[视频消息]"}
                return await self._handle_video_message(segment)
            case RealMessageType.rps:
                return await self._handle_rps_message(segment)
            case RealMessageType.dice:
                return await self._handle_dice_message(segment)
            case RealMessageType.forward:
                messages = await get_forward_message(segment, adapter=self.adapter)
                if not messages:
                    logger.warning("转发消息内容为空或获取失败")
                    return None
                return await self.handle_forward_message(messages, raw_message)
            case RealMessageType.json:
                return await self._handle_json_message(segment)
            case RealMessageType.file:
                return await self._handle_file_message(segment)

            case _:
                logger.warning(f"Unsupported segment type: {seg_type}")
                return None

    # Utility methods for handling different message types

    async def _handle_text_message(self, segment: dict) -> Seg:
        """处理纯文本消息"""
        message_data = segment.get("data", {})
        plain_text = message_data.get("text", "")
        return {"type": "text", "data": plain_text}

    async def _handle_face_message(self, segment: dict) -> Seg | None:
        """处理表情消息"""
        message_data = segment.get("data", {})
        face_raw_id = str(message_data.get("id", ""))
        if face_raw_id in QQ_FACE:
            face_content = QQ_FACE.get(face_raw_id, "[未知表情]")
            return {"type": "text", "data": face_content}
        else:
            logger.warning(f"不支持的表情：{face_raw_id}")
            return None

    async def _handle_image_message(self, segment: dict) -> Seg | None:
        """处理图片消息与表情包消息"""
        message_data = segment.get("data", {})
        image_sub_type = message_data.get("sub_type")
        image_url = message_data.get("url", "")

        if not image_url:
            logger.warning("图片消息缺少URL")
            return None

        try:
            async with asyncio.timeout(10): # 兜底超时处理
                image_base64 = await get_image_base64(image_url)
        except TimeoutError:
            logger.error(f"图片消息处理超时: {image_url}")
            return {"type": "text", "data": "[图片处理超时]"}
        except Exception as e:
            logger.error(f"图片消息处理失败: {e!s}")
            return None
        if image_sub_type == 0:
            return {"type": "image", "data": image_base64}
        elif image_sub_type not in [4, 9]:
            return {"type": "emoji", "data": image_base64}
        else:
            logger.warning(f"不支持的图片子类型：{image_sub_type}")
            return None

    async def _handle_at_message(self, segment: dict, raw_message: dict) -> Seg | None:
        """处理@消息"""
        seg_data = segment.get("data", {})
        if not seg_data:
            return None

        qq_id = seg_data.get("qq")
        self_id = raw_message.get("self_id")
        group_id = raw_message.get("group_id")

        if str(self_id) == str(qq_id):
            logger.debug("机器人被at")
            self_info = await get_self_info()
            if self_info:
                return {"type": "at", "data": f"{self_info.get('nickname')}:{self_info.get('user_id')}"}
            return None
        else:
            if qq_id and group_id:
                member_info = await get_member_info(group_id=group_id, user_id=qq_id)
                if member_info:
                    return {"type": "at", "data": f"{member_info.get('nickname')}:{member_info.get('user_id')}"}
                return None

    async def _handle_reply_message(self, segment: dict, raw_message: dict, in_reply: bool) -> Seg | None:
        """处理回复消息。

        返回的 seglist 会前置一个 ``reply`` 段（data 为被引用消息 ID），
        以便框架 ``MessageConverter`` 解析出 ``Message.reply_to``；其后保留
        可读的 ``[回复<昵称(QQ号)>：...]`` 文本预览。
        """
        if in_reply:
            return None

        seg_data = segment.get("data", {})
        if not seg_data:
            return None

        message_id = seg_data.get("id")
        if not message_id:
            return None

        message_detail = await get_message_detail(message_id)
        if not message_detail:
            logger.warning("获取被引用的消息详情失败")
            return {"type": "text", "data": "[无法获取被引用的消息]"}

        # 递归处理被引用的消息
        reply_segments: list[Seg] = []
        for reply_seg in message_detail.get("message", []):
            if isinstance(reply_seg, dict):
                reply_result = await self.handle_single_segment(reply_seg, raw_message, in_reply=True)
                if reply_result:
                    reply_segments.append(reply_result)

        sender_info = message_detail.get("sender", {})
        sender_id = sender_info.get("user_id")
        self_id = raw_message.get("self_id")

        # 若被引用的是 bot 自己发的消息，昵称用 "你"，避免协议端不填昵称时回退成 "未知用户"
        if sender_id and self_id and str(sender_id) == str(self_id):
            sender_nickname = "你"
        else:
            sender_nickname = sender_info.get("nickname") or "未知用户"

        prefix_text = f"[回复<{sender_nickname}({sender_id})>：" if sender_id else f"[回复<{sender_nickname}>："
        suffix_text = "]，说："

        # 将被引用的消息段落转换为可读的文本占位，避免嵌套的 base64 污染
        brief_segments = [
            {"type": seg.get("type", "text"), "data": seg.get("data", "")} for seg in reply_segments
        ] or [{"type": "text", "data": "[无法获取被引用的消息]"}]

        return {
            "type": "seglist",
            "data": [
                {"type": "reply", "data": str(message_id)},
                {"type": "text", "data": prefix_text},
                *brief_segments,
                {"type": "text", "data": suffix_text},
            ],
        }

    async def _handle_record_message(self, segment: dict) -> Seg | None:
        """处理语音消息"""
        message_data = segment.get("data", {})
        file = message_data.get("file", "")
        if not file:
            logger.warning("语音消息缺少文件信息")
            return None

        try:
            record_detail = await get_record_detail(file)
            if not record_detail:
                logger.warning("获取语音消息详情失败")
                return None
            audio_base64 = record_detail.get("base64", "")
        except Exception as e:
            logger.error(f"语音消息处理失败: {e!s}")
            return None

        if not audio_base64:
            logger.error("语音消息处理失败，未获取到音频数据")
            return None

        return {"type": "voice", "data": audio_base64}

    async def _handle_video_message(self, segment: dict) -> Seg | None:
        """处理视频消息"""
        message_data = segment.get("data", {})

        video_url = message_data.get("url")
        file_path = message_data.get("filePath") or message_data.get("file_path")

        video_source = file_path if file_path else video_url
        if not video_source:
            logger.warning("视频消息缺少URL或文件路径信息")
            return {"type": "text", "data": "[视频消息]"}

        try:
            if file_path and Path(file_path).exists():
                # 本地文件处理
                async with asyncio.timeout(self._get_video_io_timeout()):
                    video_data = await asyncio.to_thread(Path(file_path).read_bytes)
                video_base64 = await asyncio.to_thread(
                    base64_encode_bytes,
                    video_data,
                )
                logger.debug(f"视频文件大小: {len(video_data) / (1024 * 1024):.2f} MB")

                return {
                    "type": "video",
                    "data": {
                        "base64": video_base64,
                        "filename": Path(file_path).name,
                        "size_mb": len(video_data) / (1024 * 1024),
                    },
                }
            elif video_url:
                # URL下载处理 - 使用配置中的下载器实例
                downloader = self._video_downloader
                if not downloader:
                    from ..video_handler import get_video_downloader
                    downloader = get_video_downloader()

                download_result = await downloader.download_video(video_url)

                if not download_result["success"]:
                    logger.warning(f"视频下载失败: {download_result.get('error', '未知错误')}")
                    return {"type": "text", "data": f"[视频消息] ({download_result.get('error', '下载失败')})"}

                video_base64 = await asyncio.to_thread(
                    base64_encode_bytes,
                    download_result["data"],
                )
                logger.debug(f"视频下载成功，大小: {len(download_result['data']) / (1024 * 1024):.2f} MB")

                return {
                    "type": "video",
                    "data": {
                        "base64": video_base64,
                        "filename": download_result.get("filename", "video.mp4"),
                        "size_mb": len(download_result["data"]) / (1024 * 1024),
                        "url": video_url,
                    },
                }
            else:
                logger.warning("既没有有效的本地文件路径，也没有有效的视频URL")
                return {"type": "text", "data": "[视频消息]"}

        except TimeoutError:
            logger.error(f"视频消息处理超时: {video_source}")
            return {"type": "text", "data": "[视频处理超时]"}
        except Exception as e:
            logger.error(f"视频消息处理失败: {e!s}")
            return {"type": "text", "data": "[视频消息处理出错]"}

    async def _handle_rps_message(self, segment: dict) -> Seg:
        """处理猜拳消息"""
        message_data = segment.get("data", {})
        res = message_data.get("result", "")
        shape_map = {"1": "布", "2": "剪刀"}
        shape = shape_map.get(res, "石头")
        return {"type": "text", "data": f"[发送了一个魔法猜拳表情，结果是：{shape}]"}

    async def _handle_dice_message(self, segment: dict) -> Seg:
        """处理骰子消息"""
        message_data = segment.get("data", {})
        res = message_data.get("result", "")
        return {"type": "text", "data": f"[扔了一个骰子，点数是{res}]"}


    async def handle_forward_message(
        self, message_list: list, raw_message: dict | None = None
    ) -> Seg | None:
        """
        递归处理转发消息，并按配置动态确定图片处理方式。

        当转发消息内的图片总数小于 forward_image_threshold 时，
        将图片解析为 base64；达到或超过该阈值时，统一替换为占位符。

        Parameters:
            message_list: list: 转发消息列表
            raw_message: dict | None: 最外层原始消息，用于引用消息处理
        """
        handled_message, image_count = await self._handle_forward_message(message_list, 0, raw_message)
        if not handled_message:
            return None

        image_threshold = self._get_forward_image_threshold()
        if 0 < image_count < image_threshold:
            logger.debug(
                f"图片数量({image_count})小于阈值({image_threshold})，开始解析图片为base64"
            )
            processed_message = await self._recursive_parse_image_seg(handled_message, True)
        elif image_count > 0:
            logger.debug(
                f"图片数量({image_count})大于等于阈值({image_threshold})，开始解析图片为占位符"
            )
            processed_message = await self._recursive_parse_image_seg(handled_message, False)
        else:
            logger.debug("没有图片，直接返回")
            processed_message = handled_message

        forward_hint = {"type": "text", "data": "这是一条转发消息：\n"}
        # 尽量扁平化：核心转换器对 seglist 有嵌套深度上限，
        # 这里将内容列表展开到同一层，降低越界截断风险。
        if processed_message.get("type") == "seglist":
            processed_items = processed_message.get("data", [])
        else:
            processed_items = [processed_message]
        return {"type": "seglist", "data": [forward_hint, *processed_items]}

    async def _recursive_parse_image_seg(self, seg_data: Seg, to_image: bool) -> Seg:
        # sourcery skip: merge-else-if-into-elif
        if seg_data.get("type") == "seglist":
            new_seg_list: list[Seg] = []
            for i_seg in seg_data.get("data", []):
                parsed_seg = await self._recursive_parse_image_seg(cast(Seg, i_seg), to_image)
                new_seg_list.append(parsed_seg)
            return {"type": "seglist", "data": new_seg_list}

        if to_image:
            if seg_data.get("type") == "image":
                image_url = cast(str, seg_data.get("data", ""))
                try:
                    encoded_image = await get_image_base64(image_url)
                except Exception as e:
                    logger.error(f"图片处理失败: {e!s}")
                    return {"type": "text", "data": "[图片]"}
                return {"type": "image", "data": encoded_image}
            if seg_data.get("type") == "emoji":
                image_url = cast(str, seg_data.get("data", ""))
                try:
                    encoded_image = await get_image_base64(image_url)
                except Exception as e:
                    logger.error(f"图片处理失败: {e!s}")
                    return {"type": "text", "data": "[表情包]"}
                return {"type": "emoji", "data": encoded_image}
            logger.debug(f"不处理类型: {seg_data.get('type')}")
            return seg_data

        if seg_data.get("type") == "image":
            return {"type": "text", "data": "[图片]"}
        if seg_data.get("type") == "emoji":
            return {"type": "text", "data": "[动画表情]"}
        logger.debug(f"不处理类型: {seg_data.get('type')}")
        return seg_data

    async def _handle_forward_message(
        self, message_list: list, layer: int, raw_message: dict | None = None
    ) -> tuple[Seg | None, int]:
        """
        递归处理转发消息

        Parameters:
            message_list: list: 转发消息列表，首层对应messages字段，后面对应content字段
            layer: int: 当前层级
            raw_message: dict | None: 最外层原始消息，用于引用消息处理

        Returns:
            seg_data: Seg: 处理后的消息段
            image_count: int: 图片数量
        """
        seg_list: list[Seg] = []
        image_count = 0
        if message_list is None:
            return None, 0

        # 构建 message_seq / message_id → 消息 映射，供转发内 reply 段按 id 定位被引用消息。
        # 转发记录里的 reply.data.id 通常是 message_seq 而非 message_id，
        # get_msg 用该值查不到任何消息，被引用内容其实就在转发记录内。
        seq_map: dict[str, dict] = {}
        for sub_message in message_list:
            if not isinstance(sub_message, dict):
                continue
            for key in ("message_seq", "message_id"):
                seq_val = sub_message.get(key)
                if seq_val is not None:
                    seq_map[str(seq_val)] = sub_message

        for sub_message in message_list:
            if not isinstance(sub_message, dict):
                continue
            sender_info: dict = sub_message.get("sender", {})
            user_nickname: str = sender_info.get("nickname", "QQ用户")
            user_nickname_str = f"【{user_nickname}】:"
            break_seg: Seg = {"type": "text", "data": "\n"}
            message_of_sub_message_list: Any = sub_message.get("message")
            if isinstance(message_of_sub_message_list, dict):
                message_of_sub_message_list = [message_of_sub_message_list]
            if not message_of_sub_message_list:
                logger.warning("转发消息内容为空")
                continue

            nickname_prefix = ("--" * layer) + user_nickname_str if layer > 0 else user_nickname_str
            sub_seg_list: list[Seg] = []

            for message_of_sub_message in message_of_sub_message_list:
                if not isinstance(message_of_sub_message, dict):
                    continue
                seg_result, count = await self._handle_forward_single_segment(
                    message_of_sub_message, raw_message, layer, seq_map
                )
                image_count += count
                if seg_result:
                    sub_seg_list.append(seg_result)

            if not sub_seg_list:
                continue

            # 尽量扁平化：核心转换器对 seglist 有嵌套深度上限，
            # 除必须独立成块的引用/合并转发外，其余内容直接展开到当前层。
            nickname_part: Seg = {"type": "text", "data": nickname_prefix}
            seg_list.extend([nickname_part, *sub_seg_list, break_seg])
        return {"type": "seglist", "data": seg_list}, image_count

    async def _handle_forward_single_segment(
        self, segment: dict, raw_message: dict | None, layer: int, seq_map: dict[str, dict] | None = None
    ) -> tuple[Seg | None, int]:
        """
        处理转发消息内的单条消息段。

        与顶层消息处理不同，此处图片段仅保留 URL（交由外层统一决定
        是否解析为 base64），并支持对引用（reply）消息的递归解析以及
        嵌套转发（forward）的递归解析。

        Parameters:
            segment: dict: 转发内容中的一条消息段
            raw_message: dict | None: 最外层原始消息，用于引用消息处理
            layer: int: 当前层级
            seq_map: dict[str, dict] | None: message_seq/message_id → 消息 映射，
                供 reply 段定位被引用消息（转发记录内回复段 id 指向 message_seq）
        Returns:
            seg_data: Seg | None: 处理后的消息段
            image_count: int: 该消息段包含的图片数量
        """
        seg_type = segment.get("type")

        if seg_type == RealMessageType.text:
            seg_data = segment.get("data")
            if isinstance(seg_data, str):
                return {"type": "text", "data": seg_data}, 0
            seg_data = seg_data or {}
            text_message = seg_data.get("text")
            if not text_message:
                return None, 0
            return {"type": "text", "data": text_message}, 0

        if seg_type == RealMessageType.image:
            image_data = segment.get("data", {})
            image_url = image_data.get("url")
            if not image_url:
                logger.warning("转发消息图片缺少URL")
                return None, 0
            sub_type = image_data.get("sub_type")
            seg_data = {"type": "image", "data": image_url} if sub_type == 0 else {"type": "emoji", "data": image_url}
            return seg_data, 1

        if seg_type == RealMessageType.forward:
            max_depth = self._get_forward_max_depth()
            if layer >= max_depth:
                return {"type": "text", "data": "【转发消息】\n"}, 0

            sub_message_data = segment.get("data")
            if not sub_message_data:
                return None, 0
            contents = await get_forward_message(segment, adapter=self.adapter)
            if not contents:
                logger.warning("嵌套转发消息内容为空")
                return None, 0

            seg_data, count = await self._handle_forward_message(contents, layer + 1, raw_message)
            if seg_data is None:
                return None, count
            head_tip: Seg = {"type": "text", "data": "合并转发消息内容：\n"}
            if seg_data.get("type") == "seglist":
                inner_items = seg_data.get("data", [])
            else:
                inner_items = [seg_data]
            return {"type": "seglist", "data": [head_tip, *inner_items]}, count

        if seg_type == RealMessageType.reply:
            # 转发内容中的引用消息：优先按 reply.data.id（对应被引用消息的
            # message_seq/message_id）在当前转发记录内定位并解析出原文内容，
            # 无法定位时回退使用 reply 段自带的内嵌 text/qq 预览。
            # 全程不产出 reply 段，避免整个转发内容被误判为引用消息。
            reply_data = segment.get("data", {})
            reply_id = str(reply_data.get("id", ""))
            referenced: dict | None = None
            if seq_map and reply_id:
                referenced = seq_map.get(reply_id)

            reply_segments: list[Seg] = []
            sender_id = reply_data.get("qq")
            sender_nickname = str(sender_id) if sender_id else "未知用户"

            if referenced:
                # 命中转发记录内的被引用消息：递归解析其消息段作为可读预览
                ref_sender = referenced.get("sender", {})
                ref_sender_id = ref_sender.get("user_id")
                ref_nickname = ref_sender.get("nickname") or "未知用户"
                if ref_sender_id:
                    sender_id = ref_sender_id
                    sender_nickname = ref_nickname

                ref_message_list: Any = referenced.get("message")
                if isinstance(ref_message_list, dict):
                    ref_message_list = [ref_message_list]
                for ref_seg in ref_message_list or []:
                    if not isinstance(ref_seg, dict):
                        continue
                    ref_result, _count = await self._handle_forward_single_segment(
                        ref_seg, raw_message, layer, seq_map
                    )
                    if ref_result:
                        reply_segments.append(ref_result)
            elif reply_data.get("text"):
                # 未能定位被引用消息：回退使用 reply 段自带的内嵌文本预览
                embedded_text = str(reply_data["text"])
                reply_segments.append({"type": "text", "data": embedded_text})

            prefix_text = f"[回复<{sender_nickname}({sender_id})>：" if sender_id else f"[回复<{sender_nickname}>："
            brief_segments = [
                {"type": seg.get("type", "text"), "data": seg.get("data", "")} for seg in reply_segments
            ] or [{"type": "text", "data": "[无法获取被引用的消息]"}]

            return {
                "type": "seglist",
                "data": [
                    {"type": "text", "data": prefix_text},
                    *brief_segments,
                    {"type": "text", "data": "]，说："},
                ],
            }, 0

        if seg_type == RealMessageType.face:
            seg_data = segment.get("data", {})
            face_raw_id = str(seg_data.get("id", ""))
            face_content = QQ_FACE.get(face_raw_id)
            if face_content:
                return {"type": "text", "data": face_content}, 0
            return None, 0

        if seg_type == RealMessageType.at:
            seg_data = segment.get("data", {})
            qq_id = seg_data.get("qq")
            if qq_id == "all":
                return {"type": "text", "data": "@全体成员 "}, 0
            return {"type": "text", "data": f"@{qq_id} "}, 0

        if seg_type == RealMessageType.record:
            return {"type": "text", "data": "[语音消息]\n"}, 0
        if seg_type == RealMessageType.video:
            return {"type": "text", "data": "[视频消息]\n"}, 0

        if seg_type in (RealMessageType.rps, RealMessageType.dice, RealMessageType.json, RealMessageType.file):
            type_label = {
                RealMessageType.rps: "[猜拳表情]",
                RealMessageType.dice: "[骰子]",
                RealMessageType.json: "[JSON卡片]",
                RealMessageType.file: "[文件]",
            }[seg_type]
            return {"type": "text", "data": type_label + "\n"}, 0

        logger.warning(f"转发消息中不支持的消息段类型: {seg_type}")
        return None, 0

    async def _handle_file_message(self, segment: dict) -> Seg | None:
        """处理文件消息"""
        message_data = segment.get("data", {})
        if not message_data:
            logger.warning("文件消息缺少 data 字段")
            return None

        # 提取文件信息
        file_name = message_data.get("file")
        file_size = message_data.get("file_size")
        file_id = message_data.get("file_id")

        logger.info(f"收到文件消息: name={file_name}, size={file_size}, id={file_id}")

        # 将文件信息打包成字典
        file_data = {
            "name": file_name,
            "size": file_size,
            "id": file_id,
        }

        return {"type": "file", "data": file_data}

    async def _handle_json_message(self, segment: dict) -> Seg | None:
        """
        处理JSON消息
        Parameters:
            segment: dict: 消息段
        Returns:
            SegPayload | None: 处理后的消息段
        """
        message_data = segment.get("data", {})
        json_data = message_data.get("data", "")

        # 检查JSON消息格式
        if not message_data or "data" not in message_data:
            logger.warning("JSON消息格式不正确")
            return {"type": "json", "data": str(message_data)}

        try:
            # 尝试将json_data解析为Python对象
            nested_data = orjson.loads(json_data)

            # 检查是否是机器人自己上传文件的回声
            if self._is_file_upload_echo(nested_data):
                logger.info("检测到机器人发送文件的回声消息，将作为文件消息处理")
                # 从回声消息中提取文件信息
                file_info = self._extract_file_info_from_echo(nested_data)
                if file_info:
                    return {"type": "file", "data": file_info}

            # 检查是否是群名片分享消息 (com.tencent.contact.lua)
            if nested_data.get("view") == "contact" and "com.tencent.contact.lua" in str(
                nested_data.get("app", "")
            ):
                logger.debug("检测到群名片分享消息，开始提取信息")
                return await self._handle_contact_share(nested_data)

            # 检查是否是QQ小程序分享消息
            if "app" in nested_data and "com.tencent.miniapp" in str(nested_data.get("app", "")):
                logger.debug("检测到QQ小程序分享消息，开始提取信息")

                # 提取目标字段
                extracted_info = {}

                # 提取 meta.detail_1 中的信息
                meta = nested_data.get("meta", {})
                detail_1 = meta.get("detail_1", {})

                if detail_1:
                    extracted_info["title"] = detail_1.get("title", "")
                    extracted_info["desc"] = detail_1.get("desc", "")
                    qqdocurl = detail_1.get("qqdocurl", "")

                    # 从qqdocurl中提取b23.tv短链接
                    if qqdocurl and "b23.tv" in qqdocurl:
                        # 查找b23.tv链接的起始位置
                        start_pos = qqdocurl.find("https://b23.tv/")
                        if start_pos != -1:
                            # 提取从https://b23.tv/开始的部分
                            b23_part = qqdocurl[start_pos:]
                            # 查找第一个?的位置，截取到?之前
                            question_pos = b23_part.find("?")
                            if question_pos != -1:
                                extracted_info["short_url"] = b23_part[:question_pos]
                            else:
                                extracted_info["short_url"] = b23_part
                        else:
                            extracted_info["short_url"] = qqdocurl
                    else:
                        extracted_info["short_url"] = qqdocurl

                # 如果成功提取到关键信息，返回格式化的文本
                if extracted_info.get("title") or extracted_info.get("desc") or extracted_info.get("short_url"):
                    content_parts = []

                    if extracted_info.get("title"):
                        content_parts.append(f"来源: {extracted_info['title']}")

                    if extracted_info.get("desc"):
                        content_parts.append(f"标题: {extracted_info['desc']}")

                    if extracted_info.get("short_url"):
                        content_parts.append(f"链接: {extracted_info['short_url']}")

                    formatted_content = "\n".join(content_parts)
                    return{
                        "type": "text",
                        "data": f"这是一条小程序分享消息，可以根据来源，考虑使用对应解析工具\n{formatted_content}",
                    }



            # 检查是否是音乐分享 (QQ音乐类型)
            if nested_data.get("view") == "music" and "com.tencent.music" in str(nested_data.get("app", "")):
                meta = nested_data.get("meta", {})
                music = meta.get("music", {})
                if music:
                    tag = music.get("tag", "未知来源")
                    logger.debug(f"检测到【{tag}】音乐分享消息 (music view)，开始提取信息")

                    title = music.get("title", "未知歌曲")
                    desc = music.get("desc", "未知艺术家")
                    jump_url = music.get("jumpUrl", "")
                    preview_url = music.get("preview", "")

                    artist = "未知艺术家"
                    song_title = title

                    if "网易云音乐" in tag:
                        artist = desc
                    elif "QQ音乐" in tag:
                        if " - " in title:
                            parts = title.split(" - ", 1)
                            song_title = parts[0]
                            artist = parts[1]
                        else:
                            artist = desc

                    formatted_content = (
                        f"这是一张来自【{tag}】的音乐分享卡片：\n"
                        f"歌曲: {song_title}\n"
                        f"艺术家: {artist}\n"
                        f"跳转链接: {jump_url}\n"
                        f"封面图: {preview_url}"
                    )
                    return {"type": "text", "data": formatted_content}

            # 检查是否是新闻/图文分享 (网易云音乐可能伪装成这种)
            elif nested_data.get("view") == "news" and "com.tencent.tuwen" in str(nested_data.get("app", "")):
                meta = nested_data.get("meta", {})
                news = meta.get("news", {})
                if news and "网易云音乐" in news.get("tag", ""):
                    tag = news.get("tag")
                    logger.debug(f"检测到【{tag}】音乐分享消息 (news view)，开始提取信息")

                    title = news.get("title", "未知歌曲")
                    desc = news.get("desc", "未知艺术家")
                    jump_url = news.get("jumpUrl", "")
                    preview_url = news.get("preview", "")

                    formatted_content = (
                        f"这是一张来自【{tag}】的音乐分享卡片：\n"
                        f"标题: {title}\n"
                        f"描述: {desc}\n"
                        f"跳转链接: {jump_url}\n"
                        f"封面图: {preview_url}"
                    )
                    return {"type": "text", "data": formatted_content}

            # 如果没有提取到关键信息，返回None
            return None

        except orjson.JSONDecodeError:
            # 如果解析失败，我们假设它不是我们关心的任何一种结构化JSON，
            # 而是普通的文本或者无法解析的格式。
            logger.debug(f"无法将data字段解析为JSON: {json_data}")
            return None
        except Exception as e:
            logger.error(f"处理JSON消息时发生未知错误: {e}")
            return None

    def _is_file_upload_echo(self, nested_data: Any) -> bool:
        """检查一个JSON对象是否是机器人自己上传文件的回声消息"""
        if not isinstance(nested_data, dict):
            return False

        # 检查 'app' 和 'meta' 字段是否存在
        if "app" not in nested_data or "meta" not in nested_data:
            return False

        # 检查 'app' 字段是否包含 'com.tencent.miniapp'
        if "com.tencent.miniapp" not in str(nested_data.get("app", "")):
            return False

        # 检查 'meta' 内部的 'detail_1' 的 'busi_id' 是否为 '1014'
        meta = nested_data.get("meta", {})
        detail_1 = meta.get("detail_1", {})
        if detail_1.get("busi_id") == "1014":
            return True

        return False

    def _extract_file_info_from_echo(self, nested_data: dict) -> dict | None:
        """从文件上传的回声消息中提取文件信息"""
        try:
            meta = nested_data.get("meta", {})
            detail_1 = meta.get("detail_1", {})

            # 文件名在 'desc' 字段
            file_name = detail_1.get("desc")

            # 文件大小在 'summary' 字段，格式为 "大小：1.7MB"
            summary = detail_1.get("summary", "")
            file_size_str = summary.replace("大小：", "").strip() # 移除前缀和空格

            # QQ API有时返回的大小不标准，这里我们只提取它给的字符串
            # 实际大小已经由 OneBot 在发送时记录，这里主要是为了保持格式一致

            if file_name and file_size_str:
                return {"file": file_name, "file_size": file_size_str, "file_id": None} # file_id在回声中不可用
        except Exception as e:
            logger.error(f"从文件回声中提取信息失败: {e}")

        return None

    async def _handle_contact_share(self, nested_data: dict[str, Any]) -> Seg | None:
        """
        处理联系人名片分享消息（群名片 / 好友推荐）。

        通过 prompt 字段使用正则提取分享类型标签与目标名称，并结合
        bizsrc 与 meta.contact 判定是群名片还是好友推荐，再从
        jumpUrl / legacyUrl 中正则提取 uin / group_code，最后组装为
        可读的文本消息。

        Args:
            nested_data: 解析后的 JSON 消息字典，预期 view 为 contact，
                app 为 com.tencent.contact.lua。

        Returns:
            SegPayload | None: 处理后的文本消息段；若提取失败则返回 None。
        """
        try:
            prompt = str(nested_data.get("prompt", ""))
            bizsrc = str(nested_data.get("bizsrc", ""))
            meta = nested_data.get("meta", {})
            contact_info = meta.get("contact", {})

            # 1. 使用正则从 prompt 中提取标签与名称
            # 群名片示例: "群名片: 墨狐狐\u200b🌟起源之地"
            # 好友推荐示例: "推荐联系人：一闪"
            # 标签部分到冒号（兼容半角/全角）为止，冒号后的内容为名称
            prompt_match = re.match(
                r"^(?P<tag>[^:：]+)[:：]\s*(?P<name>.+)$", prompt
            )
            if prompt_match:
                prompt_tag = prompt_match.group("tag").strip()
                target_name = prompt_match.group("name").strip()
            else:
                logger.debug(f"联系人名片 prompt 无法正则匹配标签，使用原始值: {prompt}")
                prompt_tag = "联系人名片"
                target_name = prompt

            # 2. 判定分享类型：群名片 or 好友推荐
            # bizsrc 含 "qun" 或 tag 含 "群" 视为群名片，否则视为好友/联系人推荐
            contact_tag = str(contact_info.get("tag", ""))
            is_group_share = (
                "qun" in bizsrc.lower()
                or "群" in prompt_tag
                or "群" in contact_tag
            )

            # 3. 从 contact 中提取附加信息
            nickname = contact_info.get("nickname", target_name)
            avatar = contact_info.get("avatar", "")
            contact_desc = contact_info.get("contact", "")
            jump_url = contact_info.get("jumpUrl", "")
            legacy_url = contact_info.get("legacyUrl", "")
            tag_label = contact_tag or prompt_tag

            # 4. 使用正则从 jumpUrl / legacyUrl 中提取 uin / group_code
            contact_number = ""
            for url_candidate in (jump_url, legacy_url):
                if not url_candidate:
                    continue
                uin_match = re.search(r"uin=(\d+)", url_candidate)
                if uin_match:
                    contact_number = uin_match.group(1)
                    break
                group_code_match = re.search(r"group_code=(\d+)", url_candidate)
                if group_code_match:
                    contact_number = group_code_match.group(1)
                    break

            # 5. 组装格式化文本（根据类型调整标签）
            content_parts: list[str] = []
            if is_group_share:
                content_parts.append(f"这是一条{tag_label}分享消息")
                content_parts.append(f"群名称: {nickname}")
                if contact_number:
                    content_parts.append(f"群号: {contact_number}")
                if contact_desc:
                    content_parts.append(f"群简介: {contact_desc}")
                if avatar:
                    content_parts.append(f"群头像: {avatar}")
            else:
                content_parts.append(f"这是一条{tag_label}分享消息")
                content_parts.append(f"昵称: {nickname}")
                if contact_number:
                    content_parts.append(f"账号: {contact_number}")
                if contact_desc:
                    content_parts.append(f"备注: {contact_desc}")
                if avatar:
                    content_parts.append(f"头像: {avatar}")

            if jump_url:
                content_parts.append(f"跳转链接: {jump_url}")

            logger.debug(
                f"联系人名片分享解析: is_group={is_group_share}, "
                f"tag={tag_label}, name={nickname}, number={contact_number}"
            )

            formatted_content = "\n".join(content_parts)
            return {"type": "text", "data": formatted_content}

        except Exception as e:
            logger.error(f"处理联系人名片分享消息时发生未知错误: {e}")
            return None

