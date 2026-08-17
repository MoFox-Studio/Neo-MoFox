"""插件 API 分模块版本号校验测试。

覆盖 ``PluginLoader._check_version_compatibility`` 的 AND 语义、
``_check_api_version_compatibility`` 的逐模块语义化版本校验、
``_check_core_version_compatibility`` 的核心版本校验，
以及 ``_prune_unloadable_plugins`` 在版本不兼容时的剔除行为。
"""

from __future__ import annotations

import pytest

from src.app.plugin_system.api import PLUGIN_API_VERSIONS
from src.core.components.loader import PluginLoader, PluginManifest


def _manifest(
    name: str = "test_plugin",
    *,
    api_version: str | dict[str, str] = "",
    min_core_version: str = "",
) -> PluginManifest:
    """构造测试用 PluginManifest。"""
    return PluginManifest(
        name=name,
        version="1.0.0",
        description="test",
        author="test",
        api_version=api_version,
        min_core_version=min_core_version,
    )


def _patch_api_version(monkeypatch: pytest.MonkeyPatch, api_name: str, version: str) -> None:
    """monkeypatch 单个 API 模块的 API_VERSION 并同步到聚合表。"""
    import src.app.plugin_system.api as api_pkg

    module = getattr(api_pkg, api_name)
    monkeypatch.setattr(module, "API_VERSION", version, raising=True)
    # 同步刷新聚合表快照（聚合表在导入时构造，校验函数每次 lazy import 读取最新值）
    monkeypatch.setitem(api_pkg.PLUGIN_API_VERSIONS, api_name, version)


# =============================================================================
# 基础：聚合表与常量
# =============================================================================


def test_plugin_api_versions_contains_all_modules() -> None:
    """PLUGIN_API_VERSIONS 应包含全部 *_api 模块，且版本为合法语义化版本号。"""
    expected = {
        "action_api",
        "adapter_api",
        "agent_api",
        "chat_api",
        "command_api",
        "config_api",
        "database_api",
        "event_api",
        "llm_api",
        "log_api",
        "media_api",
        "message_api",
        "permission_api",
        "person_api",
        "plugin_api",
        "prompt_api",
        "router_api",
        "send_api",
        "service_api",
        "storage_api",
        "stream_api",
        "tool_api",
    }
    assert set(PLUGIN_API_VERSIONS) == expected
    for name, ver in PLUGIN_API_VERSIONS.items():
        parts = ver.split(".")
        assert len(parts) == 3, f"{name} 版本应为三段式语义化版本，实际为 {ver}"
        assert all(p.isdigit() for p in parts), f"{name} 版本含非数字段: {ver}"


# =============================================================================
# 字符串形式 api_version
# =============================================================================


def test_string_api_version_full_compatible() -> None:
    """字符串形式，全部模块 1.0.0，应兼容。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(_manifest(api_version="1.0.0"))
    assert ok is True
    assert "兼容" in reason


def test_string_api_version_major_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """字符串形式，某模块 major 升级到 2.0.0，应被拒绝。"""
    _patch_api_version(monkeypatch, "llm_api", "2.0.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(_manifest(api_version="1.0.0"))
    assert ok is False
    assert "llm_api" in reason
    assert "主版本不匹配" in reason


def test_string_api_version_core_too_low(monkeypatch: pytest.MonkeyPatch) -> None:
    """字符串形式，核心版本低于插件要求，应被拒绝。"""
    _patch_api_version(monkeypatch, "send_api", "0.9.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(_manifest(api_version="1.0.0"))
    # 插件要求 1.0.0，核心提供 0.9.0 → 拒绝
    assert ok is False
    assert "send_api" in reason


# =============================================================================
# dict 形式 api_version
# =============================================================================


def test_dict_api_version_full_compatible() -> None:
    """dict 形式，声明模块均为 1.0.0，应兼容。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"llm_api": "1.0.0", "send_api": "1.0.0"})
    )
    assert ok is True
    assert "兼容" in reason


def test_dict_api_version_major_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """dict 形式，单模块 major 不匹配，应被拒绝。"""
    _patch_api_version(monkeypatch, "llm_api", "2.0.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"llm_api": "1.0.0"})
    )
    assert ok is False
    assert "llm_api" in reason


def test_dict_api_version_core_too_low(monkeypatch: pytest.MonkeyPatch) -> None:
    """dict 形式，核心版本低于插件要求，应被拒绝。"""
    _patch_api_version(monkeypatch, "llm_api", "1.2.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"llm_api": "1.5.0"})
    )
    assert ok is False
    assert "llm_api" in reason
    assert "低于" in reason


def test_dict_api_version_core_higher_warns_but_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dict 形式，核心版本高于插件要求，应警告但允许加载。"""
    _patch_api_version(monkeypatch, "llm_api", "1.5.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"llm_api": "1.0.0"})
    )
    assert ok is True
    assert "llm_api" in reason


def test_dict_api_version_unknown_key_rejected() -> None:
    """dict 形式，未知 API key 应被拒绝。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"foo_api": "1.0.0"})
    )
    assert ok is False
    assert "foo_api" in reason


