"""Bot _install_plugin_deps 单元测试

测试 Bot.initialize() 中 Phase 3.5 依赖安装阶段的行为：
- 全局开关 enabled=False 时跳过
- 无依赖的插件直接跳过
- 依赖安装成功时保留全部插件
- 依赖安装失败且 dependencies_required=True 时从 load_order 移除
- 依赖安装失败且 dependencies_required=False 时保留但记录警告
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.runtime.bot import Bot
from src.core.components.loader import PluginManifest


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_manifest(
    name: str,
    python_dependencies: list[str] | None = None,
    dependencies_required: bool = True,
) -> PluginManifest:
    """构造测试用 PluginManifest。"""
    return PluginManifest(
        name=name,
        version="1.0.0",
        description="test",
        author="test",
        python_dependencies=python_dependencies or [],
        dependencies_required=dependencies_required,
        _source_path=f"/fake/{name}",
    )


def _make_bot_with_deps_config(enabled: bool = True, skip_if_satisfied: bool = True) -> Bot:
    """构造一个带有 plugin_deps 配置的 Bot 实例（不执行真正初始化）。"""
    bot = Bot.__new__(Bot)

    bot.load_order = []
    bot.manifests = {}
    bot.load_results = {}
    bot.logger = MagicMock()

    plugin_deps_cfg = MagicMock()
    plugin_deps_cfg.enabled = enabled
    plugin_deps_cfg.install_command = "uv pip install"
    plugin_deps_cfg.skip_if_satisfied = skip_if_satisfied

    bot.config = MagicMock()
    bot.config.plugin_deps = plugin_deps_cfg

    bot.ui = MagicMock()

    return bot


# ---------------------------------------------------------------------------
# 测试：全局开关关闭
# ---------------------------------------------------------------------------


class TestInstallPluginDepsDisabled:
    """测试 plugin_deps.enabled=False 时的行为"""

    @pytest.mark.asyncio
    async def test_skips_all_when_disabled(self) -> None:
        """enabled=False 时不应调用 DependencyInstaller 并更新状态为已跳过"""
        bot = _make_bot_with_deps_config(enabled=False)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["requests"])

        with patch("src.core.components.utils.DependencyInstaller") as mock_installer_cls:
            await bot._install_plugin_deps()

        mock_installer_cls.assert_not_called()
        bot.ui.update_phase_status.assert_called_once_with("依赖安装", "已跳过（已禁用）")

    @pytest.mark.asyncio
    async def test_load_order_unchanged_when_disabled(self) -> None:
        """enabled=False 时 load_order 不应被修改"""
        bot = _make_bot_with_deps_config(enabled=False)
        bot.load_order = ["plugin_a", "plugin_b"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["requests"])
        bot.manifests["plugin_b"] = _make_manifest("plugin_b", [])

        await bot._install_plugin_deps()

        assert bot.load_order == ["plugin_a", "plugin_b"]


# ---------------------------------------------------------------------------
# 测试：无依赖时直接跳过
# ---------------------------------------------------------------------------


class TestInstallPluginDepsNoDeps:
    """测试所有插件均无依赖时的行为"""

    @pytest.mark.asyncio
    async def test_no_deps_updates_status_without_installing(self) -> None:
        """所有插件无依赖时应更新状态为无需安装"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", [])

        with patch("src.core.components.utils.DependencyInstaller") as mock_installer_cls:
            await bot._install_plugin_deps()

        mock_installer_cls.assert_not_called()
        bot.ui.update_phase_status.assert_called_with("依赖安装", "无需安装")


# ---------------------------------------------------------------------------
# 测试：安装成功
# ---------------------------------------------------------------------------


class TestInstallPluginDepsSuccess:
    """测试依赖安装成功时的行为"""

    @pytest.mark.asyncio
    async def test_load_order_unchanged_when_install_succeeds(self) -> None:
        """安装成功时 load_order 不应被修改"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a", "plugin_b"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["requests"], dependencies_required=True)
        bot.manifests["plugin_b"] = _make_manifest("plugin_b", ["httpx"], dependencies_required=True)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": True, "plugin_b": True}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        assert "plugin_a" in bot.load_order
        assert "plugin_b" in bot.load_order

    @pytest.mark.asyncio
    async def test_load_results_not_set_on_success(self) -> None:
        """安装成功时 load_results 不应预先设置为 False"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["requests"])

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": True}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        assert "plugin_a" not in bot.load_results


# ---------------------------------------------------------------------------
# 测试：安装失败 + dependencies_required=True
# ---------------------------------------------------------------------------


class TestInstallPluginDepsFailRequired:
    """测试依赖安装失败且 dependencies_required=True 时的行为"""

    @pytest.mark.asyncio
    async def test_plugin_removed_from_load_order(self) -> None:
        """dependencies_required=True 时安装失败应将插件从 load_order 中移除"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a", "plugin_b"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["bad_pkg"], dependencies_required=True)
        bot.manifests["plugin_b"] = _make_manifest("plugin_b", ["requests"], dependencies_required=True)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": False, "plugin_b": True}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        assert "plugin_a" not in bot.load_order
        assert "plugin_b" in bot.load_order

    @pytest.mark.asyncio
    async def test_load_results_set_false_for_failed_required(self) -> None:
        """dependencies_required=True 安装失败时应在 load_results 中标记 False"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["bad_pkg"], dependencies_required=True)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": False}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        assert bot.load_results.get("plugin_a") is False

    @pytest.mark.asyncio
    async def test_warning_logged_for_failed_required(self) -> None:
        """dependencies_required=True 安装失败时应记录 warning 日志"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["bad_pkg"], dependencies_required=True)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": False}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        bot.logger.warning.assert_called()


# ---------------------------------------------------------------------------
# 测试：安装失败 + dependencies_required=False
# ---------------------------------------------------------------------------


class TestInstallPluginDepsFailOptional:
    """测试依赖安装失败且 dependencies_required=False 时的行为"""

    @pytest.mark.asyncio
    async def test_plugin_kept_in_load_order(self) -> None:
        """dependencies_required=False 时安装失败不应将插件从 load_order 中移除"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["bad_pkg"], dependencies_required=False)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": False}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        assert "plugin_a" in bot.load_order

    @pytest.mark.asyncio
    async def test_load_results_not_set_for_optional_failure(self) -> None:
        """dependencies_required=False 安装失败时不应在 load_results 中预设为 False"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["bad_pkg"], dependencies_required=False)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": False}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        # dependencies_required=False 的插件不应被提前打入 load_results
        assert "plugin_a" not in bot.load_results

    @pytest.mark.asyncio
    async def test_warning_logged_for_optional_failure(self) -> None:
        """dependencies_required=False 安装失败时应记录 warning 日志"""
        bot = _make_bot_with_deps_config(enabled=True)
        bot.load_order = ["plugin_a"]
        bot.manifests["plugin_a"] = _make_manifest("plugin_a", ["bad_pkg"], dependencies_required=False)

        mock_installer = AsyncMock()
        mock_installer.install_for_plugins = AsyncMock(
            return_value={"plugin_a": False}
        )

        with patch("src.core.components.utils.DependencyInstaller", return_value=mock_installer):
            await bot._install_plugin_deps()

        bot.logger.warning.assert_called()
