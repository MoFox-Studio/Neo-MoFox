
"""媒体管理器。

负责图片和表情包的识别、存储和管理。

功能：
- 使用 VLM 识别图片和表情包内容
- 缓存识别结果到数据库，避免重复识别
- 管理媒体文件的存储和检索
- 支持按哈希值去重，节省存储和计算资源

设计原则：
- 优先从缓存读取，减少 VLM 调用
- 使用哈希值标识图片，避免重复处理
- 异步处理，不阻塞主流程
- 异常友好，识别失败不影响消息流转
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from collections.abc import Iterable
from typing import Any
from sqlalchemy import select

from src.kernel.logger import get_logger
from src.app.plugin_system.api.llm_api import get_model_set_by_task
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.core.prompt import PromptTemplate, get_prompt_manager
from src.core.config import get_core_config
from src.core.utils.base64_helper import base64_decode_to_bytes
from src.kernel.scheduler import get_unified_scheduler, TriggerType
from src.kernel.db import QueryBuilder, invalidate_model_cache
from src.kernel.db.core.session import get_db_session
from src.core.components.types import EventType, MediaEngine
from src.core.models.sql_alchemy import Images, ImageDescriptions, Voices, VoiceDescriptions
from src.kernel.event import get_event_bus
from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text, Image

logger = get_logger("media_manager")

# 单例实例
_media_manager: "MediaManager | None" = None


class MediaManager:
    """媒体管理器。
    
    管理图片、表情包等媒体资源的识别、存储和检索。
    
    主要功能：
    1. VLM 识别：调用 VLM 模型识别图片/表情包内容
    2. 缓存管理：使用哈希值缓存识别结果
    3. 数据库存储：持久化媒体信息
    4. 去重优化：相同内容的图片只识别一次
    
    Examples:
        >>> manager = get_media_manager()
        >>> description = await manager.recognize_image(base64_data, "image")
        >>> await manager.save_media_info(...)
    """

    def __init__(self):
        """初始化媒体管理器。"""
        self._vlm_model_set = None
        # stream_id -> 跳过的媒体类型集合；值为 None 表示跳过所有类型
        self._skip_recognition_streams: dict[str, frozenset[str] | None] = {}
        self._initialize_vlm()
        self._initialize_asr()
        self._register_default_recognition_handlers()
        self._register_prompts()
        self._setup_media_folders()
        self._load_cleanup_config()
        # 两个独立的清理任务：缓存清理（pending 目录）与文件清理（images/emojis 目录）
        self._cache_cleanup_task_id: str | None = None
        self._file_cleanup_task_id: str | None = None
        self._start_cleanup_scheduler()

    def _initialize_vlm(self) -> None:
        """初始化 VLM 模型配置。"""
        try:
            self._vlm_model_set = get_model_set_by_task("vlm")
            self._vlm_available = self._vlm_model_set is not None
            
            if self._vlm_available:
                logger.info("VLM 模型已加载，媒体识别功能可用")
            else:
                logger.info("未配置 VLM 模型，媒体识别功能不可用")
        except Exception as e:
            logger.error(f"初始化 VLM 模型失败: {e}")

    def _initialize_asr(self) -> None:
        """初始化 ASR 模型配置。"""
        try:
            self._asr_model_set = get_model_set_by_task("voice")
            self._asr_available = self._asr_model_set is not None

            if self._asr_available:
                logger.info("ASR 模型已加载，语音识别功能可用")
            else:
                logger.info("未配置 ASR 模型，语音识别功能不可用")
        except Exception as e:
            self._asr_model_set = None
            self._asr_available = False
            logger.error(f"初始化 ASR 模型失败: {e}")

    def _register_prompts(self) -> None:
        """注册媒体识别相关的提示词模板。"""
        try:
            manager = get_prompt_manager()
            
            # 注册图片识别提示词
            custom_prompt = get_core_config().chat.image_recognition_prompt
            default_template = "描述这张图片的内容，包含主题、主要元素。若有文字或代码，完整转述。"
            image_prompt = PromptTemplate(
                name="media.image_recognition",
                template=custom_prompt if custom_prompt else default_template
            )
            manager.register_template(image_prompt)
            
            # 注册表情包识别提示词
            emoji_prompt = PromptTemplate(
                name="media.emoji_recognition",
                template="请简要描述这个表情包的内容和含义，用一句话概括。"
            )
            manager.register_template(emoji_prompt)
            
            logger.debug("媒体识别提示词模板已注册")
        except Exception as e:
            logger.warning(f"注册提示词模板失败: {e}")

    def _setup_media_folders(self) -> None:
        """设置媒体文件夹结构。"""
        try:
            # 媒体根目录
            self.media_root = Path("data/media_cache")

            # 子文件夹
            self.pending_folder = self.media_root / "pending"  # 待识别
            self.images_folder = self.media_root / "images"    # 识别完成的图片
            self.emojis_folder = self.media_root / "emojis"    # 识别完成的表情包
            self.voices_folder = self.media_root / "voices"    # 识别完成的语音

            # 创建所有必要的文件夹
            for folder in [
                self.pending_folder,
                self.images_folder,
                self.emojis_folder,
                self.voices_folder,
            ]:
                folder.mkdir(parents=True, exist_ok=True)

            logger.info(f"媒体文件夹已初始化: {self.media_root}")
        except Exception as e:
            logger.error(f"创建媒体文件夹失败: {e}")

    def _load_cleanup_config(self) -> None:
        """从核心配置加载媒体清理参数。

        媒体清理分为两类，各自拥有独立的配置：
        - 媒体缓存清理（pending 目录）：由 ``media_cache_cleanup_*`` 控制
        - 媒体文件清理（images/emojis 目录）：由 ``media_file_cleanup_*`` 控制

        配置加载失败时直接抛出异常，不使用 fallback 默认值，
        避免掩盖配置错误导致清理行为与预期不符。
        """
        config = get_core_config()
        chat_cfg = config.chat

        # pending 目录清理配置
        self._media_cache_cleanup_enabled = chat_cfg.media_cache_cleanup_enabled
        self._media_cache_cleanup_interval_hours = chat_cfg.media_cache_cleanup_interval_hours

        # images/emojis 目录清理配置
        self._media_file_cleanup_enabled = chat_cfg.media_file_cleanup_enabled
        self._media_file_max_age_days = chat_cfg.media_file_max_age_days
        self._media_file_max_total_size_mb = chat_cfg.media_file_max_total_size_mb
        self._media_file_cleanup_interval_hours = chat_cfg.media_file_cleanup_interval_hours

    def _start_cleanup_scheduler(self) -> None:
        """启动两个独立的定时清理任务。

        - 缓存清理任务：高频清理 pending 目录中的陈旧文件
        - 文件清理任务：按用户配置间隔清理 images/emojis 目录中的已识别文件
        """
        try:
            asyncio.create_task(self._register_cache_cleanup_task())
            asyncio.create_task(self._register_file_cleanup_task())
            logger.info(
                "媒体清理调度器已启动"
                f"(缓存清理每 {self._media_cache_cleanup_interval_hours}h，"
                f"文件清理每 {self._media_file_cleanup_interval_hours}h)"
            )
        except Exception as e:
            logger.error(f"启动清理调度器失败: {e}")

    async def _register_cache_cleanup_task(self) -> None:
        """注册 pending 目录的定时清理任务到调度器。"""
        try:
            if not self._media_cache_cleanup_enabled:
                logger.info("媒体缓存清理已禁用，跳过 pending 目录清理任务注册")
                return

            scheduler = get_unified_scheduler()
            interval_seconds = self._media_cache_cleanup_interval_hours * 3600

            schedule_id = await scheduler.create_schedule(
                callback=self._cleanup_pending_folder,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": interval_seconds},
                is_recurring=True,
                task_name="media_cache_cleanup",
                force_overwrite=True,
            )

            self._cache_cleanup_task_id = schedule_id
            logger.info(
                f"媒体缓存清理任务已注册(间隔 {self._media_cache_cleanup_interval_hours}h): {schedule_id}"
            )
        except Exception as e:
            logger.error(f"注册缓存清理任务失败: {e}")

    async def _register_file_cleanup_task(self) -> None:
        """注册 images/emojis 目录的定时清理任务到调度器。"""
        try:
            if not self._media_file_cleanup_enabled:
                logger.info("媒体文件清理已禁用，跳过 images/emojis 目录清理任务注册")
                return

            scheduler = get_unified_scheduler()
            interval_seconds = self._media_file_cleanup_interval_hours * 3600

            schedule_id = await scheduler.create_schedule(
                callback=self._cleanup_media_files,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": interval_seconds},
                is_recurring=True,
                task_name="media_file_cleanup",
                force_overwrite=True,
            )

            self._file_cleanup_task_id = schedule_id
            logger.info(
                f"媒体文件清理任务已注册(间隔 {self._media_file_cleanup_interval_hours}h): {schedule_id}"
            )
        except Exception as e:
            logger.error(f"注册文件清理任务失败: {e}")

    async def _cleanup_pending_folder(self) -> None:
        """清理待识别文件夹中的陈旧文件。

        删除 pending 目录中超过 5 分钟（300 秒）未被处理的文件。
        该任务独立调度，不受 images/emojis 目录清理配置影响。
        """
        try:
            if not self.pending_folder.exists():
                return

            current_time = time.time()
            cleanup_count = 0

            # 遍历所有待识别文件
            for file_path in self.pending_folder.iterdir():
                if not file_path.is_file():
                    continue

                # 获取文件修改时间
                file_mtime = file_path.stat().st_mtime

                # 如果文件超过5分钟未处理，删除它
                if current_time - file_mtime >= 300:  # 5分钟 = 300秒
                    try:
                        file_path.unlink()
                        cleanup_count += 1
                    except Exception as e:
                        logger.warning(f"删除文件失败 {file_path.name}: {e}")

            if cleanup_count > 0:
                logger.info(f"媒体缓存清理完成，删除了 {cleanup_count} 个陈旧文件")
        except Exception as e:
            logger.error(f"清理待识别文件夹失败: {e}")

    async def _cleanup_media_files(self) -> None:
        """清理 images/emojis/voices 目录中的已识别媒体文件。

        整合 images、emojis 与 voices 三个分类目录的清理，
        各目录按文件年龄和总容量两个维度清理：
        1. 删除超过 ``media_file_max_age_days`` 的文件
        2. 若总容量超过 ``media_file_max_total_size_mb``，从最旧文件开始删除直到达标
        """
        await self._cleanup_category_folder(self.images_folder)
        await self._cleanup_category_folder(self.emojis_folder)
        await self._cleanup_category_folder(self.voices_folder)

    async def _cleanup_category_folder(self, folder: Path) -> None:
        """清理已识别媒体分类文件夹中的陈旧文件。

        按文件年龄和总容量两个维度清理：
        1. 删除超过 ``media_file_max_age_days`` 的文件
        2. 若总容量超过 ``media_file_max_total_size_mb``，从最旧文件开始删除直到达标

        Args:
            folder: 要清理的文件夹路径
        """
        try:
            if not folder.exists():
                return

            now = time.time()
            deleted_count = 0

            # 收集所有文件及其修改时间和大小
            files: list[tuple[Path, float, int]] = []
            for file_path in folder.iterdir():
                if not file_path.is_file():
                    continue
                stat = file_path.stat()
                files.append((file_path, stat.st_mtime, stat.st_size))

            if not files:
                return

            # 按修改时间排序（旧 -> 新）
            files.sort(key=lambda x: x[1])

            # 阶段 1：按天数清理
            if self._media_file_max_age_days > 0:
                cutoff_time = now - self._media_file_max_age_days * 86400
                for file_path, mtime, _ in files:
                    if mtime < cutoff_time:
                        deleted_count += self._safe_delete_media(file_path)

            # 重新收集存活文件（阶段1可能已删除部分文件）
            if deleted_count > 0:
                files = [(fp, mt, sz) for fp, mt, sz in files if fp.exists()]

            # 阶段 2：按总容量裁剪
            if self._media_file_max_total_size_mb > 0:
                max_bytes = self._media_file_max_total_size_mb * 1024 * 1024
                total_size = sum(sz for _, _, sz in files)
                if total_size > max_bytes:
                    # 从最旧开始删除
                    for file_path, _, _ in files:
                        if total_size <= max_bytes:
                            break
                        try:
                            size = file_path.stat().st_size
                            file_path.unlink()
                            total_size -= size
                            deleted_count += 1
                        except Exception as e:
                            logger.warning(f"裁剪媒体文件失败 {file_path.name}: {e}")

            if deleted_count > 0:
                logger.info(
                    f"媒体文件清理完成 [{folder.name}]，删除了 {deleted_count} 个文件"
                )
        except Exception as e:
            logger.error(f"清理媒体分类文件夹失败 [{folder.name}]: {e}")

    @staticmethod
    def _safe_delete_media(file_path: Path) -> int:
        """安全删除媒体文件，删除失败时记录警告但不抛出异常。

        Args:
            file_path: 要删除的文件路径

        Returns:
            成功删除返回 1，失败返回 0
        """
        try:
            file_path.unlink()
            return 1
        except Exception as e:
            logger.warning(f"删除媒体文件失败 {file_path.name}: {e}")
            return 0

    # ──────────────────────────────────────────
    # 公共 API：媒体识别控制
    # ──────────────────────────────────────────

    def skip_recognition_for_stream(
        self,
        stream_id: str,
        media_types: Iterable[str] | None = None,
    ) -> None:
        """注册指定聊天流跳过媒体识别。

        调用后，该 stream_id 的消息在 MessageConverter 中将不再触发
        VLM/ASR 识别，媒体数据仅落盘+入库，不改写文本描述。
        适用于聊天流程自行处理多模态内容的场景。

        Args:
            stream_id: 要跳过识别的聊天流 ID
            media_types: 要跳过的媒体类型集合（如 ``("image",)``）。为 ``None``
                时表示跳过所有类型。
        """
        if media_types is None:
            self._skip_recognition_streams[stream_id] = None
            logger.debug(f"已注册跳过识别: stream_id={stream_id[:8]} (全部类型)")
            return
        types = frozenset(media_types)
        self._skip_recognition_streams[stream_id] = types
        logger.debug(
            f"已注册跳过识别: stream_id={stream_id[:8]} (类型={sorted(types)})"
        )

    def unskip_recognition_for_stream(self, stream_id: str) -> None:
        """取消指定聊天流的媒体识别跳过。

        Args:
            stream_id: 要恢复识别的聊天流 ID
        """
        self._skip_recognition_streams.pop(stream_id, None)
        logger.debug(f"已取消跳过识别: stream_id={stream_id[:8]}")

    def should_skip_recognition(
        self,
        stream_id: str,
        media_type: str | None = None,
    ) -> bool:
        """查询指定聊天流是否应跳过媒体识别。

        Args:
            stream_id: 聊天流 ID
            media_type: 待识别媒体的类型；省略时表示
                ""该流是否对任意类型注册了跳过""，用于保留旧的整流粒度语义。

        Returns:
            True 表示该聊天流（针对给定媒体类型）应跳过识别
        """
        if stream_id not in self._skip_recognition_streams:
            return False
        types = self._skip_recognition_streams[stream_id]
        if types is None:
            return True
        if media_type is None:
            return True
        return media_type in types

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
        """识别媒体内容（图片、表情包或语音），并落盘入库。

        统一流程：计算哈希 → 查缓存 → 落盘入库 → 发事件让处理器识别 →
        回写 description。VLM/ASR 作为默认处理器通过事件链调用，
        第三方插件可订阅 ``ON_MEDIA_RECOGNIZE`` 拦截改写。

        Args:
            base64_data: base64 编码的媒体数据（语音为 WAV）
            media_type: 媒体类型，"image"、"emoji" 或 "voice"
            use_cache: 是否使用缓存（默认 True）
            stream_id: 聊天流 ID，传入事件供处理器按流决策
            skip_recognition: 为 True 时仅落盘入库，不识别（默认 False）。
                对 image/emoji/voice 均生效。

        Returns:
            媒体的文字描述；``skip_recognition=True`` 或识别失败时返回 None
        """
        try:
            is_voice = media_type == "voice"
            media_hash = self._compute_hash(base64_data)

            # 尝试从缓存读取
            if use_cache:
                cached = await (
                    self._get_cached_voice_description(media_hash)
                    if is_voice else
                    self._get_cached_description(media_hash, media_type)
                )
                if cached:
                    logger.debug(f"从缓存获取{media_type}描述: {media_hash[:8]}...")
                    return cached

            # 落盘 pending + 移动到分类目录
            pending_path = await self._save_to_pending(base64_data, media_hash, media_type)
            await self._move_to_category_folder(pending_path, media_type, media_hash)
            target_path = self._category_folder_for(media_type) / pending_path.name

            # 先入库（engine_processed=False），确保 image_id 可回查
            await self._save_recognized_media(
                media_hash, media_type, str(target_path),
                description=None, processed=False,
            )

            # skip_recognition：仅入库，不识别
            if skip_recognition:
                logger.debug(f"已持久化{media_type}（跳过识别）: {media_hash[:8]}...")
                return None

            # 发事件让处理器链识别（默认 VLM/ASR 处理器 + 第三方可拦截）
            engine = MediaEngine.ASR if is_voice else MediaEngine.VLM
            description = await self._dispatch_recognition_event(
                base64_data=base64_data,
                media_hash=media_hash,
                media_type=media_type,
                engine=engine,
                stream_id=stream_id,
            )

            if description:
                # 回写缓存 + 更新入库（engine_processed=True）
                await self._save_cache_and_update_media(
                    media_hash, media_type, description, str(target_path),
                )
                logger.info(f"成功识别{media_type}: {description[:50]}...")

            return description

        except Exception as e:
            logger.error(f"识别{media_type}失败: {e}", exc_info=True)
            return None

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
        如果没有任何处理器订阅，回退到内置引擎调用。

        Args:
            base64_data: base64 编码的媒体数据
            media_hash: 媒体哈希值
            media_type: "image"、"emoji" 或 "voice"
            engine: 引擎类型（VLM 或 ASR）
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
            _, final_params = await bus.publish(EventType.ON_MEDIA_RECOGNIZE.value, params)
        except Exception as e:
            logger.warning(f"发布媒体识别事件失败，回退内置引擎: {e}")
            final_params = params

        # 处理器链已回写 description
        description = final_params.get("description")
        if isinstance(description, str) and description:
            return description

        # 处理器设了 skip_engine 或无处理器订阅 → 回退内置引擎
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

        Args:
            base64_data: base64 编码的媒体数据
            media_type: "image"、"emoji" 或 "voice"
            engine: 引擎类型

        Returns:
            识别结果文本，失败返回 None
        """
        if engine == MediaEngine.ASR:
            if not self._asr_available or not self._asr_model_set:
                logger.debug("ASR 模型不可用，跳过语音识别")
                return None
            return await self._recognize_with_asr(base64_data)

        if not self._vlm_model_set:
            logger.debug(f"VLM 模型不可用，跳过{media_type}识别")
            return None
        return await self._recognize_with_vlm(base64_data, media_type)

    async def _on_media_recognize_vlm(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """ON_MEDIA_RECOGNIZE 事件的默认 VLM 处理回调。

        当 ``engine == "vlm"`` 且未被前序处理器处理时，调用内置 VLM 引擎
        识别图片/表情包，回写 ``description`` 和 ``engine_processed``。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            (EventDecision, params)
        """
        from src.kernel.event import EventDecision

        if params.get("engine") != MediaEngine.VLM.value:
            return EventDecision.PASS, params
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        base64_data = params.get("base64_data")
        media_type = params.get("media_type", "image")
        if not isinstance(base64_data, str) or not base64_data:
            return EventDecision.PASS, params

        description = await self._recognize_with_vlm(base64_data, media_type)
        if description:
            params["description"] = description
            params["engine_processed"] = True
            logger.debug(
                f"默认VLM识别成功: {params.get('media_hash', '')[:8]}... "
                f"→ {description[:50]}..."
            )
            return EventDecision.SUCCESS, params

        return EventDecision.PASS, params

    async def _on_media_recognize_asr(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """ON_MEDIA_RECOGNIZE 事件的默认 ASR 处理回调。

        当 ``engine == "asr"`` 且未被前序处理器处理时，调用内置 ASR 引擎
        识别语音，回写 ``description`` 和 ``engine_processed``。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            (EventDecision, params)
        """
        from src.kernel.event import EventDecision

        if params.get("engine") != MediaEngine.ASR.value:
            return EventDecision.PASS, params
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        base64_data = params.get("base64_data")
        if not isinstance(base64_data, str) or not base64_data:
            return EventDecision.PASS, params

        description = await self._recognize_with_asr(base64_data)
        if description:
            params["description"] = description
            params["engine_processed"] = True
            logger.debug(
                f"默认ASR识别成功: {params.get('media_hash', '')[:8]}... "
                f"→ {description[:50]}..."
            )
            return EventDecision.SUCCESS, params

        return EventDecision.PASS, params

    def _register_default_recognition_handlers(self) -> None:
        """注册默认 VLM/ASR 识别回调到 EventBus。

        使用 ``priority=0``（最低优先级），第三方插件用更高 priority
        即可先拦截。应在 EventManager 构建订阅映射后调用。
        """
        bus = get_event_bus()
        event_name = EventType.ON_MEDIA_RECOGNIZE.value
        try:
            bus.subscribe(event_name, self._on_media_recognize_vlm, priority=0)
            bus.subscribe(event_name, self._on_media_recognize_asr, priority=0)
            logger.debug("已注册默认媒体识别回调: vlm, asr")
        except Exception as e:
            logger.error(f"默认媒体识别回调注册失败:{e}")

    def _category_folder_for(self, media_type: str) -> Path:
        """返回媒体类型对应的分类目录。

        Args:
            media_type: "image"、"emoji" 或 "voice"

        Returns:
            对应的分类目录路径
        """
        if media_type == "image":
            return self.images_folder
        if media_type == "voice":
            return self.voices_folder
        return self.emojis_folder

    async def _save_recognized_media(
        self,
        media_hash: str,
        media_type: str,
        file_path: str,
        description: str | None,
        processed: bool,
    ) -> None:
        """统一入库入口，按 media_type 写 Images 或 Voices 表。

        Args:
            media_hash: 媒体哈希值
            media_type: "image"、"emoji" 或 "voice"
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
        else:
            await self.save_media_info(
                media_hash=media_hash,
                media_type=media_type,
                file_path=file_path,
                description=description,
                vlm_processed=processed,
            )

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
            media_type: "image"、"emoji" 或 "voice"
            description: 识别结果文本
            file_path: 文件路径
        """
        if media_type == "voice":
            await self._save_voice_description_cache(media_hash, description)
        else:
            await self._save_description_cache(media_hash, media_type, description)

        await self._save_recognized_media(
            media_hash, media_type, file_path,
            description=description, processed=True,
        )

    async def recognize_batch(
        self,
        media_list: list[tuple[str, str]],
        use_cache: bool = True
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
                use_cache=use_cache
            )
            results.append((idx, description))
        return results

    # ──────────────────────────────────────────
    # 公共 API：数据库操作
    # ──────────────────────────────────────────

    async def save_media_info(
        self,
        media_hash: str,
        media_type: str,
        file_path: str | None = None,
        description: str | None = None,
        vlm_processed: bool = False
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
                # 查找现有记录（使用 image_id 作为唯一标识）
                # 这里使用 scalars().first() 来避免数据库中存在多条重复记录导致的 MultipleResultsFound 错误
                stmt = (
                    select(Images)
                    .where(Images.image_id == media_hash)
                    .order_by(Images.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    # 更新现有记录
                    existing.count += 1
                    if description:
                        existing.description = description
                    if vlm_processed:
                        existing.vlm_processed = True
                    changed = True
                    logger.debug(f"更新媒体记录: {media_hash[:8]}... count={existing.count}")
                else:
                    # 创建新记录
                    new_image = Images(
                        image_id=media_hash,
                        path=file_path or media_hash,  # 如果没有路径，用哈希值
                        type=media_type,
                        description=description,
                        timestamp=time.time(),
                        vlm_processed=vlm_processed,
                        count=1
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
                stmt = (
                    select(Voices)
                    .where(Voices.voice_id == voice_hash)
                    .order_by(Voices.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    existing.count += 1
                    if description:
                        existing.description = description
                    if asr_processed:
                        existing.asr_processed = True
                    changed = True
                    logger.debug(f"更新语音记录: {voice_hash[:8]}... count={existing.count}")
                else:
                    new_voice = Voices(
                        voice_id=voice_hash,
                        path=file_path or voice_hash,
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

    # ──────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────

    async def _recognize_with_vlm(
        self, 
        base64_data: str, 
        media_type: str
    ) -> str | None:
        """使用 VLM 识别单个媒体。
        
        Args:
            base64_data: base64 编码的媒体数据
            media_type: 媒体类型（image 或 emoji）
            
        Returns:
            识别结果文本，失败返回 None
        """
        try:
            from src.app.plugin_system.api.llm_api import create_llm_request
            
            # 检查 VLM 模型是否可用
            if not self._vlm_model_set:
                logger.debug("VLM 模型不可用")
                return None

            # 创建 VLM 请求
            context_manager = LLMContextManager()
            request = create_llm_request(
                self._vlm_model_set,
                "image_recognition",
                context_manager=context_manager,
            )

            # 从提示词管理器获取提示词模板
            prompt_manager = get_prompt_manager()
            if media_type == "emoji":
                template = prompt_manager.get_template("media.emoji_recognition")
            else:
                template = prompt_manager.get_template("media.image_recognition")
            
            # 构建提示词（模板不需要参数，直接build）
            if template:
                prompt = await template.build()

            # 处理 base64 数据：提取纯净的 base64 内容
            clean_base64 = self._extract_clean_base64(base64_data)
            mime_type = self._extract_image_mime_type(base64_data)
            
            # 使用标准的 data URL 格式（大多数 VLM API 都支持）
            image_value = f"data:{mime_type};base64,{clean_base64}"

            # 添加 payload 并发送请求
            request.add_payload(LLMPayload(ROLE.USER, [Text(prompt), Image(image_value)]))
            response = await request.send(stream=False)
            await response

            # 提取并处理描述
            description = response.message.strip() if response.message else ""
            
            # 限制长度
            if len(description) > 100:
                description = description[:97] + "..."

            return description if description else None

        except Exception as e:
            logger.error(f"VLM 识别失败: {e}", exc_info=True)
            return None

    async def _recognize_with_asr(self, audio_base64: str) -> str | None:
        """调用 ASR 客户端执行语音转文字。

        Args:
            audio_base64: base64 编码的 WAV 音频数据。

        Returns:
            识别出的文字，失败返回 None。
        """
        try:
            registry = ModelClientRegistry()
            model_set = self._asr_model_set
            # model_set 是 list[dict]，每个元素即一个 ModelEntry
            if not isinstance(model_set, list) or not model_set:
                logger.debug("ASR model_set 中无可用模型")
                return None

            model_entry = model_set[0]
            client = registry.get_asr_client_for_model(model_entry)
            model_name = model_entry.get("model_identifier") if isinstance(model_entry, dict) else str(model_entry)

            clean_b64 = self._extract_clean_base64(audio_base64)
            audio_bytes = await asyncio.to_thread(
                base64_decode_to_bytes,
                clean_b64,
            )

            text = await client.create_transcription(
                model_name=model_name,
                audio_bytes=audio_bytes,
                request_name="voice_recognition",
                model_set=model_entry,
            )
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"ASR 请求失败: {e}", exc_info=True)
            return None

    async def _get_cached_description(
        self,
        media_hash: str,
        media_type: str
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

    async def _save_description_cache(
        self,
        media_hash: str,
        media_type: str,
        description: str
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
                # 检查是否已存在（避免重复记录导致 MultipleResultsFound）
                stmt = (
                    select(ImageDescriptions)
                    .where(
                        ImageDescriptions.image_description_hash == media_hash,
                        ImageDescriptions.type == media_type
                    )
                    .order_by(ImageDescriptions.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                # 使用 scalars().first() 避免 MultipleResultsFound 错误
                existing = result.scalars().first()

                if not existing:
                    # 创建新缓存记录
                    new_desc = ImageDescriptions(
                        image_description_hash=media_hash,
                        type=media_type,
                        description=description,
                        timestamp=time.time()
                    )
                    session.add(new_desc)
                    created = True
                    await session.commit()
                    logger.debug(f"保存描述缓存: {media_hash[:8]}...")
            if created:
                invalidate_model_cache(ImageDescriptions)

        except Exception as e:
            logger.error(f"保存描述缓存失败: {e}", exc_info=True)

    async def _get_cached_voice_description(self, voice_hash: str) -> str | None:
        """从数据库缓存获取语音识别结果。

        镜像 :meth:`_get_cached_description` 的语义，但查询 ``VoiceDescriptions`` 表。
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

    async def _save_voice_description_cache(
        self,
        voice_hash: str,
        description: str,
    ) -> None:
        """保存语音识别结果到缓存。

        镜像 :meth:`_save_description_cache` 的语义，但写入 ``VoiceDescriptions`` 表。
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

    @staticmethod
    def _extract_clean_base64(data: str) -> str:
        """提取纯净的 base64 数据（移除前缀和多余字符）。
        
        Args:
            data: 可能包含前缀的 base64 字符串
            
        Returns:
            纯净的 base64 字符串
        """
        # 移除可能的 data URL 前缀
        if data.startswith("data:"):
            # 提取 base64 部分
            if "base64," in data:
                data = data.split("base64,", 1)[1]
        elif data.startswith("base64|"):
            data = data[7:]
        
        # 移除可能的换行符和空格
        data = data.replace("\n", "").replace("\r", "").replace(" ", "")
        
        return data

    @staticmethod
    def _extract_image_mime_type(data: str) -> str:
        """从 data URL 中提取图片 MIME 类型。"""
        if data.startswith("data:") and ";base64," in data:
            mime_type = data.split(";", 1)[0][len("data:"):].strip().lower()
            if mime_type.startswith("image/"):
                return mime_type
        return "image/png"
    
    async def _save_to_pending(
        self,
        base64_data: str,
        media_hash: str,
        media_type: str
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
            # 提取纯净的 base64 数据
            clean_base64 = self._extract_clean_base64(base64_data)
            
            # 解码为二进制数据
            binary_data = await asyncio.to_thread(
                base64_decode_to_bytes,
                clean_base64,
            )
            
            # 根据类型确定文件扩展名
            if media_type == "image":
                ext = ".jpg"
            elif media_type == "voice":
                ext = ".wav"
            else:
                ext = ".png"
            
            # 生成文件名（哈希值前16位 + 类型标记 + 扩展名）
            filename = f"{media_hash[:16]}_{media_type}{ext}"
            file_path = self.pending_folder / filename
            
            # 写入文件
            await asyncio.to_thread(file_path.write_bytes, binary_data)
            logger.debug(f"媒体已保存到待识别文件夹: {filename}")
            
            return file_path
        except Exception as e:
            logger.error(f"保存到待识别文件夹失败: {e}")
            # 返回一个虚拟路径，不影响后续流程
            return self.pending_folder / f"{media_hash[:16]}_error.tmp"

    async def _move_to_category_folder(
        self,
        source_path: Path,
        media_type: str,
        media_hash: str
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
            
            # 确定目标文件夹
            if media_type == "image":
                target_folder = self.images_folder
            elif media_type == "voice":
                target_folder = self.voices_folder
            else:
                target_folder = self.emojis_folder
            
            # 确定目标文件名
            target_path = target_folder / source_path.name
            
            # 如果目标文件已存在，删除源文件即可（去重）
            if await asyncio.to_thread(target_path.exists):
                await asyncio.to_thread(source_path.unlink)
                logger.debug(f"目标文件已存在，删除源文件: {source_path.name}")
                return
            
            # 移动文件
            await asyncio.to_thread(source_path.rename, target_path)
            logger.debug(f"文件已移动到 {media_type} 文件夹: {target_path.name}")
        except Exception as e:
            logger.error(f"移动文件失败: {e}")


    @staticmethod
    def _compute_hash(data: str) -> str:
        """计算数据的 SHA256 哈希值。
        
        Args:
            data: 待哈希的数据（base64 字符串）
            
        Returns:
            十六进制哈希字符串
        """
        # 使用提取的纯净 base64 数据计算哈希
        clean_data = MediaManager._extract_clean_base64(data)
        return hashlib.sha256(clean_data.encode()).hexdigest()

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
        return MediaManager._compute_hash(data)


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