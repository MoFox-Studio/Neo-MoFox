"""skill_manager 工具测试。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, cast
from unittest.mock import AsyncMock, patch

import pytest

from plugins.skill_manager.config import SkillManagerConfig
from plugins.skill_manager.models import SkillEntry
from plugins.skill_manager.plugin import (
    SCRIPT_EXECUTION_DISABLED_MESSAGE,
    SCRIPT_EXECUTION_LEVEL_INVALID_MESSAGE,
    SCRIPT_EXECUTION_LOOKUP_FAILED_MESSAGE,
    SCRIPT_EXECUTION_UNIDENTIFIED_MESSAGE,
    SkillManagerPlugin,
)
from plugins.skill_manager.tools import SkillGetScriptTool
from src.app.plugin_system.types import PermissionLevel


@pytest.fixture(autouse=True)
def stub_permission_lookup() -> Iterator[AsyncMock]:
    """默认把调用者视为 OWNER，让非权限用例专注脚本执行本身。

    Yields:
        AsyncMock: 权限级别查询替身，权限相关用例可直接改写其返回值。
    """

    level_mock = AsyncMock(return_value=PermissionLevel.OWNER)
    with (
        patch("plugins.skill_manager.plugin.generate_person_id", return_value="person"),
        patch("plugins.skill_manager.plugin.get_user_permission_level", new=level_mock),
    ):
        yield level_mock


def _build_plugin(
    *,
    allow_script_execution: bool = True,
    powershell_bypass_execution_policy: bool = False,
    script_execution_permission_level: str = "owner",
) -> SkillManagerPlugin:
    """创建测试用插件实例。"""

    config = SkillManagerConfig()
    config.security.allow_script_execution = allow_script_execution
    config.security.powershell_bypass_execution_policy = (
        powershell_bypass_execution_policy
    )
    config.security.script_execution_permission_level = (
        script_execution_permission_level
    )
    return SkillManagerPlugin(config=config)


def _fake_message() -> SimpleNamespace:
    """构造带身份字段的触发消息替身。"""

    return SimpleNamespace(platform="test", sender_id="user_1", stream_id="stream")


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


def _prepare_script(
    tmp_path: Path,
    *,
    file_name: str,
    content: str,
    allow_script_execution: bool = True,
    powershell_bypass_execution_policy: bool = False,
    script_execution_permission_level: str = "owner",
    bind_message: bool = True,
) -> tuple[SkillGetScriptTool, Path]:
    """创建带单个脚本的 skill，并返回绑定好的工具实例与脚本路径。"""

    plugin = _build_plugin(
        allow_script_execution=allow_script_execution,
        powershell_bypass_execution_policy=powershell_bypass_execution_policy,
        script_execution_permission_level=script_execution_permission_level,
    )
    skill_root = tmp_path / "demo"
    script_dir = skill_root / "scripts"
    script_dir.mkdir(parents=True)
    _register_skill(plugin, skill_root)

    script_path = script_dir / file_name
    script_path.write_text(content, encoding="utf-8")

    tool = SkillGetScriptTool(plugin=cast(Any, plugin))
    if bind_message:
        tool._bind_runtime_context(stream_id="stream", message=cast(Any, _fake_message()))
    return tool, script_path


def _fake_process(
    *,
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> AsyncMock:
    """构造一个立即返回的子进程替身。"""

    process = AsyncMock()
    process.returncode = returncode
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    return process


# ==================== 脚本执行总开关 ====================


def test_script_tool_is_not_registered_when_execution_disabled() -> None:
    """未开启脚本执行时不应注册 get_script 组件。"""

    plugin = _build_plugin(allow_script_execution=False)

    assert SkillGetScriptTool not in plugin.get_components()


def test_script_tool_is_registered_when_execution_enabled() -> None:
    """显式开启脚本执行后才注册 get_script 组件。"""

    plugin = _build_plugin(allow_script_execution=True)

    assert SkillGetScriptTool in plugin.get_components()


def test_security_defaults_are_fail_closed() -> None:
    """默认配置下脚本执行关闭、策略绕过关闭、权限门为 OWNER。"""

    plugin = SkillManagerPlugin(config=SkillManagerConfig())

    assert plugin.allow_script_execution is False
    assert plugin.powershell_bypass_execution_policy is False
    assert plugin.script_execution_permission_level is PermissionLevel.OWNER


def test_security_defaults_apply_without_typed_config() -> None:
    """配置缺失时应回退到同一套 fail-closed 默认值。"""

    plugin = SkillManagerPlugin(config=None)

    assert plugin.allow_script_execution is False
    assert plugin.powershell_bypass_execution_policy is False
    assert plugin.script_execution_permission_level is PermissionLevel.OWNER


@pytest.mark.asyncio
async def test_get_script_refuses_when_execution_disabled(tmp_path: Path) -> None:
    """脚本执行未开启时，工具本身也要拒绝执行。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('should not run')\n",
        allow_script_execution=False,
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is False
    assert result == SCRIPT_EXECUTION_DISABLED_MESSAGE
    assert create_mock.await_count == 0


