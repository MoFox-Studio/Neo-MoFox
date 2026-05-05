"""SkillManager 共享数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SkillEntry:
    """Skill 索引信息（不包含全文）。"""

    name: str
    description: str
    root_dir: Path
    skill_md_path: Path
    files: list[str]
