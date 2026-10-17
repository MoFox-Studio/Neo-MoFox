"""媒体文件落盘组件。

负责将 base64 编码的媒体数据写入 pending 目录，并在识别完成后
将文件从 pending 移动到对应的分类目录（images/emojis/voices/videos）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.core.managers.media_manager.config import MediaConfig
from src.core.managers.media_manager.utils import extract_clean_base64
from src.core.utils.base64_helper import base64_decode_to_bytes
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaFileStore:
    """媒体文件落盘与跨目录移动。

    Args:
        config: 提供文件夹路径与媒体类型映射的配置组件
    """

    def __init__(self, config: MediaConfig) -> None:
        self._config = config

    def category_folder_for(self, media_type: str) -> Path:
        """返回媒体类型对应的分类目录。

        Args:
            media_type: "image"、"emoji"、"voice" 或 "video"

        Returns:
            对应的分类目录路径
        """
        if media_type == "image":
            return self._config.images_folder
        if media_type == "voice":
            return self._config.voices_folder
        if media_type == "video":
            return self._config.videos_folder
        return self._config.emojis_folder

    async def save_to_pending(
        self,
        base64_data: str,
        media_hash: str,
        media_type: str,
    ) -> Path:
        """保存媒体文件到待识别文件夹。

        Args:
            base64_data: base64 编码的媒体数据
            media_hash: 媒体哈希值
            media_type: 媒体类型

        Returns:
            保存的文件路径
        """
        try:
            clean_base64 = extract_clean_base64(base64_data)

            binary_data = await asyncio.to_thread(
                base64_decode_to_bytes,
                clean_base64,
            )

            if media_type == "image":
                ext = ".jpg"
            elif media_type == "voice":
                ext = ".wav"
            elif media_type == "video":
                ext = ".mp4"
            else:
                ext = ".png"

            filename = f"{media_hash[:16]}_{media_type}{ext}"
            file_path = self._config.pending_folder / filename

            await asyncio.to_thread(file_path.write_bytes, binary_data)
            logger.debug(f"媒体已保存到待识别文件夹: {filename}")

            return file_path
        except Exception as e:
            logger.error(f"保存到待识别文件夹失败: {e}")
            # 返回一个虚拟路径，不影响后续流程
            return self._config.pending_folder / f"{media_hash[:16]}_error.tmp"

    async def move_to_category_folder(
        self,
        source_path: Path,
        media_type: str,
        media_hash: str,
    ) -> None:
        """将识别完成的文件移动到对应的分类文件夹。

        Args:
            source_path: 源文件路径（待识别文件夹中的文件）
            media_type: 媒体类型
            media_hash: 媒体哈希值
        """
        try:
            if not await asyncio.to_thread(source_path.exists):
                logger.debug(f"源文件不存在，跳过移动: {source_path.name}")
                return

            target_folder = self.category_folder_for(media_type)
            target_path = target_folder / source_path.name

            if await asyncio.to_thread(target_path.exists):
                await asyncio.to_thread(source_path.unlink)
                logger.debug(f"目标文件已存在，删除源文件: {source_path.name}")
                return

            await asyncio.to_thread(source_path.rename, target_path)
            logger.debug(f"文件已移动到 {media_type} 文件夹: {target_path.name}")
        except Exception as e:
            logger.error(f"移动文件失败: {e}")
