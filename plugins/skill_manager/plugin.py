from __future__ import annotations

import os
import re
from pathlib import Path

from src.core.components import BasePlugin, register_plugin
from src.core.prompt import SystemReminderInsertType
from src.kernel.logger import get_logger

from .config import SkillManagerConfig
from .handlers import SkillManagerLoadHandler
from .models import SkillEntry
from .tools import SkillGetReferenceTool, SkillGetScriptTool, SkillGetTool

logger = get_logger("skill_manager")
_FRONT_MATTER_FIELD_RE = re.compile(r"^(name|description)\s*:\s*(.+)$", re.IGNORECASE)


def _is_path_inside(base_dir: Path, target_path: Path) -> bool:
    """判断目标路径是否在指定根目录内部。"""

    try:
        target_path.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def _strip_quoted_text(value: str) -> str:
    """去除首尾引号并清理空白。"""

    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def _parse_skill_front_matter(raw_text: str) -> tuple[str | None, str | None]:
    """从 SKILL.md 首段 front matter 提取 name 和 description。"""

    lines = raw_text.splitlines()
    if len(lines) < 3:
        return None, None
    if lines[0].strip() != "---":
        return None, None

    parsed_name: str | None = None
    parsed_description: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        matched = _FRONT_MATTER_FIELD_RE.match(line.strip())
        if not matched:
            continue
        key, value = matched.groups()
        normalized_value = _strip_quoted_text(value)
        if key.lower() == "name":
            parsed_name = normalized_value
        elif key.lower() == "description":
            parsed_description = normalized_value
    return parsed_name, parsed_description


