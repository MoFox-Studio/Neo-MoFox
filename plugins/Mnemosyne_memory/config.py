"""mnemosyne_memory 插件配置。"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any, ClassVar, Self
from uuid import uuid4

from pydantic import model_validator

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section

from .diary.config import DiaryConfig
from .vnext.config_sections import VNextConfig


def _restore_diary_parent_fields(data: Any) -> Any:
    """Recover known parent fields emitted under the final legacy child table."""
    if not isinstance(data, dict) or not isinstance(data.get("diary"), dict):
        return data
    diary = data["diary"]
    private = diary.get("private")
    if not isinstance(private, dict):
        return data
    misplaced = (set(DiaryConfig.model_fields) - {"group", "private"}) & private.keys()
    if not misplaced and "recovery_messages" not in private:
        return data
    restored, policy = dict(diary), dict(private)
    for name in misplaced:
        value = policy.pop(name)
        # An explicitly configured parent value takes precedence.
        restored.setdefault(name, value)
    policy.pop("recovery_messages", None)
    restored["private"] = policy
    return {**data, "diary": restored}


@config_section("plugin", title="插件设置", tag="plugin")
class PluginSection(SectionBase):
    """插件级开关。"""

    enabled: bool = Field(
        default=True,
        description="插件总开关",
        label="启用插件",
        tag="plugin",
    )


@config_section("storage", title="存储配置", tag="database")
class StorageSection(SectionBase):
    """vNext 数据库与向量索引路径。"""

    vector_db_path: str = Field(
        default="data/mnemosyne_memory/chroma",
        description="向量数据库路径（ChromaDB）",
        label="向量库路径",
        input_type="text",
        tag="file",
    )
    vnext_db_path: str = Field(
        default="data/mnemosyne_memory/vnext.db",
        description="正式记忆数据库路径",
        label="记忆数据库路径",
        input_type="text",
        tag="file",
    )


class MnemosyneMemoryConfig(BaseConfig):
    """Mnemosyne Memory 连续记忆配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "正式记忆、人物印象、前馈检索与聊天日记配置"

    @model_validator(mode="before")
    @classmethod
    def restore_legacy_diary_layout(cls, data: Any) -> Any:
        """Accept saved legacy layouts from both model_validate and WebUI writes."""
        return _restore_diary_parent_fields(data)

    @classmethod
    def load(cls, path: str | Path, *, auto_update: bool = False) -> Self:
        """Repair before the host's default merge can discard misplaced values."""
        config_path = Path(path)
        if auto_update and config_path.is_file():
            original_text = config_path.read_text(encoding="utf-8")
            raw = tomllib.loads(original_text)
            restored = _restore_diary_parent_fields(raw)
            if restored is not raw:
                from src.kernel.config.core import _render_toml_with_signature

                # Validate first: malformed user values must not be reset to defaults.
                config = cls.from_dict(restored)
                rendered = _render_toml_with_signature(cls, config.model_dump())
                reparsed = cls.from_dict(tomllib.loads(rendered))
                if reparsed.model_dump() != config.model_dump():
                    raise ValueError("日记配置修复后的保存回读结果不一致")
                backup = config_path.with_name(
                    f"{config_path.name}.diary-layout-{uuid4().hex}.bak"
                )
                shutil.copy2(config_path, backup)
                config_path.write_text(rendered, encoding="utf-8")
                return config
        return super().load(config_path, auto_update=auto_update)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """过滤本地配置中的废弃字段，保留当前配置的严格校验。"""
        filtered: dict[str, Any] = dict(data)
        for section in (
            "retrieval",
            "write_conflict",
            "short_term",
            "flashback",
            "journal",
            "persona",
            "internal_llm",
        ):
            filtered.pop(section, None)

        storage = filtered.get("storage")
        if isinstance(storage, dict):
            filtered_storage = dict(storage)
            filtered_storage.pop("metadata_db_path", None)
            filtered["storage"] = filtered_storage

        vnext = filtered.get("vnext")
        if isinstance(vnext, dict):
            filtered_vnext = dict(vnext)
            filtered_vnext.pop("candidate_encoder", None)
            filtered_vnext.pop("sleep", None)
            persona = filtered_vnext.get("persona")
            if isinstance(persona, dict):
                filtered_persona = dict(persona)
                filtered_persona.pop("max_length", None)
                filtered_vnext["persona"] = filtered_persona
            filtered["vnext"] = filtered_vnext

        diary = filtered.get("diary")
        if isinstance(diary, dict):
            filtered_diary = dict(diary)
            filtered_diary.pop("recovery_messages", None)
            filtered["diary"] = filtered_diary

        return super().from_dict(filtered)

    plugin: PluginSection = Field(label="插件设置", default_factory=PluginSection)
    storage: StorageSection = Field(label="存储配置", default_factory=StorageSection)

    vnext: VNextConfig = Field(label="记忆与召回", default_factory=VNextConfig)
    diary: DiaryConfig = Field(label="聊天日记", default_factory=DiaryConfig)
