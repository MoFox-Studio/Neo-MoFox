"""配置原子保存、文件权限与并发修改保护的回归测试。"""

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.kernel.config import ConfigBase, Field, SectionBase, config_section, core


class ExampleConfig(ConfigBase):
    """用于触发配置签名更新的最小模型。"""

    @config_section("settings")
    class Settings(SectionBase):
        """自动补齐的配置节。"""

        value: int = Field(default=1)

    settings: Settings = Field(default_factory=Settings)


def test_atomic_replace_preserves_mode(tmp_path: Path) -> None:
    """替换已有配置时保留访问权限，首次创建也不残留临时文件。"""
    path = tmp_path / "config.toml"
    core._atomic_write_text(path, "value = 1\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
    original_mode = stat.S_IMODE(path.stat().st_mode)
    core._atomic_write_text(path, "value = 2\n", expected_text="value = 1\n")
    assert path.read_text(encoding="utf-8") == "value = 2\n"
    assert stat.S_IMODE(path.stat().st_mode) == original_mode
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_write_failure_keeps_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """磁盘写入或替换失败时，原配置保持完整，临时文件被回收。"""
    path = tmp_path / "config.toml"
    path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(core.os, operation, MagicMock(side_effect=OSError("busy")))
    with pytest.raises(OSError, match="busy"):
        core._atomic_write_text(path, "replacement")
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_edit_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """签名生成期间编辑器保存的新值不会被旧版本配置覆盖。"""
    path = tmp_path / "config.toml"
    path.write_text("[settings]\nvalue = 2\n", encoding="utf-8")
    render = core._render_toml_with_signature

    def edit_during_render(model: type[ConfigBase], data: dict) -> str:
        """模拟另一个进程在签名渲染期间完成保存。"""
        path.write_text("[settings]\nvalue = 3\n", encoding="utf-8")
        return render(model, data)

    monkeypatch.setattr(core, "_render_toml_with_signature", edit_during_render)
    with pytest.raises(RuntimeError, match="配置文件在加载期间发生变化"):
        ExampleConfig.load(path, auto_update=True)
    assert "value = 3" in path.read_text(encoding="utf-8")
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("different_owner", [True, False])
def test_posix_owner_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    different_owner: bool,
) -> None:
    """模拟 POSIX 所有者差异，避免用临时文件所有者替换共享配置所有者。"""
    path = tmp_path / "config.toml"
    path.write_text("original", encoding="utf-8")
    original = path.stat()
    proxy = MagicMock(wraps=os)
    proxy.name = "posix"
    proxy.stat.return_value = SimpleNamespace(
        st_uid=original.st_uid + int(different_owner),
        st_gid=original.st_gid,
    )
    proxy.chown = MagicMock()
    monkeypatch.setattr(core, "os", proxy)
    core._atomic_write_text(path, "replacement")
    assert path.read_text(encoding="utf-8") == "replacement"
    if different_owner:
        assert proxy.chown.call_count == 1
        assert proxy.chown.call_args.args[1:] == (original.st_uid, original.st_gid)
    else:
        proxy.chown.assert_not_called()


def test_temporary_file_already_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部清理同时移除了临时文件时，仍向上传递最初的保存错误。"""
    path = tmp_path / "config.toml"
    path.write_text("original", encoding="utf-8")

    def remove_before_replace(source: str, destination: Path) -> None:
        """模拟临时文件被另一清理器删除。"""
        Path(source).unlink()
        raise OSError("replace failed")

    monkeypatch.setattr(core.os, "replace", remove_before_replace)
    with pytest.raises(OSError, match="replace failed"):
        core._atomic_write_text(path, "replacement")
    assert path.read_text(encoding="utf-8") == "original"
