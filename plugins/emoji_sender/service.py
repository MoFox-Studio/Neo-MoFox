"""emoji_sender 服务实现。

- 定时入库：从主程序 media cache 抽取表情包，VLM 决策是否收藏并输出标注
- 收藏入库：复制源文件到插件 data 目录，并把描述 embedding 写入插件自有向量库
- 检索发送：按情感 tag 过滤后，向量检索 topN 并在阈值内按温度采样表情包

约束：
- persona 提示词来自主配置 `get_core_config().personality`
- 情感 tag 预设为插件内置常量，不进入配置
- 每次入库任务开头固定执行 data_dir ↔ 向量库记录对齐
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import random
import shutil
import time
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chromadb.api.models.Collection import Collection
from chromadb.errors import ChromaError

from src.app.plugin_system.api import database_api
from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    create_llm_request,
    get_model_set_by_task,
)
from src.app.plugin_system.api.media_api import get_media_info
from src.app.plugin_system.api.send_api import send_emoji
from src.app.plugin_system.base import BaseService
from src.core.config import get_core_config
from src.core.models.sql_alchemy import ImageDescriptions, Images
from src.core.utils.base64_helper import base64_encode_bytes
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger
from src.kernel.vector_db import get_vector_db_service

from .config import EmojiSenderConfig

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None


logger = get_logger("emoji_sender")


EMOTION_TAG_PRESET: tuple[str, ...] = (
    "开心",
    "难过",
    "生气",
    "惊讶",
    "害羞",
    "尴尬",
    "无语",
    "委屈",
    "嘲讽",
    "疑惑",
    "赞同",
    "否定",
    "兴奋",
    "疲惫",
    "害怕",
    "厌恶",
    "紧张",
    "冷漠",
)

_ALLOWED_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

_INGEST_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class MemeCandidate:
    """检索得到的候选表情包。"""

    meme_id: str
    tag: str
    path: str
    description: str
    distance: float
    note: str = ""


class EmojiSenderService(BaseService):
    """emoji_sender 服务。

    对外提供：
    - search_best：按 tag + 向量检索，返回满足阈值并经温度采样的表情包
    - send_best：发送检索到的表情包
    - search_candidates：按 tag + 向量检索，返回去重分页后的候选列表（picker 模式）
    - send_by_id：按 meme id 前缀精确发送指定表情包（picker 模式）
    - collect_meme：按聊天媒体哈希收藏指定图片
    - refresh_meme：重新调用 VLM 更新聊天图片描述及已有收藏的标注和向量
    - ingest_once：执行一次入库任务（对齐→抽取→VLM 决策→写入）
    """

    name: str = "emoji_sender"
    description: str = "表情包收藏、检索与发送服务"

    _SHORT_ID_LENGTH = 12

    def __init__(self, plugin: Any) -> None:
        """初始化使用历史并共享插件的拒绝哈希集合。"""
        super().__init__(plugin)
        self._usage_history: dict[str, deque[str]] = {}
        self._rejected_hashes: set[str] = plugin._rejected_hashes

    def _dedup_enabled(self) -> bool:
        """使用历史去重是否开启。"""
        return bool(self._cfg().dedup.enabled)

    def _recently_used(self, stream_id: str) -> set[str]:
        """获取指定聊天流最近已发送的表情包 id 集合。"""
        history = self._usage_history.get(stream_id)
        if not history:
            return set()
        return set(history)

    def _record_usage(self, stream_id: str, meme_id: str) -> None:
        """记录一次成功的表情包发送（仅当去重开启时记录）。"""
        if not self._dedup_enabled() or not stream_id or not meme_id:
            return
        window = max(1, int(self._cfg().dedup.window))
        history = self._usage_history.get(stream_id)
        if history is None:
            history = deque(maxlen=window)
            self._usage_history[stream_id] = history
        history.append(meme_id)

    def _selection_temperature(self) -> float:
        """获取检索候选采样温度。"""
        return max(0.0, float(self._cfg().vector.temperature))

    def _select_candidate(self, candidates: list[MemeCandidate]) -> MemeCandidate | None:
        """按距离与温度从候选中选择一个表情包。"""
        if not candidates:
            return None

        ordered_candidates = sorted(candidates, key=lambda candidate: candidate.distance)
        temperature = self._selection_temperature()
        if temperature <= 0.0 or len(ordered_candidates) == 1:
            return ordered_candidates[0]

        base_distance = ordered_candidates[0].distance
        weights = [
            math.exp(-max(0.0, candidate.distance - base_distance) / temperature)
            for candidate in ordered_candidates
        ]
        if not any(weight > 0.0 for weight in weights):
            return ordered_candidates[0]

        return random.choices(ordered_candidates, weights=weights, k=1)[0]

    def _cfg(self) -> EmojiSenderConfig:
        """获取插件配置实例。"""
        cfg = self.plugin.config
        if not isinstance(cfg, EmojiSenderConfig):
            raise RuntimeError("emoji_sender plugin config 未正确加载")
        return cfg

    @staticmethod
    def _media_cache_dir() -> Path:
        """media cache 的表情包目录。"""
        return Path("data") / "media_cache" / "emojis"

    def _manual_memes_dir(self) -> Path:
        """手动表情包目录。"""
        path = Path(self._cfg().ingest.manual_memes_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _list_meme_files(directory: Path, *, ordered: bool = False) -> list[Path]:
        """列出目录中的表情包文件。"""
        if not directory.exists():
            return []

        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in _ALLOWED_SUFFIXES
        ]
        return sorted(candidates) if ordered else candidates

    @staticmethod
    def _read_file_bytes(path: Path) -> bytes:
        """读取文件字节内容。"""
        return path.read_bytes()

    @staticmethod
    def _copy_file(source: Path, target: Path) -> None:
        """复制文件并保留元数据。"""
        shutil.copy2(source, target)

    @classmethod
    def _read_file_with_hash(cls, path: Path) -> tuple[bytes, str]:
        """读取文件并计算 sha256。"""
        payload = cls._read_file_bytes(path)
        return payload, cls._sha256_bytes(payload)

    @classmethod
    def _count_meme_files(cls, directory: Path) -> int:
        """统计目录中的表情包文件数量。"""
        return len(cls._list_meme_files(directory))

    @classmethod
    def _scan_store_paths(cls, directory: Path) -> set[str]:
        """扫描表情包存储目录中的文件路径。"""
        return {cls._path_to_store_value(path) for path in cls._list_meme_files(directory)}

    @staticmethod
    def _delete_file(path_value: str) -> None:
        """删除指定路径的文件。"""
        Path(path_value).unlink(missing_ok=True)

    async def _pick_next_manual_meme_file(self) -> Path | None:
        """从手动目录获取下一个未入库且未被拒绝的表情包文件。"""
        manual_dir = await asyncio.to_thread(self._manual_memes_dir)
        candidates = await asyncio.to_thread(self._list_meme_files, manual_dir, ordered=True)
        if not candidates:
            return None

        # 逐个检查，找到第一个未处理的
        for candidate in candidates:
            try:
                _, meme_id = await asyncio.to_thread(self._read_file_with_hash, candidate)
            except Exception:
                continue

            if meme_id in self._rejected_hashes:
                continue
            if not await self._already_ingested(meme_id):
                return candidate

        return None

    def _data_dir(self) -> Path:
        """插件表情包复制目录。"""
        return Path(self._cfg().storage.data_dir)

    def _vector_db_path(self) -> str:
        """向量数据库路径。"""
        return str(self._cfg().vector.db_path)

    def _collection_name(self) -> str:
        """向量集合名。"""
        return str(self._cfg().vector.collection_name)

    def _vector_db(self):
        """获取（缓存的）向量数据库服务实例。"""
        return get_vector_db_service(self._vector_db_path())

    @staticmethod
    def _build_candidate(*, distance: float, metadata: dict[str, Any]) -> MemeCandidate | None:
        """从向量检索元数据中构建候选表情包。"""
        path_value = str(metadata.get("path") or "").strip()
        tag = str(metadata.get("tag") or "").strip()
        description = str(metadata.get("description") or metadata.get("documents") or "").strip()
        meme_id = str(metadata.get("meme_id") or "").strip()

        if not path_value or not tag or not meme_id:
            return None

        return MemeCandidate(
            meme_id=meme_id,
            tag=tag,
            path=path_value,
            description=description,
            distance=distance,
            note=str(metadata.get("note") or "").strip(),
        )

    @staticmethod
    def _path_to_store_value(path: Path) -> str:
        """将路径转为存储在向量库 metadata 的字符串。"""
        return path.resolve().as_posix()

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        """计算 bytes 的 sha256 十六进制值。"""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _chat_media_hash(data: bytes) -> str:
        """计算聊天媒体 ID：对原始文件的纯 base64 文本取 SHA-256。"""
        return hashlib.sha256(base64_encode_bytes(data).encode("ascii")).hexdigest()

    @staticmethod
    def _guess_mime(suffix: str) -> str:
        """根据后缀猜测 MIME。"""
        suffix = suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/png")

    @staticmethod
    def _compress_image_for_vlm(image_bytes: bytes, mime: str, max_size_mb: float = 5.0) -> tuple[bytes, str, bool]:
        """将图片压缩至指定大小用于 VLM 识别。

        - 静态图（JPG/PNG/WebP）：逐步降低质量和分辨率
        - GIF：均匀采样最多 6 帧拼成网格图，以 JPEG 发给 VLM（仅用于识别，不影响入库的原文件）

        Returns:
            (用于 VLM 的 bytes, mime 类型, is_gif_frames_collage)
        """
        if PILImage is None:
            raise RuntimeError("PIL 未安装。请运行: uv add pillow")

        max_bytes = int(max_size_mb * 1024 * 1024)

        # GIF：提取多个关键帧拼成网格图
        if mime == "image/gif":
            try:
                img = PILImage.open(io.BytesIO(image_bytes))
                total_frames: int = getattr(img, "n_frames", 1)

                # 均匀采样，最多取 6 帧
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

                # 拼成网格（最多 3 列）
                cols = min(3, len(frames))
                rows = (len(frames) + cols - 1) // cols
                fw, fh = frames[0].size
                grid_img = PILImage.new("RGB", (fw * cols, fh * rows), (255, 255, 255))
                for i, frame in enumerate(frames):
                    x = (i % cols) * fw
                    y = (i // cols) * fh
                    grid_img.paste(frame.resize((fw, fh)), (x, y))

                output = io.BytesIO()
                grid_img.save(output, format="JPEG", quality=80)
                result_bytes = output.getvalue()

                # 如果网格图还是超限，缩小分辨率
                if len(result_bytes) > max_bytes:
                    scale = (max_bytes / len(result_bytes)) ** 0.5
                    new_w = max(1, int(grid_img.width * scale))
                    new_h = max(1, int(grid_img.height * scale))
                    grid_img = grid_img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
                    output = io.BytesIO()
                    grid_img.save(output, format="JPEG", quality=75)
                    result_bytes = output.getvalue()

                logger.debug(
                    f"GIF 提取 {len(frames)} 帧拼成网格用于 VLM: "
                    f"{len(image_bytes)} → {len(result_bytes)} 字节 "
                    f"(总帧数 {total_frames})"
                )
                return result_bytes, "image/jpeg", True

            except Exception as e:
                raise RuntimeError(f"GIF 处理失败: {e}") from e

        # 静态图：不超限则直接返回
        if len(image_bytes) <= max_bytes:
            return image_bytes, mime, False

        # 静态图：超限则逐步压缩
        try:
            img = PILImage.open(io.BytesIO(image_bytes))
        except Exception as e:
            raise RuntimeError(f"无法打开图片: {e}") from e

        output_format = {
            "image/png": "JPEG",   # PNG 超大时转 JPEG 压缩效果更好
            "image/jpeg": "JPEG",
            "image/jpg": "JPEG",
            "image/webp": "WEBP",
        }.get(mime, "JPEG")
        output_mime = "image/jpeg" if output_format == "JPEG" else mime

        quality = 85
        scale = 1.0
        compressed = b""

        while quality >= 30 or scale > 0.4:
            output = io.BytesIO()
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
                        f"图片已压缩: {len(image_bytes)} → {len(compressed)} 字节 "
                        f"(质量 {quality}, 缩放 {scale:.2f})"
                    )
                    return compressed, output_mime, False
            except Exception as e:
                raise RuntimeError(f"压缩图片失败: {e}") from e

            if quality > 30:
                quality = max(30, quality - 10)
            else:
                scale = round(scale - 0.1, 1)

        logger.warning(f"图片压缩后仍超限，使用最后结果: {len(compressed)} 字节")
        return compressed, output_mime, False
    
    @staticmethod
    def _build_persona_prompt():
        """从主配置人格字段组装 persona 指令片段。"""
        p = get_core_config().personality

        alias = "、".join(p.alias_names) if p.alias_names else ""
        safety = "\n".join(f"- {x}" for x in (p.safety_guidelines or []))
        negatives = "\n".join(f"- {x}" for x in (p.negative_behaviors or []))

        parts = [
            f"你的昵称：{p.nickname}",
            f"你的别名：{alias}" if alias else "",
            f"核心人格：{p.personality_core}",
            f"人格侧面：{p.personality_side}" if p.personality_side else "",
            f"身份：{p.identity}",
            f"背景故事（不应主动复述）：{p.background_story}" if p.background_story else "",
            f"回复风格：{p.reply_style}",
            "安全与互动底线：\n" + safety if safety else "",
            "禁止行为：\n" + negatives if negatives else "",
        ]
        return "\n".join([x for x in parts if x])

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        """从模型输出中提取 JSON object。"""
        if not text:
            return None

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        candidate = text[start : end + 1]
        try:
            obj = json.loads(candidate)
        except Exception:
            return None

        if isinstance(obj, dict):
            return obj
        return None

    async def _align_data_dir_with_db(self) -> None:
        """对齐 data_dir 与向量库记录。

        规则：
        - data_dir 中被删除的文件：清除向量库对应条目
        - data_dir 中多余的文件（库里无记录）：删除该文件

        该方法应在每次入库任务开头执行。
        """
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        vdb = self._vector_db()
        collection = self._collection_name()
        await vdb.get_or_create_collection(collection)

        # 1) 扫描磁盘文件
        files_on_disk = await asyncio.to_thread(self._scan_store_paths, data_dir)

        # 2) 扫描向量库记录（全量 get）
        paths_in_db: set[str] = set()
        offset = 0
        limit = 512
        while True:
            data = await vdb.get(
                collection_name=collection,
                limit=limit,
                offset=offset,
                include=["metadatas"],
            )
            ids: list[str] = list(data.get("ids") or [])
            metadatas: list[dict[str, Any]] = list(data.get("metadatas") or [])

            if not ids:
                break

            # 容错：metadatas 可能长度不一致
            for i, record_id in enumerate(ids):
                meta = metadatas[i] if i < len(metadatas) else {}
                path_value = str(meta.get("path") or "").strip()
                if not path_value:
                    # metadata 缺失 path，直接删掉该条
                    await vdb.delete(collection_name=collection, ids=[record_id])
                    continue
                paths_in_db.add(path_value)

            offset += len(ids)

        # 3) data 被删 -> 清库
        missing_files = sorted(paths_in_db - files_on_disk)
        for missing_path in missing_files:
            await vdb.delete(collection_name=collection, where={"path": missing_path})

        # 4) 磁盘多余 -> 删文件
        orphan_files = sorted(files_on_disk - paths_in_db)
        for orphan_path in orphan_files:
            try:
                await asyncio.to_thread(self._delete_file, orphan_path)
            except Exception as e:
                logger.warning(f"删除孤儿文件失败: {orphan_path} - {e}")

    async def _pick_random_media_cache_file(self) -> Path | None:
        """按随机顺序从 media cache 选择未入库且未被拒绝的图片。"""
        root = self._media_cache_dir()
        candidates = await asyncio.to_thread(self._list_meme_files, root)
        if not candidates:
            return None
        random.shuffle(candidates)
        for candidate in candidates:
            try:
                _, meme_id = await asyncio.to_thread(self._read_file_with_hash, candidate)
            except OSError as error:
                logger.warning(f"读取缓存表情包失败: {candidate} - {error}")
                continue

            if meme_id in self._rejected_hashes:
                continue
            if not await self._already_ingested(meme_id):
                return candidate

        return None

    async def _already_ingested(self, source_hash: str) -> bool:
        """检查某个表情包（按 hash）是否已入库。"""
        vdb = self._vector_db()
        collection = self._collection_name()
        await vdb.get_or_create_collection(collection)
        data = await vdb.get(
            collection_name=collection,
            where={"source_hash": source_hash},
            limit=1,
            include=["metadatas"],
        )
        ids: list[str] = list(data.get("ids") or [])
        return bool(ids)

    async def _vlm_decide_and_label(
        self,
        *,
        image_base64: str,
        mime: str,
        is_gif_collage: bool = False,
        decide_collection: bool = True,
        extra_prompt: str = "",
    ) -> dict[str, Any] | None:
        """调用 VLM 标注图片，可选执行收藏决策或附加识别提示。"""
        try:
            model_set = get_model_set_by_task("vlm")
        except Exception:
            logger.debug("未配置 VLM 任务模型，跳过入库")
            return None

        persona = self._build_persona_prompt()
        tag_list = "、".join(EMOTION_TAG_PRESET)

        gif_hint = (
            "注意：这是一个 GIF 动图表情包的关键帧截图（网格排列），请综合所有帧的内容进行描述。\n"
            if is_gif_collage else ""
        )
        task = (
            "你将看到一张表情包图片。你的任务：根据人设，决定你是否愿意把它收藏起来以后自己使用。\n"
            if decide_collection
            else "你将看到一张图片或表情包。请重新查看图片并生成标注，不判断是否收藏。\n"
        )
        output_schema = (
            '{"keep": true/false, "description": "描述内容，文字：\'图中文字\'", "emotion_tags": ["标签1", "标签2"]}\n\n'
            if decide_collection
            else '{"description": "描述内容，文字：\'图中文字\'", "emotion_tags": ["标签1", "标签2"]}\n\n'
        )
        decision_hint = "- keep：根据人设决定是否收藏\n" if decide_collection else ""
        collection_rules = (
            "收藏标准：\n"
            "- 质量高且表达生动的表情包\n"
            "- 避免收藏低质、冒犯、违规或与人设不符的\n\n"
            if decide_collection else ""
        )

        prompt = (
            task
            + gif_hint
            + "你必须输出严格 JSON（不要输出任何额外文字），格式如下：\n"
            + output_schema
            + "description 要求（文字部分不计入字数限制）：\n"
            "- 概括表情包传达的核心情绪、氛围和画面主要特征\n"
            "- 准确复述图中所有文字，格式为：文字：'逐字抄录'，放在末尾\n"
            "- 如果确保认出表情包的具体来源（作品名、角色名等），请补充说明\n"
            "- 无法确定出处则省略，只做客观描述\n"
            "- 总体 40 字以内（不计图中文字）\n"
            "- 无文字则省略文字部分\n\n"
            "JSON 字段说明：\n"
            + decision_hint
            + "- emotion_tags：必须从预设标签中选择（可多选）；仅拒绝收藏时可为空\n"
            "- 预设标签："
            + tag_list
            + "\n\n"
            + collection_rules
            + "人设（来自主配置）：\n"
            + persona
        )
        if extra_prompt.strip():
            prompt += (
                "\n\n识别时需要重点复核的提示：\n"
                + extra_prompt.strip()
                + "\n请以原图为依据核对提示，不要把提示中的猜测当作事实；仍按上述 JSON 格式输出。"
            )

        from src.kernel.llm import Image, LLMContextManager, LLMPayload, ROLE, Text

        context_manager = LLMContextManager()
        request = create_llm_request(
            model_set=model_set,
            request_name="emoji_sender_label",
            context_manager=context_manager,
        )

        image_value = f"data:{mime};base64,{image_base64}"
        request.add_payload(LLMPayload(ROLE.USER, [Text(prompt), Image(image_value)]))

        try:
            response = await request.send(stream=False)
            await response
        except Exception as e:
            logger.warning(f"VLM 标注失败: {e}")
            return None

        raw = (response.message or "").strip()
        obj = self._extract_json_object(raw)
        if obj is None:
            logger.warning("VLM 输出无法解析为 JSON，跳过")
            return None

        keep = obj.get("keep") if decide_collection else True
        if not isinstance(keep, bool):
            logger.warning("VLM 收藏决策必须为布尔值，跳过")
            return None

        description = str(obj.get("description") or "").strip()
        tags = obj.get("emotion_tags")
        if not isinstance(tags, list):
            tags = []

        filtered_tags = list(dict.fromkeys(
            str(t).strip() for t in tags if isinstance(t, (str, int, float)) and str(t).strip() in EMOTION_TAG_PRESET
        ))

        if keep and (not description or not filtered_tags):
            logger.warning("VLM 收藏标注缺少有效描述或情感标签，跳过")
            return None

        if len(description) > 200:
            description = description[:197] + "..."

        return {
            "keep": keep,
            "description": description,
            "emotion_tags": filtered_tags,
        }

    async def _recognize_requested_meme(
        self, payload: bytes, mime: str, extra_prompt: str = ""
    ) -> dict[str, Any] | None:
        """重新查看指定图片并生成标注，不决定是否收藏。"""
        vlm_bytes, vlm_mime, is_gif_collage = await asyncio.to_thread(
            self._compress_image_for_vlm, payload, mime
        )
        image_base64 = await asyncio.to_thread(base64_encode_bytes, vlm_bytes)
        labeled = await self._vlm_decide_and_label(
            image_base64=image_base64,
            mime=vlm_mime,
            is_gif_collage=is_gif_collage,
            decide_collection=False,
            extra_prompt=extra_prompt,
        )
        if not labeled or not labeled["description"] or not labeled["emotion_tags"]:
            return None
        return labeled

    async def _label_requested_meme(
        self, payload: bytes, mime: str, extra_prompt: str = ""
    ) -> tuple[dict[str, Any], list[float]] | None:
        """为指定图片生成标注和收藏库检索向量。"""
        labeled = await self._recognize_requested_meme(payload, mime, extra_prompt)
        if labeled is None:
            return None
        embedding = await self._embed_query(labeled["description"], "emoji_sender_label_embedding")
        if not embedding:
            return None
        return labeled, embedding

    async def _replace_media_description(
        self, media_info: dict[str, Any], description: str
    ) -> None:
        """覆盖媒体描述和识别缓存，缓存写入失败时恢复媒体记录。"""
        media_id = str(media_info["image_id"])
        media_type = str(media_info["type"])
        cached = await database_api.get_by(
            ImageDescriptions, image_description_hash=media_id, type=media_type
        )
        updated = await database_api.update(
            Images, id=media_info["id"], obj_in={"description": description, "vlm_processed": True}
        )
        if updated is None:
            raise RuntimeError("图片媒体记录已不可用")
        values = {"description": description, "timestamp": time.time()}
        try:
            if cached is None:
                await database_api.create(
                    ImageDescriptions,
                    {**values, "image_description_hash": media_id, "type": media_type},
                )
            else:
                updated = await database_api.update(ImageDescriptions, id=cached.id, obj_in=values)
                if updated is None:
                    raise RuntimeError("图片描述缓存已不可用")
        except Exception:
            restored = await database_api.update(
                Images,
                id=media_info["id"],
                obj_in={
                    "description": media_info.get("description"),
                    "vlm_processed": bool(media_info.get("vlm_processed")),
                },
            )
            if restored is None:
                raise RuntimeError("图片媒体记录恢复失败")
            raise

    def _refresh_journal_dir(self) -> Path:
        """刷新恢复记录目录，与收藏文件目录分离。"""
        directory = self._data_dir()
        return directory.parent / f".{directory.name}_refresh"

    def _save_refresh_journal(self, meme_id: str, record: dict[str, Any]) -> None:
        """在修改数据库前原子保存刷新所需的新旧值。"""
        directory = self._refresh_journal_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{meme_id}.json"
        temporary = target.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=True)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _remove_refresh_journal(self, meme_id: str) -> None:
        """删除已完成的恢复记录及空目录。"""
        directory = self._refresh_journal_dir()
        (directory / f"{meme_id}.json").unlink(missing_ok=True)
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()

    async def _restore_refresh(
        self, record: dict[str, Any], collection: Collection | None
    ) -> bool:
        """恢复媒体和收藏的旧值；收藏库不可用时保留恢复记录。"""
        media_info = record["media_info"]
        if media_info is not None:
            updated = await database_api.update(
                Images, id=media_info["id"], obj_in={
                    "description": media_info.get("description"),
                    "vlm_processed": bool(media_info.get("vlm_processed")),
                },
            )
            if updated is None:
                raise RuntimeError("图片媒体记录恢复失败")
            cached = await database_api.get_by(
                ImageDescriptions,
                image_description_hash=media_info["image_id"], type=media_info["type"],
            )
            previous_cache = record["cache"]
            if previous_cache is None:
                if cached is not None:
                    await database_api.delete(ImageDescriptions, id=cached.id)
            else:
                values = {
                    "description": previous_cache["description"],
                    "timestamp": previous_cache["timestamp"],
                }
                if cached is None:
                    await database_api.create(ImageDescriptions, {
                        **values, "image_description_hash": media_info["image_id"],
                        "type": media_info["type"],
                    })
                elif (
                    cached.description != values["description"] or cached.timestamp != values["timestamp"]
                ) and await database_api.update(ImageDescriptions, id=cached.id, obj_in=values) is None:
                    raise RuntimeError("图片描述缓存恢复失败")
        previous = record["previous"]
        if previous is not None:
            if collection is None:
                return False
            await asyncio.to_thread(collection.upsert, **previous)
            current = await asyncio.to_thread(
                collection.get, where={"meme_id": record["meme_id"]}, include=["metadatas"]
            )
            added_ids = [record_id for record_id in current["ids"] if record_id not in previous["ids"]]
            if added_ids:
                await asyncio.to_thread(collection.delete, ids=added_ids)
        return True

    async def _recover_refreshes(self, collection: Collection | None) -> bool:
        """恢复中断的刷新；未完成的恢复阻止新的收藏库操作。"""
        async def recover() -> bool:
            """逐条恢复旧值或完成延期的收藏同步。"""
            paths = await asyncio.to_thread(lambda: sorted(self._refresh_journal_dir().glob("*.json")))
            complete = True
            for path in paths:
                record = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                if record.get("deferred"):
                    if collection is None:
                        complete = False
                        continue
                    current = await asyncio.to_thread(
                        collection.get, where={"meme_id": record["meme_id"]}, include=["metadatas"]
                    )
                    if current["ids"]:
                        metadatas = current["metadatas"]
                        if not metadatas or not metadatas[0]:
                            raise RuntimeError("延期同步的收藏记录缺少元数据")
                        labeled = record["labeled"]
                        embedding = await self._embed_query(labeled["description"], "emoji_sender_label_embedding")
                        if not embedding:
                            raise RuntimeError("延期同步的检索向量生成失败")
                        new_ids = await self._store_meme_labels(
                            collection, record["meme_id"], labeled, embedding, dict(metadatas[0])
                        )
                        obsolete_ids = [record_id for record_id in current["ids"] if record_id not in new_ids]
                        if obsolete_ids:
                            await asyncio.to_thread(collection.delete, ids=obsolete_ids)
                    await self._replace_media_description(record["media_info"], record["labeled"]["description"])
                elif not await self._restore_refresh(record, collection):
                    complete = False
                    continue
                await asyncio.to_thread(self._remove_refresh_journal, record["meme_id"])
            return complete

        if not self._refresh_journal_dir().exists():
            return True
        return bool(await self._run_refresh_task(recover()))

    async def _run_refresh_task(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        """等待受管理的保存或恢复任务完成，然后传播调用方取消。"""
        task = get_task_manager().create_task(coroutine, name="emoji_sender_refresh_commit").task
        assert task is not None
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _commit_refresh(
        self, *, meme_id: str, media_info: dict[str, Any] | None,
        labeled: dict[str, Any], collection: Collection | None = None,
        embedding: list[float] | None = None, metadata: dict[str, Any] | None = None,
        previous: dict[str, Any] | None = None, deferred: bool = False,
    ) -> None:
        """保存可恢复的刷新，调用方取消时等待保存完成再传播取消。"""
        cached = await database_api.get_by(
            ImageDescriptions, image_description_hash=media_info["image_id"], type=media_info["type"]
        ) if media_info is not None else None
        if previous is not None:
            previous = {
                "ids": previous["ids"], "metadatas": previous["metadatas"],
                "documents": previous["documents"],
                "embeddings": previous["embeddings"].tolist(),
            }
        record = {
            "meme_id": meme_id, "media_info": media_info,
            "cache": {"description": cached.description, "timestamp": cached.timestamp} if cached else None,
            "previous": previous, "deferred": deferred, "labeled": labeled,
        }

        async def persist() -> None:
            """保留恢复记录直到媒体和收藏均保存成功。"""
            await asyncio.to_thread(self._save_refresh_journal, meme_id, record)
            try:
                if collection is not None and previous is not None:
                    if embedding is None or metadata is None:
                        raise RuntimeError("收藏刷新缺少标注数据")
                    new_ids = await self._store_meme_labels(collection, meme_id, labeled, embedding, metadata)
                    obsolete_ids = [record_id for record_id in previous["ids"] if record_id not in new_ids]
                    if obsolete_ids:
                        await asyncio.to_thread(collection.delete, ids=obsolete_ids)
                if media_info is not None:
                    await self._replace_media_description(media_info, labeled["description"])
            except Exception:
                if await self._restore_refresh(record, collection):
                    await asyncio.to_thread(self._remove_refresh_journal, meme_id)
                raise
            if not deferred:
                await asyncio.to_thread(self._remove_refresh_journal, meme_id)

        await self._run_refresh_task(persist())

    async def _store_meme_labels(
        self,
        collection: Collection,
        meme_id: str,
        labeled: dict[str, Any],
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> list[str]:
        """一次写入指定图片的全部标签记录，返回写入的记录 ID。"""
        tags: list[str] = labeled["emotion_tags"]
        description: str = labeled["description"]
        ids = [f"{meme_id}:{tag}" for tag in tags]
        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            embeddings=[list(embedding) for _ in tags],
            documents=[description for _ in tags],
            metadatas=[{**metadata, "tag": tag, "description": description} for tag in tags],
        )
        return ids

    async def collect_meme(self, *, media_id: str, note: str = "") -> tuple[bool, str]:
        """按媒体哈希收藏图片并保存可选备注；调用方须验证当前聊天上下文。"""
        requested_id = media_id.strip().lower()
        if len(requested_id) != 64 or any(char not in "0123456789abcdef" for char in requested_id):
            return False, "请使用聊天中图片或表情包括号里的完整 media_id"

        async with _INGEST_LOCK:
            try:
                collection = await self._vector_db().get_or_create_collection(self._collection_name())
                if collection is None:
                    return False, "表情包库暂时不可用"
                if not await self._recover_refreshes(collection):
                    return False, "表情包库仍有待恢复的刷新，请稍后重试"
                existing = await asyncio.to_thread(
                    collection.get, where={"media_id": requested_id}, include=["metadatas"]
                )
                if existing["ids"]:
                    meme_id = existing["metadatas"][0]["meme_id"]
                    return True, f"这张表情包已经收藏过了，id={meme_id}"

                media_info = await get_media_info(requested_id)
                if not media_info or media_info.get("type") not in ("image", "emoji"):
                    return False, "没有找到这张图片或表情包的原文件"
                source_value = media_info.get("path")
                if not source_value:
                    return False, "这张图片的原文件已不可用"
                source = Path(source_value)
                if source.suffix.lower() not in _ALLOWED_SUFFIXES:
                    return False, "这张图片的文件格式不支持收藏"
                payload, meme_id = await asyncio.to_thread(self._read_file_with_hash, source)
                if self._chat_media_hash(payload) != requested_id:
                    return False, "图片文件与 media_id 不一致，未收藏"
                existing = await asyncio.to_thread(
                    collection.get, where={"meme_id": meme_id}, include=["metadatas"]
                )
                if existing["ids"]:
                    return True, f"这张表情包已经收藏过了，id={meme_id}"
                max_memes = int(self._cfg().storage.max_memes)
                count = await asyncio.to_thread(self._count_meme_files, self._data_dir())
                if max_memes > 0 and count >= max_memes:
                    return False, "表情包收藏数量已达上限"

                prepared = await self._label_requested_meme(payload, self._guess_mime(source.suffix))
                if prepared is None:
                    return False, "图片识别或检索向量生成失败，未收藏，请稍后重试"
                labeled, embedding = prepared
                target = self._data_dir() / f"{meme_id}{source.suffix.lower()}"
                await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                target_existed = target.exists()
                await asyncio.to_thread(self._copy_file, source, target)
                metadata = {
                    "meme_id": meme_id,
                    "media_id": requested_id,
                    "path": self._path_to_store_value(target),
                    "source_hash": meme_id,
                    "source_cache_path": self._path_to_store_value(source),
                    "created_at": time.time(),
                }
                if note.strip():
                    metadata["note"] = note.strip()
                try:
                    await self._store_meme_labels(collection, meme_id, labeled, embedding, metadata)
                except Exception:
                    if not target_existed:
                        await asyncio.to_thread(target.unlink, missing_ok=True)
                    raise
                self._rejected_hashes.discard(meme_id)
                return True, (
                    f"已收藏表情包，id={meme_id}\n"
                    f"标签：{'、'.join(labeled['emotion_tags'])}\n描述：{labeled['description']}"
                )
            except Exception as error:
                logger.warning(f"主动收藏表情包失败: {error}")
                return False, "收藏失败，原图未删除，请稍后重试"

    async def refresh_meme(
        self, *, meme_id: str, extra_prompt: str = "", allow_chat_media: bool = True,
        note: str | None = None, note_only: bool = False,
    ) -> tuple[bool, str]:
        """重新识别图片或仅修改收藏备注；未传备注时保留原值，空字符串清除。"""
        prefix = meme_id.strip().lower()
        if not 12 <= len(prefix) <= 64 or any(char not in "0123456789abcdef" for char in prefix):
            return False, "请使用聊天中的完整 media_id、收藏返回的完整 id 或检索结果中的 12 位 id"
        if note_only and note is None:
            return False, "只改备注时请提供 note；清除备注请传空字符串"
        if note_only and extra_prompt.strip():
            return False, "只改备注时不使用 extra_prompt；需要重新识别请将 note_only 设为 false"

        async with _INGEST_LOCK:
            try:
                collection = None
                records: dict[str, Any] = {"ids": [], "metadatas": []}
                try:
                    collection = await self._vector_db().get_or_create_collection(self._collection_name())
                except (ChromaError, OSError, RuntimeError) as error:
                    logger.warning(f"访问表情包库失败，聊天图片刷新将延期同步收藏: {error}")
                if collection is not None:
                    if not await self._recover_refreshes(collection):
                        return False, "表情包库仍有待恢复的刷新，请稍后重试"
                    try:
                        query = {"$or": [{"meme_id": prefix}, {"media_id": prefix}]} if len(prefix) == 64 else None
                        records = await asyncio.to_thread(collection.get, where=query, include=["metadatas"])
                    except (ChromaError, OSError, RuntimeError) as error:
                        logger.warning(f"查询表情包库失败，聊天图片刷新将延期同步收藏: {error}")
                        collection = None
                if collection is None and (note_only or note is not None):
                    return False, "表情包库暂时不可用，无法保存备注，未修改识别结果"
                if collection is None and (len(prefix) != 64 or not allow_chat_media):
                    return False, "表情包库暂时不可用，请使用当前聊天图片的完整 media_id"
                matched = [
                    (record_id, metadata)
                    for record_id, metadata in zip(records["ids"], records["metadatas"] or [])
                    if metadata and (
                        str(metadata.get("meme_id", "")).startswith(prefix)
                        or metadata.get("media_id") == prefix
                    )
                ]
                media_info: dict[str, Any] | None = None
                if not matched:
                    if note_only:
                        return False, "没有找到对应收藏，备注只能写在已收藏图片上，请使用收藏库 id"
                    if len(prefix) != 64:
                        return False, "没有找到对应表情包，请使用聊天中的完整 media_id 或收藏库 id"
                    if not allow_chat_media:
                        return False, "当前聊天上下文中没有这张图片，请使用当前聊天中的完整 media_id"
                    media_info = await get_media_info(prefix)
                    if not media_info or media_info.get("type") not in ("image", "emoji"):
                        return False, "没有找到这张图片，请使用聊天中的完整 media_id 或收藏库 id"
                    path_value = media_info.get("path")
                    if not path_value:
                        return False, "图片原文件已不可用，无法重新识别，请重新发送图片；已保留旧识别结果"
                    source = Path(path_value)
                    payload, full_id = await asyncio.to_thread(self._read_file_with_hash, source)
                    if self._chat_media_hash(payload) != prefix:
                        return False, "图片文件与 media_id 不一致，未修改旧识别结果"
                    if (self._refresh_journal_dir() / f"{full_id}.json").exists():
                        return False, "这张图片仍有待恢复的刷新，请在表情包库恢复后重试"
                    if collection is not None:
                        records = await asyncio.to_thread(
                            collection.get, where={"meme_id": full_id}, include=["metadatas"]
                        )
                    matched = [
                        (record_id, metadata)
                        for record_id, metadata in zip(records["ids"], records["metadatas"] or [])
                        if metadata
                    ]
                else:
                    full_ids = {str(metadata["meme_id"]) for _, metadata in matched}
                    if len(full_ids) != 1:
                        return False, "这个 id 对应多张表情包，请使用完整 id"
                    full_id = full_ids.pop()
                    if note_only:
                        assert collection is not None and note is not None
                        await self._run_refresh_task(asyncio.to_thread(
                            collection.update,
                            ids=[record_id for record_id, _ in matched],
                            metadatas=[{**record_metadata, "note": note.strip()} for _, record_metadata in matched],
                        ))
                        return True, f"已{'更新' if note.strip() else '清除'}表情包备注，id={full_id}"
                    metadata = dict(matched[0][1])
                    path_value = metadata.get("path")
                    source = Path(path_value) if path_value else None
                    if source is None or not source.is_file():
                        cache_path = metadata.get("source_cache_path")
                        source = Path(cache_path) if cache_path else None
                    if source is None or not source.is_file():
                        media_id = str(metadata.get("media_id") or "")
                        media_info = await get_media_info(media_id) if media_id else None
                        path_value = media_info.get("path") if media_info else None
                        if not path_value:
                            return False, "图片原文件已不可用，无法重新识别，请重新发送图片；已保留旧识别结果"
                        source = Path(path_value)
                    payload, source_hash = await asyncio.to_thread(self._read_file_with_hash, source)
                    if source_hash != full_id:
                        return False, "收藏文件与 id 不一致，未修改旧记录"
                    media_info = await get_media_info(self._chat_media_hash(payload))

                if not matched:
                    if note is not None:
                        return False, "备注只能写在已收藏图片上，请先收藏；未修改识别结果"
                    labeled = await self._recognize_requested_meme(
                        payload, self._guess_mime(source.suffix), extra_prompt
                    )
                    if labeled is None:
                        return False, "重新识别失败，已保留旧识别结果"
                    if media_info is None:
                        return False, "图片媒体记录已不可用，未修改旧识别结果"
                    await self._commit_refresh(
                        meme_id=full_id, media_info=media_info, labeled=labeled, deferred=collection is None
                    )
                    return True, (
                        f"已重新识别图片，media_id={prefix}\n"
                        f"标签：{'、'.join(labeled['emotion_tags'])}\n描述：{labeled['description']}"
                        + ("\n表情包库暂时不可用，已有收藏的同步将在恢复后完成" if collection is None else "")
                    )

                assert collection is not None
                metadata = dict(matched[0][1])
                if note is not None:
                    metadata["note"] = note.strip()
                prepared = await self._label_requested_meme(
                    payload, self._guess_mime(source.suffix), extra_prompt
                )
                if prepared is None:
                    return False, "重新识别或检索向量生成失败，已保留旧记录"
                labeled, embedding = prepared
                old_ids = [record_id for record_id, _ in matched]
                previous = await asyncio.to_thread(
                    collection.get, ids=old_ids, include=["metadatas", "documents", "embeddings"]
                )
                await self._commit_refresh(
                    meme_id=full_id, media_info=media_info if media_info and media_info.get("type") in ("image", "emoji") else None,
                    labeled=labeled, collection=collection, embedding=embedding,
                    metadata=metadata, previous=previous,
                )
                return True, (
                    f"已重新识别表情包，id={full_id}\n"
                    f"标签：{'、'.join(labeled['emotion_tags'])}\n描述：{labeled['description']}"
                )
            except FileNotFoundError:
                return False, "图片原文件已不可用，无法重新识别，请重新发送图片；已保留旧识别结果"
            except Exception as error:
                logger.warning(f"重新识别表情包失败: {error}")
                return False, "重新识别或保存失败，请稍后重试"

    async def ingest_once(self) -> None:
        """执行一次入库任务。

        流程：对齐 → 【优先手动目录 或 随机抽取】 → 去重检查 → 图片压缩 → VLM 决策+标注 → 复制 → embedding → 写入向量库。
        """
        if _INGEST_LOCK.locked():
            logger.debug("上一轮入库尚未结束，跳过本轮")
            return

        async with _INGEST_LOCK:
            if self._refresh_journal_dir().exists():
                collection = await self._vector_db().get_or_create_collection(self._collection_name())
                if collection is None or not await self._recover_refreshes(collection):
                    logger.warning("表情包刷新尚未恢复，暂停入库")
                    return
            max_memes = int(self._cfg().storage.max_memes)
            if max_memes > 0:
                data_dir = self._data_dir()
                current_count = await asyncio.to_thread(self._count_meme_files, data_dir)

                if current_count >= max_memes:
                    logger.info(
                        f"表情包数量已达上限，跳过入库: {current_count}/{max_memes}"
                    )
                    return

            await self._align_data_dir_with_db()

            # 优先从手动目录获取表情包，否则才从随机缓存
            source = await self._pick_next_manual_meme_file()
            if source is None:
                if not self._cfg().ingest.sample_from_media_cache:
                    return
                source = await self._pick_random_media_cache_file()

            if source is None:
                return

            try:
                payload, meme_id = await asyncio.to_thread(self._read_file_with_hash, source)
            except Exception as e:
                logger.warning(f"读取候选表情包失败: {source} - {e}")
                return

            if meme_id in self._rejected_hashes or await self._already_ingested(meme_id):
                return

            # 压缩图片用于 VLM
            try:
                mime = self._guess_mime(source.suffix)
                vlm_bytes, vlm_mime, is_gif_collage = await asyncio.to_thread(
                    self._compress_image_for_vlm,
                    payload,
                    mime,
                )
            except Exception as e:
                logger.warning(f"压缩图片失败: {source} - {e}")
                return

            image_base64 = await asyncio.to_thread(base64_encode_bytes, vlm_bytes)

            labeled = await self._vlm_decide_and_label(
                image_base64=image_base64, 
                mime=vlm_mime,
                is_gif_collage=is_gif_collage
            )
            if not labeled:
                return
            if labeled.get("keep") is False:
                self._rejected_hashes.add(meme_id)
                return

            description = str(labeled.get("description") or "").strip()
            tags: list[str] = list(labeled.get("emotion_tags") or [])
            tags = [t for t in tags if t in EMOTION_TAG_PRESET]
            if not description or not tags:
                return

            # 复制文件到插件 data 目录
            data_dir = self._data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            suffix = source.suffix.lower() if source.suffix.lower() in _ALLOWED_SUFFIXES else ".png"
            target_path = data_dir / f"{meme_id}{suffix}"
            try:
                await asyncio.to_thread(self._copy_file, source, target_path)
            except Exception as e:
                logger.warning(f"复制表情包失败: {source} -> {target_path} - {e}")
                return

            # 生成 embedding
            try:
                embedding_model_set = get_model_set_by_task("embedding")
            except Exception:
                logger.warning("未配置 embedding 任务模型，跳过入库")
                return

            try:
                emb_req = create_embedding_request(
                    model_set=embedding_model_set,
                    request_name="emoji_sender_embedding",
                    inputs=[description],
                )
                emb_resp = await emb_req.send()
                embedding = emb_resp.embeddings[0]
            except Exception as e:
                logger.warning(f"生成 embedding 失败: {e}")
                return

            # 写入向量库：每个 tag 一条记录（metadata 全标量）
            vdb = self._vector_db()
            collection = self._collection_name()
            await vdb.get_or_create_collection(collection)

            ids: list[str] = []
            embeddings: list[list[float]] = []
            documents: list[str] = []
            metadatas: list[dict[str, Any]] = []

            stored_path = self._path_to_store_value(target_path)
            now_ts = time.time()

            for tag in tags:
                ids.append(f"{meme_id}:{tag}")
                embeddings.append(list(embedding))
                documents.append(description)
                metadatas.append(
                    {
                        "meme_id": meme_id,
                        "tag": tag,
                        "path": stored_path,
                        "description": description,
                        "source_hash": meme_id,
                        "media_id": self._chat_media_hash(payload),
                        "source_cache_path": self._path_to_store_value(source),
                        "created_at": float(now_ts),
                    }
                )

            try:
                await vdb.add(
                    collection_name=collection,
                    ids=ids,
                    embeddings=embeddings,
                    documents=documents,
                    metadatas=metadatas,
                )
                logger.info(f"收藏表情包: {meme_id[:8]}... tags={tags}")
            except Exception as e:
                logger.warning(f"写入向量库失败: {e}")

    async def _embed_query(self, query: str, request_name: str) -> list[float] | None:
        """生成查询文本的 embedding，失败返回 None。"""
        try:
            embedding_model_set = get_model_set_by_task("embedding")
        except Exception:
            return None

        try:
            emb_req = create_embedding_request(
                model_set=embedding_model_set,
                request_name=request_name,
                inputs=[query],
            )
            emb_resp = await emb_req.send()
            return list(emb_resp.embeddings[0])
        except Exception as e:
            logger.warning(f"生成查询 embedding 失败: {e}")
            return None

    async def _query_candidates(
        self,
        description_query: str,
        emotion_tags: list[str] | None,
        n_results: int,
    ) -> list[MemeCandidate]:
        """按描述与 tag 过滤执行向量检索，解析并返回候选列表。"""
        query = str(description_query or "").strip()
        if not query:
            raise ValueError("description_query 不能为空")

        tags: list[str] = []
        if emotion_tags:
            tags = [str(t).strip() for t in emotion_tags if str(t).strip() in EMOTION_TAG_PRESET]

        query_embedding = await self._embed_query(query, "emoji_sender_search")
        if query_embedding is None:
            return []

        vdb = self._vector_db()
        collection = self._collection_name()
        await vdb.get_or_create_collection(collection)
        if self._refresh_journal_dir().exists():
            async with _INGEST_LOCK:
                raw_collection = await vdb.get_or_create_collection(collection)
                if raw_collection is None or not await self._recover_refreshes(raw_collection):
                    raise RuntimeError("表情包刷新尚未恢复，暂不可检索")

        where: dict[str, Any] | None = None
        if tags:
            where = {"tag": tags}

        results = await vdb.query(
            collection_name=collection,
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
        )

        ids_list: list[list[str]] = list(results.get("ids") or [])
        distances_list: list[list[float]] = list(results.get("distances") or [])
        metadatas_list: list[list[dict[str, Any]]] = list(results.get("metadatas") or [])

        if not ids_list or not ids_list[0]:
            return []

        candidates: list[MemeCandidate] = []
        for i, _ in enumerate(ids_list[0]):
            distance = float(distances_list[0][i]) if distances_list and distances_list[0] and i < len(distances_list[0]) else 999.0
            meta = metadatas_list[0][i] if metadatas_list and metadatas_list[0] and i < len(metadatas_list[0]) else {}
            cand = self._build_candidate(distance=distance, metadata=meta)
            if cand is not None:
                candidates.append(cand)
        return candidates

    async def search_best(
        self,
        description_query: str,
        emotion_tags: list[str] | None = None,
        stream_id: str = "",
    ) -> dict[str, Any] | None:
        """按 tag 过滤后执行向量检索，返回温度采样后的候选。"""
        query = str(description_query or "").strip()
        if not query:
            raise ValueError("description_query 不能为空")

        tags: list[str] = []
        if emotion_tags:
            tags = [str(t).strip() for t in emotion_tags if str(t).strip() in EMOTION_TAG_PRESET]

        top_n = int(self._cfg().vector.top_n)
        max_distance = float(self._cfg().vector.max_distance)

        all_candidates = await self._query_candidates(query, tags, top_n)
        if not all_candidates:
            return None

        # 使用历史过滤：阈值内候选全被过滤时回退全量，保持原有 fallback 行为
        recent: set[str] = set()
        if self._dedup_enabled() and stream_id:
            recent = self._recently_used(stream_id)
            if recent:
                filtered = [c for c in all_candidates if c.meme_id not in recent]
                if filtered:
                    all_candidates = filtered

        best_any: MemeCandidate | None = None
        candidates_under_threshold: list[MemeCandidate] = []
        for cand in all_candidates:
            if best_any is None or cand.distance < best_any.distance:
                best_any = cand
            if cand.distance <= max_distance:
                candidates_under_threshold.append(cand)

        fallback_used = False

        # 正常情况：在阈值内候选中按温度采样
        if candidates_under_threshold:
            best = self._select_candidate(candidates_under_threshold)
        else:
            # fallback：仅当“给了标签且标签有效（过滤后非空）”时，允许在指定标签内继续采样
            if tags and best_any is not None:
                best = self._select_candidate(all_candidates)
                fallback_used = True
            else:
                return None

        if best is None:
            return None

        return {
            "meme_id": best.meme_id,
            "tag": best.tag,
            "path": best.path,
            "description": best.description,
            "distance": best.distance,
            "fallback_used": fallback_used,
        }

    async def search_candidates(
        self,
        description_query: str,
        emotion_tags: list[str] | None = None,
        page: int = 1,
        stream_id: str = "",
    ) -> dict[str, Any] | None:
        """按 tag 过滤后向量检索，返回去重、历史过滤、分页后的候选列表。

        Returns:
            {candidates: [{meme_id, short_id, tag, description, distance}], page, total}
            无有效候选时返回 None。
        """
        tags: list[str] = []
        if emotion_tags:
            tags = [str(t).strip() for t in emotion_tags if str(t).strip() in EMOTION_TAG_PRESET]

        page_size = max(1, int(self._cfg().picker.page_size))
        page = max(1, int(page))

        # 拉取足够多的候选：页深 × 页大小 × 元余量，上限 60
        fetch_n = min(60, max(page * page_size * 3, page_size * 3))
        candidates = await self._query_candidates(description_query, tags, fetch_n)
        if not candidates:
            return None

        # 同一 meme 多 tag 多记录：按 meme_id 去重，保留最小距离
        best_by_meme: dict[str, MemeCandidate] = {}
        for cand in candidates:
            existing = best_by_meme.get(cand.meme_id)
            if existing is None or cand.distance < existing.distance:
                best_by_meme[cand.meme_id] = cand
        deduped = sorted(best_by_meme.values(), key=lambda c: c.distance)

        # 使用历史过滤：全被过滤时回退全量，避免无结果可发
        if self._dedup_enabled() and stream_id:
            recent = self._recently_used(stream_id)
            if recent:
                filtered = [c for c in deduped if c.meme_id not in recent]
                if filtered:
                    deduped = filtered

        total = len(deduped)
        start = (page - 1) * page_size
        page_items = deduped[start : start + page_size]
        if not page_items:
            return None

        return {
            "candidates": [
                {
                    "meme_id": cand.meme_id,
                    "short_id": cand.meme_id[: self._SHORT_ID_LENGTH],
                    "tag": cand.tag,
                    "description": cand.description,
                    "distance": cand.distance,
                    **({"note": cand.note} if cand.note else {}),
                }
                for cand in page_items
            ],
            "page": page,
            "total": total,
        }

    async def send_by_id(
        self,
        *,
        short_id: str,
        stream_id: str,
        platform: str | None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """按 meme id 前缀精确发送指定表情包。

        Returns:
            (ok, result, reason)，result 含 meme_id/tag/path/description，失败时为 None。
        """
        prefix = str(short_id or "").strip().lower()
        if not prefix:
            return False, None, "meme_id 不能为空"

        vdb = self._vector_db()
        collection = self._collection_name()
        await vdb.get_or_create_collection(collection)
        if self._refresh_journal_dir().exists():
            async with _INGEST_LOCK:
                raw_collection = await vdb.get_or_create_collection(collection)
                if raw_collection is None or not await self._recover_refreshes(raw_collection):
                    return False, None, "表情包刷新尚未恢复，暂不可发送"

        matched: dict[str, Any] | None = None
        offset = 0
        limit = 512
        while True:
            data = await vdb.get(
                collection_name=collection,
                limit=limit,
                offset=offset,
                include=["metadatas"],
            )
            ids: list[str] = list(data.get("ids") or [])
            metadatas: list[dict[str, Any]] = list(data.get("metadatas") or [])
            if not ids:
                break

            for i, record_id in enumerate(ids):
                meme_id = record_id.split(":", 1)[0]
                if meme_id.startswith(prefix):
                    meta = metadatas[i] if i < len(metadatas) else {}
                    matched = meta
                    break
            if matched is not None:
                break
            offset += len(ids)

        if matched is None:
            return False, None, f"未找到 id={prefix} 对应的表情包，请重新调用 search_emoji_memes 获取有效候选"

        path_value = str(matched.get("path") or "").strip()
        description = str(matched.get("description") or "").strip()
        tag = str(matched.get("tag") or "").strip()
        meme_id = str(matched.get("meme_id") or "").strip()
        if not path_value or not meme_id:
            return False, None, "表情包记录元数据不完整"

        path = Path(path_value)
        if not path.exists():
            return False, None, "表情包文件已被删除"

        try:
            payload = await asyncio.to_thread(self._read_file_bytes, path)
        except Exception as e:
            logger.warning(f"读取表情包失败: {path} - {e}")
            return False, None, "读取表情包文件失败"

        image_base64 = await asyncio.to_thread(base64_encode_bytes, payload)
        processed_plain_text = f"[表情包:{tag}:{description}]" if description else f"[表情包:{tag}]"

        ok = await send_emoji(
            emoji_data=image_base64,
            stream_id=stream_id,
            platform=platform,
            processed_plain_text=processed_plain_text,
        )

        result = {
            "meme_id": meme_id,
            "tag": tag,
            "path": path_value,
            "description": description,
            "fallback_used": False,
        }
        if ok:
            self._record_usage(stream_id, meme_id)
            return True, result, "发送成功"
        return False, result, "发送失败"

    async def send_best_detailed(
        self,
        *,
        stream_id: str,
        platform: str | None,
        description_query: str,
        emotion_tags: list[str] | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """检索并发送最佳表情包，返回详细信息。

        Returns:
            (ok, result, reason)
            - ok: 是否发送成功
            - result: search_best 的返回值（成功与否都会尽量返回，便于上层展示细节）
            - reason: 失败原因或简短状态说明
        """
        result = await self.search_best(
            description_query=description_query,
            emotion_tags=emotion_tags,
            stream_id=stream_id,
        )
        if not result:
            return False, None, "没有找到满足条件的表情包"

        path = Path(str(result["path"]))
        if not path.exists():
            # 用户可能手动删了，下一次入库会对齐；这里直接失败
            return False, result, "表情包文件已被删除"

        try:
            payload = await asyncio.to_thread(self._read_file_bytes, path)
        except Exception as e:
            logger.warning(f"读取表情包失败: {path} - {e}")
            return False, result, "读取表情包文件失败"

        image_base64 = await asyncio.to_thread(base64_encode_bytes, payload)
        desc = str(result.get("description") or "").strip()
        tag = str(result.get("tag") or "").strip()
        processed_plain_text = f"[表情包:{tag}:{desc}]" if desc else f"[表情包:{tag}]"

        ok = await send_emoji(
            emoji_data=image_base64,
            stream_id=stream_id,
            platform=platform,
            processed_plain_text=processed_plain_text,
        )

        if ok:
            self._record_usage(stream_id, str(result.get("meme_id") or ""))
            return True, result, "发送成功"
        return False, result, "发送失败"

    async def send_best(
        self,
        *,
        stream_id: str,
        platform: str | None,
        description_query: str,
        emotion_tags: list[str] | None = None,
    ) -> bool:
        """检索并发送最佳表情包。"""
        ok, _, _ = await self.send_best_detailed(
            stream_id=stream_id,
            platform=platform,
            description_query=description_query,
            emotion_tags=emotion_tags,
        )
        return ok
