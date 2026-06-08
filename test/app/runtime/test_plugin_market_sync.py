"""插件市场同步单元测试。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.app.runtime.plugin_market_sync import PluginMarketSyncService


def _make_config(
    *,
    base_url: str = "https://market.example",
    user_id: str = "mock-author",
    access_token: str = "mfox_token",
    auto_update_mfp: bool = True,
    auto_install_subscribed_missing: bool = True,
    strict_subscribed_list_mode: bool = False,
    auto_resolve_plugin_dependencies: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        plugin_market=SimpleNamespace(
            enabled=True,
            base_url=base_url,
            user_id=user_id,
            access_token=access_token,
            auto_update_mfp=auto_update_mfp,
            auto_install_subscribed_missing=auto_install_subscribed_missing,
            strict_subscribed_list_mode=strict_subscribed_list_mode,
            auto_resolve_plugin_dependencies=auto_resolve_plugin_dependencies,
            timeout=5.0,
            use_advanced_trust_env=False,
        ),
        advanced=SimpleNamespace(trust_env=False),
    )


def _build_mfp_bytes(
    plugin_name: str,
    version: str,
    *,
    dependencies: list[str] | None = None,
) -> bytes:
    payload = {
        "name": plugin_name,
        "version": version,
        "description": f"{plugin_name} test plugin",
        "author": "test",
        "dependencies": {"plugins": dependencies or [], "components": []},
        "entry_point": "plugin.py",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(payload, ensure_ascii=False))
        archive.writestr("plugin.py", "# test plugin\n")
    return buffer.getvalue()


def _write_local_mfp(path: Path, plugin_name: str, version: str) -> None:
    path.write_bytes(_build_mfp_bytes(plugin_name, version))


def _write_local_folder(path: Path, plugin_name: str, version: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "version": version,
                "description": "folder plugin",
                "author": "test",
                "dependencies": {"plugins": [], "components": []},
                "entry_point": "plugin.py",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (path / "plugin.py").write_text("# folder plugin\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_installs_subscriptions_and_dependency_plugins(tmp_path: Path) -> None:
    """订阅插件缺失时应安装，并在启用时补齐市场依赖插件。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/machine/authors/mock-author/subscriptions":
            return httpx.Response(200, json={"author_id": "mock-author", "items": [{"plugin_id": "main_plugin"}], "total": 1})
        if request.url.path == "/api/v1/plugins/main_plugin/dependencies":
            return httpx.Response(200, json={"plugin_id": "main_plugin", "items": [{"plugin_id": "dep_plugin", "exists_in_market": True}]})
        if request.url.path == "/api/v1/plugins/dep_plugin/dependencies":
            return httpx.Response(200, json={"plugin_id": "dep_plugin", "items": []})
        if request.url.path == "/api/v1/plugins/main_plugin/install":
            return httpx.Response(
                200,
                json={
                    "plugin": {"plugin_id": "main_plugin"},
                    "version": {
                        "version": "1.2.0",
                        "asset_name": "main_plugin-1.2.0.mfp",
                        "asset_download_url": "https://market.example/downloads/main_plugin-1.2.0.mfp",
                    },
                },
            )
        if request.url.path == "/api/v1/plugins/dep_plugin/install":
            return httpx.Response(
                200,
                json={
                    "plugin": {"plugin_id": "dep_plugin"},
                    "version": {
                        "version": "0.4.0",
                        "asset_name": "dep_plugin-0.4.0.mfp",
                        "asset_download_url": "https://market.example/downloads/dep_plugin-0.4.0.mfp",
                    },
                },
            )
        if request.url.path == "/downloads/main_plugin-1.2.0.mfp":
            return httpx.Response(200, content=_build_mfp_bytes("main_plugin", "1.2.0", dependencies=["dep_plugin>=0.4.0"]))
        if request.url.path == "/downloads/dep_plugin-0.4.0.mfp":
            return httpx.Response(200, content=_build_mfp_bytes("dep_plugin", "0.4.0"))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    service = PluginMarketSyncService(
        config=_make_config(),
        plugins_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    report = await service.sync()

    assert report.installed == 2
    assert report.updated == 0
    assert report.removed == 0
    assert report.resolved_dependencies == 1
    assert (tmp_path / "main_plugin-1.2.0.mfp").exists()
    assert (tmp_path / "dep_plugin-0.4.0.mfp").exists()


@pytest.mark.asyncio
async def test_sync_updates_installed_market_mfp_even_when_not_subscribed(tmp_path: Path) -> None:
    """本地已安装的市场 .mfp 即使未订阅，也应在启用自动更新时升级。"""

    _write_local_mfp(tmp_path / "main_plugin-1.0.0.mfp", "main_plugin", "1.0.0")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/machine/authors/mock-author/subscriptions":
            return httpx.Response(200, json={"author_id": "mock-author", "items": [], "total": 0})
        if request.url.path == "/api/v1/plugins/main_plugin":
            return httpx.Response(200, json={"plugin_id": "main_plugin", "status": "published"})
        if request.url.path == "/api/v1/plugins/main_plugin/dependencies":
            return httpx.Response(200, json={"plugin_id": "main_plugin", "items": []})
        if request.url.path == "/api/v1/plugins/main_plugin/install":
            return httpx.Response(
                200,
                json={
                    "plugin": {"plugin_id": "main_plugin"},
                    "version": {
                        "version": "1.2.0",
                        "asset_name": "main_plugin-1.2.0.mfp",
                        "asset_download_url": "https://market.example/downloads/main_plugin-1.2.0.mfp",
                    },
                },
            )
        if request.url.path == "/downloads/main_plugin-1.2.0.mfp":
            return httpx.Response(200, content=_build_mfp_bytes("main_plugin", "1.2.0"))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    service = PluginMarketSyncService(
        config=_make_config(),
        plugins_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    report = await service.sync()

    assert report.installed == 0
    assert report.updated == 1
    assert not (tmp_path / "main_plugin-1.0.0.mfp").exists()
    assert (tmp_path / "main_plugin-1.2.0.mfp").exists()


@pytest.mark.asyncio
async def test_strict_mode_only_removes_market_published_mfp_plugins(tmp_path: Path) -> None:
    """严格模式只删除已收录于市场的未订阅 mfp，不删除本地目录插件或无市场记录插件。"""

    _write_local_mfp(tmp_path / "market_plugin-1.0.0.mfp", "market_plugin", "1.0.0")
    _write_local_mfp(tmp_path / "orphan_plugin-1.0.0.mfp", "orphan_plugin", "1.0.0")
    _write_local_folder(tmp_path / "folder_plugin", "folder_plugin", "1.0.0")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/machine/authors/mock-author/subscriptions":
            return httpx.Response(200, json={"author_id": "mock-author", "items": [], "total": 0})
        if request.url.path == "/api/v1/plugins/market_plugin":
            return httpx.Response(200, json={"plugin_id": "market_plugin", "status": "published"})
        if request.url.path == "/api/v1/plugins/orphan_plugin":
            return httpx.Response(404, json={"error": "not found"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    service = PluginMarketSyncService(
        config=_make_config(
            auto_update_mfp=False,
            auto_install_subscribed_missing=False,
            strict_subscribed_list_mode=True,
            auto_resolve_plugin_dependencies=False,
        ),
        plugins_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    report = await service.sync()

    assert report.removed == 1
    assert not (tmp_path / "market_plugin-1.0.0.mfp").exists()
    assert (tmp_path / "orphan_plugin-1.0.0.mfp").exists()
    assert (tmp_path / "folder_plugin").exists()
