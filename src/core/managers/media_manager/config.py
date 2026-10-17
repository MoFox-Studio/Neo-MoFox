"""媒体管理器配置与状态组件。

集中管理：
- VLM / ASR 模型配置加载与可用性
- 媒体识别提示词模板注册
- 媒体文件夹结构初始化
- 清理任务参数加载
- 按聊天流的识别跳过控制

设计上无外部可变状态依赖，构造时一次性完成配置读取。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.app.plugin_system.api.llm_api import get_model_set_by_task
from src.core.config import get_core_config
from src.core.prompt import PromptTemplate, get_prompt_manager
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaConfig:
    """媒体管理器配置与运行时状态。

    Attributes:
        vlm_model_set: VLM 模型集，未配置时为 None
        vlm_available: VLM 是否可用
        asr_model_set: ASR 模型集，未配置时为 None
        asr_available: ASR 是否可用
        media_root: 媒体根目录
        pending_folder: 待识别目录
        images_folder: 已识别图片目录
        emojis_folder: 已识别表情包目录
        voices_folder: 已识别语音目录
        videos_folder: 已识别视频目录
        media_cache_cleanup_enabled: pending 目录清理开关
        media_cache_cleanup_interval_hours: pending 目录清理间隔（小时）
        media_file_cleanup_enabled: 分类目录清理开关
        media_file_max_age_days: 分类文件最大保留天数
        media_file_max_total_size_mb: 分类目录总容量上限（MB）
        media_file_cleanup_interval_hours: 分类目录清理间隔（小时）
    """

    def __init__(self) -> None:
        """初始化并加载所有配置。"""
        self.vlm_model_set = None
        self.vlm_available = False
        self.asr_model_set = None
        self.asr_available = False

        # stream_id -> 跳过的媒体类型集合；值为 None 表示跳过所有类型
        self._skip_recognition_streams: dict[str, frozenset[str] | None] = {}

        # 文件夹路径占位，由 _setup_media_folders 填充
        self.media_root: Path = Path("data/media_cache")
        self.pending_folder: Path = self.media_root / "pending"
        self.images_folder: Path = self.media_root / "images"
        self.emojis_folder: Path = self.media_root / "emojis"
        self.voices_folder: Path = self.media_root / "voices"
        self.videos_folder: Path = self.media_root / "videos"

        # 清理配置占位，由 _load_cleanup_config 填充
        self.media_cache_cleanup_enabled: bool = False
        self.media_cache_cleanup_interval_hours: float = 1.0
        self.media_file_cleanup_enabled: bool = False
        self.media_file_max_age_days: int = 7
        self.media_file_max_total_size_mb: int = 500
        self.media_file_cleanup_interval_hours: float = 24.0

        self._initialize_vlm()
        self._initialize_asr()
        self._register_prompts()
        self._setup_media_folders()
        self._load_cleanup_config()

    # ──────────────────────────────────────────
    # 模型配置
    # ──────────────────────────────────────────

    def _initialize_vlm(self) -> None:
        """初始化 VLM 模型配置。"""
        try:
            self.vlm_model_set = get_model_set_by_task("vlm")
            self.vlm_available = self.vlm_model_set is not None

            if self.vlm_available:
                logger.info("VLM 模型已加载，媒体识别功能可用")
            else:
                logger.info("未配置 VLM 模型，媒体识别功能不可用")
        except Exception as e:
            logger.error(f"初始化 VLM 模型失败: {e}")

    def _initialize_asr(self) -> None:
        """初始化 ASR 模型配置。"""
        try:
            self.asr_model_set = get_model_set_by_task("voice")
            self.asr_available = self.asr_model_set is not None

            if self.asr_available:
                logger.info("ASR 模型已加载，语音识别功能可用")
            else:
                logger.info("未配置 ASR 模型，语音识别功能不可用")
        except Exception as e:
            self.asr_model_set = None
            self.asr_available = False
            logger.error(f"初始化 ASR 模型失败: {e}")

    def _register_prompts(self) -> None:
        """注册媒体识别相关的提示词模板。"""
        try:
            manager = get_prompt_manager()

            custom_prompt = get_core_config().chat.image_recognition_prompt
            default_template = (
                "描述这张图片的内容，包含主题、主要元素。若有文字或代码，完整转述。"
            )
            image_prompt = PromptTemplate(
                name="media.image_recognition",
                template=custom_prompt if custom_prompt else default_template,
            )
            manager.register_template(image_prompt)

            custom_emoji_prompt = get_core_config().chat.emoji_recognition_prompt
            default_emoji_template = "描述这个表情包的画面内容。若有文字，完整转述。"
            emoji_prompt = PromptTemplate(
                name="media.emoji_recognition",
                template=custom_emoji_prompt
                if custom_emoji_prompt
                else default_emoji_template,
            )
            manager.register_template(emoji_prompt)

            logger.debug("媒体识别提示词模板已注册")
        except Exception as e:
            logger.warning(f"注册提示词模板失败: {e}")

    # ──────────────────────────────────────────
    # 文件夹与清理配置
    # ──────────────────────────────────────────

    def _setup_media_folders(self) -> None:
        """设置媒体文件夹结构。"""
        try:
            self.media_root = Path("data/media_cache")

            self.pending_folder = self.media_root / "pending"
            self.images_folder = self.media_root / "images"
            self.emojis_folder = self.media_root / "emojis"
            self.voices_folder = self.media_root / "voices"
            self.videos_folder = self.media_root / "videos"

            for folder in [
                self.pending_folder,
                self.images_folder,
                self.emojis_folder,
                self.voices_folder,
                self.videos_folder,
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

        self.media_cache_cleanup_enabled = chat_cfg.media_cache_cleanup_enabled
        self.media_cache_cleanup_interval_hours = (
            chat_cfg.media_cache_cleanup_interval_hours
        )

        self.media_file_cleanup_enabled = chat_cfg.media_file_cleanup_enabled
        self.media_file_max_age_days = chat_cfg.media_file_max_age_days
        self.media_file_max_total_size_mb = chat_cfg.media_file_max_total_size_mb
        self.media_file_cleanup_interval_hours = (
            chat_cfg.media_file_cleanup_interval_hours
        )

    # ──────────────────────────────────────────
    # 识别跳过控制
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
