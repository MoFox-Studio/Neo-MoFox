"""skill_manager 工具测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from plugins.skill_manager.config import SkillManagerConfig
from plugins.skill_manager.models import SkillEntry
from plugins.skill_manager.plugin import SkillManagerPlugin
from plugins.skill_manager.tools import SkillGetScriptTool


def _build_plugin() -> SkillManagerPlugin:
    """创建测试用插件实例。"""

    return SkillManagerPlugin(config=SkillManagerConfig())


def _register_skill(plugin: SkillManagerPlugin, root_dir: Path, name: str = "demo") -> SkillEntry:
    """注册一个临时 skill 供测试执行。"""

    skill_md_path = root_dir / "SKILL.md"
    skill_md_path.write_text(
        "---\nname: demo\ndescription: demo skill\n---\n",
        encoding="utf-8",
    )
    entry = SkillEntry(
        name=name,
        description="demo skill",
        root_dir=root_dir,
        skill_md_path=skill_md_path,
        files=["SKILL.md"],
    )
    typed_plugin = cast(Any, plugin)
    typed_plugin.skills[name] = entry
    typed_plugin.injected_skills.add(name)
    return entry


@pytest.mark.asyncio
async def test_get_script_executes_python_script(tmp_path: Path) -> None:
    """应继续支持原有 Python 脚本执行路径。"""

    plugin = cast(Any, _build_plugin())
    tool = SkillGetScriptTool(plugin=cast(Any, plugin))
    skill_root = tmp_path / "demo"
    script_dir = skill_root / "scripts"
    script_dir.mkdir(parents=True)
    _register_skill(plugin, skill_root)

    script_path = script_dir / "echo.py"
    script_path.write_text(
        "import sys\nprint(sys.argv[1])\n",
        encoding="utf-8",
    )

    success, result = await tool.execute("demo", "scripts/echo.py", ["Neo-MoFox"])

    assert success is True
    assert "脚本已执行: echo.py" in cast(str, result)
    assert "[stdout]\nNeo-MoFox" in cast(str, result)


@pytest.mark.asyncio
async def test_get_script_executes_powershell_script_via_subprocess(tmp_path: Path) -> None:
    """应支持 PowerShell 脚本并通过外部进程执行。"""

    plugin = cast(Any, _build_plugin())
    tool = SkillGetScriptTool(plugin=cast(Any, plugin))
    skill_root = tmp_path / "demo"
    script_dir = skill_root / "scripts"
    script_dir.mkdir(parents=True)
    _register_skill(plugin, skill_root)

    script_path = script_dir / "search.ps1"
    script_path.write_text('Write-Output "ok"\n', encoding="utf-8")

    process = AsyncMock()
    process.returncode = 0
    process.communicate = AsyncMock(return_value=(b"pwsh ok\n", b""))

    with (
        patch("plugins.skill_manager.tools.shutil.which", side_effect=lambda name: "powershell.exe" if name == "powershell" else None),
        patch("plugins.skill_manager.tools.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as create_mock,
    ):
        success, result = await tool.execute("demo", "scripts/search.ps1", "--count 3")

    assert success is True
    assert "脚本已执行: search.ps1" in cast(str, result)
    assert "[stdout]\npwsh ok" in cast(str, result)
    assert create_mock.await_count == 1
    await_args = create_mock.await_args
    assert await_args is not None
    command_args = await_args.args
    assert command_args == (
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "--count",
        "3",
    )


@pytest.mark.asyncio
async def test_get_script_reports_missing_powershell_runner(tmp_path: Path) -> None:
    """PowerShell 解释器缺失时应给出明确错误。"""

    plugin = cast(Any, _build_plugin())
    tool = SkillGetScriptTool(plugin=cast(Any, plugin))
    skill_root = tmp_path / "demo"
    script_dir = skill_root / "scripts"
    script_dir.mkdir(parents=True)
    _register_skill(plugin, skill_root)

    script_path = script_dir / "search.ps1"
    script_path.write_text('Write-Output "ok"\n', encoding="utf-8")

    with patch("plugins.skill_manager.tools.shutil.which", return_value=None):
        success, result = await tool.execute("demo", "scripts/search.ps1")

    assert success is False
    assert result == "未找到可用的 PowerShell 解释器"


@pytest.mark.asyncio
async def test_get_script_returns_timeout_for_slow_external_script(tmp_path: Path) -> None:
    """外部脚本卡住时应主动超时并终止进程。"""

    plugin = cast(Any, _build_plugin())
    tool = SkillGetScriptTool(plugin=cast(Any, plugin))
    skill_root = tmp_path / "demo"
    script_dir = skill_root / "scripts"
    script_dir.mkdir(parents=True)
    _register_skill(plugin, skill_root)

    script_path = script_dir / "search.ps1"
    script_path.write_text('Write-Output "ok"\n', encoding="utf-8")

    class FakeProcess:
        """模拟第一次 communicate 超时后，二次 communicate 会挂住的进程。"""

        def __init__(self) -> None:
            self.returncode: int | None = None
            self.communicate_calls = 0
            self._killed = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            self.communicate_calls += 1
            if self.communicate_calls >= 2:
                await asyncio.Future()
            await self._killed.wait()
            return (b"partial", b"timeout")

        async def wait(self) -> int:
            await self._killed.wait()
            return -9

        def kill(self) -> None:
            self.returncode = -9
            self._killed.set()

    process = FakeProcess()

    with (
        patch("plugins.skill_manager.tools.EXTERNAL_SCRIPT_TIMEOUT_SECONDS", 0.01),
        patch("plugins.skill_manager.tools.EXTERNAL_SCRIPT_KILL_GRACE_SECONDS", 0.05),
        patch("plugins.skill_manager.tools.shutil.which", side_effect=lambda name: "powershell.exe" if name == "powershell" else None),
        patch("plugins.skill_manager.tools.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
    ):
        success, result = await asyncio.wait_for(
            tool.execute("demo", "scripts/search.ps1", "LLM 2"),
            timeout=0.2,
        )

    assert success is False
    assert "超时" in cast(str, result)
    assert process.returncode == -9
    assert process.communicate_calls == 1