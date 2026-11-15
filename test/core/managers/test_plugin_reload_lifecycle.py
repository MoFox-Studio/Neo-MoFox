"""真实插件加载入口、失败恢复、依赖顺序和并发资源生命周期回归。"""

import asyncio
import json
import shutil
import sys
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from src.app.plugin_system.api import plugin_api
from src.core.components.loader import clear_registry
from src.core.components.registry import get_global_registry
from src.core.components.state_manager import get_global_state_manager
from src.core.managers import (
    adapter_manager,
    config_manager,
    event_manager,
    plugin_manager,
    router_manager,
)
from src.core.managers.plugin_hot_reload import PluginHotReloadService
from src.core.managers.plugin_manager import PluginManager
from src.kernel import concurrency, event
from src.kernel.concurrency import TaskManager

PLUGIN_SOURCE = '''"""测试用插件，通过公开入口声明配置和组件。"""
from src.app.plugin_system.base import (
    BasePlugin, BaseConfig, BaseAction, BaseAdapter, BaseRouter,
    Field, SectionBase, config_section, register_plugin,
)
from mofox_wire import MessageEnvelope

class Config(BaseConfig):
    """插件配置。"""
    name = "config"
    @config_section("settings")
    class Settings(SectionBase):
        """可切换的测试设置。"""
        enabled: bool = Field(default=True)
        value: int = Field(default=1)
    settings: Settings = Field(default_factory=Settings)

class Action(BaseAction):
    """配置启用时注册的动作。"""
    name = "action"
    associated_types = ["text"]
    async def execute(self) -> tuple[bool, str]:
        """返回配置值。"""
        return True, str(self.plugin.config.settings.value)

class Adapter(BaseAdapter):
    """记录启动次数且不连接外部平台的适配器。"""
    name = "adapter"
    starts = 0
    async def start(self) -> None:
        """记录启动。"""
        type(self).starts += 1
    async def stop(self) -> None:
        """允许测试注入停止失败。"""
        pass
    async def from_platform_message(self, raw: object) -> MessageEnvelope:
        """此测试不处理平台消息。"""
        raise NotImplementedError
    async def get_bot_info(self) -> dict[str, str]:
        """返回隔离的测试身份。"""
        return {"bot_id": "demo", "bot_name": "demo", "platform": "test"}

class Router(BaseRouter):
    """提供本地路由资源。"""
    name = "router"
    def register_endpoints(self) -> None:
        """仅验证挂载资源，无需端点。"""
        pass

@register_plugin
class Plugin(BasePlugin):
    """与既有注册机制兼容的插件。"""
    plugin_name = "PLUGIN_NAME"
    configs = [Config]
    def get_components(self) -> list[type]:
        """根据启用状态选择组件。"""
        return [Action, Adapter, Router] if self.config.settings.enabled else []
'''


@pytest.fixture
async def runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[PluginManager]:
    """隔离插件、事件、路由和统一任务管理，避免访问真实实例数据。"""
    monkeypatch.chdir(tmp_path)
    clear_registry()
    get_global_registry().clear()
    get_global_state_manager().clear()
    monkeypatch.setattr(event, "_event_bus", event.EventBus())
    monkeypatch.setattr(event_manager, "_event_manager", None)
    monkeypatch.setattr(config_manager, "_global_config_manager", None)
    monkeypatch.setattr(adapter_manager, "_global_adapter_manager", None)
    monkeypatch.setattr(router_manager, "_global_router_manager", None)
    monkeypatch.setattr(
        router_manager, "get_http_server", lambda: SimpleNamespace(app=app)
    )
    tasks = TaskManager()
    monkeypatch.setattr(concurrency.task_manager, "_task_manager", tasks)
    monkeypatch.setattr(concurrency, "get_task_manager", lambda: tasks)
    monkeypatch.setattr(adapter_manager, "get_task_manager", lambda: tasks)
    monkeypatch.setattr(
        "src.core.managers.plugin_hot_reload.get_task_manager", lambda: tasks
    )
    manager = PluginManager()
    monkeypatch.setattr(plugin_manager, "_global_plugin_manager", manager)
    app = FastAPI()
    adapter_manager.initialize_adapter_manager()
    router_manager.initialize_router_manager()
    yield manager
    for task in tasks.get_all_tasks():
        task.cancel()
    await asyncio.gather(
        *(info.task for info in tasks.get_all_tasks() if info.task),
        return_exceptions=True,
    )
    for name in manager.list_loaded_plugins():
        await manager.unload_plugin(name)
    tasks.shutdown_process_pool(wait=True)
    clear_registry()
    get_global_registry().clear()
    get_global_state_manager().clear()


