"""neo_emoji_sender 服务实现。

EmojiService 是无状态门面，所有共享状态位于插件级 :class:`EmojiRuntime`。
"""

from __future__ import annotations

import math
import random
import time
from pathlib import Path
from typing import TypedDict

from src.app.plugin_system.api.send_api import send_emoji
from src.app.plugin_system.base import BaseService
from src.kernel.logger import get_logger

from .events import EmojiPublisher, SearchResultItem
from .store import guess_mime, normalize_suffix, sha256_bytes
from .types import DeleteResult, EmojiHit, EmojiRecord
from ..config import NeoEmojiSenderConfig

logger = get_logger("neo_emoji_sender.service")

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

_FULL_ID_LENGTH = 64
_HEX_CHARS = frozenset("0123456789abcdef")


class BestEmojiResult(TypedDict):
    """``search_best`` / ``send_by_id`` 返回的最佳表情包详情。"""

    emoji_id: str
    meme_id: str
    tag: str
    path: str
    description: str
    distance: float
    fallback_used: bool


class EmojiCandidateItem(TypedDict):
    """``search_candidates`` 返回的单个候选。"""

    emoji_id: str
    meme_id: str
    short_id: str
    tag: str
    description: str
    distance: float


class CandidatePage(TypedDict):
    """``search_candidates`` 返回的分页结果。"""

    candidates: list[EmojiCandidateItem]
    page: int
    total: int


def is_full_emoji_id(value: str) -> bool:
    """判断是否为完整表情包 ID。

    Args:
        value: 待判断的字符串。

    Returns:
        是 64 位小写十六进制时返回 True。
    """
    text = str(value or "").strip().lower()
    return len(text) == _FULL_ID_LENGTH and all(char in _HEX_CHARS for char in text)


def compute_revision(description: str, tags: list[str]) -> str:
    """计算内容指纹。

    Args:
        description: 表情包描述。
        tags: 情感标签列表。

    Returns:
        sha256 十六进制字符串。
    """
    payload = description + "\x00" + "\n".join(sorted(set(tags)))
    return sha256_bytes(payload.encode("utf-8"))