@register_plugin
class SkillManagerPlugin(BasePlugin):
    """Skill 管理器插件。"""

    plugin_name: str = "skill_manager"
    plugin_description: str = "技能管理器"
    plugin_version: str = "1.0.0"

    configs: list[type] = [SkillManagerConfig]

    def __init__(self, config: SkillManagerConfig | None = None) -> None:
        """初始化 SkillManager 运行态。"""

        super().__init__(config)
        self._workspace_root: Path = Path(__file__).resolve().parents[2]
        self.paths: list[str] = []
        self.skills: dict[str, SkillEntry] = {}
        self.skill_contents: dict[str, str] = {}
        self.injected_skills: set[str] = set()

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""

        if (
            isinstance(self.config, SkillManagerConfig)
            and not self.config.manager.enabled
        ):
            logger.info("skill_manager 已在配置中禁用")
            return []
        return [
            SkillManagerLoadHandler,
            SkillGetTool,
            SkillGetReferenceTool,
            SkillGetScriptTool,
        ]

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时清理 system reminder。"""

        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.delete("actor", "skill_manager_catalog")
        store.delete("sub_actor", "skill_manager_catalog")

    async def refresh_skill_catalog(self) -> None:
        """扫描配置路径并刷新 skill 索引。"""

        configured_paths = self._resolve_skill_paths()
        self.paths = [str(item) for item in configured_paths]

        discovered: dict[str, SkillEntry] = {}
        for base_dir in configured_paths:
            if not base_dir.exists() or not base_dir.is_dir():
                logger.warning(f"skill 路径不存在或不可读，已跳过: {base_dir}")
                continue

            for skill_root in self._iter_skill_roots(base_dir):
                skill_md_path = skill_root / "SKILL.md"
                if not skill_md_path.is_file():
                    continue

                text = skill_md_path.read_text(encoding="utf-8")
                parsed_name, parsed_description = _parse_skill_front_matter(text)
                skill_name = (parsed_name or skill_root.name).strip()
                skill_description = (
                    parsed_description
                    or f"Skill {skill_name}，通过 get_skill 读取后可使用扩展引用与脚本"
                ).strip()

                markdown_files = [
                    path.relative_to(skill_root).as_posix()
                    for path in sorted(skill_root.rglob("*.md"))
                    if path.is_file()
                ]

                discovered[skill_name] = SkillEntry(
                    name=skill_name,
                    description=skill_description,
                    root_dir=skill_root,
                    skill_md_path=skill_md_path,
                    files=markdown_files,
                )

        self.apply_discovered_skills(discovered)
        logger.info(f"skill_manager 已刷新 skill 索引，数量: {len(self.skills)}")

    def _resolve_skill_paths(self) -> list[Path]:
        """将配置中的路径转换为绝对路径列表。"""

        default_paths = ["skill"]
        if isinstance(self.config, SkillManagerConfig):
            configured = [
                item.strip() for item in self.config.manager.paths if item.strip()
            ]
            paths = configured or default_paths
        else:
            paths = default_paths

        resolved_paths: list[Path] = []
        for raw_path in paths:
            expanded_path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
            if expanded_path.is_absolute():
                resolved_paths.append(expanded_path)
                continue
            resolved_paths.append((self._workspace_root / expanded_path).resolve())
        return resolved_paths

    @staticmethod
    def _iter_skill_roots(base_dir: Path) -> list[Path]:
        """从基目录中解析 skill 根目录集合。"""

        if (base_dir / "SKILL.md").is_file():
            return [base_dir]

        skill_roots: list[Path] = []
        for child in sorted(base_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                skill_roots.append(child)
        return skill_roots

    def apply_discovered_skills(self, discovered: dict[str, SkillEntry]) -> None:
        """应用刷新后的 skill 索引并清理运行态缓存。"""

        self.skills = discovered
        valid_names = set(self.skills.keys())
        self.skill_contents = {
            name: content
            for name, content in self.skill_contents.items()
            if name in valid_names
        }
        self.injected_skills = {
            name for name in self.injected_skills if name in valid_names
        }
        self._sync_system_reminder()

    def _resolve_skill_relative_path(
        self,
        *,
        skill_entry: SkillEntry,
        relative_path: str,
        required_suffix: str,
    ) -> tuple[Path | None, str | None]:
        """将 skill 内相对路径解析为受限的绝对路径。"""

        normalized_location = relative_path.strip().replace("\\", "/")
        if not normalized_location:
            return None, "location 不能为空"

        resolved_target = (skill_entry.root_dir / normalized_location).resolve()
        if not _is_path_inside(skill_entry.root_dir, resolved_target):
            return None, f"location 越界: {relative_path}"
        if not resolved_target.is_file():
            return None, f"文件不存在: {relative_path}"
        if resolved_target.suffix.lower() != required_suffix:
            return None, f"仅支持 {required_suffix} 文件: {relative_path}"
        return resolved_target, None

    def _sync_system_reminder(self) -> None:
        """更新 actor/sub_actor 的 skill 清单提示。"""

        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        if not self.skills:
            store.delete("actor", "skill_manager_catalog")
            store.delete("sub_actor", "skill_manager_catalog")
            return

        reminder_lines = [
            "## SkillManager 可用技能清单",
            "当任务复杂、上下文长、需要专用流程时，可按需读取 skill。",
            "先调用 get_skill(name) 注入后，再按需使用 get_reference/get_script 逐步展开。",
            "",
        ]
        for entry in sorted(self.skills.values(), key=lambda item: item.name.lower()):
            reminder_lines.append(f"- {entry.name}: {entry.description}")

        reminder_text = "\n".join(reminder_lines)

        inject_actor = True
        inject_sub_actor = True
        if isinstance(self.config, SkillManagerConfig):
            inject_actor = self.config.manager.inject_actor_reminder
            inject_sub_actor = self.config.manager.inject_sub_actor_reminder

        if inject_actor:
            store.set(
                "actor",
                name="skill_manager_catalog",
                content=reminder_text,
                insert_type=SystemReminderInsertType.DYNAMIC,
            )
        else:
            store.delete("actor", "skill_manager_catalog")

        if inject_sub_actor:
            store.set(
                "sub_actor",
                name="skill_manager_catalog",
                content=reminder_text,
                insert_type=SystemReminderInsertType.DYNAMIC,
            )
        else:
            store.delete("sub_actor", "skill_manager_catalog")