def write_plugin(name: str, dependencies: list[str] | None = None) -> Path:
    """创建包含真实清单、配置类和组件的测试插件。"""
    root = Path("plugins") / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "description": "test",
                "author": "test",
                "dependencies": {"plugins": dependencies or [], "components": []},
                "entry_point": "plugin.py",
            }
        ),
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        PLUGIN_SOURCE.replace("PLUGIN_NAME", name), encoding="utf-8"
    )
    return root


def save_config(name: str, text: str) -> Path:
    """模拟编辑器直接保存或替换配置。"""
    path = Path("config/plugins") / name / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def test_public_api_load_reload_and_unload(runtime: PluginManager) -> None:
    """通过公开 API 完成配置生效和资源替换，卸载后清空追踪。"""
    root = write_plugin("demo")
    assert await plugin_api.load_plugin(str(root))
    old = runtime.get_plugin("demo")
    old_adapter = adapter_manager.get_adapter_manager().get_adapter(
        "demo:adapter:adapter"
    )
    save_config("demo", "[settings]\nvalue = 2\nenabled = false\n")
    assert await plugin_api.reload_plugin("demo")
    assert runtime.get_plugin("demo") is not old
    assert runtime.get_plugin("demo").config.settings.value == 2
    assert not adapter_manager.get_adapter_manager().get_all_adapters()
    assert not router_manager.get_router_manager().get_all_mounted_routers()
    assert old_adapter is not None
    save_config("demo", "[settings]\nvalue = 3\nenabled = true\n")
    assert await plugin_api.reload_plugin("demo")
    assert (
        adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
        is not old_adapter
    )
    assert await plugin_api.unload_plugin("demo")
    assert not runtime.list_known_plugins()


async def test_invalid_config_keeps_working_plugin(runtime: PluginManager) -> None:
    """编辑器保存了不完整 TOML 时，原插件与适配器继续可用。"""
    root = write_plugin("demo")
    assert await runtime.load_plugin(str(root))
    old = runtime.get_plugin("demo")
    old_adapter = adapter_manager.get_adapter_manager().get_adapter(
        "demo:adapter:adapter"
    )
    save_config("demo", "[settings\nvalue = 2\n")
    assert not await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is old
    assert (
        adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
        is old_adapter
    )
    save_config("demo", "[settings]\nvalue = 2\n")
    assert await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo").config.settings.value == 2


async def test_failed_initial_load_can_retry(runtime: PluginManager) -> None:
    """首次加载失败也保留来源，修正配置后可通过公开重载 API 恢复。"""
    root = write_plugin("demo")
    save_config("demo", "[settings\n")
    assert not await plugin_api.load_plugin(str(root))
    assert "demo" in runtime.list_known_plugins()
    assert not runtime.is_plugin_loaded("demo")
    save_config("demo", "[settings]\nvalue = 4\n")
    assert await plugin_api.reload_plugin("demo")
    assert runtime.get_plugin("demo").config.settings.value == 4
    assert "demo" not in runtime._failed_plugins


