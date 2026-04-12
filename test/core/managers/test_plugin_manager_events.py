"""测试插件管理器的事件触发功能。

本测试模块验证插件管理器在组件和插件的加载/卸载过程中
正确触发 ON_COMPONENT_LOADED、ON_COMPONENT_UNLOADED 和 ON_PLUGIN_UNLOADED 事件。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import PluginManifest
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import EventType
from src.core.managers.plugin_manager import PluginManager


class _TestAction(BaseAction):
    """测试用的 Action 组件。"""

    action_name = "test_action"

    async def execute(self, *args, **kwargs):
        return {"success": True}


class _TestPlugin(BasePlugin):
    """测试用的插件。"""

    plugin_name = "test_event_plugin"

    def get_components(self) -> list[type]:
        return [_TestAction]


@pytest.fixture
def plugin_manager():
    """提供插件管理器实例的 fixture。"""
    manager = PluginManager()
    yield manager
    # 清理
    get_global_registry().clear()
    get_global_state_manager().clear()


@pytest.fixture
def mock_event_bus():
    """提供模拟的事件总线的 fixture。"""
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()
    return mock_bus


@pytest.mark.asyncio
async def test_on_component_loaded_event_triggered(plugin_manager, mock_event_bus):
    """测试组件加载时触发 ON_COMPONENT_LOADED 事件。"""
    # 清理注册表和状态管理器
    get_global_registry().clear()
    get_global_state_manager().clear()

    plugin = _TestPlugin(config=None)

    with patch("src.kernel.event.get_event_bus", return_value=mock_event_bus):
        await plugin_manager._register_components(plugin)

    # 验证事件被触发
    mock_event_bus.publish.assert_any_call(
        EventType.ON_COMPONENT_LOADED,
        {
            "signature": "test_event_plugin:action:test_action",
            "plugin_name": "test_event_plugin",
            "component_type": "action",
            "component_name": "test_action",
            "component_class": _TestAction,
        },
    )


@pytest.mark.asyncio
async def test_on_component_unloaded_event_triggered(plugin_manager, mock_event_bus):
    """测试组件卸载时触发 ON_COMPONENT_UNLOADED 事件。"""
    # 清理并注册组件
    get_global_registry().clear()
    get_global_state_manager().clear()

    plugin = _TestPlugin(config=None)
    await plugin_manager._register_components(plugin)

    # 卸载组件并验证事件
    with patch("src.kernel.event.get_event_bus", return_value=mock_event_bus):
        await plugin_manager._unregister_plugin_components("test_event_plugin")

    # 验证事件被触发
    mock_event_bus.publish.assert_any_call(
        EventType.ON_COMPONENT_UNLOADED,
        {
            "signature": "test_event_plugin:action:test_action",
            "plugin_name": "test_event_plugin",
        },
    )


@pytest.mark.asyncio
async def test_on_plugin_unloaded_event_triggered(plugin_manager, mock_event_bus, monkeypatch):
    """测试插件卸载时触发 ON_PLUGIN_UNLOADED 事件。"""
    # 清理注册表和状态管理器
    get_global_registry().clear()
    get_global_state_manager().clear()

    # 创建测试清单
    fake_manifest = PluginManifest(
        name="test_event_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    # 模拟插件加载
    plugin = _TestPlugin(config=None)
    plugin_manager._loaded_plugins["test_event_plugin"] = plugin
    plugin_manager._manifests["test_event_plugin"] = fake_manifest
    plugin_manager._plugin_paths["test_event_plugin"] = "/fake/path"

    # 注册组件
    await plugin_manager._register_components(plugin)

    # 模拟 event_manager
    mock_event_manager = MagicMock()
    mock_event_manager.unregister_plugin_handlers = AsyncMock()

    # 模拟 loader
    mock_unregister_plugin = MagicMock()

    with (
        patch("src.kernel.event.get_event_bus", return_value=mock_event_bus),
        patch(
            "src.core.managers.event_manager.get_event_manager",
            return_value=mock_event_manager,
        ),
        patch(
            "src.core.components.loader.unregister_plugin",
            mock_unregister_plugin,
        ),
    ):
        success = await plugin_manager.unload_plugin("test_event_plugin")

    assert success is True

    # 验证 ON_PLUGIN_UNLOADED 事件被触发
    mock_event_bus.publish.assert_any_call(
        EventType.ON_PLUGIN_UNLOADED,
        {
            "plugin_name": "test_event_plugin",
            "manifest": fake_manifest,
        },
    )


@pytest.mark.asyncio
async def test_component_loaded_event_failure_does_not_block_registration(
    plugin_manager, mock_event_bus
):
    """测试 ON_COMPONENT_LOADED 事件触发失败不会阻止组件注册。"""
    # 清理注册表和状态管理器
    get_global_registry().clear()
    get_global_state_manager().clear()

    # 让事件发布失败
    mock_event_bus.publish.side_effect = Exception("Event bus error")

    plugin = _TestPlugin(config=None)

    with patch("src.kernel.event.get_event_bus", return_value=mock_event_bus):
        # 应该不会抛出异常
        await plugin_manager._register_components(plugin)

    # 验证组件仍然被注册
    registry = get_global_registry()
    component = registry.get("test_event_plugin:action:test_action")
    assert component is not None


@pytest.mark.asyncio
async def test_plugin_unloaded_event_failure_does_not_block_unloading(
    plugin_manager, mock_event_bus
):
    """测试 ON_PLUGIN_UNLOADED 事件触发失败不会阻止插件卸载。"""
    # 清理注册表和状态管理器
    get_global_registry().clear()
    get_global_state_manager().clear()

    # 创建测试清单
    fake_manifest = PluginManifest(
        name="test_event_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    # 模拟插件加载
    plugin = _TestPlugin(config=None)
    plugin_manager._loaded_plugins["test_event_plugin"] = plugin
    plugin_manager._manifests["test_event_plugin"] = fake_manifest
    plugin_manager._plugin_paths["test_event_plugin"] = "/fake/path"

    # 注册组件
    await plugin_manager._register_components(plugin)

    # 让事件发布失败
    mock_event_bus.publish.side_effect = Exception("Event bus error")

    # 模拟 event_manager
    mock_event_manager = MagicMock()
    mock_event_manager.unregister_plugin_handlers = AsyncMock()

    # 模拟 loader
    mock_unregister_plugin = MagicMock()

    with (
        patch("src.kernel.event.get_event_bus", return_value=mock_event_bus),
        patch(
            "src.core.managers.event_manager.get_event_manager",
            return_value=mock_event_manager,
        ),
        patch(
            "src.core.components.loader.unregister_plugin",
            mock_unregister_plugin,
        ),
    ):
        success = await plugin_manager.unload_plugin("test_event_plugin")

    # 插件应该成功卸载，即使事件触发失败
    assert success is True
    assert "test_event_plugin" not in plugin_manager._loaded_plugins


@pytest.mark.asyncio
async def test_events_triggered_in_correct_order(plugin_manager, monkeypatch):
    """测试事件按正确的顺序触发。"""
    # 清理注册表和状态管理器
    get_global_registry().clear()
    get_global_state_manager().clear()

    events_triggered = []

    async def mock_publish(event_type, params):
        """记录事件触发顺序。"""
        events_triggered.append((event_type, params.get("signature") or params.get("plugin_name")))

    mock_event_bus = MagicMock()
    mock_event_bus.publish = mock_publish

    # 创建测试清单
    fake_manifest = PluginManifest(
        name="test_event_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    # 模拟插件加载和注册
    plugin = _TestPlugin(config=None)
    plugin_manager._loaded_plugins["test_event_plugin"] = plugin
    plugin_manager._manifests["test_event_plugin"] = fake_manifest
    plugin_manager._plugin_paths["test_event_plugin"] = "/fake/path"

    # 模拟 event_manager
    mock_event_manager = MagicMock()
    mock_event_manager.unregister_plugin_handlers = AsyncMock()

    # 模拟 loader
    mock_unregister_plugin = MagicMock()

    with (
        patch("src.kernel.event.get_event_bus", return_value=mock_event_bus),
        patch(
            "src.core.managers.event_manager.get_event_manager",
            return_value=mock_event_manager,
        ),
        patch(
            "src.core.components.loader.unregister_plugin",
            mock_unregister_plugin,
        ),
    ):
        # 注册组件（触发 ON_COMPONENT_LOADED）
        await plugin_manager._register_components(plugin)

        # 卸载插件（触发 ON_PLUGIN_UNLOADED 和 ON_COMPONENT_UNLOADED）
        await plugin_manager.unload_plugin("test_event_plugin")

    # 验证事件触发顺序
    assert len(events_triggered) == 3

    # 第一个应该是组件加载
    assert events_triggered[0][0] == EventType.ON_COMPONENT_LOADED
    assert events_triggered[0][1] == "test_event_plugin:action:test_action"

    # 第二个应该是插件卸载
    assert events_triggered[1][0] == EventType.ON_PLUGIN_UNLOADED
    assert events_triggered[1][1] == "test_event_plugin"

    # 第三个应该是组件卸载
    assert events_triggered[2][0] == EventType.ON_COMPONENT_UNLOADED
    assert events_triggered[2][1] == "test_event_plugin:action:test_action"
