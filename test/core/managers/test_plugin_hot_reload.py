"""文件监听、快照去重、失败重试和任务归属的回归测试。"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from watchfiles import Change

from src.core.managers import plugin_hot_reload as reload_module
from src.core.managers.plugin_hot_reload import PluginHotReloadService
from src.core.managers.plugin_manager import PluginManager
from src.kernel.concurrency import TaskManager


@pytest.fixture
def service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[PluginHotReloadService]:
    """提供受统一任务管理器追踪的服务和可控制的生命周期调用。"""
    manager = MagicMock(spec=PluginManager)
    manager.list_known_plugins.return_value = ["demo"]
    manager.reload_plugin = AsyncMock(return_value=True)
    manager.reconcile_plugins = AsyncMock(return_value={"demo": True})
    task_manager = TaskManager()
    monkeypatch.setattr(reload_module, "get_task_manager", lambda: task_manager)
    instance = PluginHotReloadService(
        manager,
        plugins_dir=str(tmp_path / "plugins"),
        config_dir=str(tmp_path / "config" / "plugins"),
    )
    instance.plugins_dir.mkdir()
    instance.config_dir.mkdir(parents=True)
    yield instance
    task_manager.shutdown_process_pool(wait=True)


def write_config(service: PluginHotReloadService, content: str = "value = 1") -> Path:
    """写入配置，保留真实文件内容以检查摘要和事件分类。"""
    path = service.config_dir / "demo" / "config.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_archive_catalog_uses_metadata(
    service: PluginHotReloadService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """轮询大型归档不反复读取全部内容，替换归档仍会改变目录快照。"""
    archive = service.plugins_dir / "demo.mfp"
    archive.write_bytes(b"first archive")
    monkeypatch.setattr(
        Path,
        "read_bytes",
        MagicMock(side_effect=AssertionError("archive content read")),
    )
    before = service._catalog_snapshot()
    assert before
    assert service._catalog_snapshot() == before
    archive.write_bytes(b"replaced archive with new content")
    assert service._catalog_snapshot() != before


async def test_managed_watcher_start_stop(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动幂等，监听任务为守护任务，停止后释放追踪状态。"""

    async def watch(
        *args: str, **kwargs: object
    ) -> AsyncIterator[set[tuple[Change, str]]]:
        """提供无事件的监听器，验证任务结束与重复停止。"""
        yield set()

    monkeypatch.setattr(reload_module, "awatch", watch)
    assert not service.running
    assert await service.start()
    task = service._task
    assert task is not None and task.daemon
    assert service.running
    assert await service.start()
    assert service._task is task
    await service.stop()
    await service.stop()
    assert not service.running


