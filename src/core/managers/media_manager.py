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
import base64
import hashlib
import time
from pathlib import Path
from typing import Any

from src.kernel.logger import get_logger

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
        self._skip_vlm_stream_ids: set[str] = set()  # 已注册跳过 VLM 识别的聊天流 ID
        self._initialize_vlm()
        self._register_prompts()
        self._setup_media_folders()
        self._cleanup_task_id = None
        self._start_cleanup_scheduler()

    def _initialize_vlm(self) -> None:
        """初始化 VLM 模型配置。"""
        try:
            from src.app.plugin_system.api.llm_api import get_model_set_by_task

            self._vlm_model_set = get_model_set_by_task("vlm")
            self._vlm_available = self._vlm_model_set is not None
            
            if self._vlm_available:
                logger.info("VLM 模型已加载，媒体识别功能可用")
            else:
                logger.info("未配置 VLM 模型，媒体识别功能不可用")
        except Exception as e:
            logger.error(f"初始化 VLM 模型失败: {e}")

    def _register_prompts(self) -> None:
        """注册媒体识别相关的提示词模板。"""
        try:
            from src.core.prompt import PromptTemplate, get_prompt_manager
            
            manager = get_prompt_manager()
            
            # 注册图片识别提示词
            from src.core.config import get_core_config
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
            
            # 创建所有必要的文件夹
            for folder in [self.pending_folder, self.images_folder, self.emojis_folder]:
                folder.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"媒体文件夹已初始化: {self.media_root}")
        except Exception as e:
            logger.error(f"创建媒体文件夹失败: {e}")

    def _start_cleanup_scheduler(self) -> None:
        """启动定时清理任务（每5分钟清理一次缓存）。"""
        try:
            # 延迟导入，避免循环依赖
            # 确保在异步上下文中创建任务
            asyncio.create_task(self._register_cleanup_task())
            
            logger.info("媒体缓存清理调度器已启动(每5分钟)")
        except Exception as e:
            logger.error(f"启动清理调度器失败: {e}")

    async def _register_cleanup_task(self) -> None:
        """注册定时清理任务到调度器。"""
        try:
            from src.kernel.scheduler import get_unified_scheduler, TriggerType
            
            scheduler = get_unified_scheduler()
            
            # 创建周期性清理任务（每5分钟 = 300秒）
            schedule_id = await scheduler.create_schedule(
                callback=self._cleanup_pending_folder,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": 300},  # 5分钟
                is_recurring=True,
                task_name="media_cache_cleanup"
            )
            
            self._cleanup_task_id = schedule_id
            logger.info(f"媒体缓存清理任务已注册: {schedule_id}")
        except Exception as e:
            logger.error(f"注册清理任务失败: {e}")

    async def _cleanup_pending_folder(self) -> None:
        """清理待识别文件夹中的陈旧文件。"""
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

    # ──────────────────────────────────────────
    # 公共 API：VLM 识别控制
    # ──────────────────────────────────────────

    def skip_vlm_for_stream(self, stream_id: str) -> None:
        """注册指定聊天流跳过 VLM 识别。

        调用后，该 stream_id 的消息在 MessageConverter 中将不再触发
        VLM 图片/表情包识别，媒体数据仅保留原始 base64。
        适用于聊天流程自行处理多模态内容的场景。

        Args:
            stream_id: 要跳过 VLM 识别的聊天流 ID
        """
        self._skip_vlm_stream_ids.add(stream_id)
        logger.debug(f"已注册跳过 VLM 识别: stream_id={stream_id[:8]}")

    def unskip_vlm_for_stream(self, stream_id: str) -> None:
        """取消指定聊天流的 VLM 识别跳过。

        Args:
            stream_id: 要恢复 VLM 识别的聊天流 ID
        """
        self._skip_vlm_stream_ids.discard(stream_id)
        logger.debug(f"已取消跳过 VLM 识别: stream_id={stream_id[:8]}")

    def should_skip_vlm(self, stream_id: str) -> bool:
        """查询指定聊天流是否应跳过 VLM 识别。

        Args:
            stream_id: 聊天流 ID

        Returns:
            True 表示该聊天流已注册跳过 VLM 识别
        """
        return stream_id in self._skip_vlm_stream_ids

    # ──────────────────────────────────────────
    # 公共 API：媒体识别
    # ──────────────────────────────────────────

    async def recognize_media(
        self, 
        base64_data: str, 
        media_type: str,
        use_cache: bool = True
    ) -> str | None:
        """识别媒体内容（图片或表情包）。
        
        Args:
            base64_data: base64 编码的媒体数据
            media_type: 媒体类型，"image" 或 "emoji"
            use_cache: 是否使用缓存（默认 True）
            
        Returns:
            媒体的文字描述，识别失败返回 None
        """
        try:
            # 计算哈希值
            media_hash = self._compute_hash(base64_data)
            
            # 尝试从缓存读取
            if use_cache:
                cached_description = await self._get_cached_description(
                    media_hash, 
                    media_type
                )
                if cached_description:
                    logger.debug(f"从缓存获取{media_type}描述: {media_hash[:8]}...")
                    return cached_description
            
            # 保存到待识别文件夹
            pending_file_path = await self._save_to_pending(
                base64_data,
                media_hash,
                media_type
            )
            
            # VLM 识别
            description = await self._recognize_with_vlm(base64_data, media_type)
            
            if description:
                # 保存到缓存
                await self._save_description_cache(
                    media_hash,
                    media_type,
                    description
                )
                logger.info(f"成功识别{media_type}: {description[:50]}...")
                
                # 移动到对应的分类文件夹
                await self._move_to_category_folder(
                    pending_file_path,
                    media_type,
                    media_hash
                )
                
                # 保存媒体信息到数据库
                target_folder = self.images_folder if media_type == "image" else self.emojis_folder
                target_file_path = target_folder / pending_file_path.name
                await self.save_media_info(
                    media_hash=media_hash,
                    media_type=media_type,
                    file_path=str(target_file_path),
                    description=description,
                    vlm_processed=True
                )
            else:
                # 识别失败，保持在待识别文件夹，等待定时清理
                logger.warning(f"识别失败，文件保留在待识别文件夹: {pending_file_path.name}")
            
            return description
            
        except Exception as e:
            logger.error(f"识别{media_type}失败: {e}", exc_info=True)
            return None

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
            from src.kernel.db.core.session import get_db_session
            from src.core.models.sql_alchemy import Images

            async with get_db_session() as session:
                # 查找现有记录（使用 image_id 作为唯一标识）
                # 这里使用 scalars().first() 来避免数据库中存在多条重复记录导致的 MultipleResultsFound 错误
                from sqlalchemy import select
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
                    logger.debug(f"创建新媒体记录: {media_hash[:8]}...")

                await session.commit()

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
            from src.kernel.db.core.session import get_db_session
            from src.core.models.sql_alchemy import Images
            from sqlalchemy import select

            async with get_db_session() as session:
                # 如果存在多条重复记录，取最新一条返回
                from sqlalchemy import select
                stmt = (
                    select(Images)
                    .where(Images.image_id == media_hash)
                    .order_by(Images.timestamp.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                media = result.scalars().first()

                if media:
                    return {
                        "id": media.id,
                        "image_id": media.image_id,
                        "path": media.path,
                        "type": media.type,
                        "description": media.description,
                        "count": media.count,
                        "timestamp": media.timestamp,
                        "vlm_processed": media.vlm_processed
                    }
                return None

        except Exception as e:
            logger.error(f"查询媒体信息失败: {e}", exc_info=True)
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
            from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text, Image
            from src.core.prompt import get_prompt_manager
            
            
            # 检查 VLM 模型是否可用
            if not self._vlm_model_set:
                logger.debug("VLM 模型不可用")
                return None

            # 创建 VLM 请求
            context_manager = LLMContextManager(
                max_payloads=3,
            )
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
            prompt = ""
            if template:
                prompt = await template.build()

            # 处理 base64 数据：提取纯净的 base64 内容
            clean_base64 = self._extract_clean_base64(base64_data)
            
            # 解码为二进制数据以检测 MIME 类型
            try:
                image_bytes = base64.b64decode(clean_base64)
            except Exception as e:
                logger.warning(f"base64 解码失败: {e}")
                return None
            
            # 检测真实的 MIME 类型
            detected_mime = self._detect_mime_from_bytes(image_bytes)
            is_gif = detected_mime == "image/gif"
            
            # 压缩图片（包括 GIF 多帧处理）
            vlm_bytes, vlm_mime, is_gif_collage = self._compress_image_for_vlm(
                image_bytes, detected_mime
            )
            
            # 重新编码为 base64
            vlm_base64 = base64.b64encode(vlm_bytes).decode("utf-8")
            
            # 对于 GIF 动图，添加提示说明
            gif_hint = ""
            if is_gif_collage:
                gif_hint = "（这是一个 GIF 动图的关键帧截图，请综合所有帧的内容描述动态效果）"
            
            # 使用正确的 MIME 类型构建 data URL
            image_value = f"data:{vlm_mime};base64,{vlm_base64}"

            # 添加 payload 并发送请求
            full_prompt = prompt + gif_hint
            request.add_payload(LLMPayload(ROLE.USER, [Text(full_prompt), Image(image_value)]))
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
            from src.kernel.db.core.session import get_db_session
            from src.core.models.sql_alchemy import ImageDescriptions
            from sqlalchemy import select

            async with get_db_session() as session:
                stmt = select(ImageDescriptions).where(
                    ImageDescriptions.image_description_hash == media_hash,
                    ImageDescriptions.type == media_type
                )
                result = await session.execute(stmt)
                # 使用 scalars().first() 避免 MultipleResultsFound 错误
                desc = result.scalars().first()

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
            from src.kernel.db.core.session import get_db_session
            from src.core.models.sql_alchemy import ImageDescriptions
            from sqlalchemy import select

            async with get_db_session() as session:
                # 检查是否已存在（避免重复记录导致 MultipleResultsFound）
                from sqlalchemy import select
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
                    await session.commit()
                    logger.debug(f"保存描述缓存: {media_hash[:8]}...")

        except Exception as e:
            logger.error(f"保存描述缓存失败: {e}", exc_info=True)

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
    def _detect_mime_from_bytes(data: bytes) -> str:
        """根据文件头判断 MIME 类型。

        Args:
            data: 图片二进制数据

        Returns:
            MIME 类型字符串
        """
        if len(data) < 12:
            return "image/png"

        if data[:6] == b"GIF87a" or data[:6] == b"GIF89a":
            return "image/gif"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        elif data[:2] == b"\xff\xd8":
            return "image/jpeg"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"

        return "image/png"

    @staticmethod
    def _compress_image_for_vlm(
        image_bytes: bytes,
        mime: str,
        max_size_mb: float = 5.0
    ) -> tuple[bytes, str, bool]:
        """将图片压缩至指定大小用于 VLM 识别。

        - 静态图（JPG/PNG/WebP）：逐步降低质量和分辨率
        - GIF：均匀采样最多 6 帧拼成网格图，以 JPEG 发给 VLM

        Args:
            image_bytes: 图片二进制数据
            mime: MIME 类型
            max_size_mb: 最大文件大小（MB）

        Returns:
            (用于 VLM 的 bytes, mime 类型, is_gif_frames_collage)
        """
        try:
            from PIL import Image as PILImage
            import io as pil_io
        except ImportError:
            logger.warning("PIL 未安装，无法压缩图片")
            return image_bytes, mime, False

        max_bytes = int(max_size_mb * 1024 * 1024)

        # GIF：提取多个关键帧拼成网格图
        if mime == "image/gif":
            try:
                img = PILImage.open(pil_io.BytesIO(image_bytes))
                total_frames: int = getattr(img, "n_frames", 1)

                max_frames = 6
                if total_frames <= max_frames:
                    frame_indices = list(range(total_frames))
                else:
                    step = total_frames / max_frames
                    frame_indices = [int(i * step) for i in range(max_frames)]

                frames: list[Any] = []
                for idx in frame_indices:
                    try:
                        img.seek(idx)
                        frames.append(img.convert("RGB").copy())
                    except EOFError:
                        break

                if not frames:
                    raise RuntimeError("无法提取 GIF 帧")

                cols = min(3, len(frames))
                rows = (len(frames) + cols - 1) // cols
                fw, fh = frames[0].size
                grid_img = PILImage.new("RGB", (fw * cols, fh * rows), (255, 255, 255))
                for i, frame in enumerate(frames):
                    x = (i % cols) * fw
                    y = (i // cols) * fh
                    grid_img.paste(frame.resize((fw, fh)), (x, y))

                output = pil_io.BytesIO()
                grid_img.save(output, format="JPEG", quality=80)
                result_bytes = output.getvalue()

                if len(result_bytes) > max_bytes:
                    scale = (max_bytes / len(result_bytes)) ** 0.5
                    new_w = max(1, int(grid_img.width * scale))
                    new_h = max(1, int(grid_img.height * scale))
                    grid_img = grid_img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
                    output = pil_io.BytesIO()
                    grid_img.save(output, format="JPEG", quality=75)
                    result_bytes = output.getvalue()

                logger.debug(
                    f"GIF 提取 {len(frames)} 帧拼成网格用于 VLM: "
                    f"{len(image_bytes)} → {len(result_bytes)} 字节"
                )
                return result_bytes, "image/jpeg", True

            except Exception as e:
                logger.warning(f"GIF 处理失败: {e}")
                return image_bytes, mime, False

        # 静态图：不超限则直接返回
        if len(image_bytes) <= max_bytes:
            return image_bytes, mime, False

        # 静态图：超限则逐步压缩
        try:
            img = PILImage.open(pil_io.BytesIO(image_bytes))
        except Exception as e:
            logger.warning(f"无法打开图片: {e}")
            return image_bytes, mime, False

        output_format = {
            "image/png": "JPEG",
            "image/jpeg": "JPEG",
            "image/jpg": "JPEG",
            "image/webp": "WEBP",
        }.get(mime, "JPEG")
        output_mime = "image/jpeg" if output_format == "JPEG" else mime

        quality = 85
        scale = 1.0
        compressed = b""

        while quality >= 30 or scale > 0.4:
            output = pil_io.BytesIO()
            try:
                target = img.convert("RGB") if output_format == "JPEG" else img
                if scale < 1.0:
                    new_w = max(1, int(target.width * scale))
                    new_h = max(1, int(target.height * scale))
                    target = target.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
                target.save(output, format=output_format, quality=quality)
                compressed = output.getvalue()
                if len(compressed) <= max_bytes:
                    logger.info(
                        f"图片已压缩: {len(image_bytes)} → {len(compressed)} 字节"
                    )
                    return compressed, output_mime, False
            except Exception as e:
                logger.warning(f"压缩图片失败: {e}")
                break

            if quality > 30:
                quality = max(30, quality - 10)
            else:
                scale = round(scale - 0.1, 1)

        logger.warning(f"图片压缩后仍超限，使用最后结果: {len(compressed)} 字节")
        return compressed, output_mime, False
    
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
            binary_data = base64.b64decode(clean_base64)
            
            # 根据类型确定文件扩展名
            ext = ".jpg" if media_type == "image" else ".png"
            
            # 生成文件名（哈希值前16位 + 类型标记 + 扩展名）
            filename = f"{media_hash[:16]}_{media_type}{ext}"
            file_path = self.pending_folder / filename
            
            # 写入文件
            file_path.write_bytes(binary_data)
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
            if not source_path.exists():
                logger.debug(f"源文件不存在，跳过移动: {source_path.name}")
                return
            
            # 确定目标文件夹
            target_folder = self.images_folder if media_type == "image" else self.emojis_folder
            
            # 确定目标文件名
            target_path = target_folder / source_path.name
            
            # 如果目标文件已存在，删除源文件即可（去重）
            if target_path.exists():
                source_path.unlink()
                logger.debug(f"目标文件已存在，删除源文件: {source_path.name}")
                return
            
            # 移动文件
            source_path.rename(target_path)
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