# ==================== 调用者权限门 ====================


@pytest.mark.asyncio
async def test_get_script_refuses_caller_below_required_level(
    tmp_path: Path,
    stub_permission_lookup: AsyncMock,
) -> None:
    """开关打开后仍需调用者权限达标，不能对所有聊天用户放开。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('should not run')\n",
    )
    stub_permission_lookup.return_value = PermissionLevel.USER

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is False
    assert "权限不足" in cast(str, result)
    assert "owner" in cast(str, result)
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_get_script_allows_caller_at_configured_level(
    tmp_path: Path,
    stub_permission_lookup: AsyncMock,
) -> None:
    """运维把门槛降到 operator 时，operator 调用者应放行。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
        script_execution_permission_level="operator",
    )
    stub_permission_lookup.return_value = PermissionLevel.OPERATOR

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_process(stdout=b"ok\n")),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is True
    assert "[stdout]\nok" in cast(str, result)
    assert create_mock.await_count == 1


@pytest.mark.asyncio
async def test_get_script_refuses_unidentifiable_caller(tmp_path: Path) -> None:
    """没有触发消息时无法归因到用户，必须拒绝而不是默认放行。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('should not run')\n",
        bind_message=False,
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is False
    assert result == SCRIPT_EXECUTION_UNIDENTIFIED_MESSAGE
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_get_script_refuses_on_invalid_permission_level_config(
    tmp_path: Path,
) -> None:
    """权限级别配置写错时应拒绝执行，而不是退化成无门。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('should not run')\n",
        script_execution_permission_level="root",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is False
    assert result == SCRIPT_EXECUTION_LEVEL_INVALID_MESSAGE
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_get_script_refuses_when_permission_lookup_fails(
    tmp_path: Path,
    stub_permission_lookup: AsyncMock,
) -> None:
    """权限查询本身出错时应 fail-closed。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('should not run')\n",
    )
    stub_permission_lookup.side_effect = RuntimeError("permission manager 未初始化")

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is False
    assert result == SCRIPT_EXECUTION_LOOKUP_FAILED_MESSAGE
    assert create_mock.await_count == 0


# ==================== Python 脚本子进程隔离 ====================


@pytest.mark.asyncio
async def test_get_script_executes_python_script(tmp_path: Path) -> None:
    """应继续支持 Python 脚本执行路径并回显 stdout。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="import sys\nprint(sys.argv[1])\n",
    )

    success, result = await tool.execute("demo", "scripts/echo.py", ["Neo-MoFox"])

    assert success is True
    assert "脚本已执行: echo.py" in cast(str, result)
    assert "[stdout]\nNeo-MoFox" in cast(str, result)


@pytest.mark.asyncio
async def test_get_script_runs_python_in_subprocess(tmp_path: Path) -> None:
    """Python 脚本必须以独立解释器进程执行，不得在 bot 进程内 runpy。"""

    tool, script_path = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_process(stdout=b"ok\n")),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/echo.py", ["--flag", "1"])

    assert success is True
    assert "[stdout]\nok" in cast(str, result)
    await_args = create_mock.await_args
    assert await_args is not None
    assert await_args.args == (sys.executable, str(script_path), "--flag", "1")
    assert await_args.kwargs["cwd"] == str(script_path.parent)


