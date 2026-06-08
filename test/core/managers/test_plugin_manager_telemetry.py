"""插件管理器 telemetry 测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import PluginManifest
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.managers.plugin_manager import PluginManager
from src.kernel.telemetry import (
    TelemetryConfig,
    close_telemetry_db,
    get_telemetry_collector,
    init_telemetry,
)


class _TelemetryAction(BaseAction):
    """测试用 Action。"""

    action_name = "telemetry_action"
    associated_types = ["text"]

    async def execute(self, *args, **kwargs):
        return {"success": True}


class _TelemetryPlugin(BasePlugin):
    """测试用插件。"""

    plugin_name = "telemetry_plugin"

    def get_components(self) -> list[type]:
        return [_TelemetryAction]


@pytest.fixture(autouse=True)
async def _setup_telemetry(tmp_path) -> AsyncGenerator[None, None]:
    """初始化 telemetry 并清理全局状态。"""
    await close_telemetry_db()
    get_global_registry().clear()
    get_global_state_manager().clear()
    await init_telemetry(
        config=TelemetryConfig(
            enabled=True,
            collect_plugin_events=True,
        )
    )
    yield
    await close_telemetry_db()
    get_global_registry().clear()
    get_global_state_manager().clear()


def _decode_attributes(row: dict[str, object]) -> dict[str, object]:
    """解析 attributes_json。"""
    raw = row.get("attributes_json")
    if not isinstance(raw, str) or not raw:
        return {}
    return json.loads(raw)


@pytest.mark.asyncio
async def test_load_plugin_records_plugin_loaded_event() -> None:
    """加载成功应写入 plugin_loaded 事件。"""
    manager = PluginManager()
    manifest = PluginManifest(
        name="telemetry_plugin",
        version="1.2.3",
        description="test",
        author="test",
    )
    mock_event_manager = MagicMock()
    mock_event_manager.register_plugin_handlers = AsyncMock()
    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    with (
        patch.object(manager, "_load_from_folder", AsyncMock(return_value=object())),
        patch("src.core.components.loader.get_plugin_class", return_value=_TelemetryPlugin),
        patch(
            "src.core.managers.event_manager.get_event_manager",
            return_value=mock_event_manager,
        ),
        patch("src.kernel.event.get_event_bus", return_value=mock_event_bus),
    ):
        success = await manager.load_plugin_from_manifest("plugins/telemetry_plugin", manifest)

    assert success is True
    rows = await get_telemetry_collector().get_recent(domain="plugin", limit=10)
    loaded_rows = [row for row in rows if row["event_name"] == "plugin_loaded"]
    assert loaded_rows
    attributes = _decode_attributes(loaded_rows[0])
    assert attributes["plugin_version"] == "1.2.3"
    assert attributes["component_count"] == 1
    assert attributes["source_kind"] == "folder"


@pytest.mark.asyncio
async def test_load_plugin_failure_records_plugin_load_failed_event() -> None:
    """加载失败应写入 plugin_load_failed 事件。"""
    manager = PluginManager()
    manifest = PluginManifest(
        name="telemetry_plugin",
        version="1.2.3",
        description="test",
        author="test",
    )

    with (
        patch.object(manager, "_load_from_folder", AsyncMock(return_value=object())),
        patch("src.core.components.loader.get_plugin_class", return_value=None),
    ):
        success = await manager.load_plugin_from_manifest("plugins/telemetry_plugin", manifest)

    assert success is False
    rows = await get_telemetry_collector().get_recent(domain="plugin", limit=10)
    failed_rows = [row for row in rows if row["event_name"] == "plugin_load_failed"]
    assert failed_rows
    attributes = _decode_attributes(failed_rows[0])
    assert attributes["stage"] == "plugin_class_lookup"
    assert attributes["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_unload_plugin_records_plugin_unloaded_event() -> None:
    """卸载成功应写入 plugin_unloaded 事件。"""
    manager = PluginManager()
    manifest = PluginManifest(
        name="telemetry_plugin",
        version="1.2.3",
        description="test",
        author="test",
    )
    plugin = _TelemetryPlugin(config=None)
    manager._loaded_plugins["telemetry_plugin"] = plugin
    manager._manifests["telemetry_plugin"] = manifest
    manager._plugin_paths["telemetry_plugin"] = "plugins/telemetry_plugin"
    await manager._register_components(plugin)

    mock_event_manager = MagicMock()
    mock_event_manager.unregister_plugin_handlers = AsyncMock()
    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    with (
        patch("src.kernel.event.get_event_bus", return_value=mock_event_bus),
        patch(
            "src.core.managers.event_manager.get_event_manager",
            return_value=mock_event_manager,
        ),
        patch("src.core.components.loader.unregister_plugin", MagicMock()),
    ):
        success = await manager.unload_plugin("telemetry_plugin")

    assert success is True
    rows = await get_telemetry_collector().get_recent(domain="plugin", limit=10)
    unloaded_rows = [row for row in rows if row["event_name"] == "plugin_unloaded"]
    assert unloaded_rows
    attributes = _decode_attributes(unloaded_rows[0])
    assert attributes["plugin_version"] == "1.2.3"
    assert attributes["component_count"] == 1
