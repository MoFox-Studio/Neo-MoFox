"""媒体识别编排组件。

作为媒体识别流程的统一入口，串联缓存查询 → 落盘 → 入库 →
事件链分发 → 描述回写等步骤。VLM/ASR 作为默认处理器通过事件链调用，
视频仅通过事件链交给第三方插件处理（无内置引擎）。
"""

from __future__ import annotations

from typing import Any

from src.core.components.types import EventType, MediaEngine
from src.core.managers.media_manager.cache import MediaCache
from src.core.managers.media_manager.engines import ASREngine, VLMEngine
from src.core.managers.media_manager.file_store import MediaFileStore
from src.core.managers.media_manager.repository import MediaRepository
from src.core.managers.media_manager.utils import compute_hash
from src.kernel.event import get_event_bus
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaRecognition:
    """媒体识别流程编排器。

    Args:
        file_store: 落盘组件
        repository: DB 入库组件
        cache: 描述缓存组件
        vlm_engine: VLM 引擎（兜底用）
        asr_engine: ASR 引擎（兜底用）
    """

    def __init__(
        self,
        file_store: MediaFileStore,
        repository: MediaRepository,
        cache: MediaCache,
        vlm_engine: VLMEngine,
        asr_engine: ASREngine,
    ) -> None:
        self._file_store = file_store
        self._repository = repository
        self._cache = cache
        self._vlm_engine = vlm_engine
        self._asr_engine = asr_engine

    async def recognize_media(
        self,
        base64_data: str,
        media_type: str,
        use_cache: bool = True,
        stream_id: str = "",
        skip_recognition: bool = False,
    ) -> str | None:
        """识别媒体内容（图片、表情包、语音或视频），并落盘入库。

        统一流程：计算哈希 → 查缓存 → 落盘入库 → 发事件让处理器识别 →
        回写 description。VLM/ASR 作为默认处理器通过事件链调用，
        视频仅通过事件链交给第三方插件处理（无内置引擎），
        第三方插件可订阅 ``ON_MEDIA_RECOGNIZE`` 拦截改写。

        Args:
            base64_data: base64 编码的媒体数据（语音为 WAV）
            media_type: 媒体类型，"image"、"emoji"、"voice" 或 "video"
            use_cache: 是否使用缓存（默认 True）
            stream_id: 聊天流 ID，传入事件供处理器按流决策
            skip_recognition: 为 True 时仅落盘入库，不识别（默认 False）。
                对 image/emoji/voice/video 均生效。

        Returns:
            媒体的文字描述；``skip_recognition=True`` 或识别失败时返回 None
        """
        try:
            is_voice = media_type == "voice"
            is_video = media_type == "video"
            media_hash = compute_hash(base64_data)

            if use_cache:
                if is_voice:
                    cached = await self._cache.get_cached_voice_description(media_hash)
                elif is_video:
                    cached = await self._cache.get_cached_video_description(media_hash)
                else:
                    cached = await self._cache.get_cached_description(
                        media_hash, media_type
                    )
                if cached:
                    logger.debug(f"从缓存获取{media_type}描述: {media_hash[:8]}...")
                    return cached

            pending_path = await self._file_store.save_to_pending(
                base64_data,
                media_hash,
                media_type,
            )
            await self._file_store.move_to_category_folder(
                pending_path, media_type, media_hash
            )
            target_path = (
                self._file_store.category_folder_for(media_type) / pending_path.name
            )

            await self._repository.save_recognized_media(
                media_hash,
                media_type,
                str(target_path),
                description=None,
                processed=False,
            )

            if skip_recognition:
                logger.debug(f"已持久化{media_type}（跳过识别）: {media_hash[:8]}...")
                return None

            if is_voice:
                engine = MediaEngine.ASR
            elif is_video:
                engine = MediaEngine.VIDEO
            else:
                engine = MediaEngine.VLM
            description = await self._dispatch_recognition_event(
                base64_data=base64_data,
                media_hash=media_hash,
                media_type=media_type,
                engine=engine,
                stream_id=stream_id,
            )

            if description:
                await self._save_cache_and_update_media(
                    media_hash,
                    media_type,
                    description,
                    str(target_path),
                )
                logger.info(f"成功识别{media_type}: {description[:50]}...")

            return description

        except Exception as e:
            logger.error(f"识别{media_type}失败: {e}", exc_info=True)
            return None

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
        results = []
        for idx, (base64_data, media_type) in enumerate(media_list):
            description = await self.recognize_media(
                base64_data,
                media_type,
                use_cache=use_cache,
            )
            results.append((idx, description))
        return results

    async def _dispatch_recognition_event(
        self,
        base64_data: str,
        media_hash: str,
        media_type: str,
        engine: MediaEngine,
        stream_id: str,
    ) -> str | None:
        """发布 ON_MEDIA_RECOGNIZE 事件，返回处理器链最终回写的 description。

        事件 params 的 key 集合在链中不可变，处理器只能修改已有字段的值。
        如果没有任何处理器订阅，回退到内置引擎调用（视频无内置引擎，返回 None）。

        Args:
            base64_data: base64 编码的媒体数据
            media_hash: 媒体哈希值
            media_type: "image"、"emoji"、"voice" 或 "video"
            engine: 引擎类型（VLM、ASR 或 VIDEO）
            stream_id: 聊天流 ID

        Returns:
            识别出的文字描述，无处理器或识别失败时返回 None
        """
        params: dict[str, Any] = {
            "media_hash": media_hash,
            "media_type": media_type,
            "engine": engine.value,
            "base64_data": base64_data,
            "stream_id": stream_id,
            "description": None,
            "engine_processed": False,
            "skip_engine": False,
        }

        try:
            bus = get_event_bus()
            _, final_params = await bus.publish(
                EventType.ON_MEDIA_RECOGNIZE.value, params
            )
        except Exception as e:
            logger.warning(f"发布媒体识别事件失败，回退内置引擎: {e}")
            final_params = params

        description = final_params.get("description")
        if isinstance(description, str) and description:
            return description

        if final_params.get("skip_engine"):
            return None

        return await self._call_builtin_engine(base64_data, media_type, engine)

    async def _call_builtin_engine(
        self,
        base64_data: str,
        media_type: str,
        engine: MediaEngine,
    ) -> str | None:
        """调用内置识别引擎（VLM 或 ASR），作为事件链无处理器时的兜底。

        视频无内置引擎，直接返回 None，由第三方插件通过事件链处理。

        Args:
            base64_data: base64 编码的媒体数据
            media_type: "image"、"emoji"、"voice" 或 "video"
            engine: 引擎类型

        Returns:
            识别结果文本，失败或无内置引擎时返回 None
        """
        if engine == MediaEngine.ASR:
            if not self._asr_engine.is_available:
                logger.debug("ASR 模型不可用，跳过语音识别")
                return None
            return await self._asr_engine.recognize(base64_data)

        if engine == MediaEngine.VIDEO:
            logger.debug("视频识别无内置引擎，跳过（需第三方插件订阅事件）")
            return None

        if not self._vlm_engine.is_available:
            logger.debug(f"VLM 模型不可用，跳过{media_type}识别")
            return None
        return await self._vlm_engine.recognize(base64_data, media_type)

    async def _save_cache_and_update_media(
        self,
        media_hash: str,
        media_type: str,
        description: str,
        file_path: str,
    ) -> None:
        """识别成功后写缓存并更新入库记录为 processed。

        Args:
            media_hash: 媒体哈希值
            media_type: "image"、"emoji"、"voice" 或 "video"
            description: 识别结果文本
            file_path: 文件路径
        """
        await self._cache.save_description_cache(
            media_hash, media_type, description
        )

        await self._repository.save_recognized_media(
            media_hash,
            media_type,
            file_path,
            description=description,
            processed=True,
        )
