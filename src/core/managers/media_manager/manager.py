"""媒体管理器门面。

聚合 :mod:`src.core.managers.media_manager` 子包内各组件，对外暴露统一
的 ``MediaManager`` API。

设计原则：
- Facade 仅做编排与转发，业务逻辑全部位于子组件
- 子组件通过构造注入，互相之间通过引用解耦
- 对外保留单例访问 ``get_media_manager()`` / ``initialize_media_manager()``
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.core.managers.media_manager.cache import MediaCache
from src.core.managers.media_manager.cleanup import (
    MediaCleanup,
    start_cleanup_scheduler_async,
)
from src.core.managers.media_manager.config import MediaConfig
from src.core.managers.media_manager.engines import ASREngine, VLMEngine
from src.core.managers.media_manager.event_handlers import MediaEventHandlers
from src.core.managers.media_manager.file_store import MediaFileStore
from src.core.managers.media_manager.recognition import MediaRecognition
from src.core.managers.media_manager.repository import MediaRepository
from src.core.managers.media_manager.utils import compute_media_hash
from src.kernel.logger import get_logger

logger = get_logger("media_manager")

# 单例实例
_media_manager: "MediaManager | None" = None


class MediaManager:
    """媒体管理器门面。

    管理图片、表情包、语音、视频等媒体资源的识别、存储和检索。
    通过组合 :class:`MediaConfig` / :class:`MediaFileStore` / :class:`MediaRepository` /
    :class:`MediaCache` / :class:`VLMEngine` / :class:`ASREngine` /
    :class:`MediaEventHandlers` / :class:`MediaRecognition` / :class:`MediaCleanup`
    等子组件，对外提供统一 API。

    Examples:
        >>> manager = get_media_manager()
        >>> description = await manager.recognize_image(base64_data, "image")
        >>> await manager.save_media_info(...)
    """

    def __init__(self) -> None:
        """初始化媒体管理器，构造并串联各子组件。"""
        # 配置与状态
        self._config = MediaConfig()

        # 文件落盘
        self._file_store = MediaFileStore(self._config)

        # DB 仓储与缓存
        self._repository = MediaRepository()
        self._cache = MediaCache()

        # 引擎（VLM/ASR）
        self._vlm_engine = VLMEngine(self._config)
        self._asr_engine = ASREngine(self._config)

        # 事件链默认处理器（订阅 EventBus）
        self._event_handlers = MediaEventHandlers(
            self._vlm_engine,
            self._asr_engine,
        )

        # 识别编排
        self._recognition = MediaRecognition(
            self._file_store,
            self._repository,
            self._cache,
            self._vlm_engine,
            self._asr_engine,
        )

        # 清理调度
        self._cleanup = MediaCleanup(self._config)
        start_cleanup_scheduler_async(self._cleanup)

    # ──────────────────────────────────────────
    # 公共 API：媒体识别控制
    # ──────────────────────────────────────────

    def skip_recognition_for_stream(
        self,
        stream_id: str,
        media_types: Iterable[str] | None = None,
    ) -> None:
        """注册指定聊天流跳过媒体识别。

        Args:
            stream_id: 要跳过识别的聊天流 ID
            media_types: 要跳过的媒体类型集合。为 ``None`` 时表示跳过所有类型。
        """
        self._config.skip_recognition_for_stream(stream_id, media_types)

    def unskip_recognition_for_stream(self, stream_id: str) -> None:
        """取消指定聊天流的媒体识别跳过。

        Args:
            stream_id: 要恢复识别的聊天流 ID
        """
        self._config.unskip_recognition_for_stream(stream_id)

    def should_skip_recognition(
        self,
        stream_id: str,
        media_type: str | None = None,
    ) -> bool:
        """查询指定聊天流是否应跳过媒体识别。

        Args:
            stream_id: 聊天流 ID
            media_type: 待识别媒体的类型；省略时表示整流粒度查询。

        Returns:
            True 表示该聊天流（针对给定媒体类型）应跳过识别
        """
        return self._config.should_skip_recognition(stream_id, media_type)

    # ──────────────────────────────────────────
    # 公共 API：媒体识别
    # ──────────────────────────────────────────

    async def recognize_media(
        self,
        base64_data: str,
        media_type: str,
        use_cache: bool = True,
        stream_id: str = "",
        skip_recognition: bool = False,
    ) -> str | None:
        """识别媒体内容（图片、表情包、语音或视频），并落盘入库。

        Args:
            base64_data: base64 编码的媒体数据（语音为 WAV）
            media_type: 媒体类型，"image"、"emoji"、"voice" 或 "video"
            use_cache: 是否使用缓存（默认 True）
            stream_id: 聊天流 ID，传入事件供处理器按流决策
            skip_recognition: 为 True 时仅落盘入库，不识别（默认 False）。

        Returns:
            媒体的文字描述；``skip_recognition=True`` 或识别失败时返回 None
        """
        return await self._recognition.recognize_media(
            base64_data=base64_data,
            media_type=media_type,
            use_cache=use_cache,
            stream_id=stream_id,
            skip_recognition=skip_recognition,
        )

    async def recognize_batch(
        self,
        media_list: list[tuple[str, str]],
        use_cache: bool = True,
    ) -> list[tuple[int, str | None]]:
        """批量识别多个媒体。

        Args:
            media_list: [(base64_data, media_type), ...] 列表
            use_cache: 是否使用缓存

        Returns:
            [(index, description), ...] 列表，description 为 None 表示识别失败
        """
        return await self._recognition.recognize_batch(media_list, use_cache=use_cache)

    # ──────────────────────────────────────────
    # 公共 API：数据库操作
    # ──────────────────────────────────────────

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
        await self._repository.save_media_info(
            media_hash=media_hash,
            media_type=media_type,
            file_path=file_path,
            description=description,
            vlm_processed=vlm_processed,
        )

    async def get_media_info(self, media_hash: str) -> dict[str, Any] | None:
        """根据哈希值获取媒体信息。

        Args:
            media_hash: 媒体哈希值

        Returns:
            媒体信息字典，不存在返回 None
        """
        return await self._repository.get_media_info(media_hash)

    async def save_voice_info(
        self,
        voice_hash: str,
        file_path: str | None = None,
        description: str | None = None,
        asr_processed: bool = False,
    ) -> None:
        """保存语音信息到数据库。

        Args:
            voice_hash: 语音哈希值（作为唯一标识）
            file_path: 语音文件路径（可选）
            description: ASR 识别文本（可选）
            asr_processed: 是否已经过 ASR 识别
        """
        await self._repository.save_voice_info(
            voice_hash=voice_hash,
            file_path=file_path,
            description=description,
            asr_processed=asr_processed,
        )

    async def get_voice_info(self, voice_hash: str) -> dict[str, Any] | None:
        """根据哈希值获取语音信息。

        Args:
            voice_hash: 语音哈希值

        Returns:
            语音信息字典，不存在返回 None
        """
        return await self._repository.get_voice_info(voice_hash)

    async def save_video_info(
        self,
        video_hash: str,
        file_path: str | None = None,
        description: str | None = None,
        video_processed: bool = False,
    ) -> None:
        """保存视频信息到数据库。

        Args:
            video_hash: 视频哈希值（作为唯一标识）
            file_path: 视频文件路径（可选）
            description: 视频识别文本（可选）
            video_processed: 是否已经过视频识别
        """
        await self._repository.save_video_info(
            video_hash=video_hash,
            file_path=file_path,
            description=description,
            video_processed=video_processed,
        )

    async def get_video_info(self, video_hash: str) -> dict[str, Any] | None:
        """根据哈希值获取视频信息。

        Args:
            video_hash: 视频哈希值

        Returns:
            视频信息字典，不存在返回 None
        """
        return await self._repository.get_video_info(video_hash)

    # ──────────────────────────────────────────
    # 静态 API：哈希计算（供外部模块直接调用）
    # ──────────────────────────────────────────

    @staticmethod
    def compute_media_hash(data: str) -> str:
        """计算媒体数据的哈希值（即 Images 表的 image_id）。

        与内部识别流程使用相同的哈希算法，确保哈希值可在 Images 表中回查。
        供外部模块（如 StreamManager 序列化入库时）在剔除 base64 ``data`` 前
        计算并保留 image_id，避免 data 被丢弃后无法按哈希找回图片信息。

        Args:
            data: 待哈希的数据（base64 字符串）

        Returns:
            十六进制哈希字符串
        """
        return compute_media_hash(data)


# ──────────────────────────────────────────
# 单例访问
# ──────────────────────────────────────────


def get_media_manager() -> MediaManager:
    """获取媒体管理器单例。

    Returns:
        MediaManager 实例
    """
    global _media_manager
    if _media_manager is None:
        _media_manager = MediaManager()
    return _media_manager


def initialize_media_manager() -> MediaManager:
    """初始化媒体管理器（用于显式初始化）。

    Returns:
        MediaManager 实例
    """
    global _media_manager
    _media_manager = MediaManager()
    return _media_manager