class EmojiService(BaseService):
    """表情包注册、检索、更新与删除服务。"""

    name = "emoji"
    description = "表情包注册、检索、更新与删除服务"

    @property
    def runtime(self):
        """获取插件级运行时。

        Raises:
            RuntimeError: 插件运行时尚未初始化（未启用或加载失败）。
        """
        runtime = getattr(self.plugin, "runtime", None)
        if runtime is None:
            raise RuntimeError("neo_emoji_sender 运行时未初始化，请确认插件已启用并加载成功")
        return runtime

    def _cfg(self) -> NeoEmojiSenderConfig:
        """获取强类型化的插件配置。

        Returns:
            插件配置对象。

        Raises:
            RuntimeError: 配置未加载或类型不符时抛出。
        """
        cfg = self.plugin.config
        if not isinstance(cfg, NeoEmojiSenderConfig):
            raise RuntimeError("neo_emoji_sender plugin config 未正确加载")
        return cfg

    @staticmethod
    def normalize_tags(tags: list[str] | None) -> list[str]:
        """过滤并去重情感标签。

        Args:
            tags: 原始标签列表。

        Returns:
            合法且去重后的标签列表。
        """
        if not tags:
            return []
        result: list[str] = []
        for tag in tags:
            value = str(tag).strip()
            if value in EMOTION_TAG_PRESET and value not in result:
                result.append(value)
        return result

    async def register(
        self,
        *,
        data: bytes,
        mime: str,
        description: str,
        tags: list[str],
        source_kind: str,
        source_ref: str = "",
        force: bool = False,
    ) -> EmojiRecord | None:
        """注册一张表情包。

        文件先落盘、DB 后提交；事件失败不回滚 DB，由对账补齐索引。

        Args:
            data: 文件字节内容。
            mime: MIME 类型。
            description: 表情包描述。
            tags: 情感标签列表。
            source_kind: 来源类型。
            source_ref: 来源稳定标识。
            force: 是否绕过抑制表。

        Returns:
            注册后的记录；被抑制或参数无效时返回 None。
        """
        clean_desc = str(description or "").strip()
        clean_tags = self.normalize_tags(tags)
        if not data or not clean_desc or not clean_tags:
            return None

        runtime = self.runtime
        emoji_id = sha256_bytes(data)
        suffix = normalize_suffix(Path(source_ref).suffix or mime.rsplit("/", 1)[-1])
        now = time.time()
        revision = compute_revision(clean_desc, clean_tags)

        async with runtime.mutation_lock:
            if not force and await runtime.repo.is_suppressed(emoji_id):
                logger.debug(f"表情包已被抑制，跳过注册: {emoji_id[:8]}...")
                return None
            existing = await runtime.repo.get(emoji_id)
            if existing is not None:
                return existing

            # 登记待写路径：对账器会跳过 pending 中的文件，避免把正在写入的
            # 文件当成"无 DB 记录的孤儿文件"删掉
            rel_path = runtime.store.rel_path_for(emoji_id, suffix)
            runtime.pending_paths.add(rel_path)
            try:
                emoji_id, rel_path = await runtime.store.save(data, suffix)
                runtime.pending_paths.discard(rel_path)
                record = EmojiRecord(
                    emoji_id=emoji_id,
                    rel_path=rel_path,
                    mime=mime or guess_mime(suffix),
                    size_bytes=len(data),
                    description=clean_desc,
                    revision=revision,
                    tags=tuple(clean_tags),
                    source_kind=source_kind,
                    source_ref=source_ref,
                    created_at=now,
                    updated_at=now,
                )
                inserted = await runtime.repo.insert(record)
                if not inserted:
                    return await runtime.repo.get(emoji_id)
            except Exception:
                # DB 写入失败时回滚已落盘的文件，避免留下永远对不上账的孤儿
                runtime.pending_paths.discard(runtime.store.rel_path_for(emoji_id, suffix))
                try:
                    await runtime.store.delete(runtime.store.rel_path_for(emoji_id, suffix))
                except Exception:
                    pass
                raise

        # 默认: event_handlers/vectorizer.py::_handle_upsert
        await self._publish_registered(record, is_update=False)
        logger.info(f"注册表情包: {record.emoji_id[:8]}... tags={list(record.tags)}")
        return record

    async def get(self, emoji_id: str) -> EmojiRecord | None:
        """按完整 ID 获取记录。

        Args:
            emoji_id: 表情包完整 ID。

        Returns:
            记录或 None。
        """
        return await self.runtime.repo.get(str(emoji_id or "").strip().lower())

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        tag: str | None = None,
        source_kind: str | None = None,
    ) -> tuple[list[EmojiRecord], int]:
        """分页列出记录。

        Args:
            page: 页码，从 1 开始。
            page_size: 每页数量。
            tag: 可选标签过滤。
            source_kind: 可选来源过滤。

        Returns:
            (记录列表, 总数)。
        """
        return await self.runtime.repo.list_records(
            page=page,
            page_size=page_size,
            tag=tag,
            source_kind=source_kind,
        )

    async def update(
        self,
        emoji_id: str,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> EmojiRecord | None:
        """更新描述或标签。

        revision 不变时直接返回，不触发 embedding。

        Args:
            emoji_id: 表情包完整 ID。
            description: 新描述；None 表示不变。
            tags: 新标签；None 表示不变。

        Returns:
            更新后的记录；不存在时返回 None。
        """
        normalized = str(emoji_id or "").strip().lower()
        async with self.runtime.mutation_lock:
            record = await self.runtime.repo.get(normalized)
            if record is None:
                return None

            new_desc = record.description if description is None else str(description).strip()
            new_tags = list(record.tags) if tags is None else self.normalize_tags(tags)
            if not new_desc or not new_tags:
                return record

            revision = compute_revision(new_desc, new_tags)
            # revision 是 (描述, 标签集合) 的内容指纹；没变就没有重建向量的必要，
            # 直接返回也避免对账器把这批向量判成"过期"
            if revision == record.revision:
                return record

            now = time.time()
            ok = await self.runtime.repo.update(
                normalized,
                description=new_desc,
                tags=new_tags,
                revision=revision,
                updated_at=now,
            )
            if not ok:
                return None
            updated = await self.runtime.repo.get(normalized)
            if updated is None:
                return None

        # 默认: event_handlers/vectorizer.py::_handle_upsert
        await self._publish_registered(updated, is_update=True)
        return updated

    async def delete(self, emoji_id: str, *, reason: str = "user_deleted") -> DeleteResult:
        """删除表情包，并写入抑制记录。

        DB 删除成功即视为领域删除成功；向量与文件清理失败由对账兜底。

        Args:
            emoji_id: 表情包完整 ID。
            reason: 抑制原因。

        Returns:
            删除结果。
        """
        normalized = str(emoji_id or "").strip().lower()
        runtime = self.runtime
        if not is_full_emoji_id(normalized):
            return DeleteResult(normalized, False, False, False, "emoji_id 必须是完整 ID")

        async with runtime.mutation_lock:
            record = await runtime.repo.get(normalized)
            rel_paths = [record.rel_path] if record else await runtime.store.find_by_hash(normalized)
            # 先写抑制再删记录：来源扫描以内容哈希判重，若顺序反过来，下一轮
            # 扫描会立刻把刚删掉的表情包重新注册进来
            await runtime.repo.add_suppression(
                normalized,
                reason=reason,
                source_kind=record.source_kind if record else "",
                source_ref=record.source_ref if record else "",
            )
            db_deleted = await runtime.repo.delete(normalized)

        vector_deleted = False
        try:
            # 默认: event_handlers/vectorizer.py::_handle_delete
            vector_deleted, error = await EmojiPublisher.deleted(emoji_id=normalized)
            if error:
                logger.warning(f"向量清理未完成: {normalized[:8]}... {error}")
        except Exception as e:
            logger.warning(f"发布删除事件失败: {e}")

        file_deleted = True
        for rel_path in rel_paths:
            try:
                await runtime.store.delete(rel_path)
            except Exception as e:
                logger.warning(f"删除表情包文件失败 {rel_path}: {e}")
                file_deleted = False

        if not db_deleted and not rel_paths:
            return DeleteResult(normalized, False, file_deleted, vector_deleted, "未找到表情包")
        return DeleteResult(normalized, db_deleted, file_deleted, vector_deleted, "删除成功")

    async def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        limit: int = 8,
        exclude_ids: list[str] | None = None,
    ) -> list[EmojiHit]:
        """通过事件链执行向量检索并回表。

        严格事件链：不直接访问向量库，也不做数据库回退。

        Args:
            query: 查询文本。
            tags: 可选标签过滤。
            limit: 结果数量上限。
            exclude_ids: 需要排除的表情包 ID。

        Returns:
            检索命中列表；事件失败时为空列表。
        """
        text = str(query or "").strip()
        if not text:
            return []
        clean_tags = self.normalize_tags(tags)
        exclude = [str(item) for item in (exclude_ids or []) if item]
        try:
            # 默认: event_handlers/vectorizer.py::_handle_search
            results: list[SearchResultItem] = await EmojiPublisher.search(
                query=text,
                tags=clean_tags,
                limit=max(1, int(limit)),
                exclude_ids=exclude,
            )
        except Exception as e:
            logger.warning(f"发布检索事件失败: {e}")
            return []

        # 向量库只存 emoji_id / tag / 距离，路径与描述始终回 SQLite 与文件系统取，
        # 因此这里必须逐条回表：DB 记录已被删、或文件已丢失的命中项直接跳过
        hits: list[EmojiHit] = []
        for item in results:
            emoji_id = str(item.get("emoji_id") or "").strip()
            if not emoji_id or emoji_id in exclude:
                continue
            record = await self.runtime.repo.get(emoji_id)
            if record is None:
                continue
            if not await self.runtime.store.exists(record.rel_path):
                continue
            raw_distance = item.get("distance")
            distance = float(raw_distance) if isinstance(raw_distance, (int, float)) else 999.0
            hits.append(
                EmojiHit(
                    record=record,
                    tag=str(item.get("tag") or (record.tags[0] if record.tags else "")),
                    distance=distance,
                )
            )
        return hits

    async def search_best(
        self,
        description_query: str,
        emotion_tags: list[str] | None = None,
        stream_id: str = "",
    ) -> BestEmojiResult | None:
        """检索并返回温度采样后的最佳表情包。

        Args:
            description_query: 目标描述。
            emotion_tags: 情感标签过滤。
            stream_id: 聊天流 ID，用于历史去重。

        Returns:
            最佳候选详情；无满足项时返回 None。
        """
        query = str(description_query or "").strip()
        if not query:
            raise ValueError("description_query 不能为空")

        cfg = self._cfg()
        top_n = int(cfg.vector.top_n)
        max_distance = float(cfg.vector.max_distance)
        tags = self.normalize_tags(emotion_tags)
        exclude = sorted(self.runtime.recently_used(stream_id)) if cfg.dedup.enabled and stream_id else []

        hits = await self.search(query, tags=tags, limit=top_n, exclude_ids=exclude)
        # 历史去重把候选全过滤光时回退到全量：宁可重复发一张，也好过无表情可发
        if not hits and exclude:
            hits = await self.search(query, tags=tags, limit=top_n)
        if not hits:
            return None

        under_threshold = [hit for hit in hits if hit.distance <= max_distance]
        fallback_used = False
        if under_threshold:
            best = self._select_candidate(under_threshold)
        elif tags:
            # 指定了情感标签却全部超出距离阈值时放宽阈值：标签是硬性条件，
            # 距离只是相关性，用户明确要"生气的表情"就不该因为不够像而返回空
            best = self._select_candidate(hits)
            fallback_used = True
        else:
            return None
        if best is None:
            return None

        return BestEmojiResult(
            emoji_id=best.record.emoji_id,
            meme_id=best.record.emoji_id,
            tag=best.tag,
            path=best.record.rel_path,
            description=best.record.description,
            distance=best.distance,
            fallback_used=fallback_used,
        )

    async def search_candidates(
        self,
        description_query: str,
        emotion_tags: list[str] | None = None,
        page: int = 1,
        stream_id: str = "",
    ) -> CandidatePage | None:
        """检索并返回去重、历史过滤、分页后的候选列表。

        Args:
            description_query: 目标描述。
            emotion_tags: 情感标签过滤。
            page: 页码，从 1 开始。
            stream_id: 聊天流 ID，用于历史去重。

        Returns:
            {candidates, page, total}；无候选时返回 None。
        """
        cfg = self._cfg()
        page_size = max(1, int(cfg.picker.page_size))
        page = max(1, int(page))
        tags = self.normalize_tags(emotion_tags)
        fetch_n = min(200, max(page * page_size * 3, page_size * 3))

        exclude: list[str] = []
        if cfg.dedup.enabled and stream_id:
            exclude = sorted(self.runtime.recently_used(stream_id))

        hits = await self.search(description_query, tags=tags, limit=fetch_n, exclude_ids=exclude)
        if not hits and exclude:
            hits = await self.search(description_query, tags=tags, limit=fetch_n)
        if not hits:
            return None

        best_by_id: dict[str, EmojiHit] = {}
        for hit in hits:
            existing = best_by_id.get(hit.record.emoji_id)
            if existing is None or hit.distance < existing.distance:
                best_by_id[hit.record.emoji_id] = hit
        ordered = sorted(best_by_id.values(), key=lambda item: item.distance)

        total = len(ordered)
        start = (page - 1) * page_size
        page_items = ordered[start : start + page_size]
        if not page_items:
            return None

        return CandidatePage(
            candidates=[
                EmojiCandidateItem(
                    emoji_id=hit.record.emoji_id,
                    meme_id=hit.record.emoji_id,
                    short_id=hit.record.emoji_id[:12],
                    tag=hit.tag,
                    description=hit.record.description,
                    distance=hit.distance,
                )
                for hit in page_items
            ],
            page=page,
            total=total,
        )

    async def send_by_id(
        self,
        *,
        short_id: str,
        stream_id: str,
        platform: str | None,
    ) -> tuple[bool, BestEmojiResult | None, str]:
        """按完整 ID 或前缀发送指定表情包。

        Args:
            short_id: 完整 ID 或前缀。
            stream_id: 聊天流 ID。
            platform: 平台标识。

        Returns:
            (是否成功, 详情, 原因)。
        """
        prefix = str(short_id or "").strip().lower()
        if not prefix:
            return False, None, "meme_id 不能为空"

        record = await self.runtime.repo.find_by_short_id(prefix)
        if record is None:
            return False, None, f"未找到 id={prefix} 对应的表情包，请重新检索候选"

        try:
            payload = await self.runtime.store.read(record.rel_path)
        except Exception as e:
            logger.warning(f"读取表情包失败 {record.rel_path}: {e}")
            return False, None, "读取表情包文件失败"

        image_base64 = await self._encode(payload)
        processed = (
            f"[表情包:{record.tags[0]}:{record.description}]"
            if record.tags and record.description
            else f"[表情包:{record.tags[0] if record.tags else ''}]"
        )
        ok = await send_emoji(
            emoji_data=image_base64,
            stream_id=stream_id,
            platform=platform,
            processed_plain_text=processed,
        )
        result = BestEmojiResult(
            emoji_id=record.emoji_id,
            meme_id=record.emoji_id,
            tag=record.tags[0] if record.tags else "",
            path=record.rel_path,
            description=record.description,
            # 按 ID 指定发送，未经过向量检索，距离无实际含义。
            distance=0.0,
            fallback_used=False,
        )
        if ok:
            self.runtime.record_usage(stream_id, record.emoji_id)
            return True, result, "发送成功"
        return False, result, "发送失败"

    async def send_best_detailed(
        self,
        *,
        stream_id: str,
        platform: str | None,
        description_query: str,
        emotion_tags: list[str] | None = None,
    ) -> tuple[bool, BestEmojiResult | None, str]:
        """检索并发送最佳表情包。

        Args:
            stream_id: 聊天流 ID。
            platform: 平台标识。
            description_query: 目标描述。
            emotion_tags: 情感标签过滤。

        Returns:
            (是否成功, 详情, 原因)。
        """
        result = await self.search_best(
            description_query=description_query,
            emotion_tags=emotion_tags,
            stream_id=stream_id,
        )
        if not result:
            return False, None, "没有找到满足条件的表情包"

        record = await self.runtime.repo.get(str(result["emoji_id"]))
        if record is None:
            return False, result, "表情包记录已不存在"
        try:
            payload = await self.runtime.store.read(record.rel_path)
        except Exception as e:
            logger.warning(f"读取表情包失败 {record.rel_path}: {e}")
            return False, result, "表情包文件已被删除"

        image_base64 = await self._encode(payload)
        desc = str(result.get("description") or "").strip()
        tag = str(result.get("tag") or "").strip()
        processed = f"[表情包:{tag}:{desc}]" if desc else f"[表情包:{tag}]"
        ok = await send_emoji(
            emoji_data=image_base64,
            stream_id=stream_id,
            platform=platform,
            processed_plain_text=processed,
        )
        if ok:
            self.runtime.record_usage(stream_id, record.emoji_id)
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
        """检索并发送最佳表情包，返回是否成功。

        Args:
            stream_id: 聊天流 ID。
            platform: 平台标识。
            description_query: 目标描述。
            emotion_tags: 情感标签过滤。

        Returns:
            是否发送成功。
        """
        ok, _, _ = await self.send_best_detailed(
            stream_id=stream_id,
            platform=platform,
            description_query=description_query,
            emotion_tags=emotion_tags,
        )
        return ok

    async def _publish_registered(self, record: EmojiRecord, is_update: bool) -> None:
        """发布注册 / 更新事件。

        Args:
            record: 已写入的记录。
            is_update: True 发布更新事件，False 发布注册事件。
        """
        publish = EmojiPublisher.updated if is_update else EmojiPublisher.registered
        try:
            ok, error = await publish(
                emoji_id=record.emoji_id,
                description=record.description,
                tags=list(record.tags),
                revision=record.revision,
                source_kind=record.source_kind,
                updated_at=record.updated_at,
            )
            if not ok:
                logger.warning(
                    f"向量索引未完成，等待对账: {record.emoji_id[:8]}... {error}"
                )
        except Exception as e:
            logger.warning(f"发布向量事件失败 {record.emoji_id[:8]}...: {e}")

    def _select_candidate(self, candidates: list[EmojiHit]) -> EmojiHit | None:
        """按距离与温度采样选择候选。

        Args:
            candidates: 候选列表。

        Returns:
            选中的候选。
        """
        if not candidates:
            return None
        ordered = sorted(candidates, key=lambda item: item.distance)
        temperature = max(0.0, float(self._cfg().vector.temperature))
        # temperature <= 0 退化为确定性选取最相似项，便于调试与复现
        if temperature <= 0.0 or len(ordered) == 1:
            return ordered[0]
        # 以最近项为基准做相对化：距离差除以温度后取指数，越近权重越大。
        # 减 base 是为了数值稳定，避免大距离时 exp 下溢成全 0
        base = ordered[0].distance
        weights = [math.exp(-max(0.0, item.distance - base) / temperature) for item in ordered]
        if not any(weight > 0.0 for weight in weights):
            return ordered[0]
        return random.choices(ordered, weights=weights, k=1)[0]

    @staticmethod
    async def _encode(payload: bytes) -> str:
        """把文件字节编码为 base64。

        Args:
            payload: 文件内容。

        Returns:
            base64 字符串。
        """
        import asyncio

        from src.core.utils.base64_helper import base64_encode_bytes

        # 编码大图时是纯 CPU 操作，放进线程池避免阻塞事件循环
        return await asyncio.to_thread(base64_encode_bytes, payload)


__all__ = ["EmojiService", "EMOTION_TAG_PRESET", "compute_revision", "is_full_emoji_id"]
