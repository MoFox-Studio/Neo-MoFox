"""Booku Memory Store 插件配置。

精简后的配置只保留存储和检索相关的配置项。
移除了插件开关、闪回、事件提醒、LLM 等高级机制。
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class BookuMemoryStoreConfig(BaseConfig):
    """Booku Memory Store 插件配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Booku Memory Store 配置"

    @config_section("storage", title="存储配置", tag="database")
    class StorageSection(SectionBase):
        metadata_db_path: str = Field(
            default="data/booku_memory_store/metadata.db",
            description="SQLite 元数据数据库路径",
            label="元数据数据库",
            input_type="text",
            tag="file"
        )
        vector_db_path: str = Field(
            default="data/chroma_db/booku_memory_store",
            description="向量数据库路径",
            label="向量数据库",
            input_type="text",
            tag="file"
        )
        default_folder_id: str = Field(
            default="default",
            description="默认文件夹 ID",
            label="默认文件夹",
            placeholder="default",
            tag="general"
        )

    @config_section("retrieval", title="检索配置", tag="ai")
    class RetrievalSection(SectionBase):
        default_top_k: int = Field(
            default=5, description="默认召回条数", label="默认召回数",
            ge=1, le=50, tag="performance"
        )
        include_archived_default: bool = Field(
            default=False, description="默认是否检索归档记忆", label="默认检索归档", tag="general"
        )
        include_knowledge_default: bool = Field(
            default=False, description="默认是否检索知识库", label="默认检索知识库", tag="general"
        )
        deduplication_threshold: float = Field(
            default=0.88, description="结果去重余弦阈值", label="去重阈值",
            ge=0.0, le=1.0, step=0.01, input_type="slider", tag="performance"
        )
        base_beta: float = Field(
            default=0.3, description="向量重塑基准强度", label="重塑基准强度",
            ge=0.0, le=1.0, step=0.05, input_type="slider", tag="ai"
        )
        logic_depth_scale: float = Field(
            default=0.5, description="逻辑深度对 beta 的增益系数", label="逻辑深度系数",
            ge=0.0, le=2.0, step=0.1, tag="ai"
        )
        core_boost_min: float = Field(
            default=1.2, description="核心标签最小增强", label="核心标签最小增强",
            ge=1.0, le=3.0, step=0.1, tag="performance"
        )
        core_boost_max: float = Field(
            default=1.4, description="核心标签最大增强", label="核心标签最大增强",
            ge=1.0, le=3.0, step=0.1, tag="performance"
        )
        diffusion_boost: float = Field(
            default=0.3, description="扩散标签增强权重", label="扩散增强权重",
            ge=0.0, le=1.0, step=0.05, tag="performance"
        )
        opposing_penalty: float = Field(
            default=0.5, description="对立标签惩罚权重", label="对立惩罚权重",
            ge=0.0, le=1.0, step=0.05, tag="performance"
        )

    @config_section("write_conflict", title="写入冲突检测", tag="ai")
    class WriteConflictSection(SectionBase):
        top_n: int = Field(
            default=8, description="写入冲突检查的检索样本数", label="检索样本数",
            ge=1, le=50, tag="performance"
        )
        energy_cutoff: float = Field(
            default=0.1, description="新颖度能量阈值，低于此值触发合并", label="新颖度阈值",
            ge=0.0, le=1.0, step=0.05, input_type="slider", tag="ai"
        )

    storage: StorageSection = Field(default_factory=StorageSection)
    retrieval: RetrievalSection = Field(default_factory=RetrievalSection)
    write_conflict: WriteConflictSection = Field(default_factory=WriteConflictSection)
