"""SkillManager 对外工具组件。"""

from __future__ import annotations

import asyncio
import io
import runpy
import shlex
import shutil
import sys
from contextlib import suppress, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Annotated, Any, cast

from src.core.components import BaseTool
from src.kernel.logger import get_logger


logger = get_logger("skill_manager.tool")
SUPPORTED_SCRIPT_SUFFIXES: tuple[str, ...] = (".py", ".ps1", ".bat", ".cmd", ".sh")
EXTERNAL_SCRIPT_TIMEOUT_SECONDS = 15.0
EXTERNAL_SCRIPT_KILL_GRACE_SECONDS = 3.0


class SkillGetTool(BaseTool):
    """读取并注入 skill 主文档。"""

    name: str = "get_skill"
    description: str = "按 skill 名称读取 SKILL.md 原文，并标记为已注入。"

    async def execute(
        self,
        name: Annotated[str, "skill 名称（来自 skill 列表）"],
    ) -> tuple[bool, str | dict]:
        """返回 SKILL.md 全文。"""

        resolved_name = name.strip()
        if not resolved_name:
            return False, "name 不能为空"

        plugin = cast(Any, self.plugin)
        entry = plugin.skills.get(resolved_name)
        if entry is None:
            return False, f"未找到 skill: {resolved_name}"

        content = plugin.skill_contents.get(resolved_name)
        if content is None:
            content = entry.skill_md_path.read_text(encoding="utf-8")
            plugin.skill_contents[resolved_name] = content

        plugin.injected_skills.add(resolved_name)
        return True, content


class SkillGetReferenceTool(BaseTool):
    """读取 skill 下的引用 markdown 文件。"""

    name: str = "get_reference"
    description: str = (
        "在已通过 get_skill(name) 注入对应 skill 后，"
        "按相对路径读取该 skill 目录中的 markdown 引用文件。"
    )

    async def execute(
        self,
        name: Annotated[str, "已注入的 skill 名称"],
        location: Annotated[str, "该 skill 目录内的 markdown 相对路径，例如 references/callable.md"],
    ) -> tuple[bool, str | dict]:
        """返回引用 markdown 原文。"""

        resolved_name = name.strip()
        if not resolved_name:
            return False, "name 不能为空"

        plugin = cast(Any, self.plugin)
        if resolved_name not in plugin.injected_skills:
            return False, f"skill '{resolved_name}' 尚未注入，请先调用 get_skill"

        entry = plugin.skills.get(resolved_name)
        if entry is None:
            return False, f"未找到 skill: {resolved_name}"

        resolved_path, error = plugin._resolve_skill_relative_path(
            skill_entry=entry,
            relative_path=location,
            required_suffix=".md",
        )
        if resolved_path is None:
            return False, error or "引用文件路径无效"

        return True, resolved_path.read_text(encoding="utf-8")


class SkillGetScriptTool(BaseTool):
    """直接执行 skill 下的脚本文件。"""

    name: str = "get_script"
    description: str = (
        "在已通过 get_skill(name) 注入对应 skill 后，"
        "按相对路径直接执行该 skill 目录下脚本文件（支持 .py/.ps1/.bat/.cmd/.sh）。"
        "可选通过 script_args 传入命令行参数。"
    )

    async def execute(
        self,
        name: Annotated[str, "已注入的 skill 名称"],
        location: Annotated[
            str,
            "该 skill 目录内脚本相对路径，例如 scripts/toolbox.py 或 scripts/search_arxiv.ps1",
        ],
        script_args: Annotated[
            list[str] | str,
            "可选脚本参数；支持字符串（如 '--check 60 --bonus 1'）或字符串列表（如 ['--check', '60']）",
        ] | None = None,
    ) -> tuple[bool, str | dict]:
        """返回脚本执行结果。"""

        resolved_name = name.strip()
        if not resolved_name:
            return False, "name 不能为空"

        plugin = cast(Any, self.plugin)
        if resolved_name not in plugin.injected_skills:
            return False, f"skill '{resolved_name}' 尚未注入，请先调用 get_skill"

        entry = plugin.skills.get(resolved_name)
        if entry is None:
            return False, f"未找到 skill: {resolved_name}"

        script_path, error = plugin._resolve_skill_relative_path(
            skill_entry=entry,
            relative_path=location,
            required_suffix=SUPPORTED_SCRIPT_SUFFIXES,
        )
        if script_path is None:
            return False, error or "脚本路径无效"

        normalized_args: list[str] = []
        if isinstance(script_args, str):
            normalized_args = shlex.split(script_args)
        elif isinstance(script_args, list):
            if not all(isinstance(item, str) for item in script_args):
                return False, "script_args 列表元素必须为字符串"
            normalized_args = script_args
        elif script_args is not None:
            return False, "script_args 必须是字符串或字符串列表"

        if script_path.suffix.lower() == ".py":
            return _execute_python_script(script_path, normalized_args)
        return await _execute_external_script(script_path, normalized_args)


