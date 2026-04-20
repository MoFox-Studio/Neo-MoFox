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
import base64
import hashlib
import io
import json
import math
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    create_llm_request,
    get_model_set_by_task,
)
from src.app.plugin_system.api.send_api import send_emoji
from src.core.components.base.service import BaseService
from src.core.config import get_core_config
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


class EmojiSenderService(BaseService):
    """emoji_sender 服务。

    对外提供：
    - search_best：按 tag + 向量检索，返回满足阈值并经温度采样的表情包
    - send_best：发送检索到的表情包
    - ingest_once：执行一次入库任务（对齐→抽取→VLM 决策→写入）
    """

    service_name: str = "emoji_sender"
    service_description: str = "表情包收藏、检索与发送服务"
    version: str = "1.0.0"

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

    async def _pick_next_manual_meme_file(self) -> Path | None:
        """从手动目录获取下一个未入库的表情包文件。"""
        manual_dir = self._manual_memes_dir()
        
        candidates: list[Path] = [
            p
            for p in sorted(manual_dir.iterdir())
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
        ]
        if not candidates:
            return None

        # 逐个检查，找到第一个未入库的
        for candidate in candidates:
            try:
                payload = candidate.read_bytes()
            except Exception:
                continue
            
            meme_id = self._sha256_bytes(payload)
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
        files_on_disk: set[str] = set()
        for p in data_dir.iterdir():
            if not p.is_file():
                continue
            if p.suffix.lower() not in _ALLOWED_SUFFIXES:
                continue
            files_on_disk.add(self._path_to_store_value(p))

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
                Path(orphan_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"删除孤儿文件失败: {orphan_path} - {e}")

    def _pick_random_media_cache_file(self) -> Path | None:
        """从 media cache 的 emojis 目录随机挑选一个文件。"""
        root = self._media_cache_dir()
        if not root.exists():
            return None

        candidates: list[Path] = [
            p
            for p in root.iterdir()
            if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
        ]
        if not candidates:
            return None
        return random.choice(candidates)

    async def _vlm_decide_and_label(
        self,
        *,
        image_base64: str,
        mime: str,
        is_gif_collage: bool = False,
    ) -> dict[str, Any] | None:
        """调用 VLM 对表情包做收藏决策与标注。"""
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

        prompt = (
            "你将看到一张表情包图片。你的任务：根据人设，决定你是否愿意把它收藏起来以后自己使用。\n"
            + gif_hint
            + "你必须输出严格 JSON（不要输出任何额外文字），格式如下：\n"
            '{"keep": true/false, "description": "描述内容，文字：\'图中文字\'", "emotion_tags": ["标签1", "标签2"]}\n\n'
            "description 要求（文字部分不计入字数限制）：\n"
            "- 概括表情包传达的核心情绪、氛围和画面主要特征\n"
            "- 准确复述图中所有文字，格式为：文字：'逐字抄录'，放在末尾\n"
            "- 如果确保认出表情包的具体来源（作品名、角色名等），请补充说明\n"
            "- 无法确定出处则省略，只做客观描述\n"
            "- 总体 40 字以内（不计图中文字）\n"
            "- 无文字则省略文字部分\n\n"
            "JSON 字段说明：\n"
            "- keep：根据人设决定是否收藏\n"
            "- emotion_tags：必须从预设标签中选择（可多选，keep=false 时可为空）\n"
            "- 预设标签："
            + tag_list
            + "\n\n"
            "收藏标准：\n"
            "- 质量高且表达生动的表情包\n"
            "- 避免收藏低质、冒犯、违规或与人设不符的\n\n"
            "人设（来自主配置）：\n"
            + persona
        )

        from src.kernel.llm import Image, LLMContextManager, LLMPayload, ROLE, Text

        context_manager = LLMContextManager(max_payloads=2)
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

        keep = bool(obj.get("keep"))
        description = str(obj.get("description") or "").strip()
        tags = obj.get("emotion_tags")
        if not isinstance(tags, list):
            tags = []

        filtered_tags = [
            str(t).strip() for t in tags if isinstance(t, (str, int, float)) and str(t).strip() in EMOTION_TAG_PRESET
        ]

        if keep and (not description or not filtered_tags):
            keep = False

        if len(description) > 200:
            description = description[:197] + "..."

        return {
            "keep": keep,
            "description": description,
            "emotion_tags": filtered_tags,
        }

    async def ingest_once(self) -> None:
        """执行一次入库任务。

        流程：对齐 → 【优先手动目录 或 随机抽取】 → 去重检查 → 图片压缩 → VLM 决策+标注 → 复制 → embedding → 写入向量库。
        """
        if _INGEST_LOCK.locked():
            logger.debug("上一轮入库尚未结束，跳过本轮")
            return

        async with _INGEST_LOCK:
            await self._align_data_dir_with_db()

            max_memes = int(self._cfg().storage.max_memes)
            if max_memes > 0:
                data_dir = self._data_dir()
                try:
                    current_count = sum(
                        1
                        for p in data_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
                    )
                except FileNotFoundError:
                    current_count = 0

                if current_count >= max_memes:
                    logger.info(
                        f"表情包数量已达上限，跳过入库: {current_count}/{max_memes}"
                    )
                    return

            # 优先从手动目录获取表情包，否则才从随机缓存
            source = await self._pick_next_manual_meme_file()
            if source is None:
                if not self._cfg().ingest.sample_from_media_cache:
                    return
                source = self._pick_random_media_cache_file()
            
            if source is None:
                return

            try:
                payload = await asyncio.to_thread(source.read_bytes)
            except Exception as e:
                logger.warning(f"读取候选表情包失败: {source} - {e}")
                return

            meme_id = self._sha256_bytes(payload)
            if await self._already_ingested(meme_id):
                return

            # 压缩图片用于 VLM
            try:
                mime = self._guess_mime(source.suffix)
                vlm_bytes, vlm_mime, is_gif_collage = await asyncio.to_thread(self._compress_image_for_vlm, payload, mime)
            except Exception as e:
                logger.warning(f"压缩图片失败: {source} - {e}")
                return

            image_base64 = base64.b64encode(vlm_bytes).decode("utf-8")

            labeled = await self._vlm_decide_and_label(
                image_base64=image_base64, 
                mime=vlm_mime,
                is_gif_collage=is_gif_collage
            )
            if not labeled or not labeled.get("keep"):
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
                shutil.copy2(source, target_path)
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

    async def search_best(
        self,
        description_query: str,
        emotion_tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """按 tag 过滤后执行向量检索，返回温度采样后的候选。"""
        query = str(description_query or "").strip()
        if not query:
            raise ValueError("description_query 不能为空")

        tags: list[str] = []
        if emotion_tags:
            tags = [str(t).strip() for t in emotion_tags if str(t).strip() in EMOTION_TAG_PRESET]

        try:
            embedding_model_set = get_model_set_by_task("embedding")
        except Exception:
            return None

        try:
            emb_req = create_embedding_request(
                model_set=embedding_model_set,
                request_name="emoji_sender_search",
                inputs=[query],
            )
            emb_resp = await emb_req.send()
            query_embedding = emb_resp.embeddings[0]
        except Exception as e:
            logger.warning(f"生成查询 embedding 失败: {e}")
            return None

        vdb = self._vector_db()
        collection = self._collection_name()
        await vdb.get_or_create_collection(collection)

        where: dict[str, Any] | None = None
        if tags:
            where = {"tag": tags}

        top_n = int(self._cfg().vector.top_n)
        max_distance = float(self._cfg().vector.max_distance)

        results = await vdb.query(
            collection_name=collection,
            query_embeddings=[list(query_embedding)],
            n_results=top_n,
            where=where,
        )

        ids_list: list[list[str]] = list(results.get("ids") or [])
        distances_list: list[list[float]] = list(results.get("distances") or [])
        metadatas_list: list[list[dict[str, Any]]] = list(results.get("metadatas") or [])

        if not ids_list or not ids_list[0]:
            return None

        all_candidates: list[MemeCandidate] = []
        best_any: MemeCandidate | None = None
        candidates_under_threshold: list[MemeCandidate] = []
        for i, _ in enumerate(ids_list[0]):
            distance = float(distances_list[0][i]) if distances_list and distances_list[0] and i < len(distances_list[0]) else 999.0
            meta = metadatas_list[0][i] if metadatas_list and metadatas_list[0] and i < len(metadatas_list[0]) else {}
            cand = self._build_candidate(distance=distance, metadata=meta)
            if cand is None:
                continue

            all_candidates.append(cand)
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
        )
        if not result:
            return False, None, "没有找到满足条件的表情包"

        path = Path(str(result["path"]))
        if not path.exists():
            # 用户可能手动删了，下一次入库会对齐；这里直接失败
            return False, result, "表情包文件已被删除"

        try:
            payload = path.read_bytes()
        except Exception as e:
            logger.warning(f"读取表情包失败: {path} - {e}")
            return False, result, "读取表情包文件失败"

        image_base64 = base64.b64encode(payload).decode("utf-8")
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
