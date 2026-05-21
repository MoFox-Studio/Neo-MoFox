from __future__ import annotations

from src.core.components.loader import PluginDependencyResolver, PluginLoader, PluginManifest


def _manifest(name: str, version: str, plugin_dependencies: list[str] | None = None) -> PluginManifest:
    return PluginManifest(
        name=name,
        version=version,
        description="test",
        author="test",
        dependencies={"plugins": plugin_dependencies or [], "components": []},
    )


def test_dependency_resolver_accepts_versioned_plugin_refs() -> None:
    resolver = PluginDependencyResolver()
    resolver.add_plugin(_manifest("asr_adapter", "1.2.0"))
    resolver.add_plugin(_manifest("funasr_asr_provider", "1.0.0", ["asr_adapter>=1.0.0"]))

    assert resolver.resolve_load_order() == ["asr_adapter", "funasr_asr_provider"]


def test_dependency_resolver_is_deterministic_for_independent_plugins() -> None:
    resolver = PluginDependencyResolver()
    resolver.add_plugin(_manifest("z_plugin", "1.0.0"))
    resolver.add_plugin(_manifest("a_plugin", "1.0.0"))
    resolver.add_plugin(_manifest("m_plugin", "1.0.0"))

    assert resolver.resolve_load_order() == ["a_plugin", "m_plugin", "z_plugin"]


def test_plugin_loader_prunes_incompatible_versioned_dependencies() -> None:
    loader = PluginLoader()
    loadable = loader._prune_unloadable_plugins(
        {
            "asr_adapter": _manifest("asr_adapter", "0.9.0"),
            "funasr_asr_provider": _manifest("funasr_asr_provider", "1.0.0", ["asr_adapter>=1.0.0"]),
        }
    )

    assert "asr_adapter" in loadable
    assert "funasr_asr_provider" not in loadable
    assert "版本不满足" in loader.get_failed_plugins()["funasr_asr_provider"]