async def test_resource_start_failure_restores_previous_instance(
    runtime: PluginManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候选适配器启动失败时恢复旧实例、配置和组件。"""
    assert await runtime.load_plugin(str(write_plugin("demo")))
    old = runtime.get_plugin("demo")
    start = runtime._start_plugin_runtime_resources
    calls = 0

    async def fail_once(name: str) -> None:
        """仅候选版本失败，旧版本恢复时使用真实资源启动。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("candidate start failed")
        await start(name)

    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", fail_once)
    save_config("demo", "[settings]\nvalue = 5\n")
    assert not await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is old
    assert config_manager.get_config_manager().get_config("demo") is old.config
    assert (
        adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter").plugin
        is old
    )


async def test_initial_resource_failure_rolls_back(
    runtime: PluginManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """加载中资源启动失败不能留下已加载标记或组件，随后允许重试。"""
    root = write_plugin("demo")
    start = runtime._start_plugin_runtime_resources
    monkeypatch.setattr(
        runtime,
        "_start_plugin_runtime_resources",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    assert not await runtime.load_plugin(str(root))
    assert not runtime.is_plugin_loaded("demo")
    assert not get_global_registry().get_by_plugin("demo")
    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", start)
    assert await runtime.reload_plugin("demo")


async def test_registration_failure_is_reported(
    runtime: PluginManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单组件注册失败必须向上传递，并回滚此前已注册的组件。"""
    root = write_plugin("demo")
    registry = get_global_registry()
    register = registry.register
    record = AsyncMock()
    monkeypatch.setattr(plugin_manager, "_record_plugin_telemetry_event", record)

    def fail_adapter(component: type, signature: str, dependencies: list[str]) -> bool:
        """动作注册后，模拟适配器注册失败。"""
        if ":adapter:" in signature:
            raise ValueError("conflicting component")
        return register(component, signature, dependencies)

    monkeypatch.setattr(registry, "register", fail_adapter)
    assert not await runtime.load_plugin(str(root))
    assert not runtime.is_plugin_loaded("demo")
    assert not registry.get_by_plugin("demo")
    assert any(
        call.kwargs.get("event_name") == "plugin_load_failed"
        and call.kwargs.get("stage") == "activation"
        for call in record.await_args_list
    )


async def test_add_restores_all_resources_delete_keeps_survivor(
    runtime: PluginManager,
) -> None:
    """新增后的资源已就绪，删除及路径形式变化不替换无关插件实例。"""
    write_plugin("first")
    assert all((await runtime.reconcile_plugins("plugins")).values())
    first = runtime.get_plugin("first")
    write_plugin("second")
    assert all(
        (await runtime.reconcile_plugins(str(Path("plugins").resolve()))).values()
    )
    assert runtime.get_plugin("first") is not first
    second = runtime.get_plugin("second")
    assert len(adapter_manager.get_adapter_manager().get_all_adapters()) == 2
    assert len(router_manager.get_router_manager().get_all_mounted_routers()) == 2
    second_router = router_manager.get_router_manager().get_mounted_router(
        "second:router:router"
    )
    shutil.rmtree(Path("plugins/first"))
    assert all((await runtime.reconcile_plugins("plugins")).values())
    assert runtime.get_plugin("second") is second
    assert not runtime.is_plugin_loaded("first")
    app = router_manager.get_http_server().app
    assert any(getattr(route, "app", None) is second_router.app for route in app.routes)


async def test_failed_unload_preserves_tracking(
    runtime: PluginManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """适配器停止失败时，目录同步报告失败并保留路径和旧实例。"""
    root = write_plugin("demo")
    assert await runtime.load_plugin(str(root))
    old = runtime.get_plugin("demo")
    adapter = adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
    monkeypatch.setattr(adapter, "stop", AsyncMock(side_effect=RuntimeError("busy")))
    shutil.rmtree(root)
    assert (await runtime.reconcile_plugins("plugins"))["demo"] is False
    assert runtime.get_plugin("demo") is old
    assert runtime.get_plugin_path("demo") == str(root)
    assert get_global_registry().get("demo:adapter:adapter") is not None
    monkeypatch.setattr(adapter, "stop", AsyncMock())
    assert await runtime.unload_plugin("demo")


async def test_concurrent_public_loads_create_one_instance(
    runtime: PluginManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个 API 同时加载只执行一次激活，避免重复组件和连接。"""
    root = write_plugin("demo")
    tasks = concurrency.get_task_manager()
    first = tasks.create_task(plugin_api.load_plugin(str(root)))
    second = tasks.create_task(plugin_api.load_plugin(str(root)))
    assert await first.task
    assert await second.task
    adapter = adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
    assert type(adapter).starts == 1


async def test_concurrent_adapter_start_and_unload(runtime: PluginManager) -> None:
    """适配器启动未完成时卸载会等待，且后续启动不能恢复旧组件。"""
    root = write_plugin("demo")
    from src.core.components.loader import load_manifest

    manifest = await load_manifest(str(root))
    assert await runtime.load_plugin_from_manifest(
        str(root), manifest, start_runtime_resources=False
    )
    manager = adapter_manager.get_adapter_manager()
    signature = "demo:adapter:adapter"
    started = asyncio.Event()
    release = asyncio.Event()
    adapter_class = get_global_registry().get(signature)

    async def start(self: object) -> None:
        """阻塞适配器的启动窗口。"""
        started.set()
        await release.wait()

    adapter_class.start = start
    tasks = concurrency.get_task_manager()
    first = tasks.create_task(manager.start_adapter(signature))
    await started.wait()
    duplicate = tasks.create_task(manager.start_adapter(signature))
    unload = tasks.create_task(runtime.unload_plugin("demo"))
    await asyncio.sleep(0)
    assert not unload.is_done()
    release.set()
    assert await first.task
    assert await duplicate.task
    assert await unload.task
    assert not manager.get_all_adapters()
    assert not await manager.start_adapter(signature)


async def test_real_file_watcher(runtime: PluginManager) -> None:
    """在同一进程内验证真实文件事件、启停切换及源码修改排除。"""
    root = write_plugin("demo")
    assert await runtime.load_plugin(str(root))
    service = PluginHotReloadService(runtime)
    await service.start()
    try:
        save_config("demo", "[settings]\nvalue = 6\nenabled = false\n")
        async with asyncio.timeout(8):
            while runtime.get_plugin("demo").config.settings.value != 6:
                await asyncio.sleep(0.05)
        assert not adapter_manager.get_adapter_manager().get_all_adapters()
        async with asyncio.timeout(8):
            while service._config_snapshots.get("demo") != service._config_snapshot(
                "demo"
            ):
                await asyncio.sleep(0.05)
        previous = runtime.get_plugin("demo")
        source = root / "plugin.py"
        source.write_text(
            source.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert runtime.get_plugin("demo") is previous
        shutil.rmtree(root)
        async with asyncio.timeout(8):
            while runtime.is_plugin_loaded("demo"):
                await asyncio.sleep(0.05)
        write_plugin("new_plugin")
        async with asyncio.timeout(8):
            while not runtime.is_plugin_loaded("new_plugin"):
                await asyncio.sleep(0.05)
    finally:
        await service.stop()


async def test_manifest_move_and_invalid_save(runtime: PluginManager) -> None:
    """来源路径变化使用新目录，绝对路径扫描不会将保存中的清单当作删除。"""
    root = write_plugin("demo")
    assert await runtime.load_plugin(str(root))
    previous = runtime.get_plugin("demo")
    manifest_path = root / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text("{", encoding="utf-8")
    assert (await runtime.reconcile_plugins(str(Path("plugins").resolve())))[
        "demo"
    ] is False
    assert runtime.get_plugin("demo") is previous
    manifest_path.write_text(manifest_text, encoding="utf-8")
    moved = root.with_name("renamed_demo")
    root.rename(moved)
    assert (await runtime.reconcile_plugins("plugins"))["demo"]
    assert runtime.get_plugin("demo") is not previous
    assert Path(runtime.get_plugin_path("demo")).resolve() == moved.resolve()


async def test_manifest_changes_reload_dependents(runtime: PluginManager) -> None:
    """清单更新按依赖顺序替换受影响插件，删除依赖时卸载依赖者。"""
    root = write_plugin("first")
    write_plugin("dependent", ["first>=1.0.0"])
    write_plugin("unrelated")
    assert all((await runtime.reconcile_plugins("plugins")).values())
    dependent = runtime.get_plugin("dependent")
    unrelated = runtime.get_plugin("unrelated")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["version"] = "1.1.0"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert all((await runtime.reconcile_plugins("plugins")).values())
    assert runtime.get_plugin("dependent") is not dependent
    assert runtime.get_plugin("unrelated") is unrelated
    shutil.rmtree(root)
    await runtime.reconcile_plugins("plugins")
    assert not runtime.is_plugin_loaded("dependent")
    assert runtime.get_plugin("unrelated") is unrelated


@pytest.mark.parametrize("suffix,prefix", [(".zip", ""), (".mfp", "packed/")])
async def test_archive_load_reload_unload(
    runtime: PluginManager,
    suffix: str,
    prefix: str,
) -> None:
    """保留目录和两种压缩格式，重载及卸载后不残留搜索路径或临时模块。"""
    root = write_plugin("demo")
    archive = root.with_suffix(suffix)
    with zipfile.ZipFile(archive, "w") as package:
        package.write(root / "manifest.json", prefix + "manifest.json")
        package.write(root / "plugin.py", prefix + "plugin.py")
    shutil.rmtree(root)
    search_paths = list(sys.path)
    assert await runtime.load_plugin(str(archive))
    old_temp = Path(runtime._archive_tmpdirs["demo"])
    save_config("demo", "[settings]\nvalue = 8\n")
    assert await runtime.reload_plugin("demo")
    assert not old_temp.exists()
    current_temp = Path(runtime._archive_tmpdirs["demo"])
    assert runtime.get_plugin("demo").config.settings.value == 8
    assert await runtime.unload_plugin("demo")
    assert not current_temp.exists()
    assert sys.path == search_paths
    assert not any(
        getattr(module, "__file__", "") and str(current_temp) in str(module.__file__)
        for module in tuple(sys.modules.values())
    )


async def test_partial_start_cleanup_failure_keeps_tracking(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动和回滚都失败时保留候选资源，修复停止后仍能重新加载。"""
    start_resources = runtime._start_plugin_runtime_resources

    async def fail_start_and_stop(name: str) -> None:
        """让真实启动入口遇到半启动且无法停止的适配器。"""
        cls = get_global_registry().get(f"{name}:adapter:adapter")
        cls.start = AsyncMock(side_effect=RuntimeError("start failed"))
        cls.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        await start_resources(name)

    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", fail_start_and_stop)
    root = write_plugin("demo")
    assert not await runtime.load_plugin(str(root))
    candidate = runtime.get_plugin("demo")
    adapter = adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
    assert adapter.plugin is candidate
    assert get_global_registry().get("demo:adapter:adapter") is not None
    assert not await runtime.load_plugin(str(root))
    monkeypatch.setattr(adapter, "stop", AsyncMock())
    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", start_resources)
    assert await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is not candidate


async def test_router_start_and_stop_failures(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由启动失败回收挂载，关闭失败保留挂载和实例供下一次重试。"""
    root = write_plugin("demo")
    from src.core.components.base.router import BaseRouter

    startup = BaseRouter.startup
    monkeypatch.setattr(
        BaseRouter, "startup", AsyncMock(side_effect=RuntimeError("offline"))
    )
    assert not await runtime.load_plugin(str(root))
    assert not router_manager.get_router_manager().get_all_mounted_routers()
    app = router_manager.get_http_server().app
    assert not any(
        getattr(route, "path", "").startswith("/router/") for route in app.routes
    )
    monkeypatch.setattr(BaseRouter, "startup", startup)
    assert await runtime.reload_plugin("demo")
    router = router_manager.get_router_manager().get_mounted_router(
        "demo:router:router"
    )
    monkeypatch.setattr(router, "shutdown", AsyncMock(side_effect=RuntimeError("busy")))
    assert not await runtime.unload_plugin("demo")
    assert runtime.is_plugin_loaded("demo")
    assert (
        router_manager.get_router_manager().get_mounted_router("demo:router:router")
        is router
    )
    monkeypatch.setattr(router, "shutdown", AsyncMock())
    assert await runtime.unload_plugin("demo")


@pytest.mark.parametrize("required,result", [(True, False), (False, True)])
async def test_only_manifest_installs_dependencies(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
    result: bool,
) -> None:
    """只使用清单依赖，不读取 requirements，必需依赖安装失败阻止加载。"""
    from src.core import config
    from src.core.components.utils import DependencyInstaller

    root = write_plugin("demo")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(
        python_dependencies=["example-package>=1"], dependencies_required=required
    )
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (root / "requirements.txt").write_text("must-not-install", encoding="utf-8")
    settings = SimpleNamespace(
        enabled=True, install_command="uv pip install", skip_if_satisfied=True
    )
    monkeypatch.setattr(
        config, "get_core_config", lambda: SimpleNamespace(plugin_deps=settings)
    )
    install = AsyncMock(return_value={"demo": False})
    monkeypatch.setattr(DependencyInstaller, "install_for_plugins", install)
    assert await runtime.load_plugin(str(root)) is result
    specifications = install.call_args.args[0]
    assert len(specifications) == 1
    assert specifications[0].packages == ["example-package>=1"]
    assert specifications[0].required is required


async def test_dependency_installer_error_can_retry(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖安装器异常可通过已记录的来源重试，不吞掉失败结果。"""
    root = write_plugin("demo")
    ensure = runtime._ensure_manifest_dependencies
    monkeypatch.setattr(
        runtime,
        "_ensure_manifest_dependencies",
        AsyncMock(side_effect=OSError("offline")),
    )
    assert not await runtime.load_plugin(str(root))
    assert "demo" in runtime.list_known_plugins()
    monkeypatch.setattr(runtime, "_ensure_manifest_dependencies", ensure)
    assert await runtime.reload_plugin("demo")


async def test_cancelled_reload_restores_previous_instance(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭超时取消候选加载时，先恢复旧插件，再向调用方传递取消。"""
    assert await runtime.load_plugin(str(write_plugin("demo")))
    previous = runtime.get_plugin("demo")
    start_resources = runtime._start_plugin_runtime_resources
    calls = 0

    async def cancel_candidate(name: str) -> None:
        """只取消候选资源启动，保留真实恢复路径。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        await start_resources(name)

    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", cancel_candidate)
    with pytest.raises(asyncio.CancelledError):
        await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is previous
    assert (
        adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter").plugin
        is previous
    )


async def test_reload_exception_and_failed_recovery(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """加载异常先恢复旧版本；恢复也失败则清理并保留可重试来源。"""
    assert await runtime.load_plugin(str(write_plugin("demo")))
    previous = runtime.get_plugin("demo")
    load = runtime._load_plugin_from_manifest
    monkeypatch.setattr(
        runtime,
        "_load_plugin_from_manifest",
        AsyncMock(side_effect=RuntimeError("load failed")),
    )
    assert not await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is previous
    monkeypatch.setattr(runtime, "_load_plugin_from_manifest", load)
    monkeypatch.setattr(
        runtime,
        "_start_plugin_runtime_resources",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    assert not await runtime.reload_plugin("demo")
    assert not runtime.is_plugin_loaded("demo")
    assert "demo" in runtime.list_known_plugins()
    assert not get_global_registry().get_by_plugin("demo")


async def test_failed_archive_reload_restores_old_modules(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """压缩包候选入口损坏时恢复原临时目录和模块，不删除仍需使用的文件。"""
    root = write_plugin("demo")
    archive = root.with_suffix(".zip")
    manifest_text = (root / "manifest.json").read_text(encoding="utf-8")
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", manifest_text)
        package.write(root / "plugin.py", "plugin.py")
    shutil.rmtree(root)
    assert await runtime.load_plugin(str(archive))
    previous = runtime.get_plugin("demo")
    old_temp = runtime._archive_tmpdirs["demo"]
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", manifest_text)
        package.writestr("plugin.py", "invalid Python !!!")
    assert not await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is previous
    assert runtime._archive_tmpdirs["demo"] == old_temp
    assert Path(old_temp).exists()
    # 停止失败时也不能丢弃旧压缩包的来源。
    adapter = adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
    monkeypatch.setattr(adapter, "stop", AsyncMock(side_effect=RuntimeError("busy")))
    assert not await runtime.reload_plugin("demo")
    assert runtime._archive_tmpdirs["demo"] == old_temp
    monkeypatch.setattr(adapter, "stop", AsyncMock())


async def test_cleanup_continues_after_hook_and_registry_errors(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """卸载钩子、处理器或组件阶段异常不跳过其余失败加载清理。"""
    assert await runtime.load_plugin(str(write_plugin("demo")))
    plugin = runtime.get_plugin("demo")
    unregister = runtime._unregister_plugin_components
    monkeypatch.setattr(
        plugin, "on_plugin_unloaded", AsyncMock(side_effect=RuntimeError("hook failed"))
    )
    monkeypatch.setattr(
        event_manager.get_event_manager(),
        "unregister_plugin_handlers",
        AsyncMock(side_effect=RuntimeError("handler failed")),
    )
    monkeypatch.setattr(
        runtime,
        "_unregister_plugin_components",
        AsyncMock(side_effect=RuntimeError("registry failed")),
    )
    assert not await runtime._rollback_failed_load(
        "demo", runtime.get_plugin_path("demo")
    )
    assert not adapter_manager.get_adapter_manager().get_all_adapters()
    assert not router_manager.get_router_manager().get_all_mounted_routers()
    assert not runtime.is_plugin_loaded("demo")
    assert config_manager.get_config_manager().get_config("demo") is None
    monkeypatch.setattr(runtime, "_unregister_plugin_components", unregister)
    await unregister("demo")


async def test_cleanup_attempts_other_resources_after_stop_exception(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配器 API 抛错也会尝试路由停止；恢复失败时仍保留原实例。"""
    assert await runtime.load_plugin(str(write_plugin("demo")))
    previous = runtime.get_plugin("demo")
    adapters = adapter_manager.get_adapter_manager()
    stop = adapters.stop_adapter
    monkeypatch.setattr(
        adapters, "stop_adapter", AsyncMock(side_effect=OSError("busy"))
    )
    start = runtime._start_plugin_runtime_resources
    monkeypatch.setattr(
        runtime,
        "_start_plugin_runtime_resources",
        AsyncMock(side_effect=OSError("offline")),
    )
    assert not await runtime.unload_plugin("demo")
    assert runtime.get_plugin("demo") is previous
    assert not router_manager.get_router_manager().get_all_mounted_routers()
    monkeypatch.setattr(adapters, "stop_adapter", stop)
    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", start)


async def test_chatter_references_are_removed(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移除旧插件的活跃 Chatter，保留其他插件的会话引用。"""
    from src.core.managers import chatter_manager

    assert await runtime.load_plugin(str(write_plugin("demo")))
    plugin = runtime.get_plugin("demo")
    chatters = {
        "old": SimpleNamespace(plugin=plugin),
        "other": SimpleNamespace(plugin=object()),
    }
    fake = SimpleNamespace(
        get_active_chatters=lambda: dict(chatters),
        unregister_active_chatter=chatters.pop,
    )
    monkeypatch.setattr(chatter_manager, "get_chatter_manager", lambda: fake)
    assert await runtime.unload_plugin("demo")
    assert set(chatters) == {"other"}


async def test_unknown_reload_and_missing_manifest(runtime: PluginManager) -> None:
    """未知插件和无效清单均明确失败，不破坏已运行的插件。"""
    assert not await runtime.reload_plugin("unknown")
    root = write_plugin("demo")
    assert await runtime.load_plugin(str(root))
    previous = runtime.get_plugin("demo")
    (root / "manifest.json").unlink()
    assert not await runtime.reload_plugin("demo")
    assert runtime.get_plugin("demo") is previous


async def test_dependency_install_can_be_disabled(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核心禁用自动安装时不会调用安装器，仍按既有策略尝试加载。"""
    from src.core import config
    from src.core.components.utils import DependencyInstaller

    root = write_plugin("demo")
    manifest_path = root / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["python_dependencies"] = ["example-package"]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(
        config,
        "get_core_config",
        lambda: SimpleNamespace(plugin_deps=SimpleNamespace(enabled=False)),
    )
    install = AsyncMock()
    monkeypatch.setattr(DependencyInstaller, "install_for_plugins", install)
    assert await runtime.load_plugin(str(root))
    install.assert_not_awaited()


async def test_cancelled_adapter_start_cleans_up(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配器启动被取消时执行停止并传播取消，不留下活跃实例。"""
    from src.core.components.loader import load_manifest

    root = write_plugin("demo")
    assert await runtime.load_plugin_from_manifest(
        str(root), await load_manifest(str(root)), start_runtime_resources=False
    )
    signature = "demo:adapter:adapter"
    cls = get_global_registry().get(signature)
    monkeypatch.setattr(cls, "start", AsyncMock(side_effect=asyncio.CancelledError))
    stop = AsyncMock()
    monkeypatch.setattr(cls, "stop", stop)
    with pytest.raises(asyncio.CancelledError):
        await adapter_manager.get_adapter_manager().start_adapter(signature)
    stop.assert_awaited_once()
    assert not adapter_manager.get_adapter_manager().get_all_adapters()


async def test_router_http_unmount_error_is_retryable(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 服务暂时不可用时保留路由；恢复后既有批量挂载入口可再次使用。"""
    assert await runtime.load_plugin(str(write_plugin("demo")))
    manager = router_manager.get_router_manager()
    signature = "demo:router:router"
    router = manager.get_mounted_router(signature)
    getter = router_manager.get_http_server
    monkeypatch.setattr(
        router_manager, "get_http_server", MagicMock(side_effect=OSError("offline"))
    )
    with pytest.raises(OSError, match="offline"):
        await manager.unmount_router(signature)
    assert manager.get_mounted_router(signature) is router
    monkeypatch.setattr(router_manager, "get_http_server", getter)
    await manager.unmount_plugin_routers("demo")
    mounted = await manager.mount_plugin_routers(runtime.get_plugin("demo"))
    assert len(mounted) == 1
    assert mounted[0] is not router


async def test_failed_dependency_blocks_dependent(runtime: PluginManager) -> None:
    """运行依赖加载失败时，依赖者不得以成功状态注册。"""
    write_plugin("first")
    write_plugin("dependent", ["first"])
    save_config("first", "[settings\n")
    results = await runtime.reconcile_plugins("plugins")
    assert results == {"first": False, "dependent": False}
    assert not runtime.is_plugin_loaded("dependent")
    save_config("first", "[settings]\nvalue = 2\n")
    assert all((await runtime.reconcile_plugins("plugins")).values())


@pytest.mark.parametrize("failure", ["constructor", "duplicate_component"])
async def test_activation_failure_cleans_registration(
    runtime: PluginManager, failure: str
) -> None:
    """构造失败和组件重复均报告加载失败，且不留下半注册组件。"""
    root = write_plugin("demo")
    source = (root / "plugin.py").read_text(encoding="utf-8")
    if failure == "constructor":
        source = source.replace(
            "configs = [Config]",
            '''configs = [Config]
    def __init__(self, config: Config | None = None) -> None:
        """模拟实例构造失败。"""
        raise RuntimeError("constructor failed")''',
        )
    else:
        source = source.replace(
            "[Action, Adapter, Router]", "[Action, Action, Adapter, Router]"
        )
    (root / "plugin.py").write_text(source, encoding="utf-8")
    assert not await runtime.load_plugin(str(root))
    assert not runtime.is_plugin_loaded("demo")
    assert not get_global_registry().get_by_plugin("demo")


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_candidate_keeps_its_own_resources(
    runtime: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    """重载后候选仍无法停止时，保留候选追踪，避免旧实例覆盖新连接。"""
    root = write_plugin("demo")
    archive = root.with_suffix(".mfp")
    with zipfile.ZipFile(archive, "w") as package:
        package.write(root / "manifest.json", "manifest.json")
        package.write(root / "plugin.py", "plugin.py")
    shutil.rmtree(root)
    assert await runtime.load_plugin(str(archive))
    previous = runtime.get_plugin("demo")
    old_directory = Path(runtime._archive_tmpdirs["demo"])
    start = runtime._start_plugin_runtime_resources

    async def fail_candidate(name: str) -> None:
        """创建无法停止的候选资源，并模拟启动失败或关闭取消。"""
        cls = get_global_registry().get(f"{name}:adapter:adapter")
        cls.start = AsyncMock(
            side_effect=asyncio.CancelledError if cancel else RuntimeError("failed")
        )
        cls.stop = AsyncMock(side_effect=RuntimeError("busy"))
        await start(name)

    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", fail_candidate)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await runtime.reload_plugin("demo")
    else:
        assert not await runtime.reload_plugin("demo")
    candidate = runtime.get_plugin("demo")
    assert candidate is not previous
    assert not old_directory.exists()
    adapter = adapter_manager.get_adapter_manager().get_adapter("demo:adapter:adapter")
    assert adapter.plugin is candidate
    monkeypatch.setattr(adapter, "stop", AsyncMock())
    monkeypatch.setattr(runtime, "_start_plugin_runtime_resources", start)
    assert await runtime.unload_plugin("demo")
