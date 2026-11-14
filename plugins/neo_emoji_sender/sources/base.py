"""来源基类。

来源只负责“发现候选”和“读取候选”，入库编排、去重、VLM 决策与
注册统一由基类完成。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, cast

from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.app.plugin_system.api.service_api import get_service
from src.kernel.logger import get_logger

from ..manager.image_utils import (
    JsonObject,
    base64_encode,
    build_persona_prompt,
    compress_image_for_vlm,
    extract_json_object,
)
from ..manager.service import EMOTION_TAG_PRESET, EmojiService
from ..manager.store import sha256_bytes
from ..manager.types import EmojiCandidate, EmojiRecord

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin, SectionBase

    from ..config import NeoEmojiSenderConfig
    from ..manager.runtime import EmojiRuntime
    from ..plugin import NeoEmojiSenderPlugin

logger = get_logger("neo_emoji_sender.source")


class EmojiSource(ABC):
    """表情包来源基类。"""

    source_kind: str = ""
    vlm_enabled: bool = True

    def __init__(self, plugin: BasePlugin) -> None:
        self.plugin = plugin
        self._ignored_refs: set[str] = set()
        self.source_name = self.source_kind

    @property
    def runtime(self) -> EmojiRuntime:
        """获取插件运行时。

        来源不持有运行时引用，统一从插件实例取，避免插件重载后拿到旧对象。

        Returns:
            当前插件运行时。

        Raises:
            RuntimeError: 运行时尚未初始化时抛出。
        """
        plugin = cast("NeoEmojiSenderPlugin", self.plugin)
        if plugin.runtime is None:
            raise RuntimeError("neo_emoji_sender runtime 未初始化")
        return plugin.runtime

    @property
    def config(self) -> NeoEmojiSenderConfig:
        """获取插件配置。

        Returns:
            强类型化的插件配置对象。
        """
        return self.runtime.config

    @property
    def interval_seconds(self) -> int:
        """获取扫描间隔。

        Returns:
            扫描间隔秒数，最小 5 秒以免过于频繁地触发扫描。
        """
        return max(5, int(self._source_config.interval_seconds))

    @property
    def batch_size(self) -> int:
        """获取每轮最多入库数量。

        Returns:
            每轮入库上限，最小 1。
        """
        return max(1, int(self._source_config.batch_size))

    @property
    def _source_config(self) -> SectionBase:
        """返回本来源对应的配置节，由子类实现。

        Returns:
            该来源的配置节。

        Raises:
            NotImplementedError: 子类未实现时抛出。
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 调度
    # ------------------------------------------------------------------

    async def start(self) -> list[str]:
        """注册一次性与周期任务。

        Returns:
            创建的 schedule ID 列表。
        """
        from src.kernel.scheduler import TriggerType, get_unified_scheduler

        scheduler = get_unified_scheduler()
        ids = [
            await scheduler.create_schedule(
                callback=self.ingest_once,
                trigger_type=TriggerType.TIME,
                trigger_config={"delay_seconds": 0},
                is_recurring=False,
                task_name=f"neo_emoji_sender_{self.source_kind}_once",
                force_overwrite=True,
            ),
            await scheduler.create_schedule(
                callback=self.ingest_once,
                trigger_type=TriggerType.TIME,
                trigger_config={"interval_seconds": self.interval_seconds},
                is_recurring=True,
                task_name=f"neo_emoji_sender_{self.source_kind}_recurring",
                force_overwrite=True,
            ),
        ]
        return ids

    # ------------------------------------------------------------------
    # 模板方法
    # ------------------------------------------------------------------

    async def ingest_once(self) -> None:
        """执行一轮入库。"""
        try:
            await self._ingest_once()
        except Exception as e:
            logger.warning(f"来源 {self.source_kind} 入库异常: {e}")

    async def _ingest_once(self) -> None:
        """内部入库流程。"""
        if not self.vlm_enabled:
            logger.debug(f"来源 {self.source_kind} 已关闭 VLM，跳过")
            return

        runtime = self.runtime
        max_memes = int(self.config.storage.max_memes)
        if max_memes > 0 and await runtime.repo.count() >= max_memes:
            logger.info(f"表情包数量已达上限 {max_memes}，跳过入库")
            return

        # 索取的候选数大于实际入库数：候选中会有重复、已入库或 VLM 拒收的，
        # 多取一些才能保证一轮下来尽量凑满 batch_size
        scan_limit = max(self.batch_size, int(self.config.sources.scan_batch_size))
        candidates = await self.collect_candidates(limit=scan_limit)
        handled = 0
        for candidate in candidates:
            if handled >= self.batch_size:
                break
            if await self._already_known(candidate):
                continue
            record = await self._ingest_candidate(candidate)
            if record is None:
                continue
            handled += 1
            try:
                await self.after_register(candidate, record)
            except Exception as e:
                # 后处理（如移动源文件）失败不应让已注册的记录作废，
                # 只记警告，下一轮扫描会按哈希认出它已存在而跳过
                logger.warning(f"来源 {self.source_kind} 后处理失败: {e}")

    async def _already_known(self, candidate: EmojiCandidate) -> bool:
        """判断候选是否已注册或已抑制。

        Args:
            candidate: 候选。

        Returns:
            已处理时返回 True。
        """
        runtime = self.runtime
        if candidate.source_ref in self._ignored_refs:
            return True
        if candidate.known_hash:
            if await runtime.repo.exists(candidate.known_hash):
                return True
            if await runtime.repo.is_suppressed(candidate.known_hash):
                self._ignored_refs.add(candidate.source_ref)
                return True
        if await runtime.repo.has_source_ref(self.source_kind, candidate.source_ref):
            return True
        return False

    async def _ingest_candidate(self, candidate: EmojiCandidate) -> EmojiRecord | None:
        """读取、标注并注册单个候选。

        Args:
            candidate: 候选。

        Returns:
            注册成功的记录；跳过时返回 None。
        """
        data = await self.load(candidate)
        if not data:
            return None
        # 以内容哈希而非来源标识判重：同一张图可能来自不同路径 / 不同缓存记录
        content_hash = sha256_bytes(data)

        runtime = self.runtime
        if await runtime.repo.exists(content_hash):
            return None
        if await runtime.repo.is_suppressed(content_hash):
            self._ignored_refs.add(candidate.source_ref)
            return None

        # 压缩是纯 CPU 操作，放线程池避免阻塞事件循环（大图或长 GIF 尤其明显）
        try:
            vlm_bytes, vlm_mime, is_collage = await asyncio.to_thread(
                compress_image_for_vlm, data, candidate.mime
            )
        except Exception as e:
            logger.warning(f"压缩候选失败 {candidate.source_ref}: {e}")
            return None

        labeled = await self._vlm_decide_and_label(
            image_base64=base64_encode(vlm_bytes),
            mime=vlm_mime,
            is_gif_collage=is_collage,
            hint=candidate.hint,
        )
        if labeled is None:
            return None
        # VLM 判为不收的表情包写入抑制表：否则每轮扫描都会把同一张图
        # 重新送去标注，白白消耗 VLM 调用
        if labeled.get("keep") is False:
            await runtime.repo.add_suppression(
                content_hash,
                reason="vlm_rejected",
                source_kind=self.source_kind,
                source_ref=candidate.source_ref,
            )
            self._ignored_refs.add(candidate.source_ref)
            logger.info(f"VLM 拒收表情包: {candidate.source_ref}")
            return None

        description = str(labeled.get("description") or "").strip()
        tags = [str(tag).strip() for tag in (labeled.get("emotion_tags") or [])]
        tags = [tag for tag in tags if tag in EMOTION_TAG_PRESET]
        if not description or not tags:
            return None

        service = get_service("neo_emoji_sender:service:emoji")
        if service is None:
            logger.warning("neo_emoji_sender service 未加载")
            return None
        record = await cast(EmojiService, service).register(
            data=data,
            mime=candidate.mime,
            description=description,
            tags=tags,
            source_kind=self.source_kind,
            source_ref=candidate.source_ref,
        )
        if record is None:
            self._ignored_refs.add(candidate.source_ref)
        return record

    async def _vlm_decide_and_label(
        self,
        *,
        image_base64: str,
        mime: str,
        is_gif_collage: bool = False,
        hint: str = "",
    ) -> JsonObject | None:
        """调用 VLM 做收藏决策与标注。

        Args:
            image_base64: 图片 base64。
            mime: 图片 MIME。
            is_gif_collage: 是否为 GIF 拼图。
            hint: 来源附带的描述提示。

        Returns:
            ``{keep, description, emotion_tags}``；失败或标注不完整时返回 None。
        """
        try:
            model_set = get_model_set_by_task("vlm")
        except Exception:
            logger.debug("未配置 VLM 任务模型，跳过入库")
            return None

        persona = build_persona_prompt()
        tag_list = "、".join(EMOTION_TAG_PRESET)
        gif_hint = (
            "注意：这是一个 GIF 动图表情包的关键帧截图（网格排列），请综合所有帧的内容进行描述。\n"
            if is_gif_collage
            else ""
        )
        hint_text = f"来源描述提示：{hint}\n" if hint else ""
        prompt = (
            "你将看到一张表情包图片。你的任务：根据人设，决定你是否愿意把它收藏起来以后自己使用。\n"
            + gif_hint
            + hint_text
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

        context_manager = LLMContextManager()
        request = create_llm_request(
            model_set=model_set,
            request_name="neo_emoji_sender_label",
            context_manager=context_manager,
        )
        request.add_payload(
            LLMPayload(ROLE.USER, [Text(prompt), Image(f"data:{mime};base64,{image_base64}")])
        )
        try:
            response = await request.send(stream=False)
            await response
        except Exception as e:
            logger.warning(f"VLM 标注失败: {e}")
            return None

        obj = extract_json_object((response.message or "").strip())
        if obj is None:
            logger.warning("VLM 输出无法解析为 JSON，跳过")
            return None

        keep = obj.get("keep")
        if not isinstance(keep, bool):
            logger.warning("VLM 收藏决策必须为布尔值，跳过")
            return None
        description = str(obj.get("description") or "").strip()
        raw_tags = obj.get("emotion_tags")
        tags = raw_tags if isinstance(raw_tags, list) else []
        filtered_tags = [
            str(tag).strip()
            for tag in tags
            if isinstance(tag, (str, int, float)) and str(tag).strip() in EMOTION_TAG_PRESET
        ]
        if keep and (not description or not filtered_tags):
            logger.warning("VLM 收藏标注缺少有效描述或情感标签，跳过")
            return None
        if len(description) > 200:
            description = description[:197] + "..."
        return JsonObject(keep=keep, description=description, emotion_tags=filtered_tags)

    # ------------------------------------------------------------------
    # 子类实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def collect_candidates(self, *, limit: int) -> list[EmojiCandidate]:
        """返回至多 limit 个候选。

        实现必须推进内部游标，不能每次都返回同一批头部候选。

        Args:
            limit: 候选数量上限。

        Returns:
            候选列表。
        """

    @abstractmethod
    async def load(self, candidate: EmojiCandidate) -> bytes:
        """读取候选文件内容。

        Args:
            candidate: 候选。

        Returns:
            文件字节。
        """

    async def after_register(self, candidate: EmojiCandidate, record: EmojiRecord) -> None:
        """注册成功后的可选后处理。

        Args:
            candidate: 候选。
            record: 已注册记录。
        """
        return None


__all__ = ["EmojiSource"]
