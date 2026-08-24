"""SkillManager 对外工具组件。"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any, cast

from src.core.components import BaseTool
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

logger = get_logger("skill_manager.tool")
SUPPORTED_SCRIPT_SUFFIXES: tuple[str, ...] = (".py", ".ps1", ".bat", ".cmd", ".sh")
SCRIPT_TIMEOUT_SECONDS = 15.0
SCRIPT_KILL_GRACE_SECONDS = 3.0

# .bat/.cmd 交给 cmd.exe 解释执行，而 Windows 上 subprocess 只能把参数列表交给
# ``subprocess.list2cmdline`` 拼成单条命令行；该函数只处理空白、反斜杠与引号，
# 完全不转义 cmd.exe 元字符（``& | < > ^ ( ) " % !``）。参数中一旦出现这些字符就会
# 逃逸成独立命令，因此拼装前必须用字符白名单挡住。
_CMD_SAFE_ARGUMENT_RE = re.compile(r"[A-Za-z0-9._:/\\@=-]+")
_CMD_SAFE_ARGUMENT_HINT = "字母、数字与 . _ : / \\ @ = -"

# 控制字符（含换行、回车、NUL）在任何解释器的参数里都没有正当用途，却能在命令行拼装、
# 日志与脚本自身的参数解析上制造歧义，因此对所有脚本类型统一拒绝。
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")


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
    """直接执行 skill 下的脚本文件。

    该工具以 bot 进程权限执行脚本，而工具调用层本身没有权限模型，因此有两道门：
    插件只在配置开启 ``[security].allow_script_execution`` 时才注册它，且每次调用都会
    重新走一遍 ``authorize_script_execution``（覆盖配置热更新与调用者权限级别）。
    """

    name: str = "get_script"
    description: str = (
        "在已通过 get_skill(name) 注入对应 skill 后，"
        "按相对路径以独立子进程执行该 skill 目录下脚本文件（支持 .py/.ps1/.bat/.cmd/.sh）。"
        "可选通过 script_args 传入命令行参数；"
        f"其中 .bat/.cmd 的参数只接受{_CMD_SAFE_ARGUMENT_HINT} 这些字符。"
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

        plugin = cast(Any, self.plugin)
        authorized, refusal = await plugin.authorize_script_execution(
            self.trigger_message
        )
        if not authorized:
            return False, refusal or "已拒绝执行 skill 脚本"

        resolved_name = name.strip()
        if not resolved_name:
            return False, "name 不能为空"

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

        normalized_args, error = _normalize_script_args(script_args)
        if normalized_args is None:
            return False, error or "script_args 无效"

        return await _execute_script_in_subprocess(
            script_path,
            normalized_args,
            powershell_bypass_execution_policy=bool(
                plugin.powershell_bypass_execution_policy
            ),
        )


def _normalize_script_args(
    script_args: list[str] | str | None,
) -> tuple[list[str] | None, str | None]:
    """把 LLM 传入的脚本参数归一化为字符串列表。

    Args:
        script_args: 原始参数，允许字符串、字符串列表或 None。

    Returns:
        tuple[list[str] | None, str | None]: (归一化后的参数, 错误信息)；
        校验失败时第一项为 None。
    """

    if script_args is None:
        return [], None
    if isinstance(script_args, str):
        normalized_args = shlex.split(script_args)
    elif isinstance(script_args, list):
        if not all(isinstance(item, str) for item in script_args):
            return None, "script_args 列表元素必须为字符串"
        normalized_args = list(script_args)
    else:
        return None, "script_args 必须是字符串或字符串列表"

    for argument in normalized_args:
        if _CONTROL_CHARACTER_RE.search(argument):
            return None, f"script_args 不能包含控制字符（含换行符）: {argument!r}"
    return normalized_args, None


async def _execute_script_in_subprocess(
    script_path: Path,
    normalized_args: list[str],
    *,
    powershell_bypass_execution_policy: bool,
) -> tuple[bool, str | dict]:
    """在独立子进程中执行 skill 脚本并回收输出。

    所有脚本类型（含 ``.py``）都走子进程：既统一拿到超时保护，也避免脚本代码与
    bot 主进程共享内存空间（配置对象、模型 API key、权限判定等全局状态）。

    Args:
        script_path: 已通过目录边界校验的脚本绝对路径。
        normalized_args: 归一化后的脚本参数。
        powershell_bypass_execution_policy: 是否允许 .ps1 绕过 PowerShell 执行策略。

    Returns:
        tuple[bool, str | dict]: (是否成功, 执行结果或错误信息)。
    """

    command, error = _build_script_command(
        script_path,
        normalized_args,
        powershell_bypass_execution_policy=powershell_bypass_execution_policy,
    )
    if command is None:
        return False, error or f"不支持的脚本类型: {script_path.suffix}"

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(script_path.parent),
            env=_build_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 输出回收任务统一走 task_manager；daemon=True 是因为超时判定与进程收尾都由
        # 本函数用 wait_for 自己控制，不需要 WatchDog 再判一次超时。
        output_task = get_task_manager().create_task(
            process.communicate(),
            name=f"skill_script_output:{script_path.name}",
            daemon=True,
        )
        communicate_task = cast("asyncio.Task[tuple[bytes, bytes]]", output_task.task)
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.shield(communicate_task),
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await _finalize_timed_out_process(
            process,
            communicate_task,
        )
        return _compose_result(
            False,
            f"脚本执行超时（{SCRIPT_TIMEOUT_SECONDS}秒）",
            _decode_captured_output(stdout_bytes, stderr_bytes),
        )
    except Exception as error:
        logger.error(f"执行 skill 脚本失败: {error}")
        return False, f"执行脚本失败: {error}"

    captured_output = _decode_captured_output(stdout_bytes, stderr_bytes)
    if process.returncode == 0:
        return _compose_result(True, f"脚本已执行: {script_path.name}", captured_output)
    return _compose_result(
        False,
        f"脚本执行退出码: {process.returncode}",
        captured_output,
    )


async def _finalize_timed_out_process(
    process: asyncio.subprocess.Process,
    communicate_task: asyncio.Task[tuple[bytes, bytes]],
) -> tuple[bytes, bytes]:
    """在脚本超时后收尾子进程并尽量回收输出。

    Args:
        process: 已被 ``kill()`` 的子进程对象。
        communicate_task: 负责读取该进程 stdout/stderr 的任务。

    Returns:
        tuple[bytes, bytes]: (标准输出字节, 标准错误字节)；
        宽限期内没能收回输出时返回两个空字节串。
    """

    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=SCRIPT_KILL_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("脚本超时后进程未在宽限期内退出，放弃等待退出码")
    except Exception as error:
        logger.warning(f"等待超时脚本进程退出时出错: {error}")

    try:
        return await asyncio.wait_for(
            communicate_task,
            timeout=SCRIPT_KILL_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("脚本超时后 communicate 未在宽限期内结束，放弃收集残余输出")
    except Exception as error:
        logger.warning(f"收集超时脚本输出时出错: {error}")

    communicate_task.cancel()
    with suppress(asyncio.CancelledError):
        await communicate_task
    return b"", b""


def _build_script_command(
    script_path: Path,
    normalized_args: list[str],
    *,
    powershell_bypass_execution_policy: bool,
) -> tuple[list[str] | None, str | None]:
    """按后缀构建脚本的子进程执行命令。

    Args:
        script_path: 脚本绝对路径。
        normalized_args: 归一化后的脚本参数。
        powershell_bypass_execution_policy: 是否允许 .ps1 绕过 PowerShell 执行策略。

    Returns:
        tuple[list[str] | None, str | None]: (命令列表, 错误信息)；
        构建失败时第一项为 None。
    """

    suffix = script_path.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(script_path), *normalized_args], None
    if suffix == ".ps1":
        return _build_powershell_command(
            script_path,
            normalized_args,
            bypass_execution_policy=powershell_bypass_execution_policy,
        )
    if suffix in {".bat", ".cmd"}:
        return _build_windows_batch_command(script_path, normalized_args)
    if suffix == ".sh":
        shell_runner = shutil.which("bash") or shutil.which("sh")
        if shell_runner is None:
            return None, "未找到可用的 shell 解释器"
        return [shell_runner, str(script_path), *normalized_args], None
    return None, f"不支持的脚本类型: {suffix}"


def _build_powershell_command(
    script_path: Path,
    normalized_args: list[str],
    *,
    bypass_execution_policy: bool,
) -> tuple[list[str] | None, str | None]:
    """构建 .ps1 的 PowerShell 执行命令。

    默认不再附加 ``-ExecutionPolicy Bypass``：执行策略是 Windows 上阻止未签名脚本
    运行的纵深防御，由代码单方面绕过等于替运维取消了这道防线。确需在受限策略的
    机器上运行未签名 skill 脚本时，运维可显式打开配置开关。

    ``-NoProfile`` 避免加载用户 profile 脚本（额外的、与 skill 无关的代码），
    ``-NonInteractive`` 让脚本在需要交互输入时直接失败而不是挂到超时。

    残留风险（有意接受，不在此处消除）：``-File`` 模式把参数当字面字符串交给脚本的
    ``param()`` 绑定器，所以不存在把参数重新解析成 PowerShell 代码的注入面，但以 ``-``
    开头的参数确实会绑定到脚本声明的命名参数上 —— 也就是说调用者可以触达脚本的完整
    参数面。这对 ``.py`` 的 argparse、``.sh`` 的 getopts 同样成立，属于「给脚本传参」
    这个功能的固有语义，无法在不砍掉传参能力的前提下消除；实际约束靠的是
    ``authorize_script_execution`` 的调用者权限门与「skill 目录内容可信」这一前提。

    Args:
        script_path: 脚本绝对路径。
        normalized_args: 归一化后的脚本参数。
        bypass_execution_policy: 是否附加 ``-ExecutionPolicy Bypass``。

    Returns:
        tuple[list[str] | None, str | None]: (命令列表, 错误信息)；
        找不到解释器时第一项为 None。
    """

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        return None, "未找到可用的 PowerShell 解释器"

    command = [powershell, "-NoProfile", "-NonInteractive"]
    if bypass_execution_policy:
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(["-File", str(script_path), *normalized_args])
    return command, None


def _build_windows_batch_command(
    script_path: Path,
    normalized_args: list[str],
) -> tuple[list[str] | None, str | None]:
    """构建 .bat/.cmd 的 cmd.exe 执行命令。

    cmd.exe 会对最终命令行再解析一遍元字符，因此这里对每个参数强制字符白名单：
    只要出现 ``& | < > ^ ( ) " % !``、空白或换行就直接拒绝，避免参数逃逸成独立命令。
    脚本路径本身来自运维放进 skill 目录的文件，与脚本内容同属可信输入，不参与校验。

    Args:
        script_path: 脚本绝对路径。
        normalized_args: 归一化后的脚本参数。

    Returns:
        tuple[list[str] | None, str | None]: (命令列表, 错误信息)；
        参数含元字符或找不到解释器时第一项为 None。
    """

    for argument in normalized_args:
        if not _CMD_SAFE_ARGUMENT_RE.fullmatch(argument):
            return None, (
                f".bat/.cmd 参数只允许{_CMD_SAFE_ARGUMENT_HINT}，已拒绝执行: {argument!r}"
            )

    # 用绝对路径定位解释器，避免 CreateProcess 的搜索顺序命中工作目录下的同名文件。
    command_interpreter = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
    if not command_interpreter:
        return None, "未找到可用的 cmd.exe 解释器"
    return [command_interpreter, "/c", str(script_path), *normalized_args], None


def _build_subprocess_env() -> dict[str, str]:
    """构建脚本子进程的环境变量。

    - ``PYTHONIOENCODING``：强制子进程内的 Python 以 UTF-8 输出，避免 Windows 默认
      走本地代码页（如 cp936）导致回收到的中文输出变成乱码。
    - ``PYTHONUNBUFFERED``：关闭块缓冲，脚本超时被杀死时仍能回收已产生的输出。

    Returns:
        dict[str, str]: 在当前进程环境基础上覆盖上述两项后的环境变量表。
    """

    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def _decode_captured_output(stdout_bytes: bytes, stderr_bytes: bytes) -> str:
    """把子进程的原始输出解码并拼接为可回显文本。

    Args:
        stdout_bytes: 子进程标准输出原始字节。
        stderr_bytes: 子进程标准错误原始字节。

    Returns:
        str: 拼接后的文本；两个流都为空时返回空字符串。
    """

    return _compose_output_text(
        stdout_bytes.decode("utf-8", errors="replace").strip(),
        stderr_bytes.decode("utf-8", errors="replace").strip(),
    )


def _compose_result(
    success: bool,
    headline: str,
    captured_output: str,
) -> tuple[bool, str]:
    """把执行结论与捕获输出合成为统一的工具返回值。

    Args:
        success: 本次执行是否算成功。
        headline: 结论行，例如 ``脚本已执行: x.py``。
        captured_output: 已拼接好的 stdout/stderr 文本，可为空。

    Returns:
        tuple[bool, str]: (是否成功, 结论行与输出拼接后的文本)。
    """

    if captured_output:
        return success, f"{headline}\n\n{captured_output}"
    return success, headline


def _compose_output_text(stdout_text: str, stderr_text: str) -> str:
    """拼接标准输出与标准错误文本。

    Args:
        stdout_text: 已去除首尾空白的标准输出文本。
        stderr_text: 已去除首尾空白的标准错误文本。

    Returns:
        str: 带 ``[stdout]`` / ``[stderr]`` 分节标记的文本；两者都为空时返回空字符串。
    """

    output_sections: list[str] = []
    if stdout_text:
        output_sections.append(f"[stdout]\n{stdout_text}")
    if stderr_text:
        output_sections.append(f"[stderr]\n{stderr_text}")
    return "\n\n".join(output_sections)
