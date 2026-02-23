"""扁平化消息发送 API

为插件提供简洁的消息发送接口，基于 transport 层的 MessageSender 实现。
"""
import asyncio
from typing import Any

from src.core.models.message import Message, MessageType


# =============================================================================
# 基础消息发送
# =============================================================================


async def send_text(
    content: str,
    stream_id: str,
    platform: str | None = None,
) -> bool:
    """发送文本消息

    Args:
        content: 文本内容
        stream_id: 聊天流 ID
        platform: 平台名称（可选，会从 stream_id 推断）

    Returns:
        是否发送成功

    Example:
        success = await send_text("Hello!", "qq_group_123456")
    """
    return await _send_message(
        content=content,
        message_type=MessageType.TEXT,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text=content,
    )


async def send_image(
    image_data: str,
    stream_id: str,
    platform: str | None = None,
) -> bool:
    """发送图片消息

    Args:
        image_data: 图片数据（base64 或 URL）
        stream_id: 聊天流 ID
        platform: 平台名称（可选）

    Returns:
        是否发送成功

    Example:
        success = await send_image(base64_image, "qq_group_123456")
    """
    return await _send_message(
        content=image_data,
        message_type=MessageType.IMAGE,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text="[图片]",
    )


async def send_emoji(
    emoji_data: str,
    stream_id: str,
    platform: str | None = None,
    processed_plain_text: str = "",
) -> bool:
    """发送表情包

    Args:
        emoji_data: 表情数据（base64 或 URL）
        stream_id: 聊天流 ID
        platform: 平台名称（可选）
        processed_plain_text: 人类可读文本（可选，如 "[表情包: 开心挥手]"）

    Returns:
        是否发送成功

    Example:
        success = await send_emoji(emoji_base64, "qq_group_123456",
                                   processed_plain_text="[表情包: 开心挥手]")
    """
    return await _send_message(
        content=emoji_data,
        message_type=MessageType.EMOJI,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text=processed_plain_text,
    )


async def send_voice(
    voice_data: str,
    stream_id: str,
    platform: str | None = None,
) -> bool:
    """发送语音消息

    Args:
        voice_data: 语音数据（base64 或 URL）
        stream_id: 聊天流 ID
        platform: 平台名称（可选）

    Returns:
        是否发送成功

    Example:
        success = await send_voice(voice_base64, "qq_user_987654")
    """
    return await _send_message(
        content=voice_data,
        message_type=MessageType.VOICE,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text="[语音]",
    )


async def send_video(
    video_data: str,
    stream_id: str,
    platform: str | None = None,
) -> bool:
    """发送视频消息

    Args:
        video_data: 视频数据（base64 或 URL）
        stream_id: 聊天流 ID
        platform: 平台名称（可选）

    Returns:
        是否发送成功

    Example:
        success = await send_video(video_base64, "qq_group_123456")
    """
    return await _send_message(
        content=video_data,
        message_type=MessageType.VIDEO,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text="[视频]",
    )


async def send_file(
    file_path: str,
    stream_id: str,
    platform: str | None = None,
    file_name: str | None = None,
) -> bool:
    """发送文件

    Args:
        file_path: 文件路径
        stream_id: 聊天流 ID
        platform: 平台名称（可选）
        file_name: 显示的文件名（可选）

    Returns:
        是否发送成功

    Example:
        success = await send_file("/path/to/file.pdf", "qq_group_123456")
    """
    content = {"path": file_path}
    if file_name:
        content["name"] = file_name

    return await _send_message(
        content=content,
        message_type=MessageType.FILE,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text=file_name or "[文件]",
    )


async def send_custom(
    content: Any,
    message_type: MessageType | str,
    stream_id: str,
    platform: str | None = None,
) -> bool:
    """发送自定义类型消息

    Args:
        content: 消息内容
        message_type: 消息类型
        stream_id: 聊天流 ID
        platform: 平台名称（可选）

    Returns:
        是否发送成功

    Example:
        success = await send_custom(
            {"key": "value"},
            "custom_type",
            "qq_group_123456"
        )
    """
    if isinstance(message_type, str):
        try:
            message_type = MessageType(message_type)
        except ValueError:
            message_type = MessageType.UNKNOWN

    return await _send_message(
        content=content,
        message_type=message_type,
        stream_id=stream_id,
        platform=platform,
        processed_plain_text="",
    )


async def send_message(message: Message) -> bool:
    """直接发送 Message 对象

    Args:
        message: Message 对象

    Returns:
        是否发送成功

    Example:
        msg = Message(content="Hello", platform="qq", stream_id="qq_group_123456")
        success = await send_message(msg)
    """
    from src.core.transport.message_send import get_message_sender

    sender = get_message_sender()
    return await sender.send_message(message)


# =============================================================================
# 内部实现
# =============================================================================


