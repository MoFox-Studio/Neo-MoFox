"""测试插件管理器注入 manifest 元数据的行为。

验证 ``PluginManager._inject_manifest_metadata``：
- 以 ``manifest.version`` 为准注入 ``plugin_version``；
- 插件类显式声明冗余元数据时触发 ``DeprecationWarning``；
- 未声明冗余元数据时不触发警告。
"""

from __future__ import annotations

import warnings

from src.core.components.base.plugin import BasePlugin
from src.core.components.loader import PluginManifest
from src.core.managers.plugin_manager import PluginManager


class _MinimalPlugin(BasePlugin):
    """未声明任何冗余元数据的最小插件。"""

    plugin_name = "minimal_plugin"

    def get_components(self) -> list[type]:
        return []


class _RedundantPlugin(BasePlugin):
    """显式声明了冗余元数据的插件。"""

    plugin_name = "redundant_plugin"
    plugin_version = "9.9.9"
    plugin_description = "旧版描述"
    plugin_author = "旧作者"

    def get_components(self) -> list[type]:
        return []


def _make_manifest(name: str, version: str = "2.0.0") -> PluginManifest:
    """构造测试用 manifest。"""
    return PluginManifest(
        name=name,
        version=version,
        description="manifest 描述",
        author="manifest 作者",
    )


def test_inject_version_from_manifest() -> None:
    """manifest.version 应注入为 plugin_version。"""
    manager = PluginManager()
    plugin = _MinimalPlugin()
    manifest = _make_manifest("minimal_plugin")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        manager._inject_manifest_metadata(plugin, manifest)

    assert plugin.plugin_version == "2.0.0"


def test_no_warning_without_redundant_metadata() -> None:
    """未声明冗余元数据时不应触发 DeprecationWarning。"""
    manager = PluginManager()
    plugin = _MinimalPlugin()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager._inject_manifest_metadata(plugin, _make_manifest("minimal_plugin"))

    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert deprecations == []


def test_deprecation_warning_on_redundant_metadata() -> None:
    """显式声明 plugin_version / plugin_description / plugin_author 应触发警告。"""
    manager = PluginManager()
    plugin = _RedundantPlugin()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager._inject_manifest_metadata(plugin, _make_manifest("redundant_plugin"))

    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    messages = " | ".join(str(w.message) for w in deprecations)

    assert "plugin_version" in messages
    assert "plugin_description" in messages
    assert "plugin_author" in messages
    # 即使声明了冗余版本，运行值仍以 manifest 为准
    assert plugin.plugin_version == "2.0.0"


def test_warning_when_declared_version_mismatches_manifest() -> None:
    """声明版本与 manifest 不一致时应触发警告。"""
    manager = PluginManager()
    plugin = _RedundantPlugin()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager._inject_manifest_metadata(plugin, _make_manifest("redundant_plugin", version="3.0.0"))

    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    messages = " | ".join(str(w.message) for w in deprecations)
    assert "9.9.9" in messages
    assert "3.0.0" in messages
    assert plugin.plugin_version == "3.0.0"
