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

CONFIG_PLUGIN_SOURCE = """
from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import register_plugin

class DemoConfig(BaseConfig):
    name = "config"

    @config_section("settings")
    class Settings(SectionBase):
        value: int = Field(default=1)

    settings: Settings = Field(default_factory=Settings)

@register_plugin
class DemoPlugin(BasePlugin):
    plugin_name = {plugin_name!r}
    configs = [DemoConfig]

    def get_components(self):
        return []
"""

TOGGLE_PLUGIN_SOURCE = """
from src.core.components.base.action import BaseAction
from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import register_plugin

class DemoConfig(BaseConfig):
    name = "config"

    @config_section("settings")
    class Settings(SectionBase):
        enabled: bool = Field(default=False)

    settings: Settings = Field(default_factory=Settings)

class DemoAction(BaseAction):
    name = "demo_action"
    associated_types = ["text"]

    async def execute(self, *args, **kwargs):
        return {{"success": True}}

@register_plugin
class DemoPlugin(BasePlugin):
    plugin_name = {plugin_name!r}
    configs = [DemoConfig]

    def get_components(self):
        return [DemoAction] if self.config.settings.enabled else []
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


def write_config_plugin(root: Path, plugin_name: str) -> Path:
    plugin_dir = write_plugin(root, plugin_name)
    (plugin_dir / "plugin.py").write_text(
        CONFIG_PLUGIN_SOURCE.format(plugin_name=plugin_name),
        encoding="utf-8",
    )
    return plugin_dir


def write_toggle_plugin(root: Path, plugin_name: str) -> Path:
    plugin_dir = write_plugin(root, plugin_name)
    (plugin_dir / "plugin.py").write_text(
        TOGGLE_PLUGIN_SOURCE.format(plugin_name=plugin_name),
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
    first_instance = manager.get_plugin("first")

    write_plugin(plugins_dir, "second")
    result = await manager.reconcile_plugins(str(plugins_dir))
    assert result["first"] is True
    assert result["second"] is True
    assert manager.get_plugin("first") is not first_instance
    assert manager.is_plugin_loaded("second")

    shutil.rmtree(plugins_dir / "first")
    result = await manager.reconcile_plugins(str(plugins_dir))

    assert "first" not in result
    assert not manager.is_plugin_loaded("first")
    assert manager.is_plugin_loaded("second")


@pytest.mark.asyncio
async def test_reconcile_does_not_reload_remaining_plugins_for_relative_path_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    plugins_dir = Path("plugins")
    plugins_dir.mkdir()
    manager = PluginManager()

    write_plugin(plugins_dir, "first")
    write_plugin(plugins_dir, "second")
    result = await manager.reconcile_plugins(str(plugins_dir))
    assert result == {"first": True, "second": True}
    second_instance = manager.get_plugin("second")

    shutil.rmtree(plugins_dir / "first")
    result = await manager.reconcile_plugins(str(plugins_dir.resolve()))

    assert result == {"second": True}
    assert manager.get_plugin("second") is second_instance
    await manager.unload_plugin("second")


@pytest.mark.asyncio
async def test_invalid_config_reload_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    config_path = tmp_path / "config" / "plugins" / "demo" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[settings]\nvalue = 1\n", encoding="utf-8")

    manager = PluginManager()
    write_config_plugin(plugins_dir, "demo")
    result = await manager.reconcile_plugins(str(plugins_dir))
    assert result == {"demo": True}
    assert manager.get_plugin("demo").config.settings.value == 1  # type: ignore[union-attr]

    config_path.write_text("[settings\nvalue = 2\n", encoding="utf-8")
    assert await manager.reload_plugin("demo") is False
    assert not manager.is_plugin_loaded("demo")
    assert manager.get_plugin_path("demo") is not None

    config_path.write_text("[settings]\nvalue = 2\n", encoding="utf-8")
    assert await manager.reload_plugin("demo") is True
    assert manager.get_plugin("demo").config.settings.value == 2  # type: ignore[union-attr]

    await manager.unload_plugin("demo")


@pytest.mark.asyncio
async def test_config_toggle_rebuilds_plugin_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    config_path = tmp_path / "config" / "plugins" / "demo" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[settings]\nenabled = false\n", encoding="utf-8")

    manager = PluginManager()
    write_toggle_plugin(plugins_dir, "demo")
    assert (await manager.reconcile_plugins(str(plugins_dir))) == {"demo": True}
    assert get_global_registry().get("demo:action:demo_action") is None

    config_path.write_text("[settings]\nenabled = true\n", encoding="utf-8")
    assert await manager.reload_plugin("demo") is True
    assert get_global_registry().get("demo:action:demo_action") is not None

    config_path.write_text("[settings]\nenabled = false\n", encoding="utf-8")
    assert await manager.reload_plugin("demo") is True
    assert get_global_registry().get("demo:action:demo_action") is None

    await manager.unload_plugin("demo")