async def _send_message(
    content: Any,
    message_type: MessageType,
    stream_id: str,
    platform: str | None = None,
    processed_plain_text: str = "",
) -> bool:
    """内部消息发送实现

    Args:
        content: 消息内容
        message_type: 消息类型
        stream_id: 聊天流 ID
        platform: 平台名称（可选，会从 stream_id 推断）
        processed_plain_text: 消息的人类可读文本，由上层调用方显式传入

    Returns:
        是否发送成功
    """
    from src.core.managers.adapter_manager import get_adapter_manager
    from src.core.transport.message_send import get_message_sender
    from src.kernel.logger import get_logger

    logger = get_logger("send_api")

    try:
        # 推断平台
        if not platform:
            # TODO: 临时方案，待 send_api 重构后由上层统一传入 (platform, chat_type, target_id) 三元组
            platform = stream_id.split("_")[0] if "_" in stream_id else "qq"

        # 获取 bot 信息
        adapter_manager = get_adapter_manager()
        bot_info = await adapter_manager.get_bot_info_by_platform(platform)

        if not bot_info:
            logger.error(f"无法获取平台 {platform} 的 bot 信息")
            return False

        # 从 stream_manager 获取流的真实信息
        from src.core.managers.stream_manager import get_stream_manager

        stream_manager = get_stream_manager()
        stream_info = await stream_manager.get_stream_info(stream_id)

        chat_type = "private"
        extra: dict[str, Any] = {}

        if stream_info:
            # 从 stream_info 获取真实聊天类型
            chat_type = stream_info.get("chat_type", "private")
            group_id = stream_info.get("group_id")
            if chat_type == "group" and group_id:
                extra["target_group_id"] = str(group_id)
            elif chat_type == "private":
                person_id = stream_info.get("person_id")
                if person_id:
                    try:
                        from src.core.utils.user_query_helper import get_user_query_helper

                        person = await get_user_query_helper().person_crud.get_by(
                            person_id=person_id
                        )
                        if person and person.user_id:
                            extra["target_user_id"] = str(person.user_id)
                        else:
                            logger.warning(
                                f"person_id={person_id} 未查询到 user_id，"
                                f"私聊消息可能发送到错误目标"
                            )
                    except Exception as e:
                        logger.warning(
                            f"person_id={person_id} 查询 user_id 失败: {e}"
                        )

                if "target_user_id" not in extra:
                    logger.error(
                        f"stream_id={stream_id} 无法解析私聊目标用户，"
                        f"消息可能发送给 bot 自身"
                    )

        # 构建消息
        message = Message(
            message_id=f"api_{message_type.value}_{id(content)}",
            content=content,
            processed_plain_text=processed_plain_text,
            message_type=message_type,
            sender_id=bot_info.get("bot_id", ""),
            sender_name=bot_info.get("bot_nickname", "Bot"),
            platform=platform,
            chat_type=chat_type,
            stream_id=stream_id,
            **extra,
        )

        # 发送消息
        sender = get_message_sender()
        return await sender.send_message(message)

    except Exception as e:
        logger.error(f"发送消息失败: {e}", exc_info=True)
        return False


# =============================================================================
# 批量发送
# =============================================================================


async def send_batch(messages: list[Message]) -> list[bool]:
    """批量发送消息

    Args:
        messages: Message 对象列表

    Returns:
        每条消息的发送结果列表

    Example:
        messages = [
            Message(content="Hello 1", platform="qq", stream_id="qq_group_123"),
            Message(content="Hello 2", platform="qq", stream_id="qq_group_456"),
        ]
        results = await send_batch(messages)
    """
    from src.core.transport.message_send import get_message_sender

    sender = get_message_sender()
    results = []

    for message in messages:
        success = await sender.send_message(message)
        results.append(success)

    return results


async def send_batch_parallel(messages: list[Message]) -> list[bool]:
    """并行批量发送消息（速度更快但顺序不保证）

    Args:
        messages: Message 对象列表

    Returns:
        每条消息的发送结果列表

    Example:
        messages = [
            Message(content="Hello 1", platform="qq", stream_id="qq_group_123"),
            Message(content="Hello 2", platform="qq", stream_id="qq_group_456"),
        ]
        results = await send_batch_parallel(messages)
    """
    from src.core.transport.message_send import get_message_sender

    sender = get_message_sender()
    tasks = [sender.send_message(msg) for msg in messages]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 将异常转换为 False
    return [result if isinstance(result, bool) else False for result in results]


# =============================================================================
# 便捷组合功能
# =============================================================================


async def send_text_with_image(
    text: str,
    image_data: str,
    stream_id: str,
    platform: str | None = None,
) -> bool:
    """发送文本 + 图片组合消息

    Args:
        text: 文本内容
        image_data: 图片数据
        stream_id: 聊天流 ID
        platform: 平台名称（可选）

    Returns:
        是否发送成功

    Example:
        success = await send_text_with_image(
            "看看这张图片",
            image_base64,
            "qq_group_123456"
        )
    """
    # 先发送文本
    text_success = await send_text(text, stream_id, platform)
    if not text_success:
        return False

    # 再发送图片
    return await send_image(image_data, stream_id, platform)


async def broadcast_text(
    content: str,
    stream_ids: list[str],
    platform: str | None = None,
) -> dict[str, bool]:
    """广播文本消息到多个聊天流

    Args:
        content: 文本内容
        stream_ids: 聊天流 ID 列表
        platform: 平台名称（可选）

    Returns:
        {stream_id: 是否成功} 的字典

    Example:
        results = await broadcast_text(
            "系统通知",
            ["qq_group_123", "qq_group_456"]
        )
    """
    messages = [
        Message(
            message_id=f"broadcast_{id(content)}_{i}",
            content=content,
            processed_plain_text=content,
            message_type=MessageType.TEXT,
            sender_id="system",
            sender_name="System",
            platform=platform or stream_id.split("_")[0],
            stream_id=stream_id,
            chat_type="group" if "group" in stream_id else "private",
        )
        for i, stream_id in enumerate(stream_ids)
    ]

    results = await send_batch_parallel(messages)
    return dict(zip(stream_ids, results))
