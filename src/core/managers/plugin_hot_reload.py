"""插件配置和插件编排变化的运行时热加载服务。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.managers.plugin_manager import PluginManager


logger = get_logger("plugin_hot_reload")


class PluginHotReloadService:
    """监听配置文件和插件目录编排变化。

    插件源码文件变化会被忽略。插件目录新增、删除、manifest 变化以及
    ``config/plugins`` 下的配置变化会进入同一条重载队列。
    """

    def __init__(
        self,
        plugin_manager: "PluginManager",
        *,
        plugins_dir: str = "plugins",
        config_dir: str = "config/plugins",
    ) -> None:
        self.plugin_manager = plugin_manager
        self.plugins_dir = Path(plugins_dir).resolve()
        self.config_dir = Path(config_dir).resolve()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._reload_lock = asyncio.Lock()
        self._config_snapshots: dict[str, tuple[tuple[str, int, int], ...]] = {}

    @property
    def running(self) -> bool:
        """返回监听任务是否仍在运行。"""
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        """启动插件目录和配置目录监听。"""
        if self.running:
            return True

        try:
            from watchfiles import awatch
        except ImportError:
            logger.warning("watchfiles 未安装，插件热加载不可用")
            return False

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event = asyncio.Event()
        self._config_snapshots = {
            plugin_name: self._config_snapshot(plugin_name)
            for plugin_name in self.plugin_manager.list_known_plugins()
        }
        self._task = asyncio.create_task(
            self._watch(awatch),
            name="mofox-plugin-hot-reload",
        )
        logger.info(
            f"插件热加载已启动: plugins={self.plugins_dir}, configs={self.config_dir}"
        )
        return True

    async def stop(self) -> None:
        """停止监听，并等待监听任务结束。"""
        task = self._task
        if task is None:
            return

        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._stop_event = None
            self._config_snapshots.clear()
        logger.info("插件热加载已停止")

    async def reload_plugin(self, plugin_name: str) -> bool:
        """通过同一互斥队列重载指定插件配置。"""
        async with self._reload_lock:
            return await self.plugin_manager.reload_plugin(plugin_name)

    async def _watch(self, awatch: Any) -> None:
        assert self._stop_event is not None
        try:
            async for changes in awatch(
                str(self.plugins_dir),
                str(self.config_dir),
                recursive=True,
                debounce=200,
                step=50,
                stop_event=self._stop_event,
            ):
                config_plugins, catalog_changed = self._classify_changes(changes)

                if catalog_changed:
                    try:
                        await self._reconcile_plugins()
                    except Exception as error:
                        logger.error(
                            f"插件目录重组异常: {error}",
                            exc_info=error,
                        )

                for plugin_name in sorted(config_plugins):
                    if plugin_name not in self.plugin_manager.list_known_plugins():
                        continue
                    current_snapshot = self._config_snapshot(plugin_name)
                    if current_snapshot == self._config_snapshots.get(plugin_name):
                        continue
                    try:
                        success = await self.reload_plugin(plugin_name)
                        self._config_snapshots[plugin_name] = self._config_snapshot(
                            plugin_name
                        )
                        if not success:
                            logger.warning(
                                f"插件配置热加载失败，等待下一次配置变化重试: {plugin_name}"
                            )
                    except Exception as error:
                        logger.error(
                            f"插件配置热加载异常: {plugin_name}: {error}",
                            exc_info=error,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(f"插件热加载监听任务退出: {error}", exc_info=error)

    async def _reconcile_plugins(self) -> None:
        async with self._reload_lock:
            await self.plugin_manager.reconcile_plugins(str(self.plugins_dir))
            self._config_snapshots = {
                plugin_name: self._config_snapshot(plugin_name)
                for plugin_name in self.plugin_manager.list_known_plugins()
            }

    def _classify_changes(self, changes: Any) -> tuple[set[str], bool]:
        config_plugins: set[str] = set()
        catalog_changed = False

        for _, raw_path in changes:
            if not self._is_relevant_path(raw_path):
                continue
            path = Path(raw_path).resolve()

            try:
                relative = path.relative_to(self.config_dir)
            except ValueError:
                relative = None
            if relative is not None and relative.parts:
                config_plugins.add(relative.parts[0])
                continue

            try:
                relative = path.relative_to(self.plugins_dir)
            except ValueError:
                continue
            if not relative.parts:
                continue
            known_plugin_path = any(
                plugin_path
                and self._is_path_within(path, Path(plugin_path).resolve())
                for plugin_name in self.plugin_manager.list_known_plugins()
                for plugin_path in [
                    self.plugin_manager.get_plugin_path(plugin_name)
                ]
                if plugin_path
                and not plugin_path.endswith((".zip", ".mfp"))
            )
            if len(relative.parts) == 1 or not known_plugin_path or (
                len(relative.parts) == 2 and relative.parts[1] == "manifest.json"
            ):
                catalog_changed = True

        return config_plugins, catalog_changed

    @staticmethod
    def _is_path_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _is_relevant_path(path: str) -> bool:
        candidate = Path(path)
        return not any(
            part in {"__pycache__", ".pytest_cache", ".git"}
            or part.startswith(".")
            for part in candidate.parts
        )

    def _config_snapshot(self, plugin_name: str) -> tuple[tuple[str, int, int], ...]:
        root = self.config_dir / plugin_name
        if not root.exists() or not root.is_dir():
            return ()

        records: list[tuple[str, int, int]] = []
        for path in root.rglob("*"):
            if not path.is_file() or not self._is_relevant_path(str(path)):
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(self.config_dir)
            except OSError:
                continue
            records.append((str(relative), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(records))