def _execute_python_script(
    script_path: Path,
    normalized_args: list[str],
) -> tuple[bool, str | dict]:
    """以内嵌方式执行 Python 脚本。"""

    old_argv = sys.argv[:]
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        sys.argv = [str(script_path), *normalized_args]
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            runpy.run_path(str(script_path), run_name="__main__")

        captured_output = _compose_captured_output(stdout_buffer, stderr_buffer)
        if captured_output:
            return True, f"脚本已执行: {script_path.name}\n\n{captured_output}"
        return True, f"脚本已执行: {script_path.name}"
    except SystemExit as error:
        captured_output = _compose_captured_output(stdout_buffer, stderr_buffer)
        exit_code = error.code
        if exit_code in (None, 0):
            if captured_output:
                return True, f"脚本已执行: {script_path.name}\n\n{captured_output}"
            return True, f"脚本已执行: {script_path.name}"
        if captured_output:
            return False, f"脚本执行退出码: {exit_code}\n\n{captured_output}"
        return False, f"脚本执行退出码: {exit_code}"
    except Exception as error:
        logger.error(f"执行 skill 脚本失败: {error}")
        captured_output = _compose_captured_output(stdout_buffer, stderr_buffer)
        if captured_output:
            return False, f"执行脚本失败: {error}\n\n{captured_output}"
        return False, f"执行脚本失败: {error}"
    finally:
        sys.argv = old_argv


async def _execute_external_script(
    script_path: Path,
    normalized_args: list[str],
) -> tuple[bool, str | dict]:
    """通过子进程执行非 Python 脚本。"""

    command, error = _build_external_script_command(script_path, normalized_args)
    if command is None:
        return False, error or f"不支持的脚本类型: {script_path.suffix}"

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(script_path.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate_task = asyncio.create_task(process.communicate())
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.shield(communicate_task),
            timeout=EXTERNAL_SCRIPT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await _finalize_timed_out_process(
            process,
            communicate_task,
        )
        captured_output = _compose_output_text(
            stdout_bytes.decode("utf-8", errors="replace").strip(),
            stderr_bytes.decode("utf-8", errors="replace").strip(),
        )
        if captured_output:
            return False, (
                f"脚本执行超时（{EXTERNAL_SCRIPT_TIMEOUT_SECONDS}秒）\n\n{captured_output}"
            )
        return False, f"脚本执行超时（{EXTERNAL_SCRIPT_TIMEOUT_SECONDS}秒）"
    except Exception as error:
        logger.error(f"执行外部 skill 脚本失败: {error}")
        return False, f"执行脚本失败: {error}"

    captured_output = _compose_output_text(
        stdout_bytes.decode("utf-8", errors="replace").strip(),
        stderr_bytes.decode("utf-8", errors="replace").strip(),
    )
    if process.returncode == 0:
        if captured_output:
            return True, f"脚本已执行: {script_path.name}\n\n{captured_output}"
        return True, f"脚本已执行: {script_path.name}"
    if captured_output:
        return False, f"脚本执行退出码: {process.returncode}\n\n{captured_output}"
    return False, f"脚本执行退出码: {process.returncode}"


async def _finalize_timed_out_process(
    process: asyncio.subprocess.Process,
    communicate_task: asyncio.Task[tuple[bytes, bytes]],
) -> tuple[bytes, bytes]:
    """在外部脚本超时后收尾子进程并尽量回收输出。"""

    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=EXTERNAL_SCRIPT_KILL_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("外部脚本超时后进程未在宽限期内退出，放弃等待退出码")
    except Exception as error:
        logger.warning(f"等待超时脚本进程退出时出错: {error}")

    try:
        return await asyncio.wait_for(
            communicate_task,
            timeout=EXTERNAL_SCRIPT_KILL_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("外部脚本超时后 communicate 未在宽限期内结束，放弃收集残余输出")
    except Exception as error:
        logger.warning(f"收集超时脚本输出时出错: {error}")

    communicate_task.cancel()
    with suppress(asyncio.CancelledError):
        await communicate_task
    return b"", b""


def _build_external_script_command(
    script_path: Path,
    normalized_args: list[str],
) -> tuple[list[str] | None, str | None]:
    """构建外部脚本执行命令。"""

    suffix = script_path.suffix.lower()
    if suffix == ".ps1":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            return None, "未找到可用的 PowerShell 解释器"
        return [powershell, "-ExecutionPolicy", "Bypass", "-File", str(script_path), *normalized_args], None
    if suffix in {".bat", ".cmd"}:
        return ["cmd.exe", "/c", str(script_path), *normalized_args], None
    if suffix == ".sh":
        shell_runner = shutil.which("bash") or shutil.which("sh")
        if shell_runner is None:
            return None, "未找到可用的 shell 解释器"
        return [shell_runner, str(script_path), *normalized_args], None
    return None, f"不支持的脚本类型: {suffix}"


def _compose_captured_output(stdout_buffer: io.StringIO, stderr_buffer: io.StringIO) -> str:
    """拼接脚本执行期间捕获的标准输出与标准错误。"""

    stdout_text = stdout_buffer.getvalue().strip()
    stderr_text = stderr_buffer.getvalue().strip()
    return _compose_output_text(stdout_text, stderr_text)


def _compose_output_text(stdout_text: str, stderr_text: str) -> str:
    """拼接标准输出与标准错误文本。"""

    output_sections: list[str] = []
    if stdout_text:
        output_sections.append(f"[stdout]\n{stdout_text}")
    if stderr_text:
        output_sections.append(f"[stderr]\n{stderr_text}")
    return "\n\n".join(output_sections)
