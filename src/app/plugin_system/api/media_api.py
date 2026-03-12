"""
Media API 模块。

提供媒体识别、批量识别与媒体信息查询能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    if media_type not in {"image", "emoji"}:
        raise ValueError("media_type 必须是 'image' 或 'emoji'")


async def recognize_media(
    base64_data: str,
    media_type: str,
    use_cache: bool = True,
) -> str | None:
    """识别媒体内容（图片或表情包）。

    Args:
        base64_data: Base64 编码的媒体内容
        media_type: 媒体类型
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

    Args:
        media_hash: 媒体哈希
        media_type: 媒体类型
        file_path: 文件路径，可选
        description: 媒体描述，可选
        vlm_processed: 是否已完成 VLM 识别

    Returns:
        None
    """
    _validate_non_empty(media_hash, "media_hash")
    _validate_media_type(media_type)
    if file_path is not None:
        _validate_non_empty(file_path, "file_path")
    return await _get_media_manager().save_media_info(
        media_hash=media_hash,
        media_type=media_type,
        file_path=file_path,
        description=description,
        vlm_processed=vlm_processed,
    )


async def get_media_info(media_hash: str) -> dict[str, Any] | None:
    """根据哈希值或路径获取媒体信息。

    Args:
        media_hash: 媒体哈希或文件路径

    Returns:
        媒体信息字典，未找到则返回 None
    """
    _validate_non_empty(media_hash, "media_hash")
    info = await _get_media_manager().get_media_info(media_hash)
    if info is not None:
        return info
    from src.core.models.sql_alchemy import Images
    from src.kernel.db.core.session import get_db_session
    from sqlalchemy import select

    async with get_db_session() as session:
        stmt = select(Images).where(Images.image_id == media_hash)
        result = await session.execute(stmt)
        media = result.scalar_one_or_none()
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
    return None


__all__ = [
    "recognize_media",
    "recognize_batch",
    "save_media_info",
    "get_media_info",
]
