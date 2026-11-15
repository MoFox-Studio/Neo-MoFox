"""Bot 手动重载使用当前插件集合，并在关闭前停止文件监听。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.runtime.bot import Bot


async def test_reload_all_uses_current_directory() -> None:
    """全量重载不使用启动清单，失败的插件保留失败结果。"""
    bot = Bot(plugins_dir="current_plugins")
    bot.ui = MagicMock()
    manager = SimpleNamespace(
        reconcile_plugins=AsyncMock(return_value={"new": True, "failed": False}),
        reload_plugin=AsyncMock(return_value=True),
        list_loaded_plugins=lambda: ["new"],
    )
    bot.plugin_manager = manager
    bot.load_order = ["removed"]
    assert await bot.reload_plugin() == {"new": True, "failed": False}
    manager.reconcile_plugins.assert_awaited_once_with("current_plugins")
    manager.reload_plugin.assert_awaited_once_with("new")
    assert bot._stats["plugins_loaded"] == 1
    assert bot._stats["plugins_failed"] == 1


async def test_reload_failed_initial_plugin() -> None:
    """已知但未加载的插件能够从终端重试，未知名称明确失败。"""
    bot = Bot()
    bot.ui = MagicMock()
    bot.plugin_manager = SimpleNamespace(
        list_known_plugins=lambda: ["waiting"],
        list_loaded_plugins=lambda: ["waiting"],
        reload_plugin=AsyncMock(return_value=True),
    )
    assert await bot.reload_plugin("waiting") == {"waiting": True}
    assert await bot.reload_plugin("unknown") == {"unknown": False}
    bot.plugin_manager.reload_plugin.assert_awaited_once_with("waiting")


async def test_shutdown_stops_watcher_before_plugins() -> None:
    """关闭先停止新文件操作，再卸载插件，防止释放过程中再次注册。"""
    order: list[str] = []

    async def stop() -> None:
        """记录监听器停止顺序。"""
        order.append("watcher")

    async def unload(name: str) -> bool:
        """记录插件清理顺序。"""
        order.append(name)
        return True

    bot = Bot()
    bot.ui = MagicMock()
    bot.plugin_hot_reload = SimpleNamespace(stop=stop)
    bot.plugin_manager = SimpleNamespace(
        list_loaded_plugins=lambda: ["plugin"],
        unload_plugin=unload,
    )
    await bot.shutdown()
    assert order == ["watcher", "plugin"]
    assert bot.plugin_hot_reload is None


async def test_initialize_plugins_starts_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初始插件编排完成后才开始监听文件，使用当前插件根目录。"""
    from src.core.managers import plugin_hot_reload

    bot = Bot(plugins_dir="isolated_plugins")
    bot.ui = MagicMock()
    bot.plugin_manager = MagicMock()
    for name in (
        "_sync_plugin_market",
        "_discover_plugins",
        "_install_plugin_deps",
        "_load_plugins",
    ):
        monkeypatch.setattr(bot, name, AsyncMock())
    watcher = SimpleNamespace(start=AsyncMock(return_value=True))
    factory = MagicMock(return_value=watcher)
    monkeypatch.setattr(plugin_hot_reload, "PluginHotReloadService", factory)
    await bot._initialize_plugins()
    factory.assert_called_once_with(
        bot.plugin_manager, plugins_dir="isolated_plugins", config_dir="config/plugins"
    )
    watcher.start.assert_awaited_once()
    assert bot.plugin_hot_reload is watcher


async def test_unload_all_includes_plugins_added_after_startup() -> None:
    """手动卸载覆盖启动后新增的插件，并在结束后清空统计。"""
    bot = Bot()
    bot.plugin_manager = SimpleNamespace(
        list_loaded_plugins=lambda: ["new"], unload_plugin=AsyncMock(return_value=True)
    )
    bot.load_order = ["old"]
    await bot._unload_all_plugins()
    assert [
        call.args[0] for call in bot.plugin_manager.unload_plugin.await_args_list
    ] == ["new", "old"]
    assert bot._stats["plugins_loaded"] == 0
