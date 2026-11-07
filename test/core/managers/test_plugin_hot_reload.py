from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.core.components.loader import clear_registry
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.managers.config_manager import reset_config_manager
from src.core.managers.plugin_hot_reload import PluginHotReloadService
from src.core.managers.plugin_manager import PluginManager


PLUGIN_SOURCE = """
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import register_plugin

@register_plugin
class DemoPlugin(BasePlugin):
    plugin_name = {plugin_name!r}

    def get_components(self):
        return []
"""


def write_plugin(root: Path, plugin_name: str) -> Path:
    plugin_dir = root / plugin_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "version": "1.0.0",
                "description": "test",
                "author": "test",
                "dependencies": {"plugins": [], "components": []},
                "entry_point": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        PLUGIN_SOURCE.format(plugin_name=plugin_name),
        encoding="utf-8",
    )
    return plugin_dir


@pytest.fixture(autouse=True)
def reset_plugin_state() -> None:
    clear_registry()
    get_global_registry().clear()
    get_global_state_manager().clear()
    reset_config_manager()
    yield
    clear_registry()
    get_global_registry().clear()
    get_global_state_manager().clear()
    reset_config_manager()


def test_classify_changes_ignores_existing_plugin_code(tmp_path: Path) -> None:
    manager = PluginManager()
    plugin_dir = write_plugin(tmp_path / "plugins", "demo")
    manager._plugin_paths["demo"] = str(plugin_dir)
    manager._manifests["demo"] = object()  # type: ignore[assignment]

    service = PluginHotReloadService(
        manager,
        plugins_dir=str(tmp_path / "plugins"),
        config_dir=str(tmp_path / "config" / "plugins"),
    )

    config_plugins, catalog_changed = service._classify_changes(
        {("modified", str(plugin_dir / "plugin.py"))}
    )

    assert config_plugins == set()
    assert catalog_changed is False


def test_classify_changes_reacts_to_config_and_new_plugin_files(
    tmp_path: Path,
) -> None:
    manager = PluginManager()
    plugin_dir = write_plugin(tmp_path / "plugins", "demo")
    manager._plugin_paths["demo"] = str(plugin_dir)
    manager._manifests["demo"] = object()  # type: ignore[assignment]

    service = PluginHotReloadService(
        manager,
        plugins_dir=str(tmp_path / "plugins"),
        config_dir=str(tmp_path / "config" / "plugins"),
    )

    config_plugins, catalog_changed = service._classify_changes(
        {
            ("modified", str(tmp_path / "config" / "plugins" / "demo" / "config.toml")),
            ("added", str(tmp_path / "plugins" / "new" / "plugin.py")),
        }
    )

    assert config_plugins == {"demo"}
    assert catalog_changed is True


@pytest.mark.asyncio
async def test_reconcile_adds_and_removes_plugins(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    manager = PluginManager()

    write_plugin(plugins_dir, "first")
    result = await manager.reconcile_plugins(str(plugins_dir))
    assert result == {"first": True}
    assert manager.is_plugin_loaded("first")

    write_plugin(plugins_dir, "second")
    result = await manager.reconcile_plugins(str(plugins_dir))
    assert result["first"] is True
    assert result["second"] is True
    assert manager.is_plugin_loaded("second")

    shutil.rmtree(plugins_dir / "first")
    result = await manager.reconcile_plugins(str(plugins_dir))

    assert "first" not in result
    assert not manager.is_plugin_loaded("first")
    assert manager.is_plugin_loaded("second")
