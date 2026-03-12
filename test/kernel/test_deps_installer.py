"""Deps 模块单元测试

测试 DependencyInstaller 的包检测与安装功能。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.utils import DependencyInstaller, PluginDepSpec


# ---------------------------------------------------------------------------
# PluginDepSpec
# ---------------------------------------------------------------------------


class TestPluginDepSpec:
    """测试 PluginDepSpec 数据类"""

    def test_default_required_is_true(self) -> None:
        """默认 required 应为 True"""
        spec = PluginDepSpec(plugin_name="my_plugin", packages=["requests"])
        assert spec.required is True

    def test_set_required_false(self) -> None:
        """可以将 required 设为 False"""
        spec = PluginDepSpec(plugin_name="my_plugin", packages=[], required=False)
        assert spec.required is False

    def test_empty_packages_by_default(self) -> None:
        """默认 packages 应为空列表"""
        spec = PluginDepSpec(plugin_name="x")
        assert spec.packages == []


# ---------------------------------------------------------------------------
# DependencyInstaller.check_missing
# ---------------------------------------------------------------------------


class TestCheckMissing:
    """测试 check_missing 方法"""

    def test_empty_requirements_returns_empty(self) -> None:
        """空输入直接返回空列表"""
        installer = DependencyInstaller()
        assert installer.check_missing([]) == []

    def test_installed_package_without_version_returns_empty(self) -> None:
        """已安装且无版本约束的包不应出现在缺失列表"""
        installer = DependencyInstaller()
        # packaging 本身一定安装了
        result = installer.check_missing(["packaging"])
        assert "packaging" not in result

    def test_nonexistent_package_is_missing(self) -> None:
        """不存在的包应出现在缺失列表"""
        installer = DependencyInstaller()
        result = installer.check_missing(["_this_package_should_never_exist_xyz123"])
        assert "_this_package_should_never_exist_xyz123" in result

    def test_invalid_requirement_format_is_skipped(self) -> None:
        """格式错误的 requirement 不应抛出异常（返回为缺失）"""
        installer = DependencyInstaller()
        # 格式无效，_is_satisfied 会返回 False，所以出现在缺失列表
        result = installer.check_missing(["!!!invalid!!!"])
        assert "!!!invalid!!!" in result

    def test_installed_package_with_satisfied_version(self) -> None:
        """已安装且版本满足约束的包不应出现在缺失列表"""
        installer = DependencyInstaller()
        import importlib.metadata as meta

        version = meta.version("packaging")
        # 要求 >=0.1 必然满足
        result = installer.check_missing([f"packaging>={version}"])
        assert not result  # 应为空

    def test_package_with_impossible_version_is_missing(self) -> None:
        """版本约束无法满足时应出现在缺失列表"""
        installer = DependencyInstaller()
        # packaging 肯定未达到 9999.0
        result = installer.check_missing(["packaging>=9999.0"])
        assert any("packaging" in r for r in result)


# ---------------------------------------------------------------------------
# DependencyInstaller._is_satisfied
# ---------------------------------------------------------------------------


class TestIsSatisfied:
    """测试 _is_satisfied 静态方法"""

    def test_returns_true_for_known_installed_package(self) -> None:
        result = DependencyInstaller._is_satisfied("packaging")
        assert result is True

    def test_returns_false_for_missing_package(self) -> None:
        result = DependencyInstaller._is_satisfied("_nonexistent_xyz123")
        assert result is False

    def test_returns_false_for_invalid_requirement(self) -> None:
        result = DependencyInstaller._is_satisfied("!!!bad format!!!")
        assert result is False


# ---------------------------------------------------------------------------
# DependencyInstaller.install
# ---------------------------------------------------------------------------


class TestInstall:
    """测试 install 方法"""

    @pytest.mark.asyncio
    async def test_empty_packages_returns_true_without_subprocess(self) -> None:
        """空包列表直接返回 True，不调用 subprocess"""
        installer = DependencyInstaller()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            result = await installer.install([], command="uv pip install")
        assert result is True
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_install_returns_true(self) -> None:
        """subprocess 返回退出码 0 时，install 应返回 True"""
        installer = DependencyInstaller()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Successfully installed x\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await installer.install(["some-package"], command="uv pip install")

        assert result is True

    @pytest.mark.asyncio
    async def test_failed_install_returns_false(self) -> None:
        """subprocess 返回非 0 退出码时，install 应返回 False"""
        installer = DependencyInstaller()

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"ERROR: not found\n"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await installer.install(["no-such-package"], command="uv pip install")

        assert result is False

    @pytest.mark.asyncio
    async def test_command_not_found_returns_false(self) -> None:
        """安装命令不存在时，install 应返回 False（不抛出）"""
        installer = DependencyInstaller()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await installer.install(["requests"], command="nonexistent_cmd")

        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_command_format_returns_false(self) -> None:
        """命令格式解析失败时，install 应返回 False"""
        installer = DependencyInstaller()
        # shlex.split 对未闭合引号会抛出 ValueError
        result = await installer.install(["requests"], command="pip 'unclosed")
        assert result is False

    @pytest.mark.asyncio
    async def test_subprocess_called_with_correct_args(self) -> None:
        """验证 subprocess 调用时正确传入了命令和包名"""
        installer = DependencyInstaller()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await installer.install(["requests>=2.28", "httpx"], command="uv pip install")

        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert call_args == ("uv", "pip", "install", "requests>=2.28", "httpx")


# ---------------------------------------------------------------------------
# DependencyInstaller.install_for_plugins
# ---------------------------------------------------------------------------


class TestInstallForPlugins:
    """测试 install_for_plugins 方法"""

    @pytest.mark.asyncio
    async def test_empty_specs_returns_empty_dict(self) -> None:
        installer = DependencyInstaller()
        result = await installer.install_for_plugins([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_spec_with_no_packages_marked_true(self) -> None:
        """无依赖的插件应直接标记为 True"""
        installer = DependencyInstaller()
        specs = [PluginDepSpec("plugin_a", packages=[])]
        result = await installer.install_for_plugins(specs)
        assert result == {"plugin_a": True}

    @pytest.mark.asyncio
    async def test_deduplication_of_packages(self) -> None:
        """多个插件声明相同包时，只应安装一次"""
        installer = DependencyInstaller()

        captured_packages: list[list[str]] = []

        async def fake_install(pkgs: list[str], command: str = "uv pip install") -> bool:
            captured_packages.append(list(pkgs))
            return True

        installer.install = fake_install  # type: ignore[method-assign]

        specs = [
            PluginDepSpec("plugin_a", ["requests>=2.28", "httpx"]),
            PluginDepSpec("plugin_b", ["requests>=2.28", "pydantic"]),
        ]
        with patch.object(installer, "check_missing", return_value=["requests>=2.28", "httpx", "pydantic"]):
            await installer.install_for_plugins(specs, skip_if_satisfied=True)

        # install 被调用一次，且包列表去重
        assert len(captured_packages) == 1
        assert len(captured_packages[0]) == 3  # 去重后 3 个不同包

    @pytest.mark.asyncio
    async def test_all_plugins_true_when_install_succeeds(self) -> None:
        """安装成功时，所有带依赖的插件均标记为 True"""
        installer = DependencyInstaller()

        specs = [
            PluginDepSpec("plugin_a", ["requests"]),
            PluginDepSpec("plugin_b", ["httpx"]),
        ]

        with patch.object(installer, "check_missing", return_value=["requests", "httpx"]), \
             patch.object(installer, "install", new=AsyncMock(return_value=True)):
            results = await installer.install_for_plugins(specs, skip_if_satisfied=True)

        assert results == {"plugin_a": True, "plugin_b": True}

    @pytest.mark.asyncio
    async def test_all_plugins_false_when_install_fails(self) -> None:
        """安装失败时，所有带依赖的插件均标记为 False"""
        installer = DependencyInstaller()

        specs = [
            PluginDepSpec("plugin_a", ["requests"], required=True),
            PluginDepSpec("plugin_b", ["httpx"], required=False),
        ]

        with patch.object(installer, "check_missing", return_value=["requests", "httpx"]), \
             patch.object(installer, "install", new=AsyncMock(return_value=False)):
            results = await installer.install_for_plugins(specs, skip_if_satisfied=True)

        assert results["plugin_a"] is False
        assert results["plugin_b"] is False

    @pytest.mark.asyncio
    async def test_skip_if_satisfied_true_calls_check_missing(self) -> None:
        """skip_if_satisfied=True 时应调用 check_missing 过滤已满足的包"""
        installer = DependencyInstaller()
        specs = [PluginDepSpec("plugin_a", ["packaging"])]

        with patch.object(installer, "check_missing", return_value=[]) as mock_check:
            results = await installer.install_for_plugins(specs, skip_if_satisfied=True)

        mock_check.assert_called_once()
        # check_missing 返回空列表（全部满足），installer.install 不应被调用
        assert results["plugin_a"] is True

    @pytest.mark.asyncio
    async def test_skip_if_satisfied_false_skips_check_missing(self) -> None:
        """skip_if_satisfied=False 时不应调用 check_missing，直接安装全部"""
        installer = DependencyInstaller()
        specs = [PluginDepSpec("plugin_a", ["packaging"])]

        with patch.object(installer, "check_missing") as mock_check, \
             patch.object(installer, "install", new=AsyncMock(return_value=True)):
            await installer.install_for_plugins(specs, skip_if_satisfied=False)

        mock_check.assert_not_called()
