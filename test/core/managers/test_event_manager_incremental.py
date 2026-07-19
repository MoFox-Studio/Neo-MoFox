"""测试事件管理器的增量注册行为。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import PluginManifest
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.components.types import EventType
from src.core.managers.event_manager import get_event_manager, reset_event_manager
from src.core.managers.plugin_manager import PluginManager
from src.kernel.event import EventDecision, get_event_bus


@pytest.fixture(autouse=True)
def reset_runtime_state() -> None:
    """清理全局注册表和事件总线，避免用例相互影响。"""
    get_global_registry().clear()
    get_global_state_manager().clear()
    get_event_bus().clear()
    reset_event_manager()


@pytest.mark.asyncio
async def test_plugin_event_handler_receives_on_all_plugin_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """插件事件处理器应能收到 ON_ALL_PLUGIN_LOADED 事件。"""
    received_events: list[tuple[str, dict[str, str]]] = []

    class StartupHandler(BaseEventHandler):
        name = "startup"
        init_subscribe = [EventType.ON_ALL_PLUGIN_LOADED]

        async def execute(
            self, event_name: str, params: dict[str, str]
        ) -> tuple[EventDecision, dict[str, str]]:
            received_events.append((event_name, dict(params)))
            return EventDecision.SUCCESS, params

    class StartupPlugin(BasePlugin):
        plugin_name = "startup_plugin"

        def get_components(self) -> list[type]:
            return [StartupHandler]

    manager = PluginManager()
    manifest = PluginManifest(
        name="startup_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    monkeypatch.setattr(manager, "_load_from_folder", AsyncMock(return_value=object()))

    import src.core.components.loader as loader_module

    monkeypatch.setattr(loader_module, "get_plugin_class", lambda _name: StartupPlugin)

    success = await manager.load_plugin_from_manifest("fake/path", manifest)

    assert success is True
    assert len(get_event_bus().get_subscribers(EventType.ON_ALL_PLUGIN_LOADED.value)) == 1

    await get_event_bus().publish(
        EventType.ON_ALL_PLUGIN_LOADED.value,
        {"phase": "done"},
    )

    assert received_events == [
        (EventType.ON_ALL_PLUGIN_LOADED.value, {"phase": "done"})
    ]


@pytest.mark.asyncio
async def test_unload_plugin_removes_event_handler_subscriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """卸载插件后，其事件处理器订阅应被清理。"""
    call_count = 0

    class ShutdownHandler(BaseEventHandler):
        name = "shutdown"
        init_subscribe = [EventType.ON_ALL_PLUGIN_LOADED]

        async def execute(
            self, event_name: str, params: dict[str, str]
        ) -> tuple[EventDecision, dict[str, str]]:
            nonlocal call_count
            call_count += 1
            return EventDecision.SUCCESS, params

    class ShutdownPlugin(BasePlugin):
        plugin_name = "shutdown_plugin"

        def get_components(self) -> list[type]:
            return [ShutdownHandler]

    manager = PluginManager()
    manifest = PluginManifest(
        name="shutdown_plugin",
        version="1.0.0",
        description="test",
        author="test",
    )

    monkeypatch.setattr(manager, "_load_from_folder", AsyncMock(return_value=object()))

    import src.core.components.loader as loader_module

    monkeypatch.setattr(loader_module, "get_plugin_class", lambda _name: ShutdownPlugin)

    success = await manager.load_plugin_from_manifest("fake/path", manifest)

    assert success is True
    assert len(get_event_bus().get_subscribers(EventType.ON_ALL_PLUGIN_LOADED.value)) == 1

    unloaded = await manager.unload_plugin("shutdown_plugin")

    assert unloaded is True
    assert get_event_bus().get_subscribers(EventType.ON_ALL_PLUGIN_LOADED.value) == []

    await get_event_bus().publish(
        EventType.ON_ALL_PLUGIN_LOADED.value,
        {"phase": "after-unload"},
    )

    assert call_count == 0


@pytest.mark.asyncio
async def test_temporary_handler_keeps_subscription_on_pass() -> None:
    """临时监听器返回 PASS 时应保留订阅。"""

    manager = get_event_manager()
    call_count = 0

    async def temporary_handler(
        event_name: str,
        params: dict[str, str],
    ) -> tuple[EventDecision, dict[str, str]]:
        nonlocal call_count
        call_count += 1
        return EventDecision.PASS, params

    temporary_id = await manager.create_temporary_handler(
        [EventType.ON_ALL_PLUGIN_LOADED],
        temporary_handler,
        priority=7,
    )

    assert temporary_id.startswith("temporary:")

    await get_event_bus().publish(
        EventType.ON_ALL_PLUGIN_LOADED.value,
        {"phase": "first"},
    )
    await get_event_bus().publish(
        EventType.ON_ALL_PLUGIN_LOADED.value,
        {"phase": "second"},
    )

    assert call_count == 2
    get_event_stats = manager.get_event_stats()
    assert get_event_stats
    assert get_event_stats["temporary_handler_count"] == 1


@pytest.mark.asyncio
async def test_temporary_handler_auto_unregisters_on_success() -> None:
    """临时监听器返回 SUCCESS 后应自动移除。"""

    manager = get_event_manager()
    call_count = 0

    async def temporary_handler(
        event_name: str,
        params: dict[str, str],
    ) -> tuple[EventDecision, dict[str, str]]:
        nonlocal call_count
        call_count += 1
        params["phase"] = params["phase"] + "-handled"
        return EventDecision.SUCCESS, params

    temporary_id = await manager.create_temporary_handler(
        [EventType.ON_ALL_PLUGIN_LOADED],
        temporary_handler,
        priority=7,
    )

    decision, params = await get_event_bus().publish(
        EventType.ON_ALL_PLUGIN_LOADED.value,
        {"phase": "once"},
    )

    assert decision is EventDecision.SUCCESS
    assert params["phase"] == "once-handled"
    assert call_count == 1
    assert manager.get_event_stats()["temporary_handler_count"] == 0
    assert await manager.unregister_temporary_handler(temporary_id) is False

    await get_event_bus().publish(
        EventType.ON_ALL_PLUGIN_LOADED.value,
        {"phase": "twice"},
    )
    assert call_count == 1