@pytest.mark.asyncio
async def test_get_script_reports_python_failure_exit_code(tmp_path: Path) -> None:
    """Python 脚本非零退出码应作为失败返回，并带上 stderr。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="fail.py",
        content="import sys\nsys.exit('boom')\n",
    )

    success, result = await tool.execute("demo", "scripts/fail.py")

    assert success is False
    assert "脚本执行退出码: 1" in cast(str, result)
    assert "[stderr]\nboom" in cast(str, result)


@pytest.mark.asyncio
async def test_get_script_forces_utf8_subprocess_output(tmp_path: Path) -> None:
    """子进程环境需强制 UTF-8 且关闭缓冲，避免中文乱码与超时丢输出。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('中文')\n",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_process()),
    ) as create_mock:
        await tool.execute("demo", "scripts/echo.py")

    await_args = create_mock.await_args
    assert await_args is not None
    env = await_args.kwargs["env"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUNBUFFERED"] == "1"


@pytest.mark.asyncio
async def test_get_script_python_output_keeps_non_ascii(tmp_path: Path) -> None:
    """真实子进程的中文输出应能原样回收。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="chinese.py",
        content="print('你好，MoFox')\n",
    )

    success, result = await tool.execute("demo", "scripts/chinese.py")

    assert success is True
    assert "[stdout]\n你好，MoFox" in cast(str, result)


# ==================== .bat/.cmd 命令注入防护 ====================


@pytest.mark.parametrize(
    "malicious_arg",
    [
        "a&whoami",
        "a|whoami",
        "a>out.txt",
        "a<in.txt",
        "a^b",
        "(a)",
        '"a"',
        "%USERNAME%",
        "!DELAYED!",
    ],
)
@pytest.mark.asyncio
async def test_get_script_rejects_cmd_metacharacter_arguments(
    tmp_path: Path,
    malicious_arg: str,
) -> None:
    """含 cmd.exe 元字符的参数必须在拼装命令行前被拒绝。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="run.bat",
        content="@echo off\r\necho %1\r\n",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute("demo", "scripts/run.bat", [malicious_arg])

    assert success is False
    assert ".bat/.cmd 参数只允许" in cast(str, result)
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_get_script_rejects_cmd_injection_from_string_args(tmp_path: Path) -> None:
    """字符串形式的参数经 shlex 拆分后仍需通过白名单校验。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="run.cmd",
        content="@echo off\r\n",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute(
            "demo",
            "scripts/run.cmd",
            "a&whoami>C:/Users/Public/p.txt",
        )

    assert success is False
    assert ".bat/.cmd 参数只允许" in cast(str, result)
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_get_script_accepts_safe_batch_arguments(tmp_path: Path) -> None:
    """白名单内的参数应正常拼进 cmd.exe 命令行。"""

    tool, script_path = _prepare_script(
        tmp_path,
        file_name="run.bat",
        content="@echo off\r\necho %1\r\n",
    )
    # 解释器校验要求 COMSPEC 是指向现有文件的绝对路径，这里造一个真实文件，
    # 避免测试依赖宿主机上真的存在 C:\Windows\system32\cmd.exe。
    fake_interpreter = tmp_path / "cmd.exe"
    fake_interpreter.write_bytes(b"")

    with (
        patch.dict(os.environ, {"COMSPEC": str(fake_interpreter)}),
        patch(
            "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_process(stdout=b"batch ok\r\n")),
        ) as create_mock,
    ):
        success, result = await tool.execute(
            "demo",
            "scripts/run.bat",
            ["--count=60", "C:/tmp/data.json"],
        )

    assert success is True
    assert "[stdout]\nbatch ok" in cast(str, result)
    await_args = create_mock.await_args
    assert await_args is not None
    assert await_args.args == (
        str(fake_interpreter),
        "/c",
        str(script_path),
        "--count=60",
        "C:/tmp/data.json",
    )


@pytest.mark.parametrize(
    ("comspec", "case"),
    [
        ("cmd.exe", "相对路径"),
        (r"C:\definitely\missing\cmd.exe", "绝对路径但文件不存在"),
    ],
)
@pytest.mark.asyncio
async def test_get_script_ignores_untrustworthy_comspec(
    tmp_path: Path,
    comspec: str,
    case: str,
) -> None:
    """COMSPEC 取值不可用时应回退到 PATH 查找，而不是把它交给 CreateProcess。

    相对路径会让 CreateProcess 走搜索顺序，可能命中工作目录（即 skill 目录）下的
    同名文件；指向不存在文件的绝对路径则会直接让执行失败。两种都必须被忽略。
    """

    tool, script_path = _prepare_script(
        tmp_path,
        file_name="run.bat",
        content="@echo off\r\n",
    )
    resolved_interpreter = tmp_path / "resolved-cmd.exe"
    resolved_interpreter.write_bytes(b"")

    with (
        patch.dict(os.environ, {"COMSPEC": comspec}),
        patch(
            "plugins.skill_manager.tools.shutil.which",
            return_value=str(resolved_interpreter),
        ),
        patch(
            "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_process(stdout=b"ok\r\n")),
        ) as create_mock,
    ):
        success, _ = await tool.execute("demo", "scripts/run.bat")

    assert success is True, f"{case} 时应回退成功"
    await_args = create_mock.await_args
    assert await_args is not None
    assert await_args.args == (
        str(resolved_interpreter.resolve()),
        "/c",
        str(script_path),
    )


@pytest.mark.asyncio
async def test_get_script_reports_missing_command_interpreter(tmp_path: Path) -> None:
    """找不到 cmd.exe 时应给出明确错误而不是拼一个裸命令名。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="run.bat",
        content="@echo off\r\n",
    )

    with (
        patch.dict(os.environ, {"COMSPEC": ""}),
        patch("plugins.skill_manager.tools.shutil.which", return_value=None),
    ):
        success, result = await tool.execute("demo", "scripts/run.bat")

    assert success is False
    assert result == "未找到可用的 cmd.exe 解释器"


