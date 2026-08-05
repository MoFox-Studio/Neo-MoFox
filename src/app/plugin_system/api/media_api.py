"""
Media API 模块。

提供媒体识别、批量识别与媒体信息查询能力。

支持四类媒体，统一通过 ``media_type`` 路由：
- ``image`` / ``emoji``：通过 VLM 识别，写入 ``Images`` / ``ImageDescriptions`` 表
- ``voice``：通过 ASR 识别，写入 ``Voices`` / ``VoiceDescriptions`` 表，并落盘到 voices 目录
- ``video``：通过事件链交给第三方插件识别（无内置引擎），写入 ``Videos`` / ``VideoDescriptions`` 表，并落盘到 videos 目录
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

API_VERSION = "1.1.0"

if TYPE_CHECKING:
    from src.core.managers.media_manager import MediaManager


def _get_media_manager() -> "MediaManager":
    """延迟获取 MediaManager，避免循环依赖。

    Returns:
        媒体管理器实例
    """
    from src.core.managers.media_manager import get_media_manager

    return get_media_manager()


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def _validate_media_type(media_type: str) -> None:
    """校验媒体类型。

    Args:
        media_type: 媒体类型

    Returns:
        None
    """
    if media_type not in {"image", "emoji", "voice", "video"}:
        raise ValueError("media_type 必须是 'image'、'emoji'、'voice' 或 'video'")


async def recognize_media(
    base64_data: str,
    media_type: str,
    use_cache: bool = True,
) -> str | None:
    """识别媒体内容（图片、表情包、语音或视频）。

    按 ``media_type`` 路由到 MediaManager 的对应识别引擎：
    - ``image`` / ``emoji``：VLM 识别
    - ``voice``：ASR 识别，识别后音频文件落盘到 voices 目录
    - ``video``：通过事件链交给第三方插件识别（无内置引擎，无插件时返回 None），
      视频文件落盘到 videos 目录

    Args:
        base64_data: Base64 编码的媒体内容（语音为 WAV）
        media_type: 媒体类型，``image`` / ``emoji`` / ``voice`` / ``video``
        use_cache: 是否使用缓存

    Returns:
        识别结果文本，未识别则返回 None
    """
    _validate_non_empty(base64_data, "base64_data")
    _validate_media_type(media_type)
    return await _get_media_manager().recognize_media(
        base64_data=base64_data,
        media_type=media_type,
        use_cache=use_cache,
    )


async def recognize_batch(
    media_list: list[tuple[str, str]],
    use_cache: bool = True,
) -> list[tuple[int, str | None]]:
    """批量识别媒体。

    支持混合类型，``media_type`` 为 ``image`` / ``emoji`` / ``voice``。
    直接委托给 MediaManager，由统一的 :meth:`recognize_media` 处理路由。

    Args:
        media_list: (base64_data, media_type) 列表
        use_cache: 是否使用缓存

    Returns:
        识别结果列表，包含索引与识别文本
    """
    if not isinstance(media_list, list) or not media_list:
        raise ValueError("media_list 必须是非空列表")
    for item in media_list:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("media_list 必须包含 (base64_data, media_type) 元组")
        _validate_non_empty(item[0], "base64_data")
        _validate_media_type(item[1])
    return await _get_media_manager().recognize_batch(
        media_list=media_list,
        use_cache=use_cache,
    )


async def save_media_info(
    media_hash: str,
    media_type: str,
    file_path: str | None = None,
    description: str | None = None,
    vlm_processed: bool = False,
) -> None:
    """保存媒体信息到数据库。

    按 ``media_type`` 路由：
    - ``image`` / ``emoji``：写入 ``Images`` 表，``vlm_processed`` 标记 VLM 识别状态
    - ``voice``：写入 ``Voices`` 表，``vlm_processed`` 映射为 ``asr_processed``
    - ``video``：写入 ``Videos`` 表，``vlm_processed`` 映射为 ``video_processed``

    Args:
        media_hash: 媒体哈希（语音时即 voice_hash，视频时即 video_hash）
        media_type: 媒体类型
        file_path: 文件路径，可选
        description: 媒体描述（语音时为 ASR 文本，视频时为识别文本），可选
        vlm_processed: 是否已完成识别（语音时映射为 asr_processed，视频时映射为 video_processed）

    Returns:
        None
    """
    _validate_non_empty(media_hash, "media_hash")
    _validate_media_type(media_type)
    if file_path is not None:
        _validate_non_empty(file_path, "file_path")
    manager = _get_media_manager()
    if media_type == "voice":
        return await manager.save_voice_info(
            voice_hash=media_hash,
            file_path=file_path,
            description=description,
            asr_processed=vlm_processed,
        )
    if media_type == "video":
        return await manager.save_video_info(
            video_hash=media_hash,
            file_path=file_path,
            description=description,
            video_processed=vlm_processed,
        )
    return await manager.save_media_info(
        media_hash=media_hash,
        media_type=media_type,
        file_path=file_path,
        description=description,
        vlm_processed=vlm_processed,
    )


async def get_media_info(media_hash: str) -> dict[str, Any] | None:
    """根据哈希值或路径获取媒体信息。

    依次查询 ``Images``、``Voices`` 与 ``Videos`` 表，命中即返回。

    Args:
        media_hash: 媒体哈希或文件路径

    Returns:
        媒体信息字典，未找到则返回 None
    """
    _validate_non_empty(media_hash, "media_hash")
    info = await _get_media_manager().get_media_info(media_hash)
    if info is not None:
        return info
    from src.core.models.sql_alchemy import Images, Videos, Voices
    from src.kernel.db import QueryBuilder

    media = await QueryBuilder(Images).filter(image_id=media_hash).first()
    if media:
        return {
            "id": media.id,
            "image_id": media.image_id,
            "path": media.path,
            "type": media.type,
            "description": media.description,
            "count": media.count,
            "timestamp": media.timestamp,
            "vlm_processed": media.vlm_processed,
        }

    voice = await QueryBuilder(Voices).filter(voice_id=media_hash).first()
    if voice:
        return {
            "id": voice.id,
            "voice_id": voice.voice_id,
            "path": voice.path,
            "type": voice.type,
            "description": voice.description,
            "count": voice.count,
            "timestamp": voice.timestamp,
            "asr_processed": voice.asr_processed,
        }

    video = await QueryBuilder(Videos).filter(video_id=media_hash).first()
    if video:
        return {
            "id": video.id,
            "video_id": video.video_id,
            "path": video.path,
            "type": video.type,
            "description": video.description,
            "count": video.count,
            "timestamp": video.timestamp,
            "video_processed": video.video_processed,
        }
    return None


async def save_description_cache(
    media_hash: str,
    media_type: str,
    description: str,
) -> None:
    """保存媒体识别描述到对应的描述缓存表。

    按 ``media_type`` 路由到 MediaManager：
    - ``image`` / ``emoji`` → ``ImageDescriptions`` 表（type 为动态值）
    - ``voice`` → ``VoiceDescriptions`` 表
    - ``video`` → ``VideoDescriptions`` 表

    描述写入缓存后，converter 收媒体时会以 ``use_cache=True`` 查该缓存，
    命中后描述直接替换占位符进入上下文（如 ``[视频(media_id):描述]``）。

    Args:
        media_hash: 媒体哈希值（作为描述缓存的主键）
        media_type: 媒体类型，``image`` / ``emoji`` / ``voice`` / ``video``
        description: 媒体识别描述文本

    Returns:
        None
    """
    _validate_non_empty(media_hash, "media_hash")
    _validate_media_type(media_type)
    _validate_non_empty(description, "description")
    await _get_media_manager().save_description_cache(
        media_hash=media_hash,
        media_type=media_type,
        description=description,
    )


async def get_media_file(media_hash: str) -> str | None:
    """根据媒体哈希读取落盘文件的 base64 内容。

    先经 MediaManager 查询媒体记录的 path，再按该路径读文件并编码为
    base64。文件不存在或读取失败（例如已被清理）时返回 None。

    Args:
        media_hash: 媒体哈希值（image_id / voice_id / video_id）

    Returns:
        base64 编码的文件内容；文件不存在或读取失败时返回 None
    """
    _validate_non_empty(media_hash, "media_hash")
    return await _get_media_manager().get_media_file(media_hash)


__all__ = [
    "API_VERSION",
    "recognize_media",
    "recognize_batch",
    "save_media_info",
    "get_media_info",
    "save_description_cache",
    "get_media_file",
]
