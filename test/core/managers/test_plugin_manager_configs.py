from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from src.core.components.base.config import BaseConfig
from src.core.components.base.plugin import BasePlugin
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import ComponentType
from src.core.components.loader import PluginManifest
from src.core.managers.plugin_manager import PluginManager


class _TestConfig(BaseConfig):
    config_name = "test_config"


@dataclass
class _FakeConfigInstance:
    value: str = "ok"


@pytest.mark.asyncio
async def test_load_plugin_uses_class_configs_before_instantiation(monkeypatch) -> None:
    """插件应在实例化前通过 class configs 加载配置。"""

    class ConfigFirstPlugin(BasePlugin):
        plugin_name = "config_first_plugin"
        configs = [_TestConfig]

        def __init__(self, config=None) -> None:
            super().__init__(config)
            self.init_has_config = config is not None

        def get_components(self) -> list[type]:
            return []

    manager = PluginManager()
    fake_manifest = PluginManifest(
        name="config_first_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    monkeypatch.setattr(manager, "_load_from_folder", AsyncMock(return_value=object()))

    import src.core.components.loader as loader_module

    monkeypatch.setattr(loader_module, "get_plugin_class", lambda _name: ConfigFirstPlugin)

    class _FakeConfigManager:
        def load_config(self, plugin_name: str, config_class: type[BaseConfig]):
            assert plugin_name == "config_first_plugin"
            assert config_class is _TestConfig
            return _FakeConfigInstance()

    import src.core.managers.config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module,
        "get_config_manager",
        lambda: _FakeConfigManager(),
    )

    manager._register_components = AsyncMock()  # type: ignore[method-assign]

    success = await manager.load_plugin_from_manifest("fake/path", fake_manifest)

    assert success is True
    loaded = manager.get_plugin("config_first_plugin")
    assert loaded is not None
    assert getattr(loaded, "init_has_config") is True
    assert loaded.config is not None


@pytest.mark.asyncio
async def test_register_components_includes_configs_class_property() -> None:
    """即使 get_components 未返回 Config，configs 里的配置类也应被注册。"""

    class RegisterConfigPlugin(BasePlugin):
        plugin_name = "register_config_plugin"
        configs = [_TestConfig]

        def get_components(self) -> list[type]:
            return []

    registry = get_global_registry()
    state_manager = get_global_state_manager()
    registry.clear()
    state_manager.clear()

    manager = PluginManager()
    plugin = RegisterConfigPlugin(config=None)

    await manager._register_components(plugin)

    config_components = registry.get_by_type(ComponentType.CONFIG)
    assert "register_config_plugin:config:test_config" in config_components
    assert config_components["register_config_plugin:config:test_config"] is _TestConfig


@pytest.mark.asyncio
async def test_load_plugin_does_not_fallback_to_get_components_config(
    monkeypatch,
) -> None:
    """未声明 class configs 时，不应从 get_components 回退加载配置。"""

    class LegacyConfigInComponentsPlugin(BasePlugin):
        plugin_name = "legacy_config_plugin"

        def __init__(self, config=None) -> None:
            super().__init__(config)

        def get_components(self) -> list[type]:
            return [_TestConfig]

    manager = PluginManager()
    fake_manifest = PluginManifest(
        name="legacy_config_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    monkeypatch.setattr(manager, "_load_from_folder", AsyncMock(return_value=object()))

    import src.core.components.loader as loader_module

    monkeypatch.setattr(
        loader_module,
        "get_plugin_class",
        lambda _name: LegacyConfigInComponentsPlugin,
    )

    class _FakeConfigManager:
        def load_config(self, plugin_name: str, config_class: type[BaseConfig]):
            raise AssertionError("不应从 get_components() 回退加载配置")

    import src.core.managers.config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module,
        "get_config_manager",
        lambda: _FakeConfigManager(),
    )

    manager._register_components = AsyncMock()  # type: ignore[method-assign]

    success = await manager.load_plugin_from_manifest("fake/path", fake_manifest)

    assert success is True
    loaded = manager.get_plugin("legacy_config_plugin")
    assert loaded is not None
    assert loaded.config is None


@pytest.mark.asyncio
async def test_register_components_ignores_config_from_get_components() -> None:
    """get_components 返回的 Config 组件应被忽略，不应注册。"""

    class IgnoreLegacyConfigPlugin(BasePlugin):
        plugin_name = "ignore_legacy_config_plugin"

        def get_components(self) -> list[type]:
            return [_TestConfig]

    registry = get_global_registry()
    state_manager = get_global_state_manager()
    registry.clear()
    state_manager.clear()

    manager = PluginManager()
    plugin = IgnoreLegacyConfigPlugin(config=None)

    await manager._register_components(plugin)

    config_components = registry.get_by_type(ComponentType.CONFIG)
    assert "ignore_legacy_config_plugin:config:test_config" not in config_components