# ==================== 全类型参数校验 ====================


@pytest.mark.parametrize("script_name", ["echo.py", "run.bat", "run.sh"])
@pytest.mark.asyncio
async def test_get_script_rejects_control_characters_for_every_script_type(
    tmp_path: Path,
    script_name: str,
) -> None:
    """控制字符在任何脚本类型的参数里都应被拒绝。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name=script_name,
        content="echo ok\n",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute(
            "demo",
            f"scripts/{script_name}",
            ["a\nwhoami"],
        )

    assert success is False
    assert "控制字符" in cast(str, result)
    assert create_mock.await_count == 0


@pytest.mark.parametrize(
    "malformed_args",
    ['--path "unterminated', "--name 'unterminated", 'a "b" "c'],
)
@pytest.mark.asyncio
async def test_get_script_rejects_unbalanced_quotes_in_string_args(
    tmp_path: Path,
    malformed_args: str,
) -> None:
    """引号不配对的字符串参数应返回拒绝结果，而不是让 ValueError 穿透。

    ``script_args`` 由 LLM 生成，引号不配对属可预期的非法输入。``shlex.split``
    对这类输入抛 ValueError，若不捕获就会突破 execute() 的 (bool, str) 返回契约。
    """

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
    )

    with patch(
        "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
        new=AsyncMock(),
    ) as create_mock:
        success, result = await tool.execute(
            "demo",
            "scripts/echo.py",
            malformed_args,
        )

    assert success is False
    assert "引号不配对" in cast(str, result)
    assert create_mock.await_count == 0


@pytest.mark.asyncio
async def test_get_script_rejects_non_string_argument_list(tmp_path: Path) -> None:
    """列表参数含非字符串元素时应直接拒绝。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
    )

    success, result = await tool.execute("demo", "scripts/echo.py", cast(Any, ["a", 1]))

    assert success is False
    assert result == "script_args 列表元素必须为字符串"


@pytest.mark.asyncio
async def test_get_script_rejects_unsupported_argument_type(tmp_path: Path) -> None:
    """非字符串/列表的参数类型应直接拒绝。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
    )

    success, result = await tool.execute("demo", "scripts/echo.py", cast(Any, {"a": 1}))

    assert success is False
    assert result == "script_args 必须是字符串或字符串列表"


# ==================== PowerShell 执行策略 ====================


@pytest.mark.asyncio
async def test_get_script_executes_powershell_without_policy_bypass(tmp_path: Path) -> None:
    """默认不得附加 -ExecutionPolicy Bypass。"""

    tool, script_path = _prepare_script(
        tmp_path,
        file_name="search.ps1",
        content='Write-Output "ok"\n',
    )

    with (
        patch(
            "plugins.skill_manager.tools.shutil.which",
            side_effect=lambda name: "powershell.exe" if name == "powershell" else None,
        ),
        patch(
            "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_process(stdout=b"pwsh ok\n")),
        ) as create_mock,
    ):
        success, result = await tool.execute("demo", "scripts/search.ps1", "--count 3")

    assert success is True
    assert "脚本已执行: search.ps1" in cast(str, result)
    assert "[stdout]\npwsh ok" in cast(str, result)
    await_args = create_mock.await_args
    assert await_args is not None
    assert await_args.args == (
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script_path),
        "--count",
        "3",
    )
    assert "Bypass" not in await_args.args


@pytest.mark.asyncio
async def test_get_script_applies_policy_bypass_only_when_opted_in(tmp_path: Path) -> None:
    """仅在运维显式开启开关后才绕过 PowerShell 执行策略。"""

    tool, script_path = _prepare_script(
        tmp_path,
        file_name="search.ps1",
        content='Write-Output "ok"\n',
        powershell_bypass_execution_policy=True,
    )

    with (
        patch(
            "plugins.skill_manager.tools.shutil.which",
            side_effect=lambda name: "pwsh.exe" if name == "pwsh" else None,
        ),
        patch(
            "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_process()),
        ) as create_mock,
    ):
        success, _ = await tool.execute("demo", "scripts/search.ps1")

    assert success is True
    await_args = create_mock.await_args
    assert await_args is not None
    assert await_args.args == (
        "pwsh.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    )


@pytest.mark.asyncio
async def test_get_script_reports_missing_powershell_runner(tmp_path: Path) -> None:
    """PowerShell 解释器缺失时应给出明确错误。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="search.ps1",
        content='Write-Output "ok"\n',
    )

    with patch("plugins.skill_manager.tools.shutil.which", return_value=None):
        success, result = await tool.execute("demo", "scripts/search.ps1")

    assert success is False
    assert result == "未找到可用的 PowerShell 解释器"