async def test_stop_cancels_watcher_after_timeout(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超时后通过 TaskInfo 取消任务，避免后台监听泄漏。"""
    import asyncio

    async def watch(
        *args: str, **kwargs: object
    ) -> AsyncIterator[set[tuple[Change, str]]]:
        """阻塞监听器以模拟驱动未响应停止事件。"""
        await asyncio.Event().wait()
        yield set()

    monkeypatch.setattr(reload_module, "awatch", watch)
    await service.start()
    task = service._task
    monkeypatch.setattr(
        reload_module.asyncio, "wait_for", AsyncMock(side_effect=TimeoutError)
    )
    await service.stop()
    assert task is not None and task.is_cancelled()


async def test_watcher_continues_after_batch_failure(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单批处理异常不会终止后续文件事件，停止事件会结束监听。"""

    async def watch(
        *args: str, **kwargs: object
    ) -> AsyncIterator[set[tuple[Change, str]]]:
        """提供异常、成功以及停止后的三个事件批次。"""
        yield set()
        yield set()
        service._stop_event.set()
        yield set()

    process = AsyncMock(side_effect=[RuntimeError("busy"), None])
    monkeypatch.setattr(reload_module, "awatch", watch)
    monkeypatch.setattr(service, "_process_changes", process)
    await service._watch()
    assert process.await_count == 2


def test_classify_only_config_and_catalog(
    service: PluginHotReloadService, tmp_path: Path
) -> None:
    """配置事件、插件增删和清单变化进入处理，源码和临时文件排除。"""
    config = write_config(service)
    events = {
        (Change.modified, str(config)),
        (Change.modified, str(config.parent / ".config.toml.tmp")),
        (Change.modified, str(service.plugins_dir / "demo" / "plugin.py")),
        (Change.modified, str(service.plugins_dir / "demo" / "resources" / "x")),
        (Change.modified, str(tmp_path / "outside.toml")),
    }
    assert service._classify_changes(events) == ({"demo"}, False)
    for path in [
        service.plugins_dir / "new",
        service.plugins_dir / "demo" / "manifest.json",
    ]:
        assert service._classify_changes({(Change.added, str(path))}) == (set(), True)
    assert service._classify_changes({(Change.modified, str(service.plugins_dir))}) == (
        set(),
        False,
    )


async def test_config_snapshot_deduplicates_same_content(
    service: PluginHotReloadService,
) -> None:
    """重复保存相同内容仅处理一次，内容变化后再次重载。"""
    path = write_config(service)
    await service._process_changes(set())
    await service._process_changes({(Change.modified, str(path))})
    assert service.plugin_manager.reload_plugin.await_count == 1
    path.write_text("value = 2", encoding="utf-8")
    await service._process_changes(set())
    assert service.plugin_manager.reload_plugin.await_count == 2


async def test_newer_config_during_reload_is_not_skipped(
    service: PluginHotReloadService,
) -> None:
    """钩子执行期间保存新版配置，下一批仍然读取到变化。"""
    path = write_config(service)

    async def reload(name: str) -> bool:
        """模拟插件已消费旧配置后用户又保存了新配置。"""
        path.write_text("value = 2", encoding="utf-8")
        return True

    service.plugin_manager.reload_plugin.side_effect = reload
    await service.reload_plugin("demo")
    service.plugin_manager.reload_plugin.side_effect = None
    await service._process_changes(set())
    assert service.plugin_manager.reload_plugin.await_count == 2


async def test_failed_reload_retries_without_another_save(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败批次保留待重试状态，并在冷却后自动重试。"""
    path = write_config(service)
    monkeypatch.setattr(reload_module.time, "monotonic", lambda: 10.0)
    service.plugin_manager.reload_plugin.return_value = False
    await service._process_changes(set())
    await service._process_changes(set())
    assert service.plugin_manager.reload_plugin.await_count == 1
    monkeypatch.setattr(reload_module.time, "monotonic", lambda: 12.0)
    service.plugin_manager.reload_plugin.return_value = True
    await service._process_changes(set())
    assert service.plugin_manager.reload_plugin.await_count == 2
    assert "demo" not in service._retry_at
    # 即使内容与上次成功版本相同，明确的失败状态也会触发重试。
    service._retry_at["demo"] = 0
    await service._process_changes({(Change.modified, str(path))})
    assert service.plugin_manager.reload_plugin.await_count == 3


async def test_exception_and_new_save_can_retry(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外部加载 API 抛错时保留重试，显式新保存可提前重新执行。"""
    path = write_config(service)
    service.plugin_manager.reload_plugin.side_effect = RuntimeError("busy")
    await service._process_changes(set())
    assert "demo" in service._retry_at
    service.plugin_manager.reload_plugin.side_effect = None
    await service._process_changes({(Change.modified, str(path))})
    assert "demo" not in service._retry_at


async def test_catalog_retries_and_keeps_config_changes(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """目录同步期间发生的配置修改继续处理，失败目录同步会冷却后重试。"""
    path = write_config(service)
    plugin = service.plugins_dir / "demo"
    plugin.mkdir()
    (plugin / "manifest.json").write_text("{}", encoding="utf-8")
    service.plugin_manager.reconcile_plugins.return_value = {"demo": False}
    monkeypatch.setattr(reload_module.time, "monotonic", lambda: 10.0)
    await service._process_changes(set())
    assert service._catalog == ()
    await service._process_changes(set())
    assert service.plugin_manager.reconcile_plugins.await_count == 1
    service.plugin_manager.reconcile_plugins.return_value = {"demo": True}
    monkeypatch.setattr(reload_module.time, "monotonic", lambda: 12.0)
    path.write_text("value = 3", encoding="utf-8")
    await service._process_changes(set())
    assert service.plugin_manager.reconcile_plugins.await_count == 2
    assert service.plugin_manager.reload_plugin.await_count == 2
    service.plugin_manager.list_known_plugins.return_value = []
    await service._process_changes(set())
    assert not service._config_snapshots


def test_hidden_host_and_transient_file_reads(
    service: PluginHotReloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """隐藏工作目录中的服务正常工作，临时文件和暂时消失文件被排除。"""
    assert service._is_relevant_path(Path("demo/config.toml"))
    path = write_config(service)
    original = Path.read_bytes

    def read_bytes(candidate: Path) -> bytes:
        """模拟文件替换的删除窗口。"""
        if candidate == path:
            raise FileNotFoundError(candidate)
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert service._config_snapshot("demo") == ()
    assert service._snapshot([path.parent / ".tmp"], service.config_dir) == ()


async def test_process_stop_does_not_start_plugin_reload(
    service: PluginHotReloadService,
) -> None:
    """关闭请求到达后不再创建新的插件事务。"""
    write_config(service)
    service._stop_event.set()
    await service._process_changes(set())
    service.plugin_manager.reload_plugin.assert_not_awaited()
