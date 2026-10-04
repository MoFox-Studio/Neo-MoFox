"""engram_memory 插件配置。"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section

from .diary.config import DiaryConfig
from .vnext.config_sections import VNextConfig


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
        default="data/engram_memory/chroma",
        description="向量数据库路径（ChromaDB）",
        label="向量库路径",
        input_type="text",
        tag="file",
    )
    vnext_db_path: str = Field(
        default="data/engram_memory/vnext.db",
        description="正式记忆数据库路径",
        label="vNext 数据库路径",
        input_type="text",
        tag="file",
    )


class EngramMemoryConfig(BaseConfig):
    """Engram Memory 连续记忆配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Engram Memory 正式记忆、人物印象与自然闪回"

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

    plugin: PluginSection = Field(default_factory=PluginSection)
    storage: StorageSection = Field(default_factory=StorageSection)

    vnext: VNextConfig = Field(default_factory=VNextConfig)
    diary: DiaryConfig = Field(default_factory=DiaryConfig)