# ==================== shell 脚本 ====================


@pytest.mark.asyncio
async def test_get_script_reports_missing_shell_runner(tmp_path: Path) -> None:
    """shell 解释器缺失时应给出明确错误。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="run.sh",
        content="echo ok\n",
    )

    with patch("plugins.skill_manager.tools.shutil.which", return_value=None):
        success, result = await tool.execute("demo", "scripts/run.sh")

    assert success is False
    assert result == "未找到可用的 shell 解释器"


@pytest.mark.asyncio
async def test_get_script_passes_shell_args_without_reparsing(tmp_path: Path) -> None:
    """.sh 走 execve，参数原样传给 bash，不做二次 shell 解析。"""

    tool, script_path = _prepare_script(
        tmp_path,
        file_name="run.sh",
        content="echo ok\n",
    )

    with (
        patch(
            "plugins.skill_manager.tools.shutil.which",
            side_effect=lambda name: "/bin/bash" if name == "bash" else None,
        ),
        patch(
            "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_process()),
        ) as create_mock,
    ):
        success, _ = await tool.execute("demo", "scripts/run.sh", ["a&whoami"])

    assert success is True
    await_args = create_mock.await_args
    assert await_args is not None
    assert await_args.args == ("/bin/bash", str(script_path), "a&whoami")


# ==================== 路径边界与超时 ====================


@pytest.mark.asyncio
async def test_get_script_requires_injected_skill(tmp_path: Path) -> None:
    """未注入的 skill 不允许执行脚本。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
    )
    cast(Any, tool.plugin).injected_skills.clear()

    success, result = await tool.execute("demo", "scripts/echo.py")

    assert success is False
    assert result == "skill 'demo' 尚未注入，请先调用 get_skill"


@pytest.mark.asyncio
async def test_get_script_rejects_out_of_tree_location(tmp_path: Path) -> None:
    """越界路径必须被目录边界校验拦下。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="echo.py",
        content="print('ok')\n",
    )
    outside_script = tmp_path / "outside.py"
    outside_script.write_text("print('outside')\n", encoding="utf-8")

    success, result = await tool.execute("demo", "../outside.py")

    assert success is False
    assert "越界" in cast(str, result)


@pytest.mark.asyncio
async def test_get_script_returns_timeout_for_slow_script(tmp_path: Path) -> None:
    """脚本卡住时应主动超时并终止进程。"""

    tool, _ = _prepare_script(
        tmp_path,
        file_name="search.ps1",
        content='Write-Output "ok"\n',
    )

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
        patch("plugins.skill_manager.tools.SCRIPT_TIMEOUT_SECONDS", 0.01),
        patch("plugins.skill_manager.tools.SCRIPT_KILL_GRACE_SECONDS", 0.05),
        patch(
            "plugins.skill_manager.tools.shutil.which",
            side_effect=lambda name: "powershell.exe" if name == "powershell" else None,
        ),
        patch(
            "plugins.skill_manager.tools.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ),
    ):
        success, result = await asyncio.wait_for(
            tool.execute("demo", "scripts/search.ps1", "LLM 2"),
            timeout=0.2,
        )

    assert success is False
    assert "超时" in cast(str, result)
    assert process.returncode == -9
    assert process.communicate_calls == 1
