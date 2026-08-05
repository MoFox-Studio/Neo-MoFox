"""媒体描述缓存组件。

提供 ImageDescriptions / VoiceDescriptions / VideoDescriptions 三张表的
描述缓存读写。三张表结构镜像一致，差异仅在 ``*_description_hash`` 字段名与
``type`` 取值（image/emoji 为动态值，voice/video 固定为 "voice"/"video"）。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from src.core.models.sql_alchemy import (
    ImageDescriptions,
    VideoDescriptions,
    VoiceDescriptions,
)
from src.kernel.db import QueryBuilder, invalidate_model_cache
from src.kernel.db.core.session import get_db_session
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaCache:
    """媒体描述缓存读写。"""

    async def get_cached_description(
        self,
        media_hash: str,
        media_type: str,
    ) -> str | None:
        """从数据库缓存获取描述。

        Args:
            media_hash: 媒体哈希值
            media_type: 媒体类型

        Returns:
            缓存的描述，不存在返回 None
        """
        try:
            desc: Any = await (
                QueryBuilder(ImageDescriptions)
                .filter(image_description_hash=media_hash, type=media_type)
                .order_by("-timestamp")
                .first()
            )
            return desc.description if desc else None

        except Exception as e:
            logger.debug(f"查询缓存失败: {e}")
            return None

    async def save_description_cache(
        self,
        media_hash: str,
        media_type: str,
        description: str,
    ) -> None:
        """保存描述到缓存。

        Args:
            media_hash: 媒体哈希值
            media_type: 媒体类型
            description: 描述文本
        """
        try:
            created = False
            async with get_db_session() as session:
                stmt = (
                    select(ImageDescriptions)
                    .where(
                        ImageDescriptions.image_description_hash == media_hash,
                        ImageDescriptions.type == media_type,
                    )
                    .order_by(ImageDescriptions.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if not existing:
                    new_desc = ImageDescriptions(
                        image_description_hash=media_hash,
                        type=media_type,
                        description=description,
                        timestamp=time.time(),
                    )
                    session.add(new_desc)
                    created = True
                    await session.commit()
                    logger.debug(f"保存描述缓存: {media_hash[:8]}...")
            if created:
                invalidate_model_cache(ImageDescriptions)

        except Exception as e:
            logger.error(f"保存描述缓存失败: {e}", exc_info=True)

    async def get_cached_voice_description(self, voice_hash: str) -> str | None:
        """从数据库缓存获取语音识别结果。

        镜像 :meth:`get_cached_description` 的语义，但查询 ``VoiceDescriptions`` 表。
        type 固定为 ``voice``。

        Args:
            voice_hash: 语音哈希值

        Returns:
            缓存的描述，不存在返回 None
        """
        try:
            desc: Any = await (
                QueryBuilder(VoiceDescriptions)
                .filter(voice_description_hash=voice_hash, type="voice")
                .order_by("-timestamp")
                .first()
            )
            return desc.description if desc else None

        except Exception as e:
            logger.debug(f"查询语音缓存失败: {e}")
            return None

    async def save_voice_description_cache(
        self,
        voice_hash: str,
        description: str,
    ) -> None:
        """保存语音识别结果到缓存。

        镜像 :meth:`save_description_cache` 的语义，但写入 ``VoiceDescriptions`` 表。
        type 固定为 ``voice``。

        Args:
            voice_hash: 语音哈希值
            description: ASR 识别文本
        """
        try:
            created = False
            async with get_db_session() as session:
                stmt = (
                    select(VoiceDescriptions)
                    .where(
                        VoiceDescriptions.voice_description_hash == voice_hash,
                        VoiceDescriptions.type == "voice",
                    )
                    .order_by(VoiceDescriptions.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if not existing:
                    new_desc = VoiceDescriptions(
                        voice_description_hash=voice_hash,
                        type="voice",
                        description=description,
                        timestamp=time.time(),
                    )
                    session.add(new_desc)
                    created = True
                    await session.commit()
                    logger.debug(f"保存语音描述缓存: {voice_hash[:8]}...")
            if created:
                invalidate_model_cache(VoiceDescriptions)

        except Exception as e:
            logger.error(f"保存语音描述缓存失败: {e}", exc_info=True)

    async def get_cached_video_description(self, video_hash: str) -> str | None:
        """从数据库缓存获取视频识别结果。

        镜像 :meth:`get_cached_voice_description` 的语义，但查询 ``VideoDescriptions`` 表。
        type 固定为 ``video``。

        Args:
            video_hash: 视频哈希值

        Returns:
            缓存的描述，不存在返回 None
        """
        try:
            desc: Any = await (
                QueryBuilder(VideoDescriptions)
                .filter(video_description_hash=video_hash, type="video")
                .order_by("-timestamp")
                .first()
            )
            return desc.description if desc else None

        except Exception as e:
            logger.debug(f"查询视频缓存失败: {e}")
            return None

    async def save_video_description_cache(
        self,
        video_hash: str,
        description: str,
    ) -> None:
        """保存视频识别结果到缓存。

        镜像 :meth:`save_voice_description_cache` 的语义，但写入 ``VideoDescriptions`` 表。
        type 固定为 ``video``。

        Args:
            video_hash: 视频哈希值
            description: 视频识别文本
        """
        try:
            created = False
            async with get_db_session() as session:
                stmt = (
                    select(VideoDescriptions)
                    .where(
                        VideoDescriptions.video_description_hash == video_hash,
                        VideoDescriptions.type == "video",
                    )
                    .order_by(VideoDescriptions.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if not existing:
                    new_desc = VideoDescriptions(
                        video_description_hash=video_hash,
                        type="video",
                        description=description,
                        timestamp=time.time(),
                    )
                    session.add(new_desc)
                    created = True
                    await session.commit()
                    logger.debug(f"保存视频描述缓存: {video_hash[:8]}...")
            if created:
                invalidate_model_cache(VideoDescriptions)

        except Exception as e:
            logger.error(f"保存视频描述缓存失败: {e}", exc_info=True)