def test_dict_api_version_mixed_known_and_unknown_rejected() -> None:
    """dict 形式，混合已知与未知 key，应因未知 key 被拒绝。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"llm_api": "1.0.0", "bar_api": "1.0.0"})
    )
    assert ok is False
    assert "bar_api" in reason


def test_empty_string_api_version_skips_api_check() -> None:
    """空字符串 api_version 不触发 API 校验（走 min_core 或警告分支）。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version="", min_core_version="1.0.0")
    )
    assert ok is True


def test_empty_dict_api_version_is_compatible() -> None:
    """空 dict 视为已声明但无校验对象 → 兼容。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(_manifest(api_version={}))
    assert ok is True


def test_invalid_version_format_rejected() -> None:
    """版本号格式无效应被拒绝。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version={"llm_api": "not-a-version"})
    )
    assert ok is False
    assert "格式无效" in reason


def test_string_equivalent_to_full_dict() -> None:
    """字符串 "1.0.0" 与全 20 模块 dict "1.0.0" 结果一致。"""
    loader = PluginLoader()
    full_dict = {name: "1.0.0" for name in PLUGIN_API_VERSIONS}
    ok_str, reason_str = loader._check_version_compatibility(_manifest(api_version="1.0.0"))
    ok_dict, reason_dict = loader._check_version_compatibility(
        _manifest(api_version=full_dict)
    )
    assert ok_str is ok_dict is True
    assert reason_str == reason_dict == "兼容"


# =============================================================================
# min_core_version 校验
# =============================================================================


def test_min_core_version_compatible() -> None:
    """min_core_version 满足时应兼容。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(min_core_version="1.0.0")
    )
    assert ok is True
    assert "兼容" in reason


def test_min_core_version_too_high_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """min_core_version 高于 CORE_VERSION 时应被拒绝。"""
    from src.core.components import loader as loader_mod

    monkeypatch.setattr(loader_mod, "CORE_VERSION", "1.0.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(min_core_version="2.0.0")
    )
    assert ok is False


def test_min_core_version_invalid_format_rejected() -> None:
    """min_core_version 格式无效应被拒绝。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(min_core_version="not-a-version")
    )
    assert ok is False
    assert "格式无效" in reason


# =============================================================================
# AND 语义：两者同时声明
# =============================================================================


def test_both_declared_both_pass() -> None:
    """两者同时声明且都满足 → 兼容。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version="1.0.0", min_core_version="1.0.0")
    )
    assert ok is True


def test_both_declared_api_fails_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    """两者同时声明，api_version 不满足 → 拒绝（即使 min_core 满足）。"""
    _patch_api_version(monkeypatch, "llm_api", "2.0.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version="1.0.0", min_core_version="1.0.0")
    )
    assert ok is False
    assert "llm_api" in reason


def test_both_declared_core_fails_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    """两者同时声明，min_core_version 不满足 → 拒绝（即使 api_version 满足）。"""
    from src.core.components import loader as loader_mod

    monkeypatch.setattr(loader_mod, "CORE_VERSION", "1.0.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version="1.0.0", min_core_version="2.0.0")
    )
    assert ok is False


def test_both_declared_both_fail_collects_both_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两者同时声明且都不满足 → 拒绝，并聚合两者原因。"""
    from src.core.components import loader as loader_mod

    _patch_api_version(monkeypatch, "llm_api", "2.0.0")
    monkeypatch.setattr(loader_mod, "CORE_VERSION", "1.0.0")
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(
        _manifest(api_version="1.0.0", min_core_version="2.0.0")
    )
    assert ok is False
    assert "llm_api" in reason
    assert "核心版本不兼容" in reason


def test_neither_declared_warns_but_loads() -> None:
    """两者均未声明 → 允许加载但发出警告。"""
    loader = PluginLoader()
    ok, reason = loader._check_version_compatibility(_manifest())
    assert ok is True
    assert "未声明" in reason


# =============================================================================
# _prune_unloadable_plugins 集成
# =============================================================================


def test_prune_removes_incompatible_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """版本不兼容的插件应被 _prune_unloadable_plugins 剔除并记入 _failed_plugins。"""
    _patch_api_version(monkeypatch, "llm_api", "2.0.0")
    loader = PluginLoader()
    manifests = {"bad": _manifest("bad", api_version="1.0.0")}
    loadable = loader._prune_unloadable_plugins(manifests)
    assert "bad" not in loadable
    assert "bad" in loader.get_failed_plugins()


def test_prune_keeps_compatible_plugin() -> None:
    """版本兼容的插件应保留在可加载集合中。"""
    loader = PluginLoader()
    manifests = {"good": _manifest("good", api_version="1.0.0")}
    loadable = loader._prune_unloadable_plugins(manifests)
    assert "good" in loadable
    assert "good" not in loader.get_failed_plugins()


def test_prune_cascade_removes_dependents() -> None:
    """不兼容插件被剔除后，依赖它的插件也应被递归剔除。"""
    loader = PluginLoader()
    manifests = {
        "base": _manifest("base", api_version=""),  # 未声明，允许加载
        "dependent": PluginManifest(
            name="dependent",
            version="1.0.0",
            description="test",
            author="test",
            api_version="",
            min_core_version="99.0.0",  # 不可能满足
            dependencies={"plugins": ["base"], "components": []},
        ),
    }
    loadable = loader._prune_unloadable_plugins(manifests)
    assert "base" in loadable
    assert "dependent" not in loadable
