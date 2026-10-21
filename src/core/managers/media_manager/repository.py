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

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.sql_alchemy import Images, Videos, Voices
from src.kernel.db import QueryBuilder, invalidate_model_cache, run_write_transaction
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaRepository:
    """媒体信息数据库读写。"""

    async def _save_info(
        self,
        model: type[Images] | type[Voices] | type[Videos],
        hash_field: str,
        processed_field: str,
        media_hash: str,
        media_type: str,
        file_path: str | None,
        description: str | None,
        processed: bool,
    ) -> None:
        """在短写事务内协调 hash/path 两种身份，保留已有描述与识别结果。"""
        effective_path = file_path or media_hash

        async def save(session: AsyncSession) -> None:
            """路径命中优先，避免 hash 和 path 分别指向两行时改坏唯一键。"""
            result = await session.execute(
                select(model)
                .where(
                    (getattr(model, hash_field) == media_hash)
                    | (model.path == effective_path)
                )
                .order_by(
                    (model.path == effective_path).desc(),
                    model.timestamp.desc(), model.id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
            existing = result.scalars().first()
            if existing is not None:
                existing.count += 1
                if getattr(existing, hash_field) != media_hash:
                    setattr(existing, hash_field, media_hash)
                    # 身份变更后按 hash 回查应选中本次实际写入的路径。
                    existing.timestamp = time.time()
                if file_path:
                    existing.path = file_path
                if description:
                    existing.description = description
                if processed:
                    setattr(existing, processed_field, True)
            else:
                dialect = session.get_bind().dialect.name
                if dialect == "sqlite":
                    insert = sqlite_insert(model)
                elif dialect == "postgresql":
                    insert = postgresql_insert(model)
                else:
                    raise ValueError(f"不支持的媒体数据库类型: {dialect}")
                # PostgreSQL 的并发首次插入也由唯一键协调，不能只依赖 SELECT。
                insert = insert.values(**{
                    hash_field: media_hash, "path": effective_path,
                    "type": media_type, "description": description,
                    "timestamp": time.time(), processed_field: processed, "count": 1,
                })
                updates = {
                    hash_field: media_hash, "count": model.count + 1,
                    "timestamp": case(
                        (getattr(model, hash_field) != media_hash, insert.excluded.timestamp),
                        else_=model.timestamp,
                    ),
                }
                if description:
                    updates["description"] = description
                if processed:
                    updates[processed_field] = True
                await session.execute(insert.on_conflict_do_update(
                    index_elements=[model.path], set_=updates,
                ))

        await run_write_transaction(save)
        invalidate_model_cache(model)
        logger.debug(f"已保存{media_type}记录: {media_hash[:8]}...")

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
            await self._save_info(
                Images, "image_id", "vlm_processed",
                media_hash, media_type, file_path, description, vlm_processed,
            )
        except Exception as exc:
            logger.error(f"保存媒体信息失败: {exc}", exc_info=True)

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
            await self._save_info(
                Voices, "voice_id", "asr_processed",
                voice_hash, "voice", file_path, description, asr_processed,
            )
        except Exception as exc:
            logger.error(f"保存语音信息失败: {exc}", exc_info=True)

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
            await self._save_info(
                Videos, "video_id", "video_processed",
                video_hash, "video", file_path, description, video_processed,
            )
        except Exception as exc:
            logger.error(f"保存视频信息失败: {exc}", exc_info=True)

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
