"""Shameimaru Memory JSON 持久化存储层。

维护三个本地 JSON 数据库：
- summaries.json：摘要层，按群聊分组（group -> GroupSummary）。
- news.json：新闻层，全局新闻条目列表。
- personas.json：人物层，person_id -> 人物背景信息文本。

知识层使用 booku_memory_store 作为知识存储数据库，不在此处持久化。

并发安全：
- 插件内所有读写都应通过插件级共享 store 单例（:func:`shared_store`），
  "读内存 + 改内存 + 写盘" 全部在同一个 ``asyncio.Lock`` 内完成，
  避免多个周期任务（摘要/新闻/Dreaming）重叠运行时互相覆盖文件。
- 提供文件变化监视（:meth:`ShameimaruMemoryStore.start_watcher`）：
  外部修改本地 JSON 文件时自动把内存缓存同步为文件内容，
  使内存缓存始终与磁盘保持一致，无需版本号冲突检测。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.app.plugin_system.api.log_api import get_logger

from .models import GroupSummary, NewsEntry, SummaryEntry

logger = get_logger("shameimaru_memory.store")

if TYPE_CHECKING:
    from .config import ShameimaruMemoryConfig

_STORE_VERSION = 1

_SUMMARIES_DEFAULT = {"version": _STORE_VERSION, "groups": {}}
_NEWS_DEFAULT = {"version": _STORE_VERSION, "entries": []}
_PERSONAS_DEFAULT = {"version": _STORE_VERSION, "persons": {}}

_WATCH_INTERVAL_SECONDS = 2.0


def shared_store(
    plugin: Any,
    config_factory: Callable[[], "ShameimaruMemoryConfig"],
) -> "ShameimaruMemoryStore":
    """获取插件级共享 store 单例（首次访问时惰性创建并挂载到插件实例）。

    Args:
        plugin: 插件实例。
        config_factory: 用于构造配置的回调，仅在首次创建时调用。

    Returns:
        ShameimaruMemoryStore: 共享存储实例。
    """
    store = plugin.__dict__.get("_memory_store")
    if store is None:
        store = ShameimaruMemoryStore(config_factory())
        plugin._memory_store = store
    return store


def _atomic_write_text(path: Path, text: str) -> None:
    """以原子方式写文件：先写临时文件再替换。"""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class ShameimaruMemoryStore:
    """摘要 / 新闻 / 人物信息三个 JSON 数据库的统一读写入口。

    实例默认是惰性加载的内存缓存；外部（或其它实例）修改了本地文件后，
    可通过 :meth:`start_watcher` 启动的监视任务自动把内存同步为文件内容。
    """

    def __init__(
        self,
        config: "ShameimaruMemoryConfig",
        *,
        watch_interval: float = _WATCH_INTERVAL_SECONDS,
    ) -> None:
        """初始化存储层。

        Args:
            config: 插件配置实例。
            watch_interval: 文件变化监视的轮询间隔（秒），便于测试注入短间隔。
        """
        self._config = config
        data_dir = Path(config.storage.data_dir)
        self.summaries_path = data_dir / "summaries.json"
        self.news_path = data_dir / "news.json"
        self.personas_path = data_dir / "personas.json"

        self._lock = asyncio.Lock()
        self._summaries: dict[str, Any] = {}
        self._news: dict[str, Any] = {}
        self._personas: dict[str, Any] = {}
        self._loaded = False
        self._file_mtimes: dict[Path, float] = {}
        self._watcher_task: asyncio.Task | None = None
        self._watch_interval = watch_interval

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        """惰性加载全部 JSON 数据库（线程安全入口）。"""
        async with self._lock:
            self._ensure_loaded_locked()

    def _ensure_loaded_locked(self) -> None:
        """锁内加载全部 JSON 数据库（调用方必须持有 ``self._lock``）。"""
        if self._loaded:
            return
        self._summaries = self._load_json(self.summaries_path, _SUMMARIES_DEFAULT)
        self._news = self._load_json(self.news_path, _NEWS_DEFAULT)
        self._personas = self._load_json(self.personas_path, _PERSONAS_DEFAULT)
        self._record_mtime(self.summaries_path)
        self._record_mtime(self.news_path)
        self._record_mtime(self.personas_path)
        self._loaded = True

    @staticmethod
    def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        """从文件加载 JSON，文件不存在或损坏时返回默认结构的副本。"""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return copy.deepcopy(default)
        except OSError as exc:
            logger.warning(f"读取记忆文件失败: {path} ({exc})")
            return copy.deepcopy(default)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f"记忆文件 JSON 解析失败，使用空数据: {path} ({exc})")
            return copy.deepcopy(default)
        if not isinstance(payload, dict):
            return copy.deepcopy(default)
        return payload

    def _record_mtime(self, path: Path) -> None:
        """记录文件当前 mtime，供文件变化监视比对。"""
        try:
            self._file_mtimes[path] = path.stat().st_mtime
        except OSError:
            self._file_mtimes.pop(path, None)

    def _write_summaries(self) -> None:
        """将摘要数据库写入磁盘（调用方必须持有 ``self._lock``）。"""
        _atomic_write_text(
            self.summaries_path,
            json.dumps(self._summaries, ensure_ascii=False, indent=2),
        )
        self._record_mtime(self.summaries_path)

    def _write_news(self) -> None:
        """将新闻数据库写入磁盘（调用方必须持有 ``self._lock``）。"""
        _atomic_write_text(
            self.news_path,
            json.dumps(self._news, ensure_ascii=False, indent=2),
        )
        self._record_mtime(self.news_path)

    def _write_personas(self) -> None:
        """将人物信息数据库写入磁盘（调用方必须持有 ``self._lock``）。"""
        _atomic_write_text(
            self.personas_path,
            json.dumps(self._personas, ensure_ascii=False, indent=2),
        )
        self._record_mtime(self.personas_path)

    # ------------------------------------------------------------------
    # 文件变化监视：外部修改本地文件时同步刷新内存缓存
    # ------------------------------------------------------------------

    async def start_watcher(self) -> None:
        """启动文件变化监视任务，外部修改本地 JSON 文件时自动同步内存缓存。"""
        if self._watcher_task is not None and not self._watcher_task.done():
            return
        await self._ensure_loaded()
        self._watcher_task = asyncio.create_task(
            self._watch_loop(),
            name="shameimaru_memory_store_watch",
        )

    async def close(self) -> None:
        """停止文件变化监视任务。"""
        task = self._watcher_task
        self._watcher_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _watch_loop(self) -> None:
        """周期比对文件 mtime，变化时把对应数据库重载到内存。"""
        while True:
            try:
                await asyncio.sleep(self._watch_interval)
                await self._reload_if_changed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"监视记忆文件变化失败: {exc}")

    async def _reload_if_changed(self) -> None:
        """文件 mtime 与内存记录不一致时，重载对应数据库。"""
        async with self._lock:
            if not self._loaded:
                return
            for path, attr, default in (
                (self.summaries_path, "_summaries", _SUMMARIES_DEFAULT),
                (self.news_path, "_news", _NEWS_DEFAULT),
                (self.personas_path, "_personas", _PERSONAS_DEFAULT),
            ):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if self._file_mtimes.get(path) == mtime:
                    continue
                setattr(self, attr, self._load_json(path, default))
                self._record_mtime(path)
                logger.debug(f"检测到记忆文件变化，已刷新内存缓存: {path.name}")

    # ------------------------------------------------------------------
    # 摘要层
    # ------------------------------------------------------------------

    async def get_group_summary(self, stream_id: str) -> GroupSummary:
        """获取指定群聊的摘要记录（不存在时返回空记录）。"""
        await self._ensure_loaded()
        raw = self._summaries.get("groups", {}).get(stream_id)
        group = GroupSummary.from_dict(raw)
        if group is None:
            return GroupSummary(stream_id=stream_id)
        return group

    async def list_group_summaries(self) -> list[GroupSummary]:
        """列出全部群聊摘要记录。"""
        await self._ensure_loaded()
        groups: list[GroupSummary] = []
        for raw in self._summaries.get("groups", {}).values():
            group = GroupSummary.from_dict(raw)
            if group is not None:
                groups.append(group)
        return groups

    async def ensure_group(
        self,
        stream_id: str,
        *,
        platform: str = "",
        group_name: str = "",
    ) -> bool:
        """确保群聊记录存在（仅写入元信息，不生成摘要条目）。

        供回复前注入器注册活跃群聊，使摘要任务只对
        「摘要文件中出现的群聊」发起 LLM 调用。

        Args:
            stream_id: 群聊聊天流 ID。
            platform: 平台标识。
            group_name: 群聊名称。

        Returns:
            bool: 记录为新建（True）或已存在（False）。
        """
        await self._ensure_loaded()
        async with self._lock:
            groups = self._summaries.setdefault("groups", {})
            if stream_id in groups:
                return False
            groups[stream_id] = GroupSummary(
                stream_id=stream_id,
                platform=platform,
                group_name=group_name,
            ).to_dict()
            self._write_summaries()
            return True

    async def append_summary(
        self,
        stream_id: str,
        entry: SummaryEntry,
        *,
        platform: str = "",
        group_id: str = "",
        group_name: str = "",
        max_entries: int,
    ) -> None:
        """向指定群聊追加一条摘要。

        条目数达到上限时删除最早的一条。同时更新群聊元信息。

        Args:
            stream_id: 群聊聊天流 ID。
            entry: 摘要条目。
            platform: 平台标识。
            group_id: 群组 ID。
            group_name: 群组名称。
            max_entries: 每个群聊的条目数上限。
        """
        await self._ensure_loaded()
        async with self._lock:
            groups = self._summaries.setdefault("groups", {})
            raw = groups.get(stream_id)
            group = GroupSummary.from_dict(raw) or GroupSummary(stream_id=stream_id)
            group.platform = platform or group.platform
            group.group_id = group_id or group.group_id
            group.group_name = group_name or group.group_name
            group.entries.append(entry)
            if max_entries > 0 and len(group.entries) > max_entries:
                group.entries = group.entries[-max_entries:]
            groups[stream_id] = group.to_dict()
            self._write_summaries()

    async def deprecate_group_summaries(self, stream_id: str) -> None:
        """将指定群聊的全部摘要条目标记为废弃（不删除）。

        摘要被新闻层消费后标记废弃而非删除：
        - 新闻层只消费未废弃的摘要，防止生成重复新闻；
        - 知识层（Dreaming）仍会读取废弃摘要了解群聊主题；
        - 废弃摘要随 ``append_summary`` 的条目上限淘汰逻辑按时间删除。

        Args:
            stream_id: 群聊聊天流 ID。
        """
        await self._ensure_loaded()
        async with self._lock:
            groups = self._summaries.get("groups", {})
            raw = groups.get(stream_id)
            group = GroupSummary.from_dict(raw)
            if group is None or not group.entries:
                return
            changed = False
            for entry in group.entries:
                if not entry.deprecated:
                    entry.deprecated = True
                    changed = True
            if changed:
                groups[stream_id] = group.to_dict()
                self._write_summaries()

    # ------------------------------------------------------------------
    # 新闻层
    # ------------------------------------------------------------------

    async def get_news(self) -> list[NewsEntry]:
        """获取全部新闻条目（按时间升序）。"""
        await self._ensure_loaded()
        entries: list[NewsEntry] = []
        for raw in self._news.get("entries", []):
            entry = NewsEntry.from_dict(raw)
            if entry is not None:
                entries.append(entry)
        entries.sort(key=lambda item: item.timestamp)
        return entries

    async def append_news(self, entry: NewsEntry, max_entries: int) -> list[NewsEntry]:
        """追加一条新闻，达到上限时删除时间最早的条目。

        Returns:
            list[NewsEntry]: 被淘汰删除的新闻条目（需要触发人物层更新）。
        """
        await self._ensure_loaded()
        evicted: list[NewsEntry] = []
        async with self._lock:
            entries: list[NewsEntry] = []
            for raw in self._news.get("entries", []):
                parsed = NewsEntry.from_dict(raw)
                if parsed is not None:
                    entries.append(parsed)
            entries.append(entry)
            if max_entries > 0 and len(entries) > max_entries:
                # 按时间升序淘汰最旧的条目，保证淘汰语义不受文件写入顺序影响
                entries.sort(key=lambda item: item.timestamp)
                overflow = len(entries) - max_entries
                evicted = entries[:overflow]
                entries = entries[overflow:]
            self._news["entries"] = [item.to_dict() for item in entries]
            self._write_news()
        return evicted

    async def remove_news(self, ids: list[str]) -> list[NewsEntry]:
        """按 ID 删除新闻条目。

        Returns:
            list[NewsEntry]: 被删除的新闻条目（需要触发人物层更新）。
        """
        if not ids:
            return []
        target_ids = set(ids)
        await self._ensure_loaded()
        removed: list[NewsEntry] = []
        async with self._lock:
            remaining: list[NewsEntry] = []
            for raw in self._news.get("entries", []):
                entry = NewsEntry.from_dict(raw)
                if entry is None:
                    continue
                if entry.id in target_ids:
                    removed.append(entry)
                else:
                    remaining.append(entry)
            self._news["entries"] = [item.to_dict() for item in remaining]
            self._write_news()
        return removed

    # ------------------------------------------------------------------
    # 人物层
    # ------------------------------------------------------------------

    async def get_persona(self, person_id: str) -> str:
        """获取指定人物的背景信息文本（不存在时返回空字符串）。"""
        await self._ensure_loaded()
        return str(self._personas.get("persons", {}).get(person_id) or "")

    async def set_persona(self, person_id: str, text: str) -> None:
        """写入指定人物的背景信息文本。"""
        content = (text or "").strip()
        await self._ensure_loaded()
        async with self._lock:
            persons = self._personas.setdefault("persons", {})
            if content:
                persons[person_id] = content
            else:
                persons.pop(person_id, None)
            self._write_personas()

    async def get_all_personas(self) -> dict[str, str]:
        """获取全部人物背景信息。"""
        await self._ensure_loaded()
        persons = self._personas.get("persons", {})
        return {str(key): str(value or "") for key, value in persons.items()}
