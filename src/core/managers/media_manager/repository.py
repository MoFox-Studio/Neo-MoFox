"""媒体信息数据库仓储组件。

提供 Images / Voices / Videos 三张表的写入与查询能力：
- ``save_*``：哈希相同则计数累加并更新描述/识别状态，否则插入新记录
- ``get_*``：按哈希查询最新记录并返回字段字典

三张表结构镜像一致，差异仅在 ``*_processed`` 字段名映射：
- Images → ``vlm_processed``
- Voices → ``asr_processed``
- Videos → ``video_processed``
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from src.core.models.sql_alchemy import Images, Videos, Voices
from src.kernel.db import QueryBuilder, invalidate_model_cache
from src.kernel.db.core.session import get_db_session
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaRepository:
    """媒体信息数据库读写。"""

    async def save_recognized_media(
        self,
        media_hash: str,
        media_type: str,
        file_path: str,
        description: str | None,
        processed: bool,
    ) -> None:
        """统一入库入口，按 media_type 写 Images / Voices / Videos 表。

        Args:
            media_hash: 媒体哈希值
            media_type: "image"、"emoji"、"voice" 或 "video"
            file_path: 文件路径
            description: 描述文本（可 None）
            processed: 是否已经过引擎识别
        """
        if media_type == "voice":
            await self.save_voice_info(
                voice_hash=media_hash,
                file_path=file_path,
                description=description,
                asr_processed=processed,
            )
        elif media_type == "video":
            await self.save_video_info(
                video_hash=media_hash,
                file_path=file_path,
                description=description,
                video_processed=processed,
            )
        else:
            await self.save_media_info(
                media_hash=media_hash,
                media_type=media_type,
                file_path=file_path,
                description=description,
                vlm_processed=processed,
            )

    async def save_media_info(
        self,
        media_hash: str,
        media_type: str,
        file_path: str | None = None,
        description: str | None = None,
        vlm_processed: bool = False,
    ) -> None:
        """保存媒体信息到数据库。

        Args:
            media_hash: 媒体哈希值（作为唯一标识）
            media_type: 媒体类型（image/emoji）
            file_path: 文件路径（可选）
            description: 描述文本（可选）
            vlm_processed: 是否已经过 VLM 处理
        """
        try:
            changed = False
            async with get_db_session() as session:
                # 查找现有记录：image_id 与 path 都是唯一相关字段，需同时校验。
                # 之所以同时按 path 查询，是因为 images.path 上有 UNIQUE 约束，
                # 若只按 image_id 查询，同一 file_path 以不同 media_hash 写入时会触发
                # "UNIQUE constraint failed: images.path"。
                effective_path = file_path or media_hash
                stmt = (
                    select(Images)
                    .where(
                        (Images.image_id == media_hash)
                        | (Images.path == effective_path)
                    )
                    .order_by(Images.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    existing.count += 1
                    if existing.image_id != media_hash:
                        existing.image_id = media_hash
                    if file_path and existing.path != file_path:
                        existing.path = file_path
                    if description:
                        existing.description = description
                    if vlm_processed:
                        existing.vlm_processed = True
                    changed = True
                    logger.debug(
                        f"更新媒体记录: {media_hash[:8]}... count={existing.count}"
                    )
                else:
                    new_image = Images(
                        image_id=media_hash,
                        path=effective_path,
                        type=media_type,
                        description=description,
                        timestamp=time.time(),
                        vlm_processed=vlm_processed,
                        count=1,
                    )
                    session.add(new_image)
                    changed = True
                    logger.debug(f"创建新媒体记录: {media_hash[:8]}...")

                await session.commit()

            if changed:
                invalidate_model_cache(Images)

        except Exception as e:
            logger.error(f"保存媒体信息失败: {e}", exc_info=True)

    async def get_media_info(self, media_hash: str) -> dict[str, Any] | None:
        """根据哈希值获取媒体信息。

        Args:
            media_hash: 媒体哈希值

        Returns:
            媒体信息字典，不存在返回 None
        """
        try:
            media: Any = await (
                QueryBuilder(Images)
                .filter(image_id=media_hash)
                .order_by("-timestamp")
                .first()
            )
            if media is not None:
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

        except Exception as e:
            logger.error(f"查询媒体信息失败: {e}", exc_info=True)
            return None

    async def save_voice_info(
        self,
        voice_hash: str,
        file_path: str | None = None,
        description: str | None = None,
        asr_processed: bool = False,
    ) -> None:
        """保存语音信息到数据库。

        镜像 :meth:`save_media_info` 的语义，但写入 ``Voices`` 表。
        哈希值相同则累加计数并更新描述/识别状态，否则插入新记录。

        Args:
            voice_hash: 语音哈希值（作为唯一标识）
            file_path: 语音文件路径（可选）
            description: ASR 识别文本（可选）
            asr_processed: 是否已经过 ASR 识别
        """
        try:
            changed = False
            async with get_db_session() as session:
                # 同 save_media_info：voices.path 上有 UNIQUE 约束，
                # 需同时按 voice_id 与 path 校验，避免同一 file_path 不同 voice_hash
                # 写入时触发 "UNIQUE constraint failed: voices.path"。
                effective_path = file_path or voice_hash
                stmt = (
                    select(Voices)
                    .where(
                        (Voices.voice_id == voice_hash)
                        | (Voices.path == effective_path)
                    )
                    .order_by(Voices.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    existing.count += 1
                    if existing.voice_id != voice_hash:
                        existing.voice_id = voice_hash
                    if file_path and existing.path != file_path:
                        existing.path = file_path
                    if description:
                        existing.description = description
                    if asr_processed:
                        existing.asr_processed = True
                    changed = True
                    logger.debug(
                        f"更新语音记录: {voice_hash[:8]}... count={existing.count}"
                    )
                else:
                    new_voice = Voices(
                        voice_id=voice_hash,
                        path=effective_path,
                        type="voice",
                        description=description,
                        timestamp=time.time(),
                        asr_processed=asr_processed,
                        count=1,
                    )
                    session.add(new_voice)
                    changed = True
                    logger.debug(f"创建新语音记录: {voice_hash[:8]}...")

                await session.commit()
            if changed:
                invalidate_model_cache(Voices)

        except Exception as e:
            logger.error(f"保存语音信息失败: {e}", exc_info=True)

    async def get_voice_info(self, voice_hash: str) -> dict[str, Any] | None:
        """根据哈希值获取语音信息。

        Args:
            voice_hash: 语音哈希值

        Returns:
            语音信息字典，不存在返回 None
        """
        try:
            voice: Any = await (
                QueryBuilder(Voices)
                .filter(voice_id=voice_hash)
                .order_by("-timestamp")
                .first()
            )
            if voice is not None:
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
            return None

        except Exception as e:
            logger.error(f"查询语音信息失败: {e}", exc_info=True)
            return None

    async def save_video_info(
        self,
        video_hash: str,
        file_path: str | None = None,
        description: str | None = None,
        video_processed: bool = False,
    ) -> None:
        """保存视频信息到数据库。

        镜像 :meth:`save_voice_info` 的语义，但写入 ``Videos`` 表。
        哈希值相同则累加计数并更新描述/识别状态，否则插入新记录。

        Args:
            video_hash: 视频哈希值（作为唯一标识）
            file_path: 视频文件路径（可选）
            description: 视频识别文本（可选）
            video_processed: 是否已经过视频识别
        """
        try:
            changed = False
            async with get_db_session() as session:
                effective_path = file_path or video_hash
                stmt = (
                    select(Videos)
                    .where(
                        (Videos.video_id == video_hash)
                        | (Videos.path == effective_path)
                    )
                    .order_by(Videos.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    existing.count += 1
                    if existing.video_id != video_hash:
                        existing.video_id = video_hash
                    if file_path and existing.path != file_path:
                        existing.path = file_path
                    if description:
                        existing.description = description
                    if video_processed:
                        existing.video_processed = True
                    changed = True
                    logger.debug(
                        f"更新视频记录: {video_hash[:8]}... count={existing.count}"
                    )
                else:
                    new_video = Videos(
                        video_id=video_hash,
                        path=effective_path,
                        type="video",
                        description=description,
                        timestamp=time.time(),
                        video_processed=video_processed,
                        count=1,
                    )
                    session.add(new_video)
                    changed = True
                    logger.debug(f"创建新视频记录: {video_hash[:8]}...")

                await session.commit()
            if changed:
                invalidate_model_cache(Videos)

        except Exception as e:
            logger.error(f"保存视频信息失败: {e}", exc_info=True)

    async def get_video_info(self, video_hash: str) -> dict[str, Any] | None:
        """根据哈希值获取视频信息。

        Args:
            video_hash: 视频哈希值

        Returns:
            视频信息字典，不存在返回 None
        """
        try:
            video: Any = await (
                QueryBuilder(Videos)
                .filter(video_id=video_hash)
                .order_by("-timestamp")
                .first()
            )
            if video is not None:
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

        except Exception as e:
            logger.error(f"查询视频信息失败: {e}", exc_info=True)
            return None
