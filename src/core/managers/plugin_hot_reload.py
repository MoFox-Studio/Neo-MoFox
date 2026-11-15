"""监听插件配置和目录编排变化，通过插件管理器执行热重载。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from watchfiles import Change, awatch

from src.kernel.concurrency import get_task_manager
from src.kernel.concurrency.task_info import TaskInfo
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.managers.plugin_manager import PluginManager

logger = get_logger("plugin_hot_reload")
FileSnapshot = tuple[tuple[str, bytes], ...]


class PluginHotReloadService:
    """合并文件事件，串行重载配置，并重试暂时失败的生命周期操作。"""

    def __init__(
        self,
        plugin_manager: PluginManager,
        *,
        plugins_dir: str = "plugins",
        config_dir: str = "config/plugins",
    ) -> None:
        """设置插件管理器和监听目录，配置目录按插件名称组织。"""
        self.plugin_manager = plugin_manager
        self.plugins_dir = Path(plugins_dir).resolve()
        self.config_dir = Path(config_dir).resolve()
        self._task: TaskInfo | None = None
        self._stop_event = asyncio.Event()
        self._reload_lock = asyncio.Lock()
        self._config_snapshots: dict[str, FileSnapshot] = {}
        self._catalog: FileSnapshot = ()
        self._retry_at: dict[str, float] = {}

    @property
    def running(self) -> bool:
        """返回统一任务管理器中的监听任务是否正在运行。"""
        return self._task is not None and not self._task.is_done()

    async def start(self) -> bool:
        """准备目录与文件快照，并创建可追踪的守护任务。"""
        if self.running:
            return True
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._config_snapshots = {
            name: self._config_snapshot(name)
            for name in self.plugin_manager.list_known_plugins()
        }
        self._catalog = self._catalog_snapshot()
        self._task = get_task_manager().create_task(
            self._watch(),
            name="mofox-plugin-hot-reload",
            daemon=True,
        )
        logger.info(f"插件热加载已启动: {self.plugins_dir}")
        return True

    async def stop(self) -> None:
        """停止接收文件事件，等待当前事务结束，超时后取消监听任务。"""
        if self._task is None:
            return
        self._stop_event.set()
        task = self._task.task
        assert task is not None
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except TimeoutError:
            self._task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._task = None
            self._config_snapshots.clear()
            self._retry_at.clear()
        logger.info("插件热加载已停止")

    async def reload_plugin(self, plugin_name: str) -> bool:
        """串行调用配置重载，只记录本次操作之前观察到的文件版本。"""
        async with self._reload_lock:
            snapshot = self._config_snapshot(plugin_name)
            success = await self.plugin_manager.reload_plugin(plugin_name)
            if success:
                self._config_snapshots[plugin_name] = snapshot
                self._retry_at.pop(plugin_name, None)
            else:
                self._retry_at[plugin_name] = time.monotonic() + 1.0
            return success

    async def _watch(self) -> None:
        """监听事件并在空闲时重新比较快照，补偿监听启动窗口和失败重试。"""
        async for changes in awatch(
            str(self.plugins_dir),
            str(self.config_dir),
            recursive=True,
            debounce=200,
            step=50,
            stop_event=self._stop_event,
            rust_timeout=250,
            yield_on_timeout=True,
        ):
            if self._stop_event.is_set():
                break
            try:
                await self._process_changes(changes)
            except Exception as error:
                logger.error("插件热加载批次失败，将在后续轮询重试", exc_info=error)

    async def _process_changes(self, changes: set[tuple[Change, str]]) -> None:
        """应用目录变化和配置变化，保留失败项目及事务期间的新文件版本。"""
        changed_configs, _ = self._classify_changes(changes)
        catalog = self._catalog_snapshot()
        if catalog != self._catalog and time.monotonic() >= self._retry_at.get("", 0):
            async with self._reload_lock:
                results = await self.plugin_manager.reconcile_plugins(
                    str(self.plugins_dir)
                )
                if all(results.values()):
                    self._catalog = catalog
                    self._retry_at.pop("", None)
                else:
                    self._retry_at[""] = time.monotonic() + 1.0
        known_names = set(self.plugin_manager.list_known_plugins())
        for name in self._config_snapshots.keys() - known_names:
            self._config_snapshots.pop(name)
            self._retry_at.pop(name, None)
        for name in sorted(known_names):
            if self._stop_event.is_set():
                break
            snapshot = self._config_snapshot(name)
            retry_due = (
                name in self._retry_at and time.monotonic() >= self._retry_at[name]
            )
            if snapshot == self._config_snapshots.get(name) and not retry_due:
                continue
            if name not in changed_configs and time.monotonic() < self._retry_at.get(
                name, 0
            ):
                continue
            try:
                await self.reload_plugin(name)
            except Exception as error:
                self._retry_at[name] = time.monotonic() + 1.0
                logger.error(f"插件配置重载失败: {name}", exc_info=error)

    def _classify_changes(
        self, changes: set[tuple[Change, str]]
    ) -> tuple[set[str], bool]:
        """分类配置和目录事件，忽略既有插件的源码、资源和隐藏临时文件。"""
        config_names: set[str] = set()
        catalog_changed = False
        for _, raw_path in changes:
            path = Path(raw_path).resolve()
            if path.is_relative_to(self.config_dir):
                relative = path.relative_to(self.config_dir)
                if (
                    relative.parts
                    and self._is_relevant_path(relative)
                    and path.suffix == ".toml"
                ):
                    config_names.add(relative.parts[0])
            elif path.is_relative_to(self.plugins_dir):
                relative = path.relative_to(self.plugins_dir)
                if self._is_relevant_path(relative):
                    catalog_changed |= len(relative.parts) == 1 or (
                        len(relative.parts) == 2 and path.name == "manifest.json"
                    )
        return config_names, catalog_changed

    @staticmethod
    def _is_relevant_path(path: Path) -> bool:
        """只检查监听根目录内的相对路径，允许宿主位于隐藏工作目录。"""
        return not any(part.startswith((".", "__")) for part in path.parts)

    def _config_snapshot(self, plugin_name: str) -> FileSnapshot:
        """使用配置内容摘要去重，避免同大小或同时间戳修改被漏掉。"""
        root = self.config_dir / plugin_name
        return self._snapshot(root.rglob("*.toml"), self.config_dir)

    def _catalog_snapshot(self) -> FileSnapshot:
        """追踪插件来源与清单内容，源码修改不会改变目录快照。"""
        paths = [
            path / "manifest.json" if path.is_dir() else path
            for path in self.plugins_dir.iterdir()
            if self._is_relevant_path(Path(path.name))
            and (path.is_dir() or path.suffix in {".zip", ".mfp"})
        ]
        return self._snapshot(paths, self.plugins_dir)

    @staticmethod
    def _snapshot(paths: Iterable[Path], root: Path) -> FileSnapshot:
        """比较配置内容和归档元数据，消失或占用的文件留待下轮比较。"""
        records: list[tuple[str, bytes]] = []
        for path in paths:
            if not PluginHotReloadService._is_relevant_path(path.relative_to(root)):
                continue
            try:
                if path.suffix in {".zip", ".mfp"}:
                    metadata = path.stat()
                    digest = f"{metadata.st_size}:{metadata.st_mtime_ns}:{metadata.st_ino}".encode()
                else:
                    digest = hashlib.sha256(path.read_bytes()).digest()
            except (FileNotFoundError, PermissionError):
                continue
            records.append((str(path.relative_to(root)), digest))
        return tuple(sorted(records))
