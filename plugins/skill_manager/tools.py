"""SkillManager 对外工具组件。"""

from __future__ import annotations

import io
import runpy
import shlex
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import Annotated

from src.core.components import BaseTool
from src.kernel.logger import get_logger


logger = get_logger("skill_manager.tool")


class SkillGetTool(BaseTool):
    """读取并注入 skill 主文档。"""

    tool_name: str = "get_skill"
    tool_description: str = "按 skill 名称读取 SKILL.md 原文，并标记为已注入。"

    async def execute(
        self,
        name: Annotated[str, "skill 名称（来自 skill 列表）"],
    ) -> tuple[bool, str | dict]:
        """返回 SKILL.md 全文。"""

        resolved_name = name.strip()
        if not resolved_name:
            return False, "name 不能为空"

        plugin = self.plugin
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

    tool_name: str = "get_reference"
    tool_description: str = (
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

        plugin = self.plugin
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
    """直接执行 skill 下 python 脚本。"""

    tool_name: str = "get_script"
    tool_description: str = (
        "在已通过 get_skill(name) 注入对应 skill 后，"
        "按相对路径直接执行该 skill 目录下 python 脚本（等价 python xxx.py）。"
        "可选通过 script_args 传入命令行参数。"
    )

    async def execute(
        self,
        name: Annotated[str, "已注入的 skill 名称"],
        location: Annotated[str, "该 skill 目录内 python 文件相对路径，例如 scripts/toolbox.py"],
        script_args: Annotated[
            list[str] | str,
            "可选脚本参数；支持字符串（如 '--check 60 --bonus 1'）或字符串列表（如 ['--check', '60']）",
        ] | None = None,
    ) -> tuple[bool, str | dict]:
        """返回脚本执行结果。"""

        resolved_name = name.strip()
        if not resolved_name:
            return False, "name 不能为空"

        plugin = self.plugin
        if resolved_name not in plugin.injected_skills:
            return False, f"skill '{resolved_name}' 尚未注入，请先调用 get_skill"

        entry = plugin.skills.get(resolved_name)
        if entry is None:
            return False, f"未找到 skill: {resolved_name}"

        script_path, error = plugin._resolve_skill_relative_path(
            skill_entry=entry,
            relative_path=location,
            required_suffix=".py",
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
            logger.exception("执行 skill 脚本失败")
            captured_output = _compose_captured_output(stdout_buffer, stderr_buffer)
            if captured_output:
                return False, f"执行脚本失败: {error}\n\n{captured_output}"
            return False, f"执行脚本失败: {error}"
        finally:
            sys.argv = old_argv


def _compose_captured_output(stdout_buffer: io.StringIO, stderr_buffer: io.StringIO) -> str:
    """拼接脚本执行期间捕获的标准输出与标准错误。"""

    stdout_text = stdout_buffer.getvalue().strip()
    stderr_text = stderr_buffer.getvalue().strip()
    output_sections: list[str] = []
    if stdout_text:
        output_sections.append(f"[stdout]\n{stdout_text}")
    if stderr_text:
        output_sections.append(f"[stderr]\n{stderr_text}")
    return "\n\n".join(output_sections)